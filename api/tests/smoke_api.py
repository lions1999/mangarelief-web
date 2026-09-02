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
os.environ.setdefault("ANON_RATE_LIMIT", "6")
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


def upload(params: dict | None = None):
    files = {"image": ("panel.png", io.BytesIO(IMG), "image/png")}
    data = {"params": json.dumps(params)} if params else {}
    return client.post("/api/jobs", files=files, data=data)


def wait_for(job_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "error", "expired"):
            return body
        time.sleep(0.5)
    return {"status": "timeout"}


# ------------------------------------------------------------------ health
r = client.get("/healthz")
check("healthz ok", r.status_code == 200 and r.json()["status"] == "ok", r.text[:120])
check("healthz reports local backend", r.json()["backend"] == "local")

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

def stl_is_valid(blob: bytes) -> bool:
    """Binary STL: 80-byte header, then a triangle count that matches the size."""
    if len(blob) < 84:
        return False
    n = int.from_bytes(blob[80:84], "little")
    return n > 0 and len(blob) == 84 + n * 50


for kind, valid in (("stl", stl_is_valid),
                    ("3mf", lambda b: b[:2] == b"PK" and len(b) > 1000)):
    d = client.get(f"/api/jobs/{job_id}/artifacts/{kind}")
    check(f"download {kind} 200", d.status_code == 200, d.status_code)
    check(f"download {kind}: valid {kind} payload", valid(d.content),
          f"{len(d.content)} bytes, head={d.content[:4]!r}")
    check(f"download {kind}: attachment filename", "attachment" in
          d.headers.get("content-disposition", ""), d.headers.get("content-disposition"))

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


def mockup(params: dict | None = None):
    files = {"image": ("panel.png", io.BytesIO(IMG), "image/png")}
    data = {"params": json.dumps(params)} if params else {}
    return client.post("/api/mockup", files=files, data=data)


r = mockup({"mode": "standard"})
check("mockup standard 200", r.status_code == 200, r.status_code)
check("mockup standard: PNG", r.headers["content-type"] == "image/png",
      r.headers.get("content-type"))
w_std, h_std = png_size(r.content)
check("mockup standard: downscaled", max(w_std, h_std) <= 700, (w_std, h_std))

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

# -------------------------------------------------------------- rate limit
codes = [upload({"mode": "standard", "max_dim": 40, "max_res_cap": 200}).status_code
         for _ in range(4)]
check("rate limit kicks in", 429 in codes, codes)

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

print("\n" + ("ALL OK" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
