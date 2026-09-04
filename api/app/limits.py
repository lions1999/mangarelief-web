"""Free-tier ceilings, IP hashing and captcha.

The generation *count* is no longer here: it moved to `quota`, which reads it
from `generations`. The sliding window that used to live in this module was
per-process, so two Cloud Run instances doubled the effective limit and a
restart reset it — a placeholder that phase 3 replaced.
"""

from __future__ import annotations

import hashlib

from .config import settings




def hash_ip(ip: str) -> str:
    """Store a salted hash, never the address itself."""
    return hashlib.sha256((settings.ip_hash_salt + "|" + (ip or "")).encode()).hexdigest()[:32]


def clamp_to_anonymous_tier(params) -> list[str]:
    """Force the free-tier ceilings, reporting what was lowered.

    Draft resolution is not just a commercial line: an Ultra run peaks past a
    gigabyte of RAM, which a free instance does not have.
    """
    notes = []
    if params.max_res_cap > settings.anon_max_res_cap:
        notes.append(f"max_res_cap lowered to {settings.anon_max_res_cap} (free tier)")
        params.max_res_cap = settings.anon_max_res_cap
    if params.max_dim > settings.anon_max_dim_mm:
        notes.append(f"max_dim lowered to {settings.anon_max_dim_mm} mm (free tier)")
        params.max_dim = settings.anon_max_dim_mm
    return notes


TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_ok(token: str, ip: str) -> bool:
    """Verify a Turnstile token. Always true when no secret is configured.

    A network failure against Cloudflare fails open on purpose: a captcha
    outage should slow abuse down, not take the whole service offline.
    """
    if not settings.turnstile_secret:
        return True
    if not token:
        return False
    try:
        import httpx

        res = httpx.post(TURNSTILE_VERIFY, timeout=10.0,
                         data={"secret": settings.turnstile_secret,
                               "response": token, "remoteip": ip})
        return bool(res.json().get("success"))
    except Exception:
        return True
