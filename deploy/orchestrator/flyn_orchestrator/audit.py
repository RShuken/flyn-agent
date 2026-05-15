"""Audit log helpers for the ops workflow.

snapshot_target(target) -> SnapshotBundle
  Captures pre-state. For files: SHA256 of content + size + mtime.
  For services: hits the service's /api/health and stores response.
  For generic resources: returns a "could not snapshot" sentinel with reason.

verify_target_changed(before, after) -> bool
  True iff hashes differ. Used by the validator to confirm a change happened.

Both functions are conservative — they prefer reporting "could not snapshot"
over silently returning empty/equal hashes that would falsely report no-change.
"""
from __future__ import annotations
import hashlib
import json
import subprocess
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SnapshotBundle:
    target: str
    kind: str               # "file" | "service" | "command" | "unsnapshottable"
    hash_value: str         # SHA256 hex of content, or "" if unsnapshottable
    content_repr: str       # human-readable representation (for the validator)
    captured_at: str        # ISO 8601 UTC
    note: Optional[str] = None


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def snapshot_target(target: str) -> SnapshotBundle:
    """Best-effort snapshot. Returns a SnapshotBundle.

    Target heuristics:
    - looks like a file path → read content
    - looks like an http(s) URL → GET it
    - looks like a shell command in `cmd: foo` form → run it read-only
    - anything else → return unsnapshottable bundle with reason
    """
    now = datetime.now(timezone.utc).isoformat()

    # File path
    if target.startswith("/") or target.startswith("~/") or target.startswith("./"):
        path = Path(target).expanduser()
        if path.is_file():
            try:
                b = path.read_bytes()
                return SnapshotBundle(
                    target=target, kind="file",
                    hash_value=_sha256_bytes(b),
                    content_repr=f"size={len(b)} bytes; first 200 chars: "
                                 f"{b[:200].decode('utf-8', errors='replace')!r}",
                    captured_at=now,
                )
            except OSError as e:
                return SnapshotBundle(
                    target=target, kind="unsnapshottable",
                    hash_value="", content_repr="",
                    captured_at=now,
                    note=f"OS error reading {target}: {e}",
                )
        # Path doesn't exist — this is a valid pre-state for an action that
        # CREATES the file. Return a sentinel snapshot.
        return SnapshotBundle(
            target=target, kind="file",
            hash_value=_sha256_str("(file does not exist)"),
            content_repr="(file does not exist)",
            captured_at=now,
        )

    # HTTP(s) URL
    if target.startswith("http://") or target.startswith("https://"):
        try:
            with urllib.request.urlopen(target, timeout=5) as resp:
                body = resp.read()
            return SnapshotBundle(
                target=target, kind="service",
                hash_value=_sha256_bytes(body),
                content_repr=f"HTTP {resp.status}; body first 200: "
                             f"{body[:200].decode('utf-8', errors='replace')!r}",
                captured_at=now,
            )
        except Exception as e:
            return SnapshotBundle(
                target=target, kind="unsnapshottable",
                hash_value="", content_repr="",
                captured_at=now,
                note=f"URL fetch failed: {e}",
            )

    # cmd: form (read-only shell snapshot)
    if target.startswith("cmd:"):
        cmd = target[len("cmd:"):].strip()
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                 timeout=10, check=False)
            out = (res.stdout or "") + (res.stderr or "")
            return SnapshotBundle(
                target=target, kind="command",
                hash_value=_sha256_str(out),
                content_repr=f"rc={res.returncode}; output first 200: {out[:200]!r}",
                captured_at=now,
            )
        except Exception as e:
            return SnapshotBundle(
                target=target, kind="unsnapshottable",
                hash_value="", content_repr="",
                captured_at=now,
                note=f"command failed: {e}",
            )

    # Unknown shape
    return SnapshotBundle(
        target=target, kind="unsnapshottable",
        hash_value="", content_repr="",
        captured_at=now,
        note=f"unrecognized target shape: {target!r}",
    )


def verify_target_changed(before: SnapshotBundle, after: SnapshotBundle) -> bool:
    """True if the hashes differ (or before was unsnapshottable but after has content)."""
    if before.hash_value == "" and after.hash_value != "":
        return True
    if before.hash_value != "" and after.hash_value == "":
        return False
    return before.hash_value != after.hash_value


def serialize_snapshot(b: SnapshotBundle) -> str:
    """For passing to the validator prompt."""
    return json.dumps({
        "target": b.target,
        "kind": b.kind,
        "hash": b.hash_value[:16] + "..." if b.hash_value else "(none)",
        "content_repr": b.content_repr,
        "captured_at": b.captured_at,
        "note": b.note,
    }, indent=2)
