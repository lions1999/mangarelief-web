"""Abuse limits for the anonymous tier.

The rate limiter is an in-memory sliding window: it is per-instance, so with
several instances the effective limit multiplies. That is a deliberate MVP
trade-off — the durable count lives in `generations` (see `store.count_recent`),
which the quota logic of phase 3 will use instead.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from .config import settings


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: int):
        self.limit = limit
        self.window_s = window_s
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """(allowed, seconds until a slot frees up)."""
        now = time.time()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, int(self.window_s - (now - hits[0])) + 1
            hits.append(now)
            return True, 0


limiter = SlidingWindowLimiter(settings.anon_rate_limit, settings.anon_rate_window_s)


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
