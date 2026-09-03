"""Recognising the caller, when there is one.

Phase 3, step 1. Nothing changes for anybody yet: limits, retention and
behaviour are identical with or without a token. What changes is that a
generation made while signed in is recorded against that account — which is
the number per-user quota will count, instead of the IP hash it counts today.

**Verification asks Supabase instead of checking the signature locally.** That
means not holding the project's JWT secret at all — one fewer secret to store,
sync between environments and rotate — and it stays correct whichever signing
scheme the project uses, symmetric or asymmetric. The cost is a round trip;
a short-lived cache removes it for the common case, one person polling one job.

An absent Authorization header is an anonymous caller, which is fine. A header
that is *present but not valid* is an error, not a silent downgrade: a session
that expired mid-visit has to be visible to the frontend so it can refresh,
and a token we cannot verify must never quietly become "anonymous" — that is
how a bug in the auth path turns into free unlimited generations.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import Header, HTTPException, status

from .config import settings

# Long enough to cover a poll cycle, short enough that a revoked session stops
# working in about a minute.
_CACHE_TTL_S = 60.0
_CACHE_MAX = 512

_cache: Dict[str, tuple] = {}
_lock = threading.Lock()


def _key(token: str) -> str:
    """Cache under a hash: the token is a bearer credential and does not need
    to sit in a dictionary in plaintext."""
    return hashlib.sha256(token.encode()).hexdigest()


def fetch_user(token: str) -> Optional[Dict[str, Any]]:
    """Ask Supabase who this token belongs to. None when the token is not
    valid; raises httpx.HTTPError when Supabase cannot be reached, which
    `verify` turns into a 503.

    Replaced wholesale in tests — there is no Supabase to talk to there.
    """
    if not settings.use_supabase:
        return None
    r = httpx.get(
        f"{settings.supabase_url}/auth/v1/user",
        headers={"apikey": settings.supabase_key,
                 "Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    if r.status_code != 200:
        return None
    body = r.json()
    uid = body.get("id")
    return {"id": uid, "email": body.get("email")} if uid else None


def verify(token: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    k = _key(token)
    with _lock:
        hit = _cache.get(k)
        if hit and hit[1] > now:
            return hit[0]
    try:
        user = fetch_user(token)
    except httpx.HTTPError:
        # Supabase unreachable. Refusing is the only safe answer: treating the
        # caller as anonymous would hand the anonymous tier to everyone during
        # an outage, and treating the token as good would accept any string.
        # Caught here rather than inside fetch_user so it covers however the
        # lookup is done — that function is the seam tests replace.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "cannot verify the session right now, try again")
    with _lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()          # tiny and rare: no need for an LRU here
        _cache[k] = (user, now + _CACHE_TTL_S)
    return user


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def current_user(authorization: Optional[str] = Header(default=None)) -> Optional[Dict[str, Any]]:
    """FastAPI dependency: the signed-in user, or None for an anonymous call."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Authorization must be 'Bearer <token>'")
    user = verify(token.strip())
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "session expired or invalid, sign in again")
    return user
