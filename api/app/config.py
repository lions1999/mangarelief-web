"""Runtime configuration, read once from the environment.

Everything the service needs to run is an env var, so the same image runs
locally (filesystem + SQLite) and on Cloud Run (Supabase) with no code change.
When Supabase credentials are absent the service falls back to local mode —
that is what makes the whole API testable before any cloud account exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


@dataclass
class Settings:
    # --- Storage / database ---------------------------------------------
    supabase_url: str = field(default_factory=lambda: _env("SUPABASE_URL").rstrip("/"))
    # Service-role key: server-side only, never shipped to a browser.
    supabase_key: str = field(default_factory=lambda: _env("SUPABASE_SERVICE_KEY"))
    # Chiave pubblica, usata solo per gli endpoint di autenticazione. E' fatta
    # per stare in un browser: senza, si ripiega sulla service-role.
    supabase_anon_key: str = field(default_factory=lambda: _env("SUPABASE_ANON_KEY"))
    supabase_bucket: str = field(default_factory=lambda: _env("SUPABASE_BUCKET", "generations"))
    local_data_dir: str = field(default_factory=lambda: _env("LOCAL_DATA_DIR", "./.data"))

    # --- Retention policy ------------------------------------------------
    # Files live 48h; the first download shortens that to 24h.
    retention_hours: int = field(default_factory=lambda: _env_int("RETENTION_HOURS", 48))
    post_download_hours: int = field(default_factory=lambda: _env_int("POST_DOWNLOAD_HOURS", 24))
    cleanup_token: str = field(default_factory=lambda: _env("CLEANUP_TOKEN"))

    # --- Upload / abuse limits ------------------------------------------
    max_upload_bytes: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_BYTES", 12 * 1024 * 1024))
    max_image_pixels: int = field(default_factory=lambda: _env_int("MAX_IMAGE_PIXELS", 40_000_000))
    # Quota durevole, contata sulle righe di `generations`. Sostituisce la
    # finestra scorrevole in memoria, che era per-istanza: con due istanze il
    # limite raddoppiava di fatto.
    quota_anon_daily: int = field(default_factory=lambda: _env_int("QUOTA_ANON_DAILY", 2))
    quota_user_daily: int = field(default_factory=lambda: _env_int("QUOTA_USER_DAILY", 5))
    quota_window_h: int = field(default_factory=lambda: _env_int("QUOTA_WINDOW_H", 24))
    # Rete di sicurezza per chi svuota il browser per rifarsi le prove gratuite.
    # Piu' alta della quota per dispositivo di proposito: sotto CGNAT un solo
    # indirizzo copre molte persone, e stringere qui punisce chi non c'entra.
    quota_anon_ip_daily: int = field(default_factory=lambda: _env_int("QUOTA_ANON_IP_DAILY", 10))
    ip_hash_salt: str = field(default_factory=lambda: _env("IP_HASH_SALT", "mangarelief-dev-salt"))
    # Cloudflare Turnstile. Unset = no challenge, which is what local
    # development and the first deploy run with.
    turnstile_secret: str = field(default_factory=lambda: _env("TURNSTILE_SECRET"))

    # --- Generation limits for the anonymous tier ------------------------
    # Draft resolution only: an Ultra run peaks well past 1 GB of RAM, which
    # no free instance survives. The technical ceiling doubles as the
    # free/paid line described in the plan.
    anon_max_res_cap: int = field(default_factory=lambda: _env_int("ANON_MAX_RES_CAP", 800))
    anon_max_dim_mm: float = field(default_factory=lambda: _env_float("ANON_MAX_DIM_MM", 200.0))
    allowed_modes: List[str] = field(default_factory=lambda: ["standard", "spot_color"])

    # --- Job queue --------------------------------------------------------
    # One worker: the pipeline is CPU- and RAM-hungry, running two at once on
    # a small instance is how you get OOM-killed instead of slow.
    max_workers: int = field(default_factory=lambda: _env_int("MAX_WORKERS", 1))
    max_queue: int = field(default_factory=lambda: _env_int("MAX_QUEUE", 8))
    job_timeout_s: int = field(default_factory=lambda: _env_int("JOB_TIMEOUT_S", 600))

    # Browsers compare the Origin header byte for byte, so a trailing slash or
    # stray whitespace in this variable silently breaks every call: the request
    # still reaches the server (a multipart POST needs no preflight, so the row
    # is written) but the browser discards the response and the page appears to
    # do nothing. Normalising here removes the commonest way to get that.
    cors_origins: List[str] = field(
        default_factory=lambda: [o.strip().rstrip("/")
                                 for o in _env("CORS_ORIGINS", "*").split(",")
                                 if o.strip()]
    )

    @property
    def use_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


settings = Settings()
