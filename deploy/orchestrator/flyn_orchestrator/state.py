"""SQLite-backed canonical task state. WAL mode for concurrent access."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .types import TaskRecord, TaskState


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    state TEXT NOT NULL,
    sender_role TEXT NOT NULL,
    sender_identifier TEXT NOT NULL,
    intent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    budget_usd REAL NOT NULL DEFAULT 5.0,
    raw_payload TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    ts TEXT NOT NULL,
    reason TEXT,
    payload TEXT,
    UNIQUE(task_id, from_state, to_state, actor)
);

CREATE TABLE IF NOT EXISTS task_id_counter (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last INTEGER NOT NULL DEFAULT 0
);
"""


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("INSERT OR IGNORE INTO task_id_counter(id, last) VALUES (1, 0)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def next_task_id(self) -> str:
        with self._connect() as conn:
            cur = conn.execute("UPDATE task_id_counter SET last = last + 1 WHERE id = 1 RETURNING last")
            n = cur.fetchone()[0]
        return f"T-{n:04d}"

    def insert_task(self, t: TaskRecord) -> None:
        now = (t.created_at or datetime.now(timezone.utc)).isoformat()
        import json as _json
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tasks(task_id, workflow, state, sender_role, sender_identifier,
                                  intent, created_at, budget_usd, raw_payload)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (t.task_id, t.workflow, t.state.value, t.sender_role, t.sender_identifier,
                  t.intent, now, t.budget_usd,
                  _json.dumps(t.raw_payload) if t.raw_payload else None))

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        import json as _json
        with self._connect() as conn:
            row = conn.execute(
                "SELECT task_id, workflow, state, sender_role, sender_identifier, intent, "
                "created_at, budget_usd, raw_payload FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return TaskRecord(
            task_id=row[0], workflow=row[1], state=TaskState(row[2]),
            sender_role=row[3], sender_identifier=row[4], intent=row[5],
            created_at=datetime.fromisoformat(row[6]) if row[6] else None,
            budget_usd=row[7],
            raw_payload=_json.loads(row[8]) if row[8] else None,
        )

    def transition(self, task_id: str, from_state: TaskState, to_state: TaskState,
                   actor: str, reason: str, payload: Optional[dict[str, Any]] = None) -> bool:
        """Returns True if a new event row was inserted, False on idempotent re-apply."""
        import json as _json
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO task_events(task_id, from_state, to_state, actor, ts, reason, payload) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (task_id, from_state.value, to_state.value, actor, now, reason,
                     _json.dumps(payload) if payload else None),
                )
                conn.execute("UPDATE tasks SET state = ? WHERE task_id = ?",
                             (to_state.value, task_id))
                return True
            except sqlite3.IntegrityError:
                return False

    def update_task_payload(self, task_id: str, fields: dict[str, Any]) -> None:
        """Merge fields into the task's raw_payload column."""
        task = self.get_task(task_id)
        if not task:
            return
        payload = dict(task.raw_payload or {})
        payload.update(fields)
        import json as _json
        with self._connect() as conn:
            conn.execute("UPDATE tasks SET raw_payload = ? WHERE task_id = ?",
                         (_json.dumps(payload), task_id))

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT from_state, to_state, actor, ts, reason FROM task_events "
                "WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [{"from_state": r[0], "to_state": r[1], "actor": r[2], "ts": r[3], "reason": r[4]}
                for r in rows]
