"""Il contratto dell'archivio contro un PostgREST vero.

Stessa lista di asserzioni che la suite esegue su SQLite, eseguita qui contro
il software che serve davvero il sito. E' la differenza fra credere e misurare:
le prove che avevamo su Supabase controllavano l'URL che il codice costruisce,
giudicato da quello stesso codice — se una convinzione su PostgREST e'
sbagliata, il codice e la sua prova sono sbagliati insieme e vanno d'accordo.

Qui a rispondere e' PostgREST, sopra un Postgres su cui sono state applicate le
migrazioni vere, non modificate. Restano fuori GoTrue, le policy RLS come sono
configurate nel progetto, e lo Storage: un verde qui non dice che Supabase
funzioni, dice che le nostre query funzionano.

    SUPABASE_TEST_URL=http://localhost:3000 PGRST_JWT_SECRET=... \\
        python api/tests/contract_supabase.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.store import SupabaseStore  # noqa: E402
from tests.contract_store import esercita  # noqa: E402

URL = os.environ.get("SUPABASE_TEST_URL", "http://localhost:3000")
SEGRETO = os.environ.get("PGRST_JWT_SECRET", "")

fails = []


def check(nome, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + nome + ((" | " + str(extra)) if extra else ""))
    if not ok:
        fails.append(nome)


def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode()


def gettone(ruolo: str) -> str:
    """Un JWT HS256 come quelli di Supabase.

    Le chiavi di Supabase *sono* JWT firmati, con dentro il ruolo: la
    service-role dichiara `role: service_role`, quella pubblica `role: anon`.
    Costruirli qui e' quel che rende la prova fedele — il codice manda le sue
    intestazioni come le manda sempre, e chi risponde le valida sul serio.
    """
    testa = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    corpo = _b64(json.dumps({"role": ruolo, "iss": "contract-test",
                             "exp": int(time.time()) + 3600}).encode())
    firma = hmac.new(SEGRETO.encode(), f"{testa}.{corpo}".encode(), hashlib.sha256).digest()
    return f"{testa}.{corpo}.{_b64(firma)}"


def attendi(secondi: float = 60.0) -> bool:
    scadenza = time.time() + secondi
    while time.time() < scadenza:
        try:
            if httpx.get(URL, timeout=3.0).status_code < 500:
                return True
        except Exception:  # noqa: BLE001 - sta ancora partendo
            pass
        time.sleep(1.0)
    return False


if not SEGRETO:
    print("PGRST_JWT_SECRET non impostato: senza, PostgREST non validerebbe niente")
    sys.exit(2)

if not attendi():
    print(f"PostgREST non risponde su {URL}")
    sys.exit(2)

servizio = SupabaseStore(URL, gettone("service_role"), rest_path="")
esercita(servizio, check, "postgrest")

# --------------------------------------------------------------------- RLS
# La tabella ha RLS attivo e nessuna policy: chi non e' service_role non deve
# vedere niente. E' la chiave anon a girare nel browser di chiunque, quindi
# questa e' la riga che separa "crediamo che RLS sia attivo" da "lo sappiamo".
# Il modo in cui fallirebbe e' insidioso: non un errore, una lista vuota — che
# si legge come "nessun dato" invece che come "non ti e' permesso".
def _righe(risposta):
    """Il corpo come lista, o None se non e' nemmeno JSON: un 500 non deve far
    esplodere la prova al posto di farla fallire."""
    try:
        corpo = risposta.json()
    except Exception:  # noqa: BLE001
        return None
    return corpo if isinstance(corpo, list) else None


r = httpx.get(f"{URL}/generations", params={"select": "*", "limit": "5"},
              headers={"Authorization": f"Bearer {gettone('anon')}"}, timeout=15.0)
check("con un token anon la tabella non si legge",
      r.status_code in (401, 403) or _righe(r) == [],
      f"{r.status_code} {r.text[:120]}")

# E che il service_role invece veda: senza BYPASSRLS vedrebbe zero righe pure
# lui, e tutte le asserzioni qui sopra sarebbero passate su un database che
# sembra solo vuoto.
r = httpx.get(f"{URL}/generations", params={"select": "id", "limit": "1"},
              headers={"Authorization": f"Bearer {gettone('service_role')}"}, timeout=15.0)
check("il service_role invece legge: le prove non giravano sul vuoto",
      r.status_code == 200 and len(_righe(r) or []) == 1, f"{r.status_code} {r.text[:120]}")

print("\n" + ("ALL OK" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
