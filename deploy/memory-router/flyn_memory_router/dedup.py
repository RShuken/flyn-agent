"""SQLite-backed dedup table. `(source, dedup_key)` is the actual key — namespaced per spec §2.5."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dedup (
    source TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (source, dedup_key)
);
"""


class DedupStore:
    """Idempotent record-then-check store, namespaced by source."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def seen(self, source: str, dedup_key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT 1 FROM dedup WHERE source = ? AND dedup_key = ? LIMIT 1",
                (source, dedup_key),
            )
            return cur.fetchone() is not None

    def record(self, source: str, dedup_key: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO dedup(source, dedup_key, first_seen) VALUES (?, ?, ?)",
                (source, dedup_key, now),
            )
