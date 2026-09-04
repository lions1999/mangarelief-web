"""Indirizzi email: forma, normalizzazione e domini usa-e-getta.

Due problemi distinti, che vanno risolti in due modi diversi.

**Le caselle usa-e-getta.** Il codice via email dimostra che una casella
esiste, non che valga qualcosa: 10minutemail riceve davvero. Contro questo c'e'
un elenco di domini, scaricato da una lista pubblica mantenuta e tenuto nel
repo, cosi' il controllo e' una ricerca in memoria e non una chiamata di rete
nel percorso di registrazione. Un'azione settimanale lo riscarica.

**Gli alias.** Gmail ignora i punti e tutto cio' che segue un `+`: mario@,
m.ario@ e mario+1@ sono la stessa casella, ma per Supabase sarebbero tre
account con cinque generazioni ciascuno. La quota per account si aggirerebbe
in dieci secondi senza nemmeno una mail temporanea. Si normalizza *prima* di
chiedere il codice, cosi' Supabase vede una sola forma e crea un solo account;
il messaggio arriva lo stesso, perche' e' Gmail a consegnarlo nella stessa
casella.

L'elenco non e' un muro: chi vuole aggirarlo apre una casella vera in un
minuto. Serve ad alzare il costo del doppio giro casuale, non a fermare chi e'
determinato — quello non lo ferma nessun elenco.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Set

# Deliberatamente permissiva: la validazione vera e' che il codice arrivi.
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}$")

_DATA = os.path.join(os.path.dirname(__file__), "data", "disposable_domains.txt")

# Domini che trattano il punto come non significativo e `+` come separatore.
_GMAIL = {"gmail.com", "googlemail.com"}


@lru_cache(maxsize=1)
def disposable_domains() -> Set[str]:
    try:
        with open(_DATA, "r", encoding="utf-8") as fh:
            return {ln.strip().lower() for ln in fh
                    if ln.strip() and not ln.startswith("#")}
    except OSError:
        # Meglio accettare tutti che rifiutare tutti: un file mancante non deve
        # impedire le registrazioni.
        return set()


def is_valid(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def domain_of(email: str) -> str:
    return (email or "").strip().rsplit("@", 1)[-1].lower().rstrip(".")


def is_disposable(email: str) -> bool:
    return domain_of(email) in disposable_domains()


def normalize(email: str) -> str:
    """La forma canonica di un indirizzo: una casella, una identita'.

    Minuscolo sempre (il dominio non e' sensibile alle maiuscole, e nessun
    provider serio lo e' sulla parte locale). Su Gmail anche via i punti e la
    parte dopo il `+`, che quel provider ignora.
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    domain = domain.rstrip(".")
    # Il `+suffisso` lo ignorano quasi tutti i provider seri (Gmail, Outlook,
    # Yahoo, Proton, iCloud), quindi si toglie ovunque: lasciarlo solo a Gmail
    # spostava il buco un dominio piu' in la'. Il rischio e' un dominio dove
    # `a+b@` sia davvero un'altra casella — raro, e il danno sarebbe due
    # persone che condividono una quota, non un accesso indebito.
    local = local.split("+", 1)[0]
    if domain in _GMAIL:
        local = local.replace(".", "")   # su Gmail il punto non conta
        domain = "gmail.com"             # googlemail.com e' la stessa casella
    return f"{local}@{domain}"
