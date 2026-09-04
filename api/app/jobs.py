"""Job execution.

One background worker runs `engine.generate` and writes its progress into the
`generations` row, so a client polling `GET /api/jobs/{id}` sees the same
percentages the desktop progress bar shows. The queue is deliberately shallow:
this pipeline is CPU- and RAM-bound, and a long queue on a small instance only
converts waiting into OOM kills.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Any, Dict, List

import cv2
import numpy as np

from engine import GenerationMode, GenerationParams, generate

from .analysis import engine_input
from . import preview

from .config import settings
from .storage import get_storage
from .store import default_expiry, get_store, iso, utcnow

log = logging.getLogger("mangarelief.jobs")

_MODE_MAP = {
    "standard": GenerationMode.STANDARD,
    "spot_color": GenerationMode.SPOT_COLOR,
}


class QueueFull(RuntimeError):
    pass


_STEM_MAX = 60


def safe_stem(filename: Optional[str]) -> Optional[str]:
    """The uploaded file's name, reduced to something safe to put in a path
    and in a Content-Disposition header.

    Client-supplied: strip directories and the extension, keep only
    [A-Za-z0-9_-], fold runs of anything else into one underscore, cap the
    length. Returns None when nothing usable is left, so the caller falls back
    to the job id rather than producing "_mangarelief_standard.stl".
    """
    if not filename:
        return None
    name = os.path.basename(filename.replace("\\", "/"))
    name = os.path.splitext(name)[0]
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_-")
    name = name[:_STEM_MAX].rstrip("_-")
    return name or None


def output_stem(source_stem: Optional[str], mode: str, color_mode: Optional[int],
                job_id: str) -> str:
    """`<image>_mangarelief_<mode>[_<N>col]`, the desktop's `<image>_3D` idea
    carried over: the artwork's name first so one panel's generations sort
    together, then what was done to it. The colour count is the one setting
    that tells two Standard runs of the same panel apart days later, so it is
    in the name. Without a usable source name the job id keeps files unique.
    """
    tag = f"{mode}_{int(color_mode)}col" if (mode == "standard" and color_mode) else mode
    if source_stem:
        return f"{source_stem}_mangarelief_{tag}"
    return f"mangarelief_{tag}_{job_id[:8]}"


def _webp(img_rgb: np.ndarray, quality: int) -> Optional[bytes]:
    """RGB -> webp. Oltre 100 OpenCV scrive senza perdita."""
    ok, buf = cv2.imencode(".webp", cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_WEBP_QUALITY, quality])
    return buf.tobytes() if ok else None


def _ridotta(rgb: np.ndarray, lato: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    s = lato / max(h, w)
    if s >= 1.0:
        return rgb
    return cv2.resize(rgb, (max(1, round(w * s)), max(1, round(h * s))),
                      interpolation=cv2.INTER_AREA)


def keep_for_history(job_id: str, rgb: np.ndarray, params_richiesti,
                     user_id: str) -> Dict[str, Any]:
    """Cio' che sopravvive alla scadenza dei file: la miniatura e la sorgente.

    Sotto `history/<id>/` e non `<id>/`, perche' la pulizia a scadenza cancella
    l'intera cartella del job e porterebbe via anche queste.

    La miniatura e' il mockup, non l'immagine caricata: lo stesso pannello a 2,
    3 e 4 colori darebbe tre miniature identiche, e distinguere quelle tre e'
    esattamente il motivo per cui una cronologia serve. Senza perdita, perche'
    su quattro colori piatti costa meno del jpeg e non inventa mezzetinte.

    La sorgente e' q92: la differenza non si vede, e comunque rigenerare non
    riproduce il file all'ultimo bit — l'immagine e' gia' stata ridotta una
    volta, rigenerando la si riduce due.
    """
    storage = get_storage()
    campi: Dict[str, Any] = {}

    mini, _ = preview.render(rgb, params_richiesti, max_px=settings.history_preview_px)
    dati = _webp(mini, 101)
    if dati:
        chiave = f"history/{job_id}/preview.webp"
        storage.put(chiave, dati, "image/webp")
        campi["preview_key"] = chiave

    dati = _webp(_ridotta(rgb, settings.history_source_px), 92)
    if dati:
        chiave = f"history/{job_id}/source.webp"
        storage.put(chiave, dati, "image/webp")
        campi["source_key"] = chiave
    return campi


def prune_sources(user_id: str) -> int:
    """Toglie la sorgente alle voci oltre le ultime `HISTORY_KEEP_SOURCES`.

    Non tocca la voce: resta in cronologia con la sua miniatura e i suoi
    parametri, e perde solo il pulsante che rigenera con un clic.
    """
    store, storage = get_store(), get_storage()
    tolte = 0
    for riga in store.sources_beyond(user_id, settings.history_keep_sources):
        try:
            storage.delete(f"history/{riga['id']}/source.webp")
        except Exception:
            log.warning("could not delete source of %s", riga["id"], exc_info=True)
        store.update(riga["id"], {"source_key": None})
        tolte += 1
    return tolte


class JobRunner:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=settings.max_workers,
                                        thread_name_prefix="mangarelief")
        self._pending = 0
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def submit(self, job_id: str, image: np.ndarray, mode: str, engine_kwargs: Dict[str, Any],
               source_stem: Optional[str] = None, user_id: Optional[str] = None,
               requested=None):
        with self._lock:
            if self._pending >= settings.max_queue:
                raise QueueFull(f"{self._pending} jobs already queued or running")
            self._pending += 1
        self._pool.submit(self._run, job_id, image, mode, engine_kwargs, source_stem,
                          user_id, requested)

    # ------------------------------------------------------------------
    def _run(self, job_id: str, image: np.ndarray, mode: str, engine_kwargs: Dict[str, Any],
             source_stem: Optional[str] = None, user_id: Optional[str] = None,
             requested=None):
        store = get_store()
        storage = get_storage()
        started = time.time()
        last_pct = -10

        def on_progress(pct: int, msg: str):
            # Throttled: a DB round-trip per percent would cost more than the mesh.
            nonlocal last_pct
            if pct - last_pct >= 5 or pct >= 100:
                last_pct = pct
                try:
                    store.update(job_id, {"progress": int(pct), "message": str(msg)[:200]})
                except Exception:
                    log.warning("progress update failed for %s", job_id, exc_info=True)

        def should_cancel() -> bool:
            return (time.time() - started) > settings.job_timeout_s

        try:
            store.update(job_id, {"status": "running", "progress": 0,
                                  "message": "Starting..."})

            with tempfile.TemporaryDirectory(prefix=f"mr-{job_id}-") as tmp:
                stem = output_stem(source_stem, mode, engine_kwargs.get("color_mode"), job_id)
                stl_path = os.path.join(tmp, stem + ".stl")
                mf3_path = os.path.join(tmp, stem + ".3mf")

                params = GenerationParams(
                    mode=_MODE_MAP[mode],
                    output_path=stl_path,
                    output_path_3mf=mf3_path,
                    source_image_name=stem,
                    **engine_kwargs,
                )
                result = generate(engine_input(image, mode), params,
                                  progress=on_progress, should_cancel=should_cancel)

                artifacts: List[Dict[str, Any]] = []
                for kind, path, ctype in (
                    ("stl", result.stl_path, "model/stl"),
                    ("3mf", result.mf3_path, "model/3mf"),
                ):
                    if not path or not os.path.exists(path):
                        continue
                    with open(path, "rb") as fh:
                        data = fh.read()
                    key = f"{job_id}/{os.path.basename(path)}"
                    storage.put(key, data, ctype)
                    artifacts.append({"kind": kind, "filename": os.path.basename(path),
                                      "key": key, "bytes": len(data)})

            # Prima il risultato, poi la cronologia: se la miniatura non si
            # scrive, il modello e' comunque pronto e scaricabile.
            cronologia: Dict[str, Any] = {}
            if user_id and requested is not None:
                try:
                    cronologia = keep_for_history(job_id, image, requested, user_id)
                except Exception:
                    log.warning("history assets failed for %s", job_id, exc_info=True)

            store.update(job_id, {
                "status": "done",
                "progress": 100,
                "message": "Completed",
                "duration_s": round(result.elapsed_s, 2),
                "artifacts": artifacts,
                # Il piano di stampa: senza queste quote lo STL scaricato non
                # dice a che altezza cambiare bobina, e il 3MF le nasconde
                # dentro il file.
                "filament_changes": [
                    {"z": z, "color": (result.slot_colors[i]
                                       if i < len(result.slot_colors) else None)}
                    for i, z in enumerate(result.color_changes_z)
                ],
                "expires_at": iso(default_expiry()),
                **cronologia,
            })
            if cronologia.get("source_key"):
                try:
                    prune_sources(user_id)
                except Exception:
                    log.warning("pruning history sources failed for %s", user_id, exc_info=True)
            log.info("job %s done in %.2fs", job_id, result.elapsed_s)

        except InterruptedError:
            store.update(job_id, {"status": "error", "message": "Timed out",
                                  "error": f"exceeded {settings.job_timeout_s}s"})
            log.warning("job %s timed out", job_id)
        except Exception as exc:  # noqa: BLE001 - the client gets a clean message
            store.update(job_id, {"status": "error", "message": "Failed",
                                  "error": f"{type(exc).__name__}: {exc}"[:500]})
            log.exception("job %s failed", job_id)
        finally:
            with self._lock:
                self._pending -= 1


runner = JobRunner()
