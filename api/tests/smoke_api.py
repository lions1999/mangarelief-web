"""End-to-end smoke test of the API, in local mode (SQLite + ./.data).

Runs the same flow a `curl` session would: analyze an image, create a job for
each mode, poll it, download both artifacts, then check the limits, the
retention policy and the cleanup endpoint.

    python tests/smoke_api.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="mr-smoke-")
os.environ.setdefault("LOCAL_DATA_DIR", os.path.join(WORK, "data"))
os.environ.setdefault("CLEANUP_TOKEN", "smoke-token")
os.environ.setdefault("QUOTA_ANON_DAILY", "300")
os.environ.setdefault("QUOTA_USER_DAILY", "300")
os.environ.setdefault("QUOTA_ANON_IP_DAILY", "600")
os.environ.setdefault("MAX_UPLOAD_BYTES", str(4 * 1024 * 1024))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.store import get_store, iso, parse_iso, utcnow  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def panel_png() -> bytes:
    """A synthetic manga-ish panel: white paper, grey block, black hatching,
    one red and one blue accent — enough for both modes to have work to do."""
    rng = np.random.default_rng(11)
    h, w = 900, 700
    img = np.full((h, w, 3), 245, np.uint8)
    cv2.rectangle(img, (60, 120), (400, 700), (130, 130, 130), -1)
    cv2.ellipse(img, (450, 400), (120, 180), 0, 0, 360, (55, 47, 231), -1)   # BGR red
    cv2.ellipse(img, (520, 620), (70, 120), 20, 0, 360, (170, 111, 39), -1)  # BGR blue
    for x in range(80, 390, 18):
        cv2.line(img, (x, 140), (x, 690), (25, 25, 25), 2)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    img = np.clip(img.astype(np.int16) + rng.normal(0, 5, img.shape).astype(np.int16),
                  0, 255).astype(np.uint8)
    return cv2.imencode(".png", img)[1].tobytes()


IMG = panel_png()
client = TestClient(app)


def upload(params: dict | None = None, filename: str = "panel.png",
           headers: dict | None = None):
    files = {"image": (filename, io.BytesIO(IMG), "image/png")}
    data = {"params": json.dumps(params)} if params else {}
    return client.post("/api/jobs", files=files, data=data, headers=headers or {})


def wait_for(job_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "error", "expired"):
            return body
        time.sleep(0.5)
    return {"status": "timeout"}


# ------------------------------------------------------------------ health
r = client.get("/")
check("root descrive il servizio", r.status_code == 200 and "docs" in r.json(), r.text[:100])

r = client.get("/healthz")
check("healthz ok", r.status_code == 200 and r.json()["status"] == "ok", r.text[:120])
check("healthz reports local backend", r.json()["backend"] == "local")

r = client.get("/healthz?deep=true")
check("healthz deep: database raggiungibile",
      r.status_code == 200 and r.json().get("database") == "ok", r.text[:200])

# ----------------------------------------------------------------- analyze
r = client.post("/api/analyze", files={"image": ("panel.png", io.BytesIO(IMG), "image/png")})
check("analyze 200", r.status_code == 200, r.text[:200])
info = r.json()
check("analyze: image size", (info["width"], info["height"]) == (700, 900), info.get("width"))
check("analyze: colour mode 2..4", info["color_mode"] in (2, 3, 4), info["color_mode"])
check("analyze: white clip in range", 180 <= info["suggested_white_clip"] <= 250,
      info["suggested_white_clip"])
check("analyze: midtones ordered light->dark",
      info["suggested_midtones"][0] > info["suggested_midtones"][1], info["suggested_midtones"])
check("analyze: auto Z ends at max_h", info["suggested_color_changes_z"][-1] == 2.4,
      info["suggested_color_changes_z"])
check("analyze: accents detected", len(info["suggested_accents"]) >= 1, info["suggested_accents"])

# ------------------------------------------------------- standard end to end
r = upload({"mode": "standard", "max_dim": 60, "max_res_cap": 300})
check("create standard job -> 202", r.status_code == 202, r.text[:200])
job_id = r.json()["job_id"]
body = wait_for(job_id)
check("standard job done", body["status"] == "done", body.get("error") or body["status"])
check("standard: progress 100", body.get("progress") == 100, body.get("progress"))
check("standard: two artifacts", len(body.get("artifacts", [])) == 2,
      [a["kind"] for a in body.get("artifacts", [])])
check("standard: duration recorded", (body.get("duration_s") or 0) > 0, body.get("duration_s"))
changes = body.get("filament_changes", [])
check("standard: piano di stampa restituito", len(changes) > 0, changes)
check("standard: quote crescenti e positive",
      all(c["z"] > 0 for c in changes)
      and [c["z"] for c in changes] == sorted(c["z"] for c in changes), changes)

spot_plan = None

def stl_is_valid(blob: bytes) -> bool:
    """Binary STL: 80-byte header, then a triangle count that matches the size."""
    if len(blob) < 84:
        return False
    n = int.from_bytes(blob[80:84], "little")
    return n > 0 and len(blob) == 84 + n * 50


# ------------------------------------------------------------ preview fetch
before = client.get(f"/api/jobs/{job_id}").json()
pv = client.get(f"/api/jobs/{job_id}/artifacts/stl?preview=true")
check("preview 200", pv.status_code == 200, pv.status_code)
check("preview: served inline", "inline" in pv.headers.get("content-disposition", ""),
      pv.headers.get("content-disposition"))
after_pv = client.get(f"/api/jobs/{job_id}").json()
check("preview does not count as a download",
      after_pv.get("downloaded_at") is None
      and after_pv["expires_at"] == before["expires_at"],
      (after_pv.get("downloaded_at"), after_pv["expires_at"] == before["expires_at"]))

for kind, valid in (("stl", stl_is_valid),
                    ("3mf", lambda b: b[:2] == b"PK" and len(b) > 1000)):
    d = client.get(f"/api/jobs/{job_id}/artifacts/{kind}")
    check(f"download {kind} 200", d.status_code == 200, d.status_code)
    check(f"download {kind}: valid {kind} payload", valid(d.content),
          f"{len(d.content)} bytes, head={d.content[:4]!r}")
    check(f"download {kind}: attachment filename", "attachment" in
          d.headers.get("content-disposition", ""), d.headers.get("content-disposition"))

# -------------------------------------------- la mesh decimata resta chiusa
# Tutti gli altri job girano a 300 px, sotto la soglia di decimazione: questo
# e' l'unico che la attraversa. Il decimatore lascia triangoli ad area zero
# che rendono la mesh non watertight senza aprire buchi, quindi fill_holes
# non aveva nulla da chiudere e il difetto passava in silenzio.
import trimesh  # noqa: E402

r = upload({"mode": "standard", "max_dim": 120, "max_res_cap": 800, "color_mode": 2})
check("job draft (800 px): accettato", r.status_code == 202, r.text[:120])
big = wait_for(r.json()["job_id"])
check("job draft: completato", big["status"] == "done", big.get("error"))
blob = client.get(f"/api/jobs/{big['job_id']}/artifacts/stl?preview=true").content
mesh = trimesh.load(io.BytesIO(blob), file_type="stl")
_, edge_count = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
check("job draft: la mesh e' passata dalla decimazione", len(mesh.faces) < 200_000,
      len(mesh.faces))
check("job draft: STL watertight dopo la decimazione", mesh.is_watertight,
      f"aperti {int((edge_count == 1).sum())}, non-manifold {int((edge_count > 2).sum())}")
check("job draft: normali coerenti", mesh.volume > 0, round(float(mesh.volume), 1))

# ------------------------------------------- i file portano il nome dell'opera
# <immagine>_mangarelief_<modalita'>[_<N>col]: prima il nome dell'artwork, cosi'
# le generazioni di uno stesso pannello si ordinano insieme; il nome arriva dal
# client e va ridotto a qualcosa di sicuro per un path e per un header.
from app.jobs import output_stem, safe_stem  # noqa: E402
check("safe_stem: spazi, punti e parentesi -> underscore, senza estensione",
      safe_stem("Gol D. Roger (cap 967).jpeg") == "Gol_D_Roger_cap_967", safe_stem("Gol D. Roger (cap 967).jpeg"))
check("safe_stem: niente directory", safe_stem("../../etc/passwd") == "passwd", safe_stem("../../etc/passwd"))
check("safe_stem: solo simboli -> None", safe_stem("   ") is None and safe_stem("日本.png") is None)
check("safe_stem: troncato", len(safe_stem("a" * 200 + ".png") or "") <= 60)
check("output_stem: nome + modalita' + colori",
      output_stem("roger", "standard", 2, "cf35a1b2ff") == "roger_mangarelief_standard_2col")
check("output_stem: senza nome torna all'id",
      output_stem(None, "spot_color", None, "cf35a1b2ff") == "mangarelief_spot_color_cf35a1b2")

r = upload({"mode": "standard", "max_dim": 60, "max_res_cap": 300, "color_mode": 4},
           filename="Gol D. Roger (cap 967).jpeg")
check("job con nome: accettato", r.status_code == 202, r.text[:120])
named = wait_for(r.json()["job_id"])
names = sorted(a["filename"] for a in named.get("artifacts", []))
check("gli artifact portano il nome dell'immagine, la modalita' e i colori",
      names == ["Gol_D_Roger_cap_967_mangarelief_standard_4col.3mf",
                "Gol_D_Roger_cap_967_mangarelief_standard_4col.stl"], names)
d = client.get(f"/api/jobs/{named['job_id']}/artifacts/stl")
cd = d.headers.get("content-disposition", "")
check("download: Content-Disposition con il nome e la forma RFC 5987",
      'filename="Gol_D_Roger_cap_967_mangarelief_standard_4col.stl"' in cd and "filename*=UTF-8''" in cd, cd)

r = upload({"mode": "spot_color", "max_dim": 60, "max_res_cap": 300, "spot_accents": [[231, 47, 55]]},
           filename="   .png")
anon = wait_for(r.json()["job_id"])
check("nome inutilizzabile: si torna all'id, mai a '_mangarelief_'",
      all(a["filename"].startswith("mangarelief_spot_color_") for a in anon.get("artifacts", [])),
      [a["filename"] for a in anon.get("artifacts", [])])

# ---------------------------------------------------------- retention policy
after = client.get(f"/api/jobs/{job_id}").json()
check("retention: downloaded_at set", after.get("downloaded_at") is not None)
delta_h = (parse_iso(after["expires_at"]) - utcnow()).total_seconds() / 3600.0
check("retention: expiry shortened to ~24h", 23.0 < delta_h <= 24.1, round(delta_h, 2))

# ----------------------------------------------------------- spot color mode
r = upload({"mode": "spot_color", "max_dim": 60, "max_res_cap": 300,
            "spot_accents": [[231, 47, 55]], "spot_coverage": 40})
check("create spot job -> 202", r.status_code == 202, r.text[:200])
spot_body = wait_for(r.json()["job_id"])
check("spot job done", spot_body["status"] == "done",
      spot_body.get("error") or spot_body["status"])
spot_plan = spot_body.get("filament_changes", [])
check("spot: ogni cambio porta il suo colore",
      len(spot_plan) > 0 and all(c.get("color", "").startswith("#") for c in spot_plan),
      spot_plan)

# accents left out -> detected server-side
r = upload({"mode": "spot_color", "max_dim": 60, "max_res_cap": 300})
check("spot without accents -> 202 (auto-detected)", r.status_code == 202, r.text[:200])
auto_body = wait_for(r.json()["job_id"])
check("spot auto-accent job done", auto_body["status"] == "done",
      auto_body.get("error") or auto_body["status"])
rec = get_store().get(r.json()["job_id"])
check("spot auto-accent: accents stored in params",
      len(rec["params"]["resolved"]["spot_accents"]) >= 1,
      rec["params"]["resolved"]["spot_accents"])

# ------------------------------------------------------------------ mockup
def png_size(blob: bytes):
    """PNG header: 8-byte signature, then IHDR with width and height."""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(blob[16:20], "big"), int.from_bytes(blob[20:24], "big")


def mockup(params: dict | None = None, headers: dict | None = None):
    files = {"image": ("panel.png", io.BytesIO(IMG), "image/png")}
    data = {"params": json.dumps(params)} if params else {}
    return client.post("/api/mockup", files=files, data=data, headers=headers or {})


r = mockup({"mode": "standard"})
check("mockup standard 200", r.status_code == 200, r.status_code)
check("mockup standard: PNG", r.headers["content-type"] == "image/png",
      r.headers.get("content-type"))
w_std, h_std = png_size(r.content)
check("mockup standard: downscaled", max(w_std, h_std) <= 700, (w_std, h_std))

std_png = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
tones = sorted(np.unique(std_png).tolist())
check("mockup standard: mostra le bande stampate, non una scala continua",
      2 <= len(tones) <= 4, tones)

# i toni scelti a mano devono comparire nell'anteprima
r = mockup({"mode": "standard", "sampled_values": [250, 200, 90, 10],
            "color_changes_z": [1.4, 2.0, 2.4]})
check("mockup standard: rispetta i toni scelti a mano", r.status_code == 200, r.status_code)
manual = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
scelti = set(np.unique(manual).tolist())
check("mockup standard: dipinge esattamente i toni indicati",
      scelti <= {250, 200, 90, 10} and len(scelti) >= 2, sorted(scelti))

r = mockup({"mode": "spot_color", "spot_accents": [[231, 47, 55]], "spot_coverage": 40})
check("mockup spot 200", r.status_code == 200, r.status_code)
spot_png = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
colours = np.unique(spot_png.reshape(-1, 3), axis=0)
check("mockup spot: only palette colours (white + accent + black)",
      len(colours) == 3, len(colours))

r = mockup({"mode": "spot_color"})
check("mockup spot without accents: auto-detected", r.status_code == 200, r.status_code)

r = mockup({"mode": "spot_color", "autodetect_accents": False})
check("mockup spot with no accent and no autodetect -> 422", r.status_code == 422,
      r.status_code)

# ------------------------------------------- numero di colori scelto a mano
# Le quote sono quelle a cui il colore ENTRA: un layer sopra la terrazza del
# colore precedente. Con base 1,0 e layer 0,2 il primo cambio e' sempre 1,2;
# esportare le cime delle terrazze (2,4 a due colori) colorava un layer solo
# e lasciava le pareti del colore sotto.
for n, attesi, quote in ((2, 1, [1.2]), (3, 2, [1.2, 1.8]), (4, 3, [1.2, 1.6, 2.2])):
    r = upload({"mode": "standard", "max_dim": 60, "max_res_cap": 300, "color_mode": n})
    check(f"{n} colori: job accettato", r.status_code == 202, r.text[:160])
    body_n = wait_for(r.json()["job_id"])
    check(f"{n} colori: {attesi} cambi filamento",
          body_n["status"] == "done" and len(body_n["filament_changes"]) == attesi,
          body_n.get("error") or body_n.get("filament_changes"))
    check(f"{n} colori: ogni colore entra un layer sopra la terrazza precedente",
          [c["z"] for c in body_n.get("filament_changes", [])] == quote,
          [c["z"] for c in body_n.get("filament_changes", [])])

check("un numero di colori fuori range viene rifiutato",
      upload({"color_mode": 5}).status_code == 422)

r = mockup({"mode": "standard", "color_mode": 2})
due = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
r = mockup({"mode": "standard", "color_mode": 4})
quattro = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
check("l'anteprima segue il numero di colori",
      len(np.unique(due)) == 2 and len(np.unique(quattro)) >= 3,
      (len(np.unique(due)), len(np.unique(quattro))))
check("l'anteprima a 2 colori usa carta e inchiostro, non due grigi",
      set(np.unique(due).tolist()) == {15, 250}, np.unique(due).tolist())

# La bobina di mezzo deve coprire davvero la meta' scura dell'immagine — la
# stessa che a 2 colori finisce tutta in inchiostro. A 3 colori la quota
# intermedia veniva letta da color_changes_z[0], che a 3 colori vale 0.0 per
# convenzione: il midtone finiva sotto il piano di base, l'oggetto annunciava
# due cambi filamento e ne usava uno solo (qui il midtone copriva 23.619 pixel
# invece di 167.372).
r = mockup({"mode": "standard", "color_mode": 3})
tre = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
sampled = info["suggested_sampled_values"]
scuri_due = int((due == sampled[3]).sum())
midtone_tre = int((tre == sampled[2]).sum())
check("a 3 colori il midtone copre la stessa area che a 2 colori e' inchiostro",
      midtone_tre >= 0.8 * scuri_due, (midtone_tre, scuri_due))
check("a 3 colori il midtone non e' finito sotto il piano di base",
      sampled[2] in np.unique(tre).tolist(), np.unique(tre).tolist())

# ------------------- l'anteprima deve ESSERE la classificazione, non somigliarle
# Ricostruiamo qui quello che fa il job — stesso ingresso, stessi due passi
# dell'engine — e pretendiamo che l'anteprima coincida. Saltando
# prepare_source_image il mockup mostrava tutt'altro: 11-15% dei pixel su una
# bobina diversa, e a 2 colori un'immagine che non si muoveva affatto.
from engine import GenerationParams, GenerationMode, prepare_source_image, standard_heightmap  # noqa: E402
from app.analysis import engine_input, resolve_params  # noqa: E402
from app.schemas import JobParams  # noqa: E402

rgb_ref = cv2.cvtColor(cv2.imdecode(np.frombuffer(IMG, np.uint8), cv2.IMREAD_COLOR),
                       cv2.COLOR_BGR2RGB)


def classificazione(color_mode, sampled_values=None):
    """Le bande e i toni che la mesh produrra' davvero."""
    jp = JobParams(mode="standard", color_mode=color_mode,
                   **({"sampled_values": sampled_values} if sampled_values else {}))
    par = GenerationParams(mode=GenerationMode.STANDARD, **resolve_params(rgb_ref, jp)[0])
    z = standard_heightmap(prepare_source_image(engine_input(rgb_ref, "standard"), par), par)
    b = np.zeros(z.shape, np.int32)
    for c in (c for c in par.color_changes_z if c > 0):
        b += (z >= c - 1e-9).astype(np.int32)
    toni = _band_tones_ref(par.sampled_values, color_mode)
    return np.array(toni, np.uint8)[np.clip(b, 0, len(toni) - 1)], toni


def _band_tones_ref(sampled, color_mode):
    if color_mode >= 4:
        return list(sampled)
    if color_mode == 3:
        return [sampled[0], sampled[2], sampled[3]]
    return [sampled[0], sampled[3]]


for n in (2, 3, 4):
    atteso, toni = classificazione(n)
    r = mockup({"mode": "standard", "color_mode": n})
    vista = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    atteso_s = cv2.resize(atteso, (vista.shape[1], vista.shape[0]),
                          interpolation=cv2.INTER_NEAREST)
    check(f"{n} colori: l'anteprima e' la classificazione della mesh",
          float((vista != atteso_s).mean()) < 0.005,
          f"discordanza {float((vista != atteso_s).mean()):.2%}")
    check(f"{n} colori: la riduzione non inventa toni",
          set(np.unique(vista).tolist()) <= set(toni), np.unique(vista).tolist())

# A 2 colori il controllo e' la copertura (sotto), non gli swatch: Paper e Ink
# possono muoversi quanto vogliono e l'anteprima — come la mesh — non deve
# cambiare. E' l'invarianza che rende sensato aver tolto quegli swatch dalla UI.
quote = []
for paper in (250, 200, 150, 100):
    sv = [paper, 210, 150, 15]
    atteso, toni = classificazione(2, sv)
    r = mockup({"mode": "standard", "color_mode": 2, "sampled_values": sv})
    vista = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    # Alla stessa scala: il nearest sottocampiona un tratteggio rado, quindi
    # confrontare una quota a piena risoluzione con una ridotta misura la
    # riduzione, non l'accordo fra anteprima e mesh.
    atteso_s = cv2.resize(atteso, (vista.shape[1], vista.shape[0]),
                          interpolation=cv2.INTER_NEAREST)
    ink_mesh = float((atteso_s == toni[1]).mean())
    ink_vista = float((vista == toni[1]).mean())
    quote.append(round(ink_vista, 3))
    check(f"2 colori, Paper={paper}: anteprima e mesh d'accordo",
          abs(ink_mesh - ink_vista) < 0.01, (round(ink_mesh, 3), round(ink_vista, 3)))
check("2 colori: gli swatch non muovono piu' nulla (il controllo e' la copertura)",
      len(set(quote)) == 1, quote)

# ----------------------------------------------- 2 colori: taglio di copertura
# A 2 colori il controllo e' la copertura, non gli swatch: l'inchiostro deve
# calare in modo monotono al crescere del taglio, l'endpoint deve dire quanta
# area sta vicino al taglio, e l'analisi deve riportare il livello d'inchiostro.
check("analyze: livello d'inchiostro (Otsu) riportato",
      isinstance(info.get("bw_ink_level"), int) and 0 < info["bw_ink_level"] < 255,
      info.get("bw_ink_level"))
inks, ambig = [], []
for cut in (0.2, 0.35, 0.5, 0.7):
    r = mockup({"mode": "standard", "color_mode": 2, "bw_coverage": cut})
    check(f"copertura {cut}: mockup 200", r.status_code == 200, r.status_code)
    img_c = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    inks.append(float((img_c == sampled[3]).mean()))
    ambig.append(r.headers.get("x-mangarelief-ambiguous"))
check("copertura: l'inchiostro cala al crescere del taglio",
      all(a >= b for a, b in zip(inks, inks[1:])), [round(v, 3) for v in inks])
check("copertura: il taglio ha effetto", inks[0] > inks[-1], (inks[0], inks[-1]))
# Gli header custom vanno dichiarati alla CORS, altrimenti il browser li
# nasconde a JavaScript: la nota sui limiti del piano gratuito viaggiava dal
# primo giorno e il frontend leggeva null ogni volta.
r_cors = mockup({"mode": "standard", "color_mode": 2, "bw_coverage": 0.35},
                headers={"Origin": "https://mangarelief-web.pages.dev"})
esposti = r_cors.headers.get("access-control-expose-headers", "").lower()
check("CORS: gli header custom sono esposti al browser",
      all(h in esposti for h in ("x-mangarelief-ambiguous", "x-mangarelief-notes",
                                 "x-mangarelief-expires-at")), esposti)
check("copertura: l'endpoint riporta l'ambiguita'",
      all(a is not None and 0.0 <= float(a) <= 1.0 for a in ambig), ambig)
check("copertura fuori range -> 422",
      mockup({"mode": "standard", "color_mode": 2, "bw_coverage": 1.5}).status_code == 422)
r = mockup({"mode": "standard", "color_mode": 4, "bw_coverage": 0.3})
check("a 4 colori la copertura viene ignorata e non produce l'header",
      r.status_code == 200 and r.headers.get("x-mangarelief-ambiguous") is None)
# anche il job vero deve accettarla e produrre un oggetto a due quote
r = upload({"mode": "standard", "max_dim": 60, "max_res_cap": 300, "color_mode": 2,
            "bw_coverage": 0.3})
check("job con copertura: accettato", r.status_code == 202, r.text[:120])
bj = wait_for(r.json()["job_id"])
check("job con copertura: completato con un cambio filamento",
      bj["status"] == "done" and len(bj["filament_changes"]) == 1,
      bj.get("error") or bj.get("filament_changes"))

# Il pannello di prova non ha nero pieno (le linee stanno a 25, sopra il
# black_clip), quindi la banda d'inchiostro non esiste: e' corretto che non
# compaia. Con quattro toni veri devono comparire tutte e quattro le bande.
patches = np.zeros((400, 400, 3), np.uint8)
for i, v in enumerate((255, 200, 110, 0)):
    patches[:, i * 100:(i + 1) * 100] = v
files = {"image": ("patches.png", io.BytesIO(cv2.imencode(".png", patches)[1].tobytes()),
                   "image/png")}
r = client.post("/api/mockup", files=files,
                data={"params": json.dumps({"mode": "standard", "color_mode": 4,
                                            "sampled_values": [250, 190, 116, 15]})})
bande = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
check("quattro toni veri -> quattro bande stampate",
      set(np.unique(bande).tolist()) == {250, 190, 116, 15}, np.unique(bande).tolist())

# ---------------------------------------------------------------- free tier
r = upload({"mode": "standard", "max_dim": 240, "max_res_cap": 1600})
check("free tier: request accepted", r.status_code == 202, r.text[:200])
check("free tier: caps reported in header", "X-MangaRelief-Notes" in r.headers,
      dict(r.headers).get("x-mangarelief-notes"))
capped = get_store().get(r.json()["job_id"])["params"]["resolved"]
check("free tier: res cap lowered to 800", capped["max_res_cap"] == 800, capped["max_res_cap"])
check("free tier: max_dim lowered to 200", capped["max_dim"] == 200.0, capped["max_dim"])
wait_for(r.json()["job_id"])

# --------------------------------------------------------------- validation
check("unknown mode rejected", upload({"mode": "phone_cover"}).status_code == 422)
check("max_h <= base_h rejected", upload({"base_h": 2.0, "max_h": 1.0}).status_code == 422)
check("out-of-range max_dim rejected", upload({"max_dim": 900}).status_code == 422)
check("bad accent rejected", upload({"mode": "spot_color",
                                     "spot_accents": [[300, 0, 0]]}).status_code == 422)
bad = client.post("/api/jobs", files={"image": ("x.png", io.BytesIO(b"not an image"), "image/png")})
check("undecodable upload rejected", bad.status_code == 422, bad.status_code)
big = client.post("/api/jobs",
                  files={"image": ("big.png", io.BytesIO(b"0" * (5 * 1024 * 1024)), "image/png")})
check("oversize upload rejected", big.status_code == 413, big.status_code)
check("unknown job -> 404", client.get("/api/jobs/nope").status_code == 404)
check("unknown artifact kind -> 404",
      client.get(f"/api/jobs/{job_id}/artifacts/obj").status_code == 404)

# ------------------------------------------------------------------ limits
# Le regole del servizio servite come numeri: e' quello che il benvenuto
# racconta a chi arriva, e serve che venga da qui e non dal testo della pagina.
from app.config import settings as _s  # noqa: E402

lim = client.get("/api/limits")
check("limits: risponde senza autenticazione e senza dispositivo", lim.status_code == 200,
      lim.status_code)
L = lim.json()
check("limits: le quote sono quelle configurate",
      L["anon_generations"] == _s.quota_anon_daily
      and L["user_generations"] == _s.quota_user_daily
      and L["window_h"] == _s.quota_window_h, L)
check("limits: la ritenzione e' quella configurata",
      L["retention_h"] == _s.retention_hours
      and L["post_download_h"] == _s.post_download_hours, L)
check("limits: il tetto di caricamento e' in MB", L["max_upload_mb"] == 4, L["max_upload_mb"])
check("limits: riporta i limiti tecnici del piano gratuito",
      L["max_res_cap"] == _s.anon_max_res_cap and L["max_dim_mm"] == _s.anon_max_dim_mm, L)
check("limits: elenca le modalita' aperte", L["modes"] == _s.allowed_modes, L["modes"])
# Un endpoint pubblico che nasce dalle impostazioni e' il posto giusto in cui
# far scivolare per sbaglio una chiave: qui si controlla che non succeda.
check("limits: non espone nulla di segreto",
      not any(k in L for k in ("supabase_key", "supabase_url", "ip_hash_salt",
                               "cleanup_token", "turnstile_secret"))
      and _s.ip_hash_salt not in json.dumps(L), L)


# ------------------------------------------------------------------- quota
# Il conteggio sta sul database, non in memoria: la finestra scorrevole di
# prima era per-processo, quindi due istanze raddoppiavano il limite e un
# riavvio lo azzerava. Qui si stringono le soglie per davvero e si verifica
# che il rifiuto arrivi al numero giusto, per il dispositivo giusto.
from datetime import timedelta  # noqa: E402

from app import auth as _auth  # noqa: E402
from app import quota as _quota  # noqa: E402
from app.config import settings as _cfg  # noqa: E402


def _errore_su(fn):
    """Il tipo dell'eccezione sollevata, o None se non ne solleva."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - e' proprio quello che misuriamo
        return type(exc)
    return None


DISPOSITIVO = "aaaaaaaa-1111-2222-3333-444444444444"
ALTRO = "bbbbbbbb-1111-2222-3333-444444444444"
_orig = (_cfg.quota_anon_daily, _cfg.quota_user_daily, _cfg.quota_anon_ip_daily)
_cfg.quota_anon_daily, _cfg.quota_user_daily, _cfg.quota_anon_ip_daily = 2, 300, 600
try:
    def piccolo(dev=None, token=None):
        h = {}
        if dev:
            h["X-MangaRelief-Device"] = dev
        if token:
            h["Authorization"] = f"Bearer {token}"
        return upload({"mode": "standard", "max_dim": 40, "max_res_cap": 200},
                      headers=h or None)

    esiti = [piccolo(DISPOSITIVO).status_code for _ in range(3)]
    check("anonimo: due generazioni passano, la terza no", esiti == [202, 202, 429], esiti)

    # Il conteggio e' per dispositivo, non per IP: sotto CGNAT un indirizzo
    # copre migliaia di persone, e contare l'IP negherebbe la prova a chi non
    # ha ancora fatto nulla. Stesso IP (i test girano tutti da 'testclient'),
    # browser diverso, quota intatta.
    check("un altro browser dallo stesso IP ha la sua quota",
          piccolo(ALTRO).status_code == 202)

    q = client.get("/api/quota", headers={"X-MangaRelief-Device": DISPOSITIVO}).json()
    check("la quota si legge senza consumarne una",
          q["plan"] == "anonymous" and q["limit"] == 2 and q["used"] >= 2
          and q["remaining"] == 0, q)
    check("dice quando si libera uno slot", q["reset_at"], q)
    q2 = client.get("/api/quota", headers={"X-MangaRelief-Device": DISPOSITIVO}).json()
    check("leggerla non e' un consumo", q2["used"] == q["used"], (q["used"], q2["used"]))

    # Senza intestazione si ricade sull'IP: altrimenti basterebbe ometterla per
    # non essere contati affatto.
    _cfg.quota_anon_ip_daily = 1
    check("senza id dispositivo si conta l'IP, non si passa liberi",
          piccolo().status_code == 429)
    _cfg.quota_anon_ip_daily = 600

    # Con un account il conteggio e' su user_id: svuotare il browser non serve.
    _cfg.quota_user_daily = 1
    _fetch_orig = _auth.fetch_user
    _auth.fetch_user = lambda t: {"id": "99999999-0000-0000-0000-000000000001",
                                  "email": "quota@esempio.it"} if t == "tok" else None
    _auth.reset_cache()
    check("con account: la prima passa", piccolo(DISPOSITIVO, "tok").status_code == 202)
    check("con account: la seconda no, anche cambiando browser",
          piccolo(ALTRO, "tok").status_code == 429)
    qa = client.get("/api/quota", headers={"Authorization": "Bearer tok"}).json()
    check("la quota di chi ha l'account e' del piano registrato",
          qa["plan"] == "registered" and qa["limit"] == 1, qa)

    # Le prove anonime si attaccano all'account al primo accesso: chi prova e
    # poi si registra non riparte dal totale pieno.
    st = get_store()
    prima = st.usage_since("user_id", "99999999-0000-0000-0000-000000000001",
                           utcnow() - timedelta(hours=24))[0]
    spostate = st.link_device(ALTRO, "99999999-0000-0000-0000-000000000001",
                              utcnow() - timedelta(hours=24))
    dopo = st.usage_since("user_id", "99999999-0000-0000-0000-000000000001",
                          utcnow() - timedelta(hours=24))[0]
    check("le generazioni anonime del browser passano all'account",
          spostate >= 1 and dopo == prima + spostate, (prima, spostate, dopo))
    check("collegare due volte non le conta due volte",
          st.link_device(ALTRO, "99999999-0000-0000-0000-000000000001",
                         utcnow() - timedelta(hours=24)) == 0)

    # Piano senza tetto: il ruolo sta in app_metadata, che solo la chiave
    # service-role puo' scrivere. In user_metadata se lo scriverebbe l'utente,
    # e un piano modificabile da chi ne beneficia non e' un piano.
    _cfg.quota_user_daily = 1
    ILLIM = {"id": "55555555-0000-0000-0000-000000000009",
             "email": "titolare@esempio.it", "plan": "unlimited"}
    _auth.fetch_user = lambda t: ILLIM if t == "illim" else None
    _auth.reset_cache()
    _risposte_ill = [piccolo(DISPOSITIVO, "illim") for _ in range(3)]
    esiti_ill = [r.status_code for r in _risposte_ill]
    _job_ids_illim = [r.json()["job_id"] for r in _risposte_ill if r.status_code == 202]
    check("piano illimitato: il tetto sul numero non si applica",
          esiti_ill == [202, 202, 202], esiti_ill)
    qi = client.get("/api/quota", headers={"Authorization": "Bearer illim"}).json()
    check("la quota illimitata si dichiara tale",
          qi["plan"] == "unlimited" and qi["limit"] is None and qi["remaining"] is None, qi)
    check("anche senza tetto le generazioni si contano lo stesso", qi["used"] >= 3, qi)

    # Il tetto tolto e' quello sul CONTEGGIO, non sulla ritenzione: lo spazio
    # e' la risorsa scarsa, e resta 48h come per tutti.
    check("i file del piano illimitato scadono come gli altri",
          all(abs((parse_iso(r["expires_at"]) - parse_iso(r["created_at"])).total_seconds()
                  / 3600 - 48) < 0.2
              for r in [get_store().get(j) for j in _job_ids_illim]),
          [(r["created_at"], r["expires_at"]) for r in
           [get_store().get(j) for j in _job_ids_illim]])

    # Un piano scritto dove se lo scrive l'utente non deve valere nulla:
    # fetch_user legge app_metadata, non user_metadata.
    _auth.fetch_user = lambda t: {"id": "55555555-0000-0000-0000-00000000000a",
                                  "email": "furbo@esempio.it", "plan": None}
    _auth.reset_cache()
    check("senza piano in app_metadata si resta sul piano registrato",
          client.get("/api/quota", headers={"Authorization": "Bearer x"}).json()["plan"]
          == "registered")

    check("un id dispositivo malformato viene ignorato, non finisce in query",
          _quota.clean_device_id("../../etc") is None
          and _quota.clean_device_id("x" * 200) is None)
    check("un campo non ammesso per la quota viene rifiutato",
          _errore_su(lambda: st.usage_since("params", "x", utcnow())) is ValueError)
finally:
    _cfg.quota_anon_daily, _cfg.quota_user_daily, _cfg.quota_anon_ip_daily = _orig
    _auth.fetch_user = _fetch_orig
    _auth.reset_cache()

# ------------------------------------------------------------------ cleanup
check("cleanup without token -> 401",
      client.post("/api/internal/cleanup").status_code == 401)

store = get_store()
store.update(job_id, {"expires_at": iso(utcnow().replace(year=utcnow().year - 1))})
check("expired job reads as expired",
      client.get(f"/api/jobs/{job_id}").json()["status"] == "expired")
check("expired download -> 410",
      client.get(f"/api/jobs/{job_id}/artifacts/stl").status_code == 410)

r = client.post("/api/internal/cleanup", headers={"X-Cleanup-Token": "smoke-token"})
check("cleanup 200", r.status_code == 200, r.text[:200])
check("cleanup removed the expired job", r.json()["jobs_cleaned"] >= 1, r.json())
check("cleanup deleted its files", r.json()["files_deleted"] >= 2, r.json())
check("cleaned job has no artifacts",
      client.get(f"/api/jobs/{job_id}").json()["artifacts"] == [])

# ------------------------------------------------------- riconoscimento utente
# Fase 3, passo 1: l'API impara CHI chiede. Niente cambia per nessuno — stessi
# limiti, stessa ritenzione — ma una generazione fatta da autenticati viene
# registrata sull'account, che e' la colonna su cui poggera' la quota.
import httpx  # noqa: E402  (ri-importato: il blocco deve reggersi da solo)

from app import auth  # noqa: E402


def drena(timeout: float = 120.0):
    """Aspetta che la coda si svuoti: il blocco del rate limit qui sopra lascia
    job in volo, e con MAX_QUEUE a 8 il prossimo invio si prenderebbe un 503
    che non c'entra niente con cio' che stiamo verificando."""
    scadenza = time.time() + timeout
    while time.time() < scadenza:
        if client.get("/healthz").json().get("queue", 0) == 0:
            return
        time.sleep(0.3)


def invia_autenticato(token: str | None = None):
    """Un invio isolato: coda vuota, cosi' l'esito dipende solo
    dall'autenticazione e non da un 503 lasciato dai blocchi precedenti.
    Le quote qui sono larghe (vedi le variabili in cima), le stringe solo il
    blocco che le misura."""
    drena()
    headers = {"Authorization": token} if token else None
    return upload({"mode": "standard", "max_dim": 60, "max_res_cap": 300,
                   "color_mode": 2}, headers=headers)


FINTO = {"id": "11111111-2222-3333-4444-555555555555", "email": "tu@esempio.it"}
_vero_fetch = auth.fetch_user
auth.fetch_user = lambda token: FINTO if token == "token-buono" else None
auth.reset_cache()
try:
    r = invia_autenticato("Bearer token-buono")
    check("con un token valido il job e' accettato", r.status_code == 202, r.text[:120])
    check("la generazione e' registrata sull'account",
          get_store().get(r.json()["job_id"]).get("user_id") == FINTO["id"],
          get_store().get(r.json()["job_id"]).get("user_id"))

    r = invia_autenticato()
    check("senza token il job resta anonimo", r.status_code == 202, r.text[:120])
    check("anonimo: nessun account sulla riga",
          get_store().get(r.json()["job_id"]).get("user_id") is None)

    # Un token che non si riesce a verificare non deve MAI diventare "anonimo"
    # in silenzio: e' cosi' che un difetto nell'autenticazione si trasforma in
    # generazioni illimitate gratis.
    check("un token non valido -> 401, non un fallback ad anonimo",
          invia_autenticato("Bearer token-scaduto").status_code == 401)
    check("uno schema diverso da Bearer -> 401",
          invia_autenticato("Basic abc").status_code == 401)
    check("Bearer senza token -> 401", invia_autenticato("Bearer ").status_code == 401)

    # Verificato una volta, poi in cache: non un round trip a Supabase per ogni
    # richiesta di chi sta interrogando un job.
    chiamate = []
    auth.fetch_user = lambda t: (chiamate.append(t), FINTO)[1]
    auth.reset_cache()
    for _ in range(3):
        invia_autenticato("Bearer token-buono")
    check("il token verificato viene messo in cache", len(chiamate) == 1, len(chiamate))

    # Supabase irraggiungibile: rifiutare e' l'unica risposta sicura. Trattarlo
    # come anonimo regalerebbe il piano gratuito a tutti durante un disservizio.
    def _giu(token):
        raise httpx.ConnectError("connessione rifiutata", request=httpx.Request("GET", "https://x"))
    auth.fetch_user = _giu
    auth.reset_cache()
    check("Supabase giu' durante la verifica -> 503, mai un accesso concesso",
          invia_autenticato("Bearer token-buono").status_code == 503)
finally:
    auth.fetch_user = _vero_fetch
    auth.reset_cache()
    drena()

# ----------------------------------------------------------- accesso via codice
# Il codice invece del magic link: un link apre una scheda nuova e chi ha gia'
# caricato l'immagine e regolato i parametri li perde. La richiesta passa dalla
# nostra API perche' e' l'unico punto in cui possiamo rifiutare le caselle
# usa-e-getta e ricondurre gli alias a una sola identita'.
from app import emails as _mail  # noqa: E402

check("normalizza gli alias Gmail sulla stessa casella",
      _mail.normalize("M.ario+spam@GMail.com") == "mario@gmail.com"
      == _mail.normalize("m.a.r.i.o@googlemail.com"),
      _mail.normalize("M.ario+spam@GMail.com"))
check("il +suffisso cade su tutti i domini, non solo Gmail",
      _mail.normalize("Mario.Rossi+x@outlook.com") == "mario.rossi@outlook.com",
      _mail.normalize("Mario.Rossi+x@outlook.com"))
check("il punto resta significativo fuori da Gmail",
      _mail.normalize("m.ario@outlook.com") == "m.ario@outlook.com")
check("le temporanee note sono in elenco",
      all(_mail.is_disposable(f"x@{d}") for d in
          ("mailinator.com", "10minutemail.com", "guerrillamail.com", "yopmail.com")))
check("i provider veri non sono in elenco",
      not any(_mail.is_disposable(f"x@{d}") for d in
              ("gmail.com", "outlook.com", "icloud.com", "proton.me", "libero.it")))
check("l'elenco e' consistente", len(_mail.disposable_domains()) > 1000,
      len(_mail.disposable_domains()))

inviate = []


def _finto_auth_post(path, payload, params=None):
    inviate.append((path, payload, params))

    class R:
        status_code = 200

        @staticmethod
        def json():
            if path == "otp":
                return {}
            return {"access_token": "at-1", "refresh_token": "rt-1", "expires_at": 999,
                    "user": {"id": "77777777-8888-9999-aaaa-bbbbbbbbbbbb",
                             "email": payload.get("email")}}
    if path == "verify" and payload.get("token") != "123456":
        R.status_code = 403
    return R()


_auth_post_orig = auth._auth_post
auth._auth_post = _finto_auth_post
auth._sends.clear()
try:
    check("email non valida -> 422",
          client.post("/api/auth/code", json={"email": "non-una-email"}).status_code == 422)
    check("casella usa-e-getta rifiutata prima di spedire",
          client.post("/api/auth/code", json={"email": "x@mailinator.com"}).status_code == 422
          and not inviate)
    r = client.post("/api/auth/code", json={"email": "M.ario+promo@GMail.com"})
    check("codice richiesto -> 204", r.status_code == 204, r.status_code)
    check("a Supabase arriva l'indirizzo normalizzato, non l'alias",
          inviate[-1][1]["email"] == "mario@gmail.com", inviate[-1][1])

    # Freno agli invii: il sito non deve diventare un mezzo per spedire email
    # a indirizzi altrui.
    esiti = [client.post("/api/auth/code", json={"email": f"tale{i}@esempio.it"}).status_code
             for i in range(8)]
    check("troppi codici dallo stesso IP -> 429", 429 in esiti, esiti)
    auth._sends.clear()

    check("codice sbagliato -> 401",
          client.post("/api/auth/verify",
                      json={"email": "mario@gmail.com", "code": "000000"}).status_code == 401)

    # Le prove anonime di questo browser passano all'account nello stesso
    # passaggio in cui si accede.
    DEV_NUOVO = "cccccccc-1111-2222-3333-444444444444"
    drena()
    upload({"mode": "standard", "max_dim": 40, "max_res_cap": 200},
           headers={"X-MangaRelief-Device": DEV_NUOVO})
    drena()
    r = client.post("/api/auth/verify", json={"email": "M.ario+promo@GMail.com",
                                              "code": "123456", "device_id": DEV_NUOVO})
    check("codice giusto -> sessione", r.status_code == 200, r.text[:120])
    sess = r.json()
    check("la sessione porta i token e l'utente",
          sess["access_token"] == "at-1" and sess["refresh_token"] == "rt-1"
          and sess["user_id"], sess)
    check("accedendo, le prove anonime del browser passano all'account",
          sess["linked"] >= 1, sess["linked"])
    check("verify usa l'indirizzo normalizzato",
          inviate[-1][1]["email"] == "mario@gmail.com", inviate[-1][1])
    check("collegare di nuovo lo stesso browser non aggiunge nulla",
          client.post("/api/auth/verify",
                      json={"email": "mario@gmail.com", "code": "123456",
                            "device_id": DEV_NUOVO}).json()["linked"] == 0)

    r = client.post("/api/auth/refresh", json={"refresh_token": "rt-1"})
    check("il refresh restituisce una sessione nuova",
          r.status_code == 200 and r.json()["access_token"] == "at-1", r.status_code)

    # L'elenco non e' un muro: la chiave anon e' pubblica, quindi un account
    # con casella usa-e-getta puo' comunque nascere scavalcando il nostro
    # endpoint. Rifiutarlo all'uso e' cio' che lo rende inutile.
    auth.fetch_user = lambda t: {"id": "66666666-0000-0000-0000-000000000001",
                                 "email": "furbo@mailinator.com"}
    auth.reset_cache()
    check("account con casella usa-e-getta: rifiutato all'uso, non solo all'iscrizione",
          upload({"mode": "standard"},
                 headers={"Authorization": "Bearer qualsiasi"}).status_code == 403)
finally:
    auth._auth_post = _auth_post_orig
    auth.fetch_user = _vero_fetch
    auth.reset_cache()
    auth._sends.clear()
    drena()

# ------------------------------------------------------------------- CORS
# Due righe nella tabella e una pagina che "non fa nulla" e' la firma di un
# CORS che non combacia: il POST multipart non ha preflight, quindi arriva e
# scrive la riga, ma il browser scarta la risposta.
from app.config import Settings  # noqa: E402

os.environ["CORS_ORIGINS"] = " https://mangarelief-web.pages.dev/ , https://esempio.it "
normalizzate = Settings().cors_origins
check("CORS: barra finale e spazi normalizzati",
      normalizzate == ["https://mangarelief-web.pages.dev", "https://esempio.it"],
      normalizzate)
del os.environ["CORS_ORIGINS"]

r = client.post("/api/analyze",
                files={"image": ("panel.png", io.BytesIO(IMG), "image/png")},
                headers={"Origin": "https://mangarelief-web.pages.dev"})
check("CORS: risposta con Access-Control-Allow-Origin",
      "access-control-allow-origin" in {k.lower() for k in r.headers},
      dict(r.headers).get("access-control-allow-origin"))

# --------------------------------- cleanup: ogni errore deve avere un nome
# Il cleanup rispondeva 500 "Internal Server Error" perche' intercettavo solo
# gli errori HTTP: un problema di connessione o di URL cadeva fuori.
import httpx  # noqa: E402
import app.main as main_module  # noqa: E402


class _Boom:
    def list_expired(self, *a, **k):
        raise ConnectionError("connessione rifiutata")


real_get_store = main_module.get_store
main_module.get_store = lambda: _Boom()
try:
    r = client.post("/api/internal/cleanup", headers={"X-Cleanup-Token": "smoke-token"})
finally:
    main_module.get_store = real_get_store

check("cleanup: errore non-HTTP -> 502 con la causa",
      r.status_code == 502 and "ConnectionError" in r.text, f"{r.status_code} {r.text[:120]}")


class _NoDns:
    def list_expired(self, *a, **k):
        req = httpx.Request("GET", "https://typo.supabase.co/rest/v1/generations")
        raise httpx.ConnectError("[Errno -2] Name or service not known", request=req)


main_module.get_store = lambda: _NoDns()
try:
    r = client.post("/api/internal/cleanup", headers={"X-Cleanup-Token": "smoke-token"})
finally:
    main_module.get_store = real_get_store

check("cleanup: errore DNS nomina l'host irraggiungibile",
      r.status_code == 502 and "typo.supabase.co" in r.text, f"{r.status_code} {r.text[:160]}")

# ------------------------------------- Supabase query encoding (no network)
# The nightly cleanup failed in production with a 500 because the ISO
# timestamp was interpolated straight into the URL: a raw "+" in a query
# string is a space, so PostgREST got a malformed date and answered 400.
from app.store import SupabaseStore  # noqa: E402

captured = {}


def _fake_get(url, **kwargs):
    captured["url"] = str(httpx.Request("GET", url, params=kwargs.get("params")).url)

    class R:
        headers = {"content-range": "*/0"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return []

    return R()


real_get, httpx.get = httpx.get, _fake_get
try:
    store = SupabaseStore("https://example.supabase.co", "key")
    store.list_expired(utcnow())
    listed = captured["url"]
    store.count_recent("abc", utcnow())
    counted = captured["url"]
    store.usage_since("device_id", "aaaa-bbbb", utcnow())
    used = captured["url"]
finally:
    httpx.get = real_get

for name, url in (("list_expired", listed), ("count_recent", counted),
                  ("usage_since", used)):
    check(f"{name}: timestamp url-encoded, non un '+' grezzo",
          "%2B" in url and "+" not in url.split("?", 1)[1], url)

# usage_since deve chiedere conteggio e riga piu' vecchia in un giro solo:
# il totale arriva nell'header, la piu' vecchia nel corpo.
check("usage_since: filtra sul campo giusto e ordina per prendere la piu' vecchia",
      "device_id=eq.aaaa-bbbb" in used and "order=created_at.asc" in used
      and "limit=1" in used, used)

# link_device usa PATCH, non GET: va intercettato a parte. Deve toccare SOLO
# le righe ancora anonime, altrimenti riattribuirebbe generazioni gia' di
# qualcun altro.
patched = {}


def _fake_patch(url, **kwargs):
    patched["url"] = str(httpx.Request("PATCH", url, params=kwargs.get("params")).url)
    patched["json"] = kwargs.get("json")

    class R:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return [{"id": "x"}]

    return R()


real_patch, httpx.patch = httpx.patch, _fake_patch
try:
    spostate = SupabaseStore("https://example.supabase.co", "key").link_device(
        "aaaa-bbbb", "11111111-2222-3333-4444-555555555555", utcnow())
finally:
    httpx.patch = real_patch

check("link_device: tocca solo le righe ancora anonime di quel dispositivo",
      "device_id=eq.aaaa-bbbb" in patched["url"] and "user_id=is.null" in patched["url"],
      patched["url"])
check("link_device: timestamp url-encoded anche qui",
      "%2B" in patched["url"] and "+" not in patched["url"].split("?", 1)[1],
      patched["url"])
check("link_device: scrive l'account e riporta quante ne ha spostate",
      patched["json"] == {"user_id": "11111111-2222-3333-4444-555555555555"}
      and spostate == 1, (patched["json"], spostate))

print("\n" + ("ALL OK" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
