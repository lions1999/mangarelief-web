"""The `generations` table.

It is three things at once, as the plan intended: the job state a client polls,
the retention ledger the cleanup endpoint walks, and the usage log that will
back per-user quota in phase 3. Job state lives in the table rather than in
process memory so polling still works when the platform runs more than one
instance.

Two back ends, one interface: SQLite locally, Supabase (PostgREST) in production.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

COLUMNS = ("id", "created_at", "user_id", "ip_hash", "device_id", "mode", "params",
           "status", "progress", "message", "duration_s", "error", "artifacts",
           "filament_changes", "expires_at", "downloaded_at",
           # Cronologia: cosa resta quando i file sono scaduti. Vedi la
           # migrazione 20260904140000_history.sql.
           "image_name", "preview_key", "source_key", "hidden_at")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    txt = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(txt)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SqliteStore:
    """Local development store. Same columns as the Supabase table."""

    def __init__(self, root: str):
        os.makedirs(root, exist_ok=True)
        self.path = os.path.join(root, "generations.db")
        self._lock = threading.Lock()
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    user_id TEXT,
                    ip_hash TEXT,
                    device_id TEXT,
                    mode TEXT NOT NULL,
                    params TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT DEFAULT '',
                    duration_s REAL,
                    error TEXT,
                    artifacts TEXT NOT NULL DEFAULT '[]',
                    filament_changes TEXT NOT NULL DEFAULT '[]',
                    expires_at TEXT,
                    downloaded_at TEXT,
                    image_name TEXT,
                    preview_key TEXT,
                    source_key TEXT,
                    hidden_at TEXT
                )
            """)
            # I database locali nati prima della cronologia non hanno le
            # colonne nuove: aggiungerle qui evita di dover cancellare .data
            # a ogni aggiornamento dello schema.
            presenti = {r["name"] for r in con.execute("PRAGMA table_info(generations)")}
            for nome in ("image_name", "preview_key", "source_key", "hidden_at"):
                if nome not in presenti:
                    con.execute(f"ALTER TABLE generations ADD COLUMN {nome} TEXT")

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=30.0)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["params"] = json.loads(d["params"] or "{}")
        d["artifacts"] = json.loads(d["artifacts"] or "[]")
        d["filament_changes"] = json.loads(d["filament_changes"] or "[]")
        return d

    def insert(self, record: Dict[str, Any]) -> None:
        payload = dict(record)
        payload["params"] = json.dumps(payload.get("params", {}))
        payload["artifacts"] = json.dumps(payload.get("artifacts", []))
        payload["filament_changes"] = json.dumps(payload.get("filament_changes", []))
        with self._lock, self._connect() as con:
            con.execute(
                f"INSERT INTO generations ({','.join(COLUMNS)}) "
                f"VALUES ({','.join('?' * len(COLUMNS))})",
                [payload.get(c) for c in COLUMNS],
            )

    def update(self, job_id: str, fields: Dict[str, Any]) -> None:
        payload = dict(fields)
        if "params" in payload:
            payload["params"] = json.dumps(payload["params"])
        if "artifacts" in payload:
            payload["artifacts"] = json.dumps(payload["artifacts"])
        if "filament_changes" in payload:
            payload["filament_changes"] = json.dumps(payload["filament_changes"])
        sets = ", ".join(f"{k} = ?" for k in payload)
        with self._lock, self._connect() as con:
            con.execute(f"UPDATE generations SET {sets} WHERE id = ?",
                        list(payload.values()) + [job_id])

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM generations WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def usage_since(self, field: str, value: str, since: datetime) -> tuple:
        """(quante generazioni, la piu' vecchia) per un'identita' nella finestra.

        Due valori in una query sola: il conteggio decide se puoi generare, la
        piu' vecchia dice *quando* si libera uno slot — con una finestra
        scorrevole non e' la mezzanotte, e' quella riga piu' 24 ore.
        """
        if field not in ("user_id", "ip_hash", "device_id"):
            raise ValueError(f"campo non ammesso per la quota: {field}")
        with self._connect() as con:
            row = con.execute(
                f"SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM generations "
                f"WHERE {field} = ? AND created_at >= ?",
                (value, iso(since)),
            ).fetchone()
        return int(row["n"]), row["oldest"]

    def count_recent(self, ip_hash: str, since: datetime) -> int:
        return self.usage_since("ip_hash", ip_hash, since)[0]

    def link_device(self, device_id: str, user_id: str, since: datetime) -> int:
        """Attribuisce all'account le generazioni anonime fatte da quel browser.

        Cosi' chi prova due volte e poi si registra non riparte dal totale
        pieno. Nessun caso speciale nel conteggio: una volta scritto user_id,
        la quota per utente le include da sola.
        """
        with self._connect() as con:
            cur = con.execute(
                "UPDATE generations SET user_id = ? "
                "WHERE device_id = ? AND user_id IS NULL AND created_at >= ?",
                (user_id, device_id, iso(since)),
            )
            return int(cur.rowcount or 0)

    def history(self, user_id: str, limit: int = 60) -> List[Dict[str, Any]]:
        """Le generazioni di un account, dalla piu' recente. Le nascoste no:
        chi ha svuotato una voce non deve rivedersela."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM generations WHERE user_id = ? AND hidden_at IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def sources_beyond(self, user_id: str, keep: int) -> List[Dict[str, Any]]:
        """Le voci di quell'account che conservano ancora la sorgente oltre le
        `keep` piu' recenti: sono quelle da potare.

        La sorgente pesa quindici volte la miniatura, ed e' l'unica cosa nella
        cronologia che possa riempire il bucket. Le voci restano, con la loro
        miniatura e i loro parametri; perdono solo il clic che rigenera.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, source_key FROM generations "
                "WHERE user_id = ? AND source_key IS NOT NULL "
                "ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                (user_id, keep),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_expired(self, now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM generations WHERE status != 'expired' "
                "AND expires_at IS NOT NULL AND expires_at <= ? LIMIT ?",
                (iso(now), limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]


class SupabaseStore:
    """PostgREST client for the same table.

    Every query goes through httpx's `params`, never an f-string URL: an ISO
    timestamp ends in `+00:00`, and a raw `+` in a query string means a space.
    Interpolated by hand, PostgREST receives a malformed date and answers 400 —
    which is exactly how the nightly cleanup first failed in production.
    """

    def __init__(self, url: str, key: str):
        self.base = f"{url}/rest/v1/generations"
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def insert(self, record: Dict[str, Any]) -> None:
        r = httpx.post(self.base, json=record,
                       headers={**self.headers, "Prefer": "return=minimal"}, timeout=30.0)
        r.raise_for_status()

    def update(self, job_id: str, fields: Dict[str, Any]) -> None:
        r = httpx.patch(self.base, params={"id": f"eq.{job_id}"}, json=fields,
                        headers={**self.headers, "Prefer": "return=minimal"}, timeout=30.0)
        r.raise_for_status()

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        r = httpx.get(self.base, params={"id": f"eq.{job_id}", "select": "*"},
                      headers=self.headers, timeout=30.0)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

    def usage_since(self, field: str, value: str, since: datetime) -> tuple:
        """Conteggio e riga piu' vecchia in un solo giro: il totale arriva
        nell'header Content-Range, la piu' vecchia nel corpo."""
        if field not in ("user_id", "ip_hash", "device_id"):
            raise ValueError(f"campo non ammesso per la quota: {field}")
        r = httpx.get(
            self.base,
            params={field: f"eq.{value}", "created_at": f"gte.{iso(since)}",
                    "select": "created_at", "order": "created_at.asc", "limit": "1"},
            headers={**self.headers, "Prefer": "count=exact"}, timeout=30.0)
        r.raise_for_status()
        total = int(r.headers.get("content-range", "*/0").split("/")[-1] or 0)
        rows = r.json() or []
        return total, (rows[0]["created_at"] if rows else None)

    def count_recent(self, ip_hash: str, since: datetime) -> int:
        return self.usage_since("ip_hash", ip_hash, since)[0]

    def link_device(self, device_id: str, user_id: str, since: datetime) -> int:
        r = httpx.patch(
            self.base,
            params={"device_id": f"eq.{device_id}", "user_id": "is.null",
                    "created_at": f"gte.{iso(since)}"},
            json={"user_id": user_id},
            headers={**self.headers, "Prefer": "return=representation"}, timeout=30.0)
        r.raise_for_status()
        return len(r.json() or [])

    def history(self, user_id: str, limit: int = 60) -> List[Dict[str, Any]]:
        r = httpx.get(
            self.base,
            params={"user_id": f"eq.{user_id}", "hidden_at": "is.null",
                    "select": "*", "order": "created_at.desc", "limit": str(limit)},
            headers=self.headers, timeout=30.0)
        r.raise_for_status()
        return r.json()

    def sources_beyond(self, user_id: str, keep: int) -> List[Dict[str, Any]]:
        # PostgREST non ha un OFFSET senza LIMIT: si chiede una pagina che
        # comincia dopo le `keep` da tenere. Il tetto alto e' una rete, non un
        # numero significativo — chi ne ha piu' di cosi' viene potato al giro
        # successivo.
        r = httpx.get(
            self.base,
            params={"user_id": f"eq.{user_id}", "source_key": "not.is.null",
                    "select": "id,source_key", "order": "created_at.desc",
                    "offset": str(keep), "limit": "200"},
            headers=self.headers, timeout=30.0)
        r.raise_for_status()
        return r.json()

    def list_expired(self, now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        r = httpx.get(
            self.base,
            params={"status": "neq.expired", "expires_at": f"lte.{iso(now)}",
                    "select": "*", "limit": str(limit)},
            headers=self.headers, timeout=30.0)
        r.raise_for_status()
        return r.json()


_store = None


def get_store():
    global _store
    if _store is None:
        _store = (SupabaseStore(settings.supabase_url, settings.supabase_key)
                  if settings.use_supabase else SqliteStore(settings.local_data_dir))
    return _store


def default_expiry(now: Optional[datetime] = None) -> datetime:
    return (now or utcnow()) + timedelta(hours=settings.retention_hours)


def shortened_expiry(current: Optional[datetime], now: Optional[datetime] = None) -> datetime:
    """After a download the file only needs to survive another day."""
    now = now or utcnow()
    shortened = now + timedelta(hours=settings.post_download_hours)
    return min(current, shortened) if current else shortened
