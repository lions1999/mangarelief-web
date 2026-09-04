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
from .emails import is_disposable

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
    if not uid:
        return None
    # Il piano sta in app_metadata, non in user_metadata: il primo lo scrive
    # solo la chiave service-role, il secondo puo' scriverselo l'utente. Un
    # piano modificabile da chi ne beneficia non e' un piano.
    #
    # La chiave si chiama `plan` e non `role` perche' GoTrue ha gia' un campo
    # `role` suo, che vale "authenticated" per tutti: sovrapporsi confonderebbe
    # due cose diverse.
    meta = body.get("app_metadata") or {}
    return {"id": uid, "email": body.get("email"),
            "plan": (meta.get("plan") or "").strip().lower() or None}


def _reject_disposable(user: Optional[Dict[str, Any]]) -> None:
    """Il controllo che conta davvero.

    La chiave anon e' pubblica, quindi nulla impedisce di chiedere il codice a
    Supabase scavalcando il nostro endpoint e il suo elenco. Rifiutare qui —
    al momento dell'uso, su ogni richiesta autenticata — rende quell'account
    inutile comunque.
    """
    if user and user.get("email") and is_disposable(user["email"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "questo indirizzo email non e' accettato, usane uno permanente")


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
    _reject_disposable(user)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "session expired or invalid, sign in again")
    return user


# ---------------------------------------------------------------------------
# Accesso: codice di sei cifre via email
# ---------------------------------------------------------------------------
# Il codice invece del link: un magic link apre una scheda nuova, e chi ha gia'
# caricato l'immagine e regolato i parametri li perderebbe tutti. Col codice
# resti dove sei.
#
# La richiesta passa di qui e non direttamente da Supabase perche' altrimenti
# il nostro backend non vedrebbe mai l'indirizzo, e non potrebbe ne' rifiutare
# i domini usa-e-getta ne' normalizzare gli alias.

_SEND_WINDOW_S = 900
_SEND_MAX_PER_IP = 6
_sends: Dict[str, list] = {}


def _too_many_sends(ip_hash: str) -> bool:
    """Freno agli invii, per non trasformare il sito in un mezzo per spedire
    email a indirizzi altrui. E' in memoria e quindi per-istanza, il che qui
    basta: il limite vero e' quello di Supabase, che rifiuta invii ravvicinati
    allo stesso indirizzo. Qui si spende un'email, non CPU."""
    now = time.time()
    with _lock:
        recenti = [t for t in _sends.get(ip_hash, []) if now - t < _SEND_WINDOW_S]
        _sends[ip_hash] = recenti
        if len(recenti) >= _SEND_MAX_PER_IP:
            return True
        recenti.append(now)
        return False


def _auth_key() -> str:
    """La chiave per gli endpoint di autenticazione. La anon e' quella giusta;
    senza, si ripiega sulla service-role, che funziona ma e' piu' potente del
    necessario."""
    return settings.supabase_anon_key or settings.supabase_key


def _auth_post(path: str, payload: dict, params: Optional[dict] = None) -> httpx.Response:
    if not settings.use_supabase:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "authentication is not configured on this deployment")
    try:
        return httpx.post(
            f"{settings.supabase_url}/auth/v1/{path}",
            json=payload, params=params or {},
            headers={"apikey": _auth_key(), "Content-Type": "application/json"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"cannot reach the sign-in service: {type(exc).__name__}")


def send_code(email: str, ip_hash: str) -> None:
    """Chiede a Supabase di spedire il codice. Non dice mai se l'indirizzo era
    gia' registrato: sarebbe un modo per scoprire chi ha un account qui."""
    if _too_many_sends(ip_hash):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "troppi codici richiesti, riprova fra qualche minuto")
    r = _auth_post("otp", {"email": email, "create_user": True})
    if r.status_code >= 400:
        # 429 di Supabase = invii troppo ravvicinati allo stesso indirizzo.
        if r.status_code == 429:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "un codice e' gia' stato inviato da poco, controlla la posta")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "the sign-in service refused the request")


def verify_code(email: str, code: str) -> Dict[str, Any]:
    """Scambia il codice con una sessione. Solleva 401 se non combacia."""
    r = _auth_post("verify", {"type": "email", "email": email, "token": code})
    if r.status_code >= 400:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "codice non valido o scaduto")
    return r.json()


def refresh_session(refresh_token: str) -> Dict[str, Any]:
    r = _auth_post("token", {"refresh_token": refresh_token},
                   params={"grant_type": "refresh_token"})
    if r.status_code >= 400:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessione scaduta, accedi di nuovo")
    return r.json()
