"""Artifact storage: local directory in development, Supabase Storage in production.

Both back ends expose the same four operations, so nothing above this module
knows which one is in use.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

import httpx

from .config import settings


class LocalStorage:
    """Files under LOCAL_DATA_DIR/files. Used whenever Supabase is not configured."""

    def __init__(self, root: str):
        self.root = os.path.join(root, "files")
        os.makedirs(self.root, exist_ok=True)

    def _abs(self, key: str) -> str:
        path = os.path.normpath(os.path.join(self.root, key))
        if not path.startswith(os.path.abspath(self.root)):
            raise ValueError("path traversal blocked")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._abs(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def get(self, key: str) -> bytes:
        with open(self._abs(key), "rb") as fh:
            return fh.read()

    def delete(self, key: str) -> bool:
        """Un oggetto solo. `delete_prefix` cancella cartelle: la sorgente di
        una voce di cronologia e' un file dentro una cartella che deve restare
        (accanto c'e' la miniatura, che sopravvive alla potatura)."""
        path = self._abs(key)
        if not os.path.isfile(path):
            return False
        os.remove(path)
        return True

    def delete_prefix(self, prefix: str) -> int:
        path = self._abs(prefix)
        if not os.path.isdir(path):
            return 0
        n = sum(len(files) for _, _, files in os.walk(path))
        shutil.rmtree(path, ignore_errors=True)
        return n


class SupabaseStorage:
    """Supabase Storage over its REST API, with the service-role key."""

    def __init__(self, url: str, key: str, bucket: str):
        self.base = f"{url}/storage/v1"
        self.bucket = bucket
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Scrive un oggetto, sostituendolo se la chiave esiste gia'.

        `cache-control` e' esplicito perche' il valore ereditato non era una
        scelta: senza, Supabase serve le letture da una cache di un'ora, e
        riscrivere una chiave lascia rileggere il contenuto vecchio. Misurato
        sul bucket vero — dopo la sostituzione l'elenco riportava la dimensione
        nuova mentre la lettura restituiva ancora quella vecchia.

        Oggi non ci morderebbe (ogni chiave nasce da un identificativo unico e
        non viene mai riscritta), ma una proprieta' che si crede di avere e non
        si ha e' peggio di una che si sa di non avere. Cachare qui non ci fa
        guadagnare niente: chi legge e' la nostra API, che sulle proprie
        risposte mette le intestazioni che vuole.
        """
        r = httpx.post(
            f"{self.base}/object/{self.bucket}/{key}",
            content=data,
            headers={**self.headers, "Content-Type": content_type, "x-upsert": "true",
                     "cache-control": "no-store"},
            timeout=120.0,
        )
        r.raise_for_status()

    def get(self, key: str) -> bytes:
        r = httpx.get(f"{self.base}/object/{self.bucket}/{key}",
                      headers=self.headers, timeout=120.0)
        r.raise_for_status()
        return r.content

    def delete(self, key: str) -> bool:
        """Cancella un oggetto. False se non c'era.

        Supabase risponde **400**, non 404, quando la chiave non esiste — con
        "not found" nel corpo. Misurato sul bucket vero: la versione che
        aspettava solo il 404 sollevava un'eccezione al posto di rispondere
        "non c'era", e chi chiama (la potatura della cronologia) la
        interpretava come un guasto dello storage.

        Il 400 si legge solo quando dice davvero questo: un 400 diverso e' un
        errore, e trasformarlo in "non c'era" nasconderebbe il motivo.
        """
        r = httpx.request("DELETE", f"{self.base}/object/{self.bucket}/{key}",
                          headers=self.headers, timeout=60.0)
        if r.status_code == 404:
            return False
        if r.status_code == 400 and "not found" in r.text.lower():
            return False
        r.raise_for_status()
        return True

    # L'elenco ne restituisce al massimo questi per volta: e' un limite del
    # servizio, non una nostra preferenza.
    _PAGINA = 100

    def delete_prefix(self, prefix: str) -> int:
        """Cancella tutti gli oggetti sotto un prefisso, e riporta quanti.

        A giri, non in uno solo. La versione precedente chiedeva una pagina da
        cento e cancellava quella: con piu' di cento oggetti gli altri
        restavano nel bucket **e la funzione rispondeva 200**, cioe' file
        orfani senza un errore da nessuna parte. Misurato sul bucket vero con
        105 oggetti: ne cancellava 100 e ne lasciava 5.

        Oggi sotto un prefisso ce ne sono due o tre (uno STL, un 3MF, o la
        coppia miniatura/sorgente), quindi non stava perdendo niente. Ma con
        1 GB di spazio e 9 MB a generazione, una perdita silenziosa e' il
        genere di cosa che ci si accorge tardi.

        Non scende nelle sottocartelle: non ne abbiamo, e una cancellazione
        ricorsiva scritta senza averne bisogno e' solo un modo piu' ampio di
        sbagliare.
        """
        radice = prefix.rstrip("/")
        totale = 0
        # Un tetto ai giri: se il servizio continuasse a riportare gli stessi
        # nomi, meglio uscire che restare in cerchio per sempre.
        for _ in range(1000):
            listing = httpx.post(
                f"{self.base}/object/list/{self.bucket}",
                json={"prefix": radice + "/", "limit": self._PAGINA},
                headers=self.headers, timeout=60.0,
            )
            listing.raise_for_status()
            names = [f"{radice}/{item['name']}" for item in listing.json()]
            if not names:
                break
            r = httpx.request(
                "DELETE", f"{self.base}/object/{self.bucket}",
                json={"prefixes": names}, headers=self.headers, timeout=60.0,
            )
            r.raise_for_status()
            totale += len(names)
            if len(names) < self._PAGINA:
                break
        return totale


_storage: Optional[object] = None


def get_storage():
    global _storage
    if _storage is None:
        if settings.use_supabase:
            _storage = SupabaseStorage(settings.supabase_url, settings.supabase_key,
                                       settings.supabase_bucket)
        else:
            _storage = LocalStorage(settings.local_data_dir)
    return _storage
