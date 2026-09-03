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
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import numpy as np

from engine import GenerationMode, GenerationParams, generate

from .analysis import engine_input

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

    def submit(self, job_id: str, image: np.ndarray, mode: str, engine_kwargs: Dict[str, Any]):
        with self._lock:
            if self._pending >= settings.max_queue:
                raise QueueFull(f"{self._pending} jobs already queued or running")
            self._pending += 1
        self._pool.submit(self._run, job_id, image, mode, engine_kwargs)

    # ------------------------------------------------------------------
    def _run(self, job_id: str, image: np.ndarray, mode: str, engine_kwargs: Dict[str, Any]):
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
                stem = f"mangarelief_{mode}_{job_id[:8]}"
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
            })
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
