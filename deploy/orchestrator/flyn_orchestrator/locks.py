"""File-domain locks for parallel builders within a task.

Stores one JSON file per active worker at `<locks_dir>/<worker_id>.json`.
Atomic via O_EXCL on file creation. `acquire` raises LockConflict if any
other live (non-expired) lock claims an overlapping file glob.

Overlap test is conservative: two globs overlap if one is a prefix of the
other OR they share a common directory-prefix with wildcards on both sides.
Implementation uses fnmatch — false positives possible (some non-overlapping
globs flagged), zero false negatives. Safer for Phase 2 MVP.
"""
from __future__ import annotations
import fnmatch
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional


class LockConflict(Exception):
    pass


@dataclass(frozen=True)
class LockRecord:
    task_id: str
    worker_id: str
    claimed_files: list[str]
    started_at: datetime
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _globs_overlap(globs_a: list[str], globs_b: list[str]) -> bool:
    """Conservative overlap test. Returns True if ANY pair of globs (one from
    each list) match each other under fnmatch OR share a common directory prefix
    with wildcard endings."""
    for a in globs_a:
        for b in globs_b:
            if a == b:
                return True
            # If a matches b as a pattern OR b matches a as a pattern, they overlap
            if fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
                return True
            # Or they share a common directory prefix and both end with wildcards
            a_dir, _, a_file = a.rpartition("/")
            b_dir, _, b_file = b.rpartition("/")
            if a_dir and b_dir:
                if a_dir == b_dir:
                    return True  # same dir, different files — conservative: overlap
                if a_dir.startswith(b_dir + "/") or b_dir.startswith(a_dir + "/"):
                    if "*" in a or "*" in b:
                        return True
    return False


class LockManager:
    def __init__(self, locks_dir: Path) -> None:
        self._dir = locks_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, worker_id: str) -> Path:
        # Sanitize worker_id for filesystem
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in worker_id)
        return self._dir / f"{safe}.json"

    def _read_record(self, path: Path) -> Optional[LockRecord]:
        try:
            d = json.loads(path.read_text())
            return LockRecord(
                task_id=d["task_id"],
                worker_id=d["worker_id"],
                claimed_files=d["claimed_files"],
                started_at=datetime.fromisoformat(d["started_at"]),
                expires_at=datetime.fromisoformat(d["expires_at"]),
            )
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return None

    def list_active(self) -> List[LockRecord]:
        now = _now()
        out = []
        for p in self._dir.glob("*.json"):
            rec = self._read_record(p)
            if rec is None:
                continue
            if rec.expires_at > now:
                out.append(rec)
        return out

    def acquire(self, *, task_id: str, worker_id: str, file_globs: list[str],
                ttl_seconds: int = 1800) -> None:
        now = _now()
        # First, prune expired locks so we don't false-conflict
        for p in self._dir.glob("*.json"):
            rec = self._read_record(p)
            if rec and rec.expires_at <= now:
                try:
                    p.unlink()
                except OSError:
                    pass
        # Check overlap with all remaining locks
        for rec in self.list_active():
            if _globs_overlap(file_globs, rec.claimed_files):
                raise LockConflict(
                    f"worker {worker_id!r} cannot claim {file_globs!r}: "
                    f"overlaps with active lock {rec.worker_id!r} (files {rec.claimed_files!r})"
                )
        # Atomic create
        path = self._path_for(worker_id)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise LockConflict(f"worker {worker_id!r} already has a lock file at {path}")
        try:
            payload = {
                "task_id": task_id,
                "worker_id": worker_id,
                "claimed_files": list(file_globs),
                "started_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            }
            os.write(fd, json.dumps(payload, indent=2).encode())
        finally:
            os.close(fd)

    def release(self, worker_id: str) -> None:
        try:
            self._path_for(worker_id).unlink()
        except FileNotFoundError:
            pass

    def prune_expired(self) -> int:
        now = _now()
        removed = 0
        for p in self._dir.glob("*.json"):
            rec = self._read_record(p)
            if rec is None or rec.expires_at <= now:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
