"""Owner registry, access grants, and audit logging.

Per-owner physical isolation: each owner's conversation messages live in
their own SQLite file at <conv_root>/<owner_id>.db. Cross-owner reads
require an explicit grant row in owners.db and every cross-owner read
writes to audit_log.

The shared owners.db sits at <conv_root>/owners.db. Schema is created on
first OwnerRegistry construction (idempotent CREATE TABLE IF NOT EXISTS).
Principals.json seeds the registry on construction (idempotent
INSERT OR REPLACE).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    principals_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    viewer      TEXT NOT NULL,
    owned_by    TEXT NOT NULL,
    granted_at  TEXT NOT NULL,
    granted_by  TEXT NOT NULL,
    reason      TEXT,
    PRIMARY KEY (viewer, owned_by)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    viewer    TEXT NOT NULL,
    owned_by  TEXT NOT NULL,
    op        TEXT NOT NULL,
    q         TEXT
);
"""


@dataclass(frozen=True)
class Owner:
    id: str
    display_name: str
    chat_id_map: dict[str, str] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OwnerRegistry:
    def __init__(self, owners_db_path: Path, principals_json: Path) -> None:
        self._db_path = owners_db_path
        self._principals = principals_json
        self._lock = Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
        if self._principals.exists():
            self._seed_from_principals()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path)
        c.row_factory = sqlite3.Row
        return c

    def _seed_from_principals(self) -> None:
        try:
            data = json.loads(self._principals.read_text())
        except (OSError, json.JSONDecodeError):
            return
        with self._lock, self._conn() as c:
            for o in data.get("owners", []):
                c.execute(
                    "INSERT OR REPLACE INTO owners (id, display_name, principals_json) "
                    "VALUES (?, ?, ?)",
                    (o["id"], o.get("display_name", o["id"]),
                     json.dumps(o.get("principals", {}))),
                )

    # --- Resolution ---

    def resolve_from_chat(self, channel: str, sender_id: str) -> Owner | None:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id, display_name, principals_json FROM owners"
            ).fetchall()
        for row in rows:
            principals = json.loads(row["principals_json"])
            if principals.get(channel) == sender_id:
                return Owner(
                    id=row["id"],
                    display_name=row["display_name"],
                    chat_id_map=principals,
                )
        return None

    def db_path_for(self, owner_id: str, conv_root: Path) -> Path:
        return conv_root / f"{owner_id}.db"

    # --- Access ---

    def viewer_can_read(self, viewer: str, owned_by: str) -> bool:
        if viewer == owned_by:
            return True
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM grants WHERE viewer = ? AND owned_by = ?",
                (viewer, owned_by),
            ).fetchone()
        return row is not None

    def list_accessible_owners(self, viewer: str) -> set[str]:
        out: set[str] = set()
        with self._lock, self._conn() as c:
            row = c.execute("SELECT id FROM owners WHERE id = ?", (viewer,)).fetchone()
            if row:
                out.add(viewer)
            for r in c.execute(
                "SELECT owned_by FROM grants WHERE viewer = ?", (viewer,)
            ):
                out.add(r["owned_by"])
        return out

    def grant(self, viewer: str, owned_by: str, *,
              granted_by: str, reason: str = "") -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO grants "
                "(viewer, owned_by, granted_at, granted_by, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (viewer, owned_by, _now_iso(), granted_by, reason),
            )
            c.execute(
                "INSERT INTO audit_log (ts, viewer, owned_by, op, q) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(), viewer, owned_by, "grant", reason or None),
            )

    def revoke(self, viewer: str, owned_by: str, *, revoked_by: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "DELETE FROM grants WHERE viewer = ? AND owned_by = ?",
                (viewer, owned_by),
            )
            c.execute(
                "INSERT INTO audit_log (ts, viewer, owned_by, op, q) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(), viewer, owned_by, "revoke", revoked_by),
            )

    # --- Audit ---

    def append_audit(self, viewer: str, owned_by: str, *,
                     op: str, q: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO audit_log (ts, viewer, owned_by, op, q) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(), viewer, owned_by, op, q),
            )

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT ts, viewer, owned_by, op, q FROM audit_log "
                "ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
