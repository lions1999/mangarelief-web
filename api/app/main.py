"""MangaRelief web API.

Three endpoints carry the product — create a job, poll it, download the result
— plus an image analysis helper (the numbers the desktop UI derives on load)
and an internal cleanup endpoint that enforces the retention policy.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
import re
import uuid
from urllib.parse import urlparse
from typing import List, Optional

from fastapi import (Depends, FastAPI, Form, Header, HTTPException, Request,
                     Response, UploadFile, File, status)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

import httpx

from .analysis import analyze, resolve_params
from . import auth as auth_mod
from .auth import current_user
from . import emails
from .config import settings
from .imaging import ImageTooLarge, UndecodableImage, decode_upload
from .jobs import safe_stem, QueueFull, runner
from .limits import clamp_to_anonymous_tier, hash_ip, turnstile_ok
from . import preview
from . import quota
from .schemas import (AnalysisResult, Artifact, CodeRequest, FilamentChange,
                      JobCreated, JobParams, JobStatus, JobView, RefreshRequest,
                      SessionOut, VerifyRequest)
from .storage import get_storage
from .store import (get_store, iso, parse_iso, shortened_expiry, default_expiry,
                    utcnow)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mangarelief.api")

app = FastAPI(
    title="MangaRelief API",
    version="0.1.0",
    description="Turns 2D artwork into terraced 3D-printable meshes (STL + Bambu 3MF).",
)

def _preview_origin_regex(origins: List[str]) -> Optional[str]:
    """Also allow the per-deployment preview URLs of a Cloudflare Pages project.

    Pages serves every build at <hash>.<project>.pages.dev as well as at the
    production hostname. Testing on one of those would otherwise fail CORS for
    no obvious reason. Only subdomains of the configured project are allowed,
    not pages.dev at large.
    """
    projects = [re.escape(host) for o in origins
                if (host := o.removeprefix("https://")).endswith(".pages.dev")]
    return r"https://[a-z0-9-]+\.(" + "|".join(projects) + ")" if projects else None


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_origin_regex=_preview_origin_regex(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # Custom headers are invisible to cross-origin JavaScript unless listed
    # here. Notes rode on the job response from day one and the frontend read
    # null every time — the free-tier message never reached anyone.
    expose_headers=["X-MangaRelief-Notes", "X-MangaRelief-Ambiguous",
                    "X-MangaRelief-Expires-At"],
)

CONTENT_TYPES = {"stl": "model/stl", "3mf": "model/3mf"}


# ---------------------------------------------------------------- helpers
def client_ip(request: Request) -> str:
    """Cloud Run and Cloudflare both put the real address first in XFF."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def display_name(filename: Optional[str]) -> Optional[str]:
    """Il nome del file caricato, ripulito quanto basta per mostrarlo.

    Diverso da `safe_stem`, che produce un nome da mettere in un percorso: qui
    servono gli spazi e gli accenti, perche' e' cosi' che chi guarda la
    cronologia riconosce la propria tavola. Si tolgono i caratteri di
    controllo e la lunghezza eccessiva, e nient'altro.
    """
    if not filename:
        return None
    nome = filename.replace("\\", "/").rsplit("/", 1)[-1]
    nome = "".join(c for c in nome if c.isprintable()).strip()
    return nome[:120] or None


def read_upload(image: UploadFile) -> bytes:
    """Read the upload, refusing anything past the size limit as we go."""
    chunks, total = [], 0
    while True:
        chunk = image.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"upload exceeds {settings.max_upload_bytes // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    if not total:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    return b"".join(chunks)


def decode_or_422(data: bytes):
    try:
        return decode_upload(data)
    except ImageTooLarge as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except UndecodableImage as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def parse_params(raw: Optional[str]) -> JobParams:
    if not raw:
        return JobParams()
    try:
        return JobParams.model_validate_json(raw)
    except ValidationError as exc:
        # exc.errors() carries the original exception objects in `ctx`, which
        # are not JSON serialisable; the model's own json() is already clean.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            json.loads(exc.json())) from exc


def _describe(exc: Exception) -> str:
    """A short, safe description of a failure, for a caller who has to fix it.

    HTTP errors carry the upstream status and body; transport errors carry the
    host they failed to reach, because "Name or service not known" without a
    hostname is a dead end — the answer is almost always a typo in the URL.
    Neither leaks credentials: the keys travel in headers we never echo.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.response.status_code} {exc.response.text[:300]}"
    if isinstance(exc, httpx.RequestError) and exc.request is not None:
        return f"{type(exc).__name__} reaching {exc.request.url.host}: {exc}"[:300]
    return f"{type(exc).__name__}: {exc}"[:300]


def record_to_view(record: dict, base_url: str) -> JobView:
    expires = parse_iso(record.get("expires_at"))
    job_status = record["status"]
    if expires and expires <= utcnow() and job_status != "error":
        job_status = JobStatus.EXPIRED.value

    artifacts = []
    if job_status == JobStatus.DONE.value:
        for a in record.get("artifacts") or []:
            artifacts.append(Artifact(
                kind=a["kind"], filename=a["filename"], bytes=a["bytes"],
                download_url=f"{base_url}api/jobs/{record['id']}/artifacts/{a['kind']}",
            ))

    return JobView(
        job_id=record["id"],
        status=JobStatus(job_status),
        progress=int(record.get("progress") or 0),
        message=record.get("message") or "",
        mode=record["mode"],
        created_at=record["created_at"],
        expires_at=record.get("expires_at"),
        downloaded_at=record.get("downloaded_at"),
        duration_s=record.get("duration_s"),
        error=record.get("error"),
        artifacts=artifacts,
        filament_changes=[FilamentChange(**c)
                          for c in (record.get("filament_changes") or [])],
    )


# ----------------------------------------------------------------- routes
@app.get("/")
def root():
    """Say what this is.

    Without it the root path answers a bare `{"detail":"Not Found"}`, which
    reads like a broken deployment to anyone who opens the base URL — and the
    base URL is exactly what people paste into a browser first.
    """
    return {
        "service": "MangaRelief API",
        "docs": "/docs",
        "health": "/api/health",
        "source": "https://github.com/lions1999/mangarelief-web",
    }


# Due percorsi, la stessa risposta, e il primo e' quello che conta.
#
# Su Cloud Run `/healthz` non arriva mai qui: viene respinto dal bordo di
# Google prima ancora di entrare nel percorso che serve questo servizio. E'
# misurato, non supposto — le risposte lo dicono da sole:
#
#   /healthz    404 text/html, referrer-policy: no-referrer, nessun'altra
#   /healthzz   404 application/json, server: Google Frontend,
#                   x-cloud-trace-context: 8ce7d4f1...
#
# `/healthzz` (due z, un percorso che qui dentro non esiste altrettanto) e' il
# 404 di FastAPI, e torna firmato e tracciato. `/healthz` non porta ne' la
# firma ne' l'identificativo di traccia che Google assegna alle richieste
# destinate a un servizio: non e' il container ad aver detto di no, e' che il
# container non l'ha mai sentita.
#
# La forma dell'intercettazione, sempre misurata: corrispondenza letterale ed
# esatta su quella stringa. `/healthz/` prende il 307 con cui FastAPI redirige
# per la barra finale (quindi la richiesta e' arrivata), `/Healthz` passa,
# `/healthz/x` passa; la query non conta (`/healthz?deep=true` viene preso
# uguale) e vale sia per GET sia per POST. Non e' una lista di nomi di sonda:
# `/livez`, `/readyz`, `/healthcheck` e `/_ah/health` arrivano tutti fin qui.
# Solo quella stringa e' sottratta.
#
# Il controllo dello stato serve soprattutto in produzione, quindi vive dove la
# produzione lo lascia arrivare. `/healthz` resta registrato perche' funziona
# ovunque tranne li' — in locale, in un container qualsiasi, dietro un altro
# proxy — e perche' toglierlo romperebbe qualunque cosa lo stia gia' chiamando.
@app.get("/api/health")
@app.get("/healthz")
def healthz(deep: bool = False):
    """Liveness, plus an optional database round-trip.

    `?deep=true` actually queries the store. Without it a misconfigured or
    unreachable database only shows up when a real job fails, which is a slow
    and confusing way to find out.
    """
    body = {
        "status": "ok",
        "backend": "supabase" if settings.use_supabase else "local",
        "queue": runner.pending,
        "version": app.version,
    }
    if settings.use_supabase:
        # The host, never the key: a project ref is public (it is in the
        # dashboard URL), and seeing it here is what catches a typo.
        body["supabase_host"] = urlparse(settings.supabase_url).hostname
    if deep:
        try:
            get_store().list_expired(utcnow(), limit=1)
            body["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - surfacing the reason is the point
            body["status"] = "degraded"
            body["database"] = _describe(exc)
            return body

        # Che il database risponda non vuol dire che abbia le colonne che
        # questo codice scrive. Una migrazione non applicata non rompe una
        # novita': rompe *ogni* generazione, perche' l'insert nomina tutte le
        # colonne — ed e' successo davvero, fra un push e la migrazione fatta
        # dopo. Qui si vede da un link invece che da chi carica un'immagine.
        try:
            problema = get_store().schema_problem()
        except Exception as exc:  # noqa: BLE001
            problema = _describe(exc)
        if problema:
            body["status"] = "degraded"
            body["schema"] = f"{problema} — apply the pending migration in supabase/migrations"
        else:
            body["schema"] = "ok"
    return body


@app.post("/api/analyze", response_model=AnalysisResult)
def analyze_image(
    image: UploadFile = File(...),
    params: Optional[str] = Form(None),
):
    """The tone analysis the desktop UI runs when an image is loaded.

    Lets a client show sensible defaults — and pre-filled Spot accents —
    without reimplementing the K-Means landmark logic in the browser.
    """
    p = parse_params(params)
    rgb = decode_or_422(read_upload(image))
    info = analyze(rgb, p.base_h, p.max_h, p.layer_height, p.halftone_threshold)
    return AnalysisResult(**info)


@app.post("/api/mockup")
def mockup(
    image: UploadFile = File(...),
    params: Optional[str] = Form(None),
):
    """A 2D preview of how the image will be classified, as a PNG.

    Spot Color is unusable without it: accents and coverage cannot be tuned
    blind, and a full generation per adjustment is far too slow. Deliberately
    computed at a small resolution — this is called on every slider move
    (debounced), not once per job.

    Il calcolo sta in `preview.render`, che serve anche la miniatura della
    cronologia: due implementazioni della stessa figura finirebbero per
    contraddirsi in faccia a chi guarda.
    """
    p = parse_params(params)
    rgb = decode_or_422(read_upload(image))
    try:
        immagine, extra_headers = preview.render(rgb, p)
    except preview.NoAccents as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    png = preview.encode_png(immagine)
    if png is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "preview encoding failed")

    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store", **extra_headers})


def _disposition(kind: str, filename: str) -> str:
    """Both forms: the plain one for old clients, filename* for anything
    outside ASCII. The stems are ASCII by construction, but the header is the
    wrong place to rely on that."""
    from urllib.parse import quote
    plain = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    return f"{kind}; filename=\"{plain}\"; filename*=UTF-8''{quote(filename)}"


@app.post("/api/auth/code", status_code=status.HTTP_204_NO_CONTENT)
def auth_code(body: CodeRequest, request: Request):
    """Manda un codice di sei cifre all'indirizzo indicato.

    Passa di qui e non dal browser direttamente perche' e' l'unico punto in cui
    possiamo rifiutare le caselle usa-e-getta e ricondurre gli alias a una sola
    identita' — `m.ario+x@gmail.com` e `mario@gmail.com` sono la stessa casella
    e devono essere lo stesso account, altrimenti la quota si aggira creando
    alias a raffica.

    Risponde uguale che l'indirizzo sia gia' registrato o no: distinguere
    sarebbe un modo per scoprire chi ha un account qui.
    """
    raw = (body.email or "").strip()
    if not emails.is_valid(raw):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "that is not a valid email address")
    if emails.is_disposable(raw):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "disposable addresses are not accepted, use a permanent one")
    auth_mod.send_code(emails.normalize(raw), hash_ip(client_ip(request)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/auth/verify", response_model=SessionOut)
def auth_verify(body: VerifyRequest):
    """Scambia il codice con una sessione, e attribuisce all'account le
    generazioni gia' fatte da anonimo su questo browser.

    Il collegamento avviene qui, nello stesso passaggio: chi ha provato due
    volte e poi si registra non riparte dal totale pieno — ed e' scritto nel
    messaggio di benvenuto, perche' scoprirlo dopo sarebbe una sorpresa
    sgradevole.
    """
    email = emails.normalize((body.email or "").strip())
    sess = auth_mod.verify_code(email, body.code.strip())
    user = sess.get("user") or {}
    uid = user.get("id")
    if not uid or not sess.get("access_token"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "the sign-in service returned an unusable session")

    linked = 0
    device = quota.clean_device_id(body.device_id)
    if device:
        since = utcnow() - timedelta(hours=settings.quota_window_h)
        try:
            linked = get_store().link_device(device, uid, since)
        except Exception:  # noqa: BLE001
            # Un fallimento qui regalerebbe generazioni, non le toglierebbe:
            # meglio accedere comunque che bloccare chi si sta registrando.
            log.warning("could not link device generations", exc_info=True)

    return SessionOut(access_token=sess["access_token"],
                      refresh_token=sess.get("refresh_token", ""),
                      expires_at=sess.get("expires_at"),
                      email=user.get("email"), user_id=uid, linked=linked)


@app.post("/api/auth/refresh", response_model=SessionOut)
def auth_refresh(body: RefreshRequest):
    sess = auth_mod.refresh_session(body.refresh_token)
    user = sess.get("user") or {}
    if not user.get("id") or not sess.get("access_token"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "your session expired, sign in again")
    return SessionOut(access_token=sess["access_token"],
                      refresh_token=sess.get("refresh_token", ""),
                      expires_at=sess.get("expires_at"),
                      email=user.get("email"), user_id=user["id"])


@app.get("/api/limits")
def read_limits():
    """Le regole del servizio, in numeri.

    Ogni numero che l'interfaccia dice sul servizio — quante generazioni, per
    quanto restano i file, quanto puo' pesare un caricamento — e' in realta'
    un'impostazione, e il testo che se lo riscrive dentro invecchia in
    silenzio: il piede della pagina ha annunciato "5 per hour" per mesi dopo
    che il limite era gia' diventato altro. Servirli da qui e' l'unico modo
    perche' la pagina non possa contraddire il server.

    Non dice nulla su chi sta chiedendo: e' la stessa risposta per tutti, e la
    quota personale resta a `/api/quota`.
    """
    return {
        "anon_generations": settings.quota_anon_daily,
        "user_generations": settings.quota_user_daily,
        "window_h": settings.quota_window_h,
        "retention_h": settings.retention_hours,
        "post_download_h": settings.post_download_hours,
        "max_upload_mb": round(settings.max_upload_bytes / (1024 * 1024)),
        "max_dim_mm": settings.anon_max_dim_mm,
        "max_res_cap": settings.anon_max_res_cap,
        "modes": list(settings.allowed_modes),
    }


@app.get("/api/quota")
def read_quota(
    request: Request,
    user: Optional[dict] = Depends(current_user),
    x_mangarelief_device: Optional[str] = Header(default=None),
):
    """Quante generazioni restano, senza consumarne una.

    Serve al contatore in pagina: chi arriva deve sapere quante ne ha *prima*
    di caricare un'immagine e scoprire di non poterla usare.
    """
    return quota.current(get_store(), user,
                         quota.clean_device_id(x_mangarelief_device),
                         hash_ip(client_ip(request))).as_dict()


@app.post("/api/jobs", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    request: Request,
    response: Response,
    image: UploadFile = File(...),
    params: Optional[str] = Form(None),
    turnstile_token: Optional[str] = Form(None),
    user: Optional[dict] = Depends(current_user),
    x_mangarelief_device: Optional[str] = Header(default=None),
):
    p = parse_params(params)
    if p.mode.value not in settings.allowed_modes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"mode '{p.mode.value}' is not available on the web service")

    ip = client_ip(request)
    if not turnstile_ok(turnstile_token or "", ip):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "captcha verification failed, reload and try again")
    ip_key = hash_ip(ip)
    device = quota.clean_device_id(x_mangarelief_device)
    quota.enforce(get_store(), user, device, ip_key)

    notes = clamp_to_anonymous_tier(p)
    data = read_upload(image)
    rgb = decode_or_422(data)
    engine_kwargs, info = resolve_params(rgb, p)

    if p.mode.value == "spot_color" and not engine_kwargs["spot_accents"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "spot_color needs at least one accent colour: the image has no vivid "
            "area to detect one, so pass spot_accents explicitly")

    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "created_at": iso(utcnow()),
        # Null for an anonymous caller. This is the column per-user quota
        # counts, in place of the IP hash it counts today.
        "user_id": user["id"] if user else None,
        "ip_hash": ip_key,
        # Quale browser: e' su questo che si contano le prove anonime, e il
        # filo che le lega all'account quando ci si registra dopo aver provato.
        "device_id": device,
        "mode": p.mode.value,
        "params": {"requested": p.model_dump(mode="json"),
                   "resolved": engine_kwargs, "analysis": info, "notes": notes},
        "status": JobStatus.QUEUED.value,
        "progress": 0,
        "message": "Queued",
        "duration_s": None,
        "error": None,
        "artifacts": [],
        "filament_changes": [],
        "expires_at": iso(default_expiry()),
        "downloaded_at": None,
        # Il nome com'e' stato caricato: serve solo a farsi riconoscere nella
        # cronologia, quindi si tiene com'era invece di ridurlo come i nomi dei
        # file prodotti. Ripulito lo stesso: e' testo di chi chiama.
        "image_name": display_name(image.filename),
    }

    try:
        get_store().insert(record)
    except Exception as exc:  # noqa: BLE001 - the caller has to know why
        # The row must exist before the job starts, so a database refusal has
        # to fail the request — but with the reason attached. The most common
        # cause is the anon key instead of the service-role one, which row
        # level security rejects on insert while still allowing reads.
        log.exception("insert refused")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"could not record the job: {_describe(exc)}") from exc

    try:
        runner.submit(job_id, rgb, p.mode.value, engine_kwargs,
                      source_stem=safe_stem(image.filename),
                      # Solo chi ha un account ha una cronologia, quindi solo
                      # per lui si conservano miniatura e sorgente.
                      user_id=user["id"] if user else None,
                      requested=p)
    except QueueFull as exc:
        get_store().update(job_id, {"status": JobStatus.ERROR.value,
                                    "message": "Busy", "error": str(exc)})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "server busy, try again shortly",
                            headers={"Retry-After": "30"}) from exc

    if notes:
        response.headers["X-MangaRelief-Notes"] = "; ".join(notes)
    return JobCreated(job_id=job_id, status=JobStatus.QUEUED,
                      status_url=f"{request.base_url}api/jobs/{job_id}")


@app.get("/api/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, request: Request):
    record = get_store().get(job_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown job")
    return record_to_view(record, str(request.base_url))


@app.get("/api/jobs/{job_id}/artifacts/{kind}")
def download_artifact(job_id: str, kind: str, preview: bool = False):
    """Stream one artifact.

    `preview=true` serves the same bytes for the in-page 3D viewer without
    counting as a download: the retention rule is "24h after the user takes
    the file", and looking at it in the browser is not taking it. It also
    keeps the free preview / paid download split of the roadmap meaningful.
    """
    if kind not in CONTENT_TYPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown artifact kind")

    store = get_store()
    record = store.get(job_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown job")

    expires = parse_iso(record.get("expires_at"))
    if expires and expires <= utcnow():
        raise HTTPException(status.HTTP_410_GONE, "this result has expired")
    if record["status"] != JobStatus.DONE.value:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"job is '{record['status']}', not ready to download")

    artifact = next((a for a in (record.get("artifacts") or []) if a["kind"] == kind), None)
    if not artifact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {kind} in this job")

    data = get_storage().get(artifact["key"])

    if preview:
        return Response(
            content=data,
            media_type=CONTENT_TYPES[kind],
            headers={
                "Content-Disposition": _disposition("inline", artifact["filename"]),
                "X-MangaRelief-Expires-At": record.get("expires_at") or "",
            },
        )

    # Retention: the first real download shortens the file's life to 24h.
    now = utcnow()
    fields = {"expires_at": iso(shortened_expiry(expires, now))}
    if not record.get("downloaded_at"):
        fields["downloaded_at"] = iso(now)
    store.update(job_id, fields)

    return Response(
        content=data,
        media_type=CONTENT_TYPES[kind],
        headers={
            "Content-Disposition": _disposition("attachment", artifact["filename"]),
            "X-MangaRelief-Expires-At": fields["expires_at"],
        },
    )


# ------------------------------------------------------------- cronologia
# Le generazioni di un account sopravvivono ai loro file: i file scadono a 48
# ore perche' pesano 9 MB l'uno, la voce resta perche' pesa 7 KB. Vedi la
# migrazione 20260904140000_history.sql per il perche' di ogni pezzo.


def _mia_riga(job_id: str, user: Optional[dict]) -> dict:
    """La riga, se e' di chi la chiede.

    Stessa risposta per «non esiste» e «non e' tua»: distinguerle direbbe a
    chiunque provi un identificativo se quella generazione esiste.
    """
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in to see your generations")
    record = get_store().get(job_id)
    if not record or record.get("user_id") != user["id"] or record.get("hidden_at"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such generation")
    return record


def _voce(record: dict, base_url: str) -> dict:
    """Una voce di cronologia: la stessa vista del job, piu' cio' che serve a
    riconoscerla e a rifarla.

    Lo stato e gli allegati vengono da `record_to_view`, non ricalcolati qui:
    e' la funzione che sa gia' che una riga «done» ma scaduta si legge come
    scaduta, e due implementazioni di quella regola divergono al primo ritocco.
    """
    vista = record_to_view(record, base_url)
    risolti = (record.get("params") or {}).get("resolved") or {}
    return {
        "id": record["id"],
        "created_at": record.get("created_at"),
        "image_name": record.get("image_name"),
        "mode": record.get("mode"),
        "color_mode": risolti.get("color_mode"),
        "status": vista.status.value,
        "expires_at": record.get("expires_at") if vista.artifacts else None,
        "preview_url": (f"{base_url}api/history/{record['id']}/preview"
                        if record.get("preview_key") else None),
        # Rifacibile solo finche' la sorgente c'e': si conserva per le ultime
        # generazioni di ogni account, poi si pota. La voce resta comunque.
        "can_regenerate": bool(record.get("source_key")),
        "filament_changes": [c.model_dump() for c in vista.filament_changes],
        "artifacts": [a.model_dump() for a in vista.artifacts],
    }


@app.get("/api/history")
def read_history(request: Request, user: Optional[dict] = Depends(current_user)):
    """Le generazioni di chi chiede, dalla piu' recente.

    Serve un account: le righe anonime sono legate a un browser, e mostrarle
    per browser vorrebbe dire far vedere a chi usa lo stesso computer cosa ha
    generato qualcun altro.
    """
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in to see your generations")
    base = str(request.base_url)
    righe = get_store().history(user["id"], settings.history_max)
    return {"entries": [_voce(r, base) for r in righe],
            "keep_sources": settings.history_keep_sources}


@app.get("/api/history/{job_id}/preview")
def history_preview(job_id: str):
    """La miniatura di una voce.

    Senza autenticazione, come i file prodotti: la chiave e' l'identificativo
    del job, 32 cifre esadecimali casuali. Un'immagine servita a un tag <img>
    non puo' portare un'intestazione di autorizzazione, e l'alternativa —
    scaricarla in JavaScript e passarla come blob — costerebbe complicazione
    per una miniatura di 7 KB che raffigura cio' che chi la vede ha gia' visto.
    """
    record = get_store().get(job_id)
    chiave = (record or {}).get("preview_key")
    if not record or not chiave or record.get("hidden_at"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no preview for this generation")
    try:
        dati = get_storage().get(chiave)
    except Exception as exc:  # noqa: BLE001
        log.warning("preview %s unreadable", job_id, exc_info=True)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "preview is gone") from exc
    # Immutabile: una volta scritta non cambia mai, e il browser puo' tenersela.
    return Response(content=dati, media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.post("/api/history/{job_id}/regenerate", response_model=JobCreated,
          status_code=status.HTTP_202_ACCEPTED)
def regenerate(job_id: str, request: Request,
               user: Optional[dict] = Depends(current_user),
               x_mangarelief_device: Optional[str] = Header(default=None)):
    """Rifa' una generazione scaduta dalla sorgente conservata.

    Consuma una generazione della giornata, come qualunque altra: rifare il
    file costa esattamente quanto farlo la prima volta, e non contarlo sarebbe
    una porta di servizio sulla quota.

    Riusa i parametri *risolti* dell'originale, non quelli richiesti: i toni
    campionati erano stati letti sull'immagine intera, e ricalcolarli sulla
    copia ridotta darebbe un modello leggermente diverso da quello che si sta
    chiedendo di riavere.
    """
    record = _mia_riga(job_id, user)
    chiave = record.get("source_key")
    if not chiave:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this generation is too old to be redone from here: load the artwork again")

    ip_key = hash_ip(client_ip(request))
    quota.enforce(get_store(), user, quota.clean_device_id(x_mangarelief_device), ip_key)

    try:
        rgb = decode_or_422(get_storage().get(chiave))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("source of %s unreadable", job_id, exc_info=True)
        raise HTTPException(status.HTTP_410_GONE,
                            "the stored artwork is no longer available") from exc

    vecchi = record.get("params") or {}
    engine_kwargs = dict(vecchi.get("resolved") or {})
    try:
        richiesti = JobParams(**(vecchi.get("requested") or {}))
    except ValidationError:
        # Una riga scritta da una versione precedente puo' non superare piu' la
        # validazione di oggi: la miniatura si ricava lo stesso dai risolti.
        richiesti = JobParams(mode=record.get("mode", "standard"))

    nuovo_id = uuid.uuid4().hex
    get_store().insert({
        "id": nuovo_id,
        "created_at": iso(utcnow()),
        "user_id": user["id"],
        "ip_hash": ip_key,
        "device_id": quota.clean_device_id(x_mangarelief_device),
        "mode": record.get("mode"),
        "params": {**vecchi, "regenerated_from": job_id},
        "status": JobStatus.QUEUED.value,
        "progress": 0,
        "message": "Queued",
        "duration_s": None,
        "error": None,
        "artifacts": [],
        "filament_changes": [],
        "expires_at": iso(default_expiry()),
        "downloaded_at": None,
        "image_name": record.get("image_name"),
    })

    try:
        runner.submit(nuovo_id, rgb, record.get("mode"), engine_kwargs,
                      source_stem=safe_stem(record.get("image_name")),
                      user_id=user["id"], requested=richiesti)
    except QueueFull as exc:
        get_store().update(nuovo_id, {"status": JobStatus.ERROR.value,
                                      "message": "Busy", "error": str(exc)})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "server busy, try again shortly",
                            headers={"Retry-After": "30"}) from exc

    return JobCreated(job_id=nuovo_id, status=JobStatus.QUEUED,
                      status_url=f"{request.base_url}api/jobs/{nuovo_id}")


@app.delete("/api/history/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget(job_id: str, user: Optional[dict] = Depends(current_user)):
    """Toglie una voce dalla cronologia e cancella i suoi file.

    La riga resta, marcata `hidden_at`. Non e' prudenza: la riga *e'* il
    registro su cui si conta la quota, e cancellarla darebbe a chiunque il modo
    di azzerare il proprio contatore — genera, scarica, cancella, rigenera.
    Sparisce dalla cronologia e i file se ne vanno davvero; a essere contata
    resta.
    """
    record = _mia_riga(job_id, user)
    storage = get_storage()
    for prefisso in (job_id, f"history/{job_id}"):
        try:
            storage.delete_prefix(prefisso)
        except Exception:
            log.warning("could not delete files of %s", job_id, exc_info=True)
    get_store().update(job_id, {"hidden_at": iso(utcnow()), "artifacts": [],
                                "preview_key": None, "source_key": None,
                                "status": JobStatus.EXPIRED.value, "message": "Deleted"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/internal/cleanup")
def cleanup(x_cleanup_token: str = Header(default="")):
    """Delete expired artifacts. Called nightly by a scheduled job."""
    if not settings.cleanup_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "CLEANUP_TOKEN is not configured")
    if x_cleanup_token != settings.cleanup_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad cleanup token")

    store, storage = get_store(), get_storage()
    now = utcnow()
    jobs_cleaned = files_deleted = 0

    try:
        expired = store.list_expired(now)
    except Exception as exc:  # noqa: BLE001 - naming the cause is the point
        # Catch everything, not just HTTP status errors: a bad URL, DNS or a
        # timeout raises a different type and would fall through as a bare 500,
        # which is precisely the dead end this endpoint kept producing.
        log.exception("cleanup: listing expired rows failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"database query failed: {_describe(exc)}") from exc

    for record in expired:
        try:
            files_deleted += storage.delete_prefix(record["id"])
        except Exception:
            log.warning("could not delete files of %s", record["id"], exc_info=True)
        try:
            store.update(record["id"], {"status": JobStatus.EXPIRED.value,
                                        "artifacts": [], "message": "Expired"})
        except Exception as exc:  # noqa: BLE001
            log.exception("cleanup: marking %s expired failed", record["id"])
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                f"database update failed: {_describe(exc)}") from exc
        jobs_cleaned += 1

    log.info("cleanup: %d jobs, %d files", jobs_cleaned, files_deleted)
    return {"jobs_cleaned": jobs_cleaned, "files_deleted": files_deleted}
