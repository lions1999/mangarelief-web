"""Quante generazioni ti restano, e chi le sta contando.

Il conteggio sta sul database, non in memoria. La finestra scorrevole di prima
viveva dentro il processo: con due istanze di Cloud Run il limite raddoppiava
di fatto, e a ogni riavvio ripartiva da zero. Contare le righe di
`generations` e' l'unico modo perche' il numero sia lo stesso ovunque.

Chi conta cosa:

- **Autenticato** — si conta `user_id`. Nessun trucco lo aggira: svuotare il
  browser o cambiare rete non cambia chi sei.
- **Anonimo** — si conta `device_id`, un identificativo casuale che il browser
  si genera e conserva. *Non* l'IP: sotto CGNAT (rete mobile, molti provider
  fissi) un solo indirizzo copre migliaia di persone, e limitare per IP
  significa negare la prova gratuita a chi non ha ancora fatto niente.

Sul dispositivo si aggira svuotando i dati del browser, ed e' accettato: il
tetto per IP resta come seconda rete, piu' alto proprio perche' non deve
colpire i vicini di CGNAT. Chi vuole aggirarlo davvero lo aggira comunque
(le email usa-e-getta non finiscono mai); l'obiettivo e' scoraggiare il doppio
giro casuale, non costruire un muro che non esiste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from .config import settings
from .store import parse_iso, utcnow

# Un id di dispositivo e' generato dal browser: prima di finire in una query
# va ristretto a una forma nota. UUID o esadecimale, niente altro.
_DEVICE_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def clean_device_id(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    return raw if _DEVICE_RE.match(raw) else None


# Piano senza tetto sul numero di generazioni. La ritenzione NON cambia: i file
# scadono a 48 ore, 24 dal primo scaricamento, come per tutti — il tetto tolto
# e' quello sul conteggio, non quello sullo spazio, che e' la risorsa scarsa.
UNLIMITED = "unlimited"


@dataclass
class Quota:
    plan: str                 # "anonymous" | "registered" | "unlimited"
    limit: Optional[int]      # None = nessun tetto
    used: int
    reset_at: Optional[str]   # quando si libera il primo slot, ISO

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> Optional[int]:
        return None if self.unlimited else max(0, self.limit - self.used)

    def as_dict(self) -> Dict[str, Any]:
        return {"plan": self.plan, "limit": self.limit, "used": self.used,
                "remaining": self.remaining, "reset_at": self.reset_at}


def _reset_at(oldest_iso: Optional[str]) -> Optional[str]:
    """Con una finestra scorrevole lo slot si libera quando la generazione piu'
    vecchia esce dalla finestra, non a mezzanotte."""
    oldest = parse_iso(oldest_iso)
    if not oldest:
        return None
    return (oldest + timedelta(hours=settings.quota_window_h)).isoformat()


def current(store, user: Optional[dict], device_id: Optional[str],
            ip_hash: str) -> Quota:
    """Lo stato della quota per chi sta chiedendo. Non solleva: serve anche a
    mostrare il contatore prima che qualcuno provi a generare."""
    since = utcnow() - timedelta(hours=settings.quota_window_h)

    if user:
        used, oldest = store.usage_since("user_id", user["id"], since)
        if user.get("plan") == UNLIMITED:
            # Il conteggio si fa comunque: serve a vedere quanto si sta usando
            # il servizio, anche quando non lo si sta limitando.
            return Quota(UNLIMITED, None, used, None)
        return Quota("registered", settings.quota_user_daily, used, _reset_at(oldest))

    # Senza un id di dispositivo utilizzabile si ricade sull'IP: altrimenti
    # basterebbe omettere l'intestazione per non essere contati affatto.
    if device_id:
        used, oldest = store.usage_since("device_id", device_id, since)
    else:
        used, oldest = store.usage_since("ip_hash", ip_hash, since)
    return Quota("anonymous", settings.quota_anon_daily, used, _reset_at(oldest))


def enforce(store, user: Optional[dict], device_id: Optional[str],
            ip_hash: str) -> Quota:
    """Come `current`, ma rifiuta con 429 quando non ne restano.

    Nota su una gara possibile: due richieste simultanee possono contare
    entrambe lo stesso numero e passare entrambe. Con un worker alla volta e
    questo traffico il caso peggiore e' una generazione in piu', il che non
    giustifica un lock distribuito.
    """
    q = current(store, user, device_id, ip_hash)
    if q.unlimited:
        return q
    if q.remaining <= 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            (f"you have used all {q.limit} generations "
             f"{'on your account' if user else 'available without an account'}"),
            headers={"X-MangaRelief-Reset-At": q.reset_at or ""})

    # Seconda rete, solo per gli anonimi: chi svuota il browser per rifarsi le
    # prove gratuite viene fermato qui, con una soglia larga perche' sotto
    # CGNAT questo indirizzo non e' suo soltanto.
    if not user:
        since = utcnow() - timedelta(hours=settings.quota_window_h)
        ip_used, ip_oldest = store.usage_since("ip_hash", ip_hash, since)
        if ip_used >= settings.quota_anon_ip_daily:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many account-less generations from this network: sign in to continue",
                headers={"X-MangaRelief-Reset-At": _reset_at(ip_oldest) or ""})
    return q
