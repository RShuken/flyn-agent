"""Hot-tier adapter: appends/maintains pins in MEMORY.md with TTL-based decay.

Decay rules (spec §2.5):
    - active task: pin survives 72h from last update
    - completed/failed/cancelled task: pin survives 24h from terminal state
    - permanent (Owner-only): never decays unless explicitly unpinned

The pin store is SQLite-backed (sibling to dedup) to survive supervisor restarts.
The MEMORY.md file is rewritten from the store on every change — never edited in place.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from ..types import InboundEvent
from .base import WriteResult


_SCHEMA = """
CREATE TABLE IF NOT EXISTS hot_pins (
    subject TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    pinned_at TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    permanent INTEGER NOT NULL DEFAULT 0,
    task_state TEXT NOT NULL DEFAULT 'active'
);
"""

_HOT_HEADER = "## Active pins"


@dataclass
class PinRecord:
    subject: str
    body: str
    pinned_at: datetime
    permanent: bool
    task_state: str          # 'active' | 'completed' | 'failed' | 'cancelled'


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _PinStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, p: PinRecord) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO hot_pins(subject, body, pinned_at, last_updated, permanent, task_state)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject) DO UPDATE SET
                    body=excluded.body,
                    last_updated=excluded.last_updated,
                    permanent=excluded.permanent,
                    task_state=excluded.task_state
            """, (p.subject, p.body, p.pinned_at.isoformat(), _now().isoformat(),
                  1 if p.permanent else 0, p.task_state))

    def delete(self, subject: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM hot_pins WHERE subject = ?", (subject,))
            return cur.rowcount > 0

    def list_all(self) -> list[PinRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT subject, body, pinned_at, permanent, task_state FROM hot_pins ORDER BY pinned_at"
            ).fetchall()
        return [PinRecord(subject=r[0], body=r[1],
                          pinned_at=datetime.fromisoformat(r[2]),
                          permanent=bool(r[3]), task_state=r[4]) for r in rows]


class HotMemoryMdAdapter:
    name = "hot.memory_md"

    def __init__(self, memory_md: Path,
                 store_path: Path | None = None,
                 now: Callable[[], datetime] = _now,
                 completed_ttl: timedelta = timedelta(hours=24),
                 active_ttl: timedelta = timedelta(hours=72)) -> None:
        self._md = memory_md
        self._store = _PinStore(store_path or (memory_md.parent / ".hot_pins.db"))
        self._now = now
        self._completed_ttl = completed_ttl
        self._active_ttl = active_ttl

    def write(self, event: InboundEvent) -> WriteResult:
        # Infer task_state from raw_payload if provided.
        task_state = "active"
        if event.raw_payload and "task_state" in event.raw_payload:
            task_state = str(event.raw_payload["task_state"])
        permanent = bool(event.raw_payload and event.raw_payload.get("permanent"))
        self._store.upsert(PinRecord(
            subject=event.subject, body=event.body, pinned_at=self._now(),
            permanent=permanent, task_state=task_state,
        ))
        self._render()
        return WriteResult(target=self.name, ok=True, detail=f"pinned {event.subject}")

    def pin_permanent(self, subject: str, body: str) -> None:
        self._store.upsert(PinRecord(
            subject=subject, body=body, pinned_at=self._now(),
            permanent=True, task_state="active",
        ))
        self._render()

    def unpin(self, subject: str) -> bool:
        ok = self._store.delete(subject)
        if ok:
            self._render()
        return ok

    def decay(self) -> int:
        """Remove pins past their TTL. Returns count removed.

        Always re-renders MEMORY.md so that pins inserted directly via
        _store.upsert() (e.g. permanent pins) are reflected even when
        nothing was removed.
        """
        now = self._now()
        removed = 0
        for p in self._store.list_all():
            if p.permanent:
                continue
            ttl = self._completed_ttl if p.task_state != "active" else self._active_ttl
            if now - p.pinned_at > ttl:
                self._store.delete(p.subject)
                removed += 1
        self._render()
        return removed

    def _render(self) -> None:
        text = self._md.read_text() if self._md.exists() else "# MEMORY\n\n"
        # locate (or append) the "Active pins" section, replace its body
        lines = text.splitlines()
        try:
            header_idx = next(i for i, ln in enumerate(lines) if ln.strip() == _HOT_HEADER)
        except StopIteration:
            # append section at end
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([_HOT_HEADER, ""])
            header_idx = len(lines) - 2
        # find next top-level (## ...) heading after header_idx
        end_idx = len(lines)
        for i in range(header_idx + 1, len(lines)):
            if lines[i].startswith("## "):
                end_idx = i
                break
        # build new section body
        body_lines: list[str] = [""]
        for p in self._store.list_all():
            marker = " *(permanent)*" if p.permanent else ""
            body_lines.append(f"- **{p.subject}**{marker}: {p.body}")
        body_lines.append("")
        new_text = "\n".join(lines[: header_idx + 1] + body_lines + lines[end_idx:]) + "\n"
        self._md.write_text(new_text)
