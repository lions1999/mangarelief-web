#!/usr/bin/env python3
"""Verifica lo storage vero, su un prefisso che appartiene solo a questa prova.

E' l'ultimo pezzo dove il codice si fida di assunzioni mai misurate, e non e'
automatizzabile a poco prezzo: un finto proverebbe le mie convinzioni con le
mie convinzioni, e un job in CI vorrebbe dire mettere la chiave service-role
nei secret di GitHub — un secondo posto da cui puo' uscire, per una verifica
che serve solo quando si tocca lo storage.

L'assunzione piu' rischiosa e' dentro `delete_prefix`: elenca gli oggetti sotto
un prefisso e poi li cancella, dando per scontato che i nomi tornino relativi
al prefisso. Se tornassero assoluti, cancellerebbe percorsi inesistenti
**rispondendo 200** — file orfani che restano nel bucket per sempre, senza un
errore da nessuna parte. Con 1 GB di spazio e 9 MB a generazione, e' il genere
di perdita che non si nota finche' non e' tardi.

Uso:

    export SUPABASE_URL=https://<progetto>.supabase.co
    export SUPABASE_SERVICE_KEY=<la chiave service-role>
    export SUPABASE_BUCKET=generations          # opzionale
    python scripts/check_storage.py             # veloce
    python scripts/check_storage.py --molti     # anche il limite di 100
    python scripts/check_storage.py --locale    # contro lo storage su disco

`--locale` non tocca niente in rete e serve a due cose: provare questo script
prima di puntarlo sul bucket vero, e confrontare le due implementazioni fra
loro — sono la stessa interfaccia, e devono rispondere allo stesso modo, come
per l'archivio.

La chiave non viene stampata ne' registrata da nessuna parte. Tutto quel che
questa prova scrive sta sotto `_check/<istante>-<casuale>/` e viene cancellato
alla fine, anche se qualcosa fallisce a meta'.
"""

from __future__ import annotations

import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))

from app.storage import LocalStorage, SupabaseStorage  # noqa: E402

# Ogni chiave scritta da qui parte da questo prefisso, e la cancellazione non
# tocca nient'altro: il bucket contiene i modelli di chi usa il sito.
RADICE = "_check"

fails = []


def check(nome, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + nome + ((" | " + str(extra)) if extra else ""))
    if not ok:
        fails.append(nome)


def check_fn(nome, fn, extra=""):
    """Come `check`, ma il controllo e' una funzione: se solleva, e' un
    fallimento con dentro il motivo — non la fine della prova. La prima
    versione valutava l'espressione prima di entrare in `check`, e la prima
    risposta inattesa dello storage vero ha buttato giu' tutto il resto."""
    try:
        esito = fn()
    except Exception as exc:  # noqa: BLE001
        check(nome, False, f"{type(exc).__name__}: {exc}"[:200])
        return
    check(nome, bool(esito), extra)


def errore_di(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - e' proprio quello che si misura
        return type(exc).__name__
    return None


molti = "--molti" in sys.argv

if "--locale" in sys.argv:
    import tempfile

    bucket = tempfile.mkdtemp(prefix="mr-storage-")
    storage = LocalStorage(bucket)
else:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_BUCKET", "generations")

    if not url or not key:
        print(__doc__.split("Uso:")[1].strip())
        print("\nSUPABASE_URL o SUPABASE_SERVICE_KEY non impostate: "
              "non c'e' niente da verificare.")
        sys.exit(2)
    storage = SupabaseStorage(url, key, bucket)
prefisso = f"{RADICE}/{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
assert prefisso.startswith(RADICE + "/")
print(f"bucket: {bucket}\nprefisso di prova: {prefisso}/\n")

try:
    # ------------------------------------------------------ scrivere e rileggere
    dati = b"MangaRelief storage check \xc3\xa8 \x00\x01\x02" * 64
    storage.put(f"{prefisso}/a.bin", dati, "application/octet-stream")
    letto = storage.get(f"{prefisso}/a.bin")
    check("quel che si scrive si rilegge identico", letto == dati,
          f"{len(letto)} byte contro {len(dati)}")

    # x-upsert: riscrivere la stessa chiave deve sostituire, non fallire.
    check_fn("riscrivere la stessa chiave non da' errore",
             lambda: storage.put(f"{prefisso}/a.bin", b"nuovo",
                                 "application/octet-stream") is None)
    riletto = storage.get(f"{prefisso}/a.bin")
    check("e rileggendo si trova il contenuto nuovo", riletto == b"nuovo",
          f"{len(riletto)} byte")
    if riletto != b"nuovo" and isinstance(storage, SupabaseStorage):
        # Non si indovina il rimedio: si guarda cosa risponde davvero.
        #
        # L'elenco dice gia' che l'oggetto e' stato sostituito, quindi il
        # problema e' nella lettura. Le intestazioni della risposta dicono di
        # chi e' la colpa: `age` e `cf-cache-status` sono di una cache davanti,
        # `cache-control` dice quale politica e' stata registrata sull'oggetto
        # — e se non e' quella che abbiamo chiesto scrivendolo, la nostra
        # richiesta e' stata ignorata.
        import httpx as _hx

        url_oggetto = f"{storage.base}/object/{storage.bucket}/{prefisso}/a.bin"
        el = _hx.post(f"{storage.base}/object/list/{storage.bucket}",
                      json={"prefix": prefisso + "/", "limit": 10},
                      headers=storage.headers, timeout=60.0)
        voci = {v["name"]: v for v in (el.json() if el.status_code == 200 else [])}
        meta = (voci.get("a.bin") or {}).get("metadata") or {}
        print(f"    -> l'elenco dice: dimensione={meta.get('size')}, "
              f"cacheControl={meta.get('cacheControl')!r}, "
              f"aggiornato={(voci.get('a.bin') or {}).get('updated_at')}")

        rr = _hx.get(url_oggetto, headers=storage.headers, timeout=30.0)
        interessanti = ("cache-control", "age", "etag", "last-modified",
                        "cf-cache-status", "x-cache", "content-length")
        print("    -> la lettura risponde: "
              + ", ".join(f"{k}={rr.headers.get(k)!r}" for k in interessanti
                          if rr.headers.get(k) is not None))

        # Quanto ci mette a diventare coerente: se ci arriva, e' una cache che
        # si aggiorna con calma e la proprieta' che ci manca e' solo "subito".
        # Se non ci arriva mai, e' registrata cosi' e va cambiata all'origine.
        atteso = len(b"nuovo")
        partenza = time.time()
        coerente = None
        while time.time() - partenza < 75:
            time.sleep(5)
            corpo = _hx.get(url_oggetto, headers=storage.headers, timeout=30.0).content
            if corpo == b"nuovo":
                coerente = round(time.time() - partenza)
                break
        if coerente is not None:
            print(f"    -> la lettura si allinea dopo circa {coerente}s: e' una cache "
                  f"che si aggiorna con ritardo, non una scrittura che non arriva.")
        else:
            print(f"    -> dopo 75s la lettura restituisce ancora {atteso} byte di ritardo: "
                  f"non e' un ritardo, e' la politica registrata sull'oggetto.")

    check("una chiave che non esiste solleva, non restituisce vuoto",
          errore_di(lambda: storage.get(f"{prefisso}/mai-scritta.bin")) is not None)

    # -------------------------------------------------- cancellare un oggetto solo
    # Serve alla potatura della cronologia: la sorgente se ne va, la miniatura
    # che le sta accanto deve restare.
    storage.put(f"{prefisso}/coppia/source.webp", b"sorgente", "image/webp")
    storage.put(f"{prefisso}/coppia/preview.webp", b"miniatura", "image/webp")
    check("delete toglie l'oggetto indicato",
          storage.delete(f"{prefisso}/coppia/source.webp") is True)
    check("e sparisce davvero",
          errore_di(lambda: storage.get(f"{prefisso}/coppia/source.webp")) is not None)
    check("mentre quello accanto resta",
          storage.get(f"{prefisso}/coppia/preview.webp") == b"miniatura")
    check_fn("cancellare due volte non e' un errore, dice solo che non c'era",
             lambda: storage.delete(f"{prefisso}/coppia/source.webp") is False)

    # ------------------------------------------------------- cancellare una cartella
    # L'assunzione da verificare: i nomi che l'elenco restituisce sono relativi
    # al prefisso. Se fossero assoluti, questa cancellerebbe `prefisso/prefisso/...`
    # rispondendo 200, e i file resterebbero li' per sempre.
    cartella = f"{prefisso}/job"
    for i in range(3):
        storage.put(f"{cartella}/f{i}.bin", b"x" * (i + 1), "application/octet-stream")
    quanti = storage.delete_prefix(cartella)
    check("delete_prefix riporta quanti ne ha cancellati", quanti == 3, quanti)
    rimasti = [i for i in range(3)
               if errore_di(lambda i=i: storage.get(f"{cartella}/f{i}.bin")) is None]
    check("e li ha cancellati davvero, non ha solo risposto 200", not rimasti, rimasti)

    check("delete_prefix su una cartella vuota risponde zero",
          storage.delete_prefix(f"{prefisso}/mai-esistita") == 0)

    # ------------------------------------------------ il limite di 100 dell'elenco
    if molti:
        # `delete_prefix` elenca con limit: 100. Con piu' oggetti di cosi', o
        # ne restano indietro in silenzio, o la funzione li prende comunque.
        # Qui si scopre quale delle due.
        tanti = f"{prefisso}/tanti"
        for i in range(105):
            storage.put(f"{tanti}/f{i:03d}.bin", b"y", "application/octet-stream")
        quanti = storage.delete_prefix(tanti)
        superstiti = [i for i in range(105)
                      if errore_di(lambda i=i: storage.get(f"{tanti}/f{i:03d}.bin")) is None]
        check("oltre 100 oggetti la cartella si svuota lo stesso",
              not superstiti, f"cancellati {quanti}, rimasti {len(superstiti)}")
        if superstiti:
            print("    -> delete_prefix va chiamato in ciclo finche' non riporta 0,\n"
                  "       oppure l'elenco va paginato: cosi' lascia file orfani.")
    else:
        print("(salto il limite di 100: rilancia con --molti per provarlo)")

finally:
    # Sempre, anche dopo un fallimento a meta': questa prova non deve lasciare
    # spazzatura nel bucket di produzione.
    try:
        residui = storage.delete_prefix(prefisso)
        for sotto in ("coppia", "job", "tanti"):
            residui += storage.delete_prefix(f"{prefisso}/{sotto}")
        print(f"\npulizia: {residui} oggetti rimossi da {prefisso}/")
    except Exception as exc:  # noqa: BLE001
        print(f"\nATTENZIONE: pulizia fallita ({type(exc).__name__}): "
              f"cancella a mano il prefisso {prefisso}/ nel bucket {bucket}")

print("\n" + ("TUTTO OK" if not fails else "FALLITI: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
