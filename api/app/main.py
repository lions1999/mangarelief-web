"""MangaRelief web API.

Three endpoints carry the product — create a job, poll it, download the result
— plus an image analysis helper (the numbers the desktop UI derives on load)
and an internal cleanup endpoint that enforces the retention policy.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from urllib.parse import urlparse
from typing import List, Optional

from fastapi import (Depends, FastAPI, Form, Header, HTTPException, Request,
                     Response, UploadFile, File, status)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

import cv2
import httpx
import numpy as np

from engine import GenerationMode, GenerationParams, standard_heightmap
from engine.color_utils import classify_spot_pixels, downsample_for_analysis

from .analysis import analyze, filtered_gray, resolve_params
from .config import settings
from .imaging import ImageTooLarge, UndecodableImage, decode_upload
from .jobs import QueueFull, runner
from .limits import clamp_to_anonymous_tier, hash_ip, limiter, turnstile_ok
from .schemas import (AnalysisResult, Artifact, FilamentChange, JobCreated,
                      JobParams, JobStatus, JobView)
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
)

CONTENT_TYPES = {"stl": "model/stl", "3mf": "model/3mf"}


# ---------------------------------------------------------------- helpers
def client_ip(request: Request) -> str:
    """Cloud Run and Cloudflare both put the real address first in XFF."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


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
        "health": "/healthz",
        "source": "https://github.com/lions1999/mangarelief-web",
    }


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


MOCKUP_MAX_PX = 700


def _band_tones(sampled: List[int], color_mode: int) -> List[int]:
    """The grey each printed band shows, light at the bottom, dark on top.

    Mirrors which levels the desktop selector hides: 4 colours use all four
    sampled tones, 3 drop L1, 2 keep only paper and ink.
    """
    if color_mode >= 4:
        return list(sampled)
    if color_mode == 3:
        return [sampled[0], sampled[2], sampled[3]]
    return [sampled[0], sampled[3]]


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
    """
    p = parse_params(params)
    rgb = decode_or_422(read_upload(image))
    small = downsample_for_analysis(rgb, MOCKUP_MAX_PX)

    if p.mode.value == "spot_color":
        accents = [tuple(a) for a in p.spot_accents]
        if not accents and p.autodetect_accents:
            accents = analyze(rgb, n_accents=2)["suggested_accents"][:2]
        if not accents:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "spot_color needs at least one accent colour to preview")
        white_clip = p.white_clip if p.white_clip is not None else 235
        black_clip = p.black_clip if p.black_clip is not None else 15
        palette, idx = classify_spot_pixels(small, accents, coverage=p.spot_coverage,
                                            white_clip=white_clip, black_clip=black_clip)
        preview = np.array(palette, dtype=np.uint8)[idx]
    else:
        # Standard mode: paint each pixel with the tone it will actually print
        # in. The heightmap comes from the engine, so this cannot drift from
        # the mesh; the bands come from the filament changes, so what you see
        # is which bobbin covers which area.
        engine_kwargs, _ = resolve_params(rgb, p)
        params = GenerationParams(mode=GenerationMode.STANDARD, **engine_kwargs)
        gray = filtered_gray(small)
        z = standard_heightmap(gray, params)

        changes = [c for c in params.color_changes_z if c > 0]
        tones = _band_tones(params.sampled_values, params.color_mode)
        band = np.zeros(z.shape, dtype=np.int32)
        for c in changes[:-1]:          # l'ultimo cambio è il top, non apre banda
            band += (z > c).astype(np.int32)
        preview = cv2.cvtColor(np.array(tones, dtype=np.uint8)[np.clip(band, 0, len(tones) - 1)],
                               cv2.COLOR_GRAY2RGB)

    ok, buf = cv2.imencode(".png", cv2.cvtColor(preview, cv2.COLOR_RGB2BGR))
    if not ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "preview encoding failed")

    return Response(content=buf.tobytes(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/jobs", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    request: Request,
    response: Response,
    image: UploadFile = File(...),
    params: Optional[str] = Form(None),
    turnstile_token: Optional[str] = Form(None),
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
    allowed, retry_after = limiter.check(ip_key)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"rate limit reached, retry in {retry_after}s",
                            headers={"Retry-After": str(retry_after)})

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
        "user_id": None,                # phase 3: the authenticated user
        "ip_hash": ip_key,
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
        runner.submit(job_id, rgb, p.mode.value, engine_kwargs)
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
                "Content-Disposition": f'inline; filename="{artifact["filename"]}"',
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
            "Content-Disposition": f'attachment; filename="{artifact["filename"]}"',
            "X-MangaRelief-Expires-At": fields["expires_at"],
        },
    )


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
