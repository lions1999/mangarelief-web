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

COLUMNS = ("id", "created_at", "user_id", "ip_hash", "mode", "params", "status",
           "progress", "message", "duration_s", "error", "artifacts",
           "expires_at", "downloaded_at")


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
                    mode TEXT NOT NULL,
                    params TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT DEFAULT '',
                    duration_s REAL,
                    error TEXT,
                    artifacts TEXT NOT NULL DEFAULT '[]',
                    expires_at TEXT,
                    downloaded_at TEXT
                )
            """)

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=30.0)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["params"] = json.loads(d["params"] or "{}")
        d["artifacts"] = json.loads(d["artifacts"] or "[]")
        return d

    def insert(self, record: Dict[str, Any]) -> None:
        payload = dict(record)
        payload["params"] = json.dumps(payload.get("params", {}))
        payload["artifacts"] = json.dumps(payload.get("artifacts", []))
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
        sets = ", ".join(f"{k} = ?" for k in payload)
        with self._lock, self._connect() as con:
            con.execute(f"UPDATE generations SET {sets} WHERE id = ?",
                        list(payload.values()) + [job_id])

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM generations WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def count_recent(self, ip_hash: str, since: datetime) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM generations WHERE ip_hash = ? AND created_at >= ?",
                (ip_hash, iso(since)),
            ).fetchone()
        return int(row["n"])

    def list_expired(self, now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM generations WHERE status != 'expired' "
                "AND expires_at IS NOT NULL AND expires_at <= ? LIMIT ?",
                (iso(now), limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]


class SupabaseStore:
    """PostgREST client for the same table."""

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
        r = httpx.patch(f"{self.base}?id=eq.{job_id}", json=fields,
                        headers={**self.headers, "Prefer": "return=minimal"}, timeout=30.0)
        r.raise_for_status()

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        r = httpx.get(f"{self.base}?id=eq.{job_id}&select=*", headers=self.headers, timeout=30.0)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

    def count_recent(self, ip_hash: str, since: datetime) -> int:
        r = httpx.get(
            f"{self.base}?ip_hash=eq.{ip_hash}&created_at=gte.{iso(since)}&select=id",
            headers={**self.headers, "Prefer": "count=exact", "Range": "0-0"}, timeout=30.0)
        r.raise_for_status()
        content_range = r.headers.get("content-range", "*/0")
        return int(content_range.split("/")[-1] or 0)

    def list_expired(self, now: datetime, limit: int = 200) -> List[Dict[str, Any]]:
        r = httpx.get(
            f"{self.base}?status=neq.expired&expires_at=lte.{iso(now)}&select=*&limit={limit}",
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
