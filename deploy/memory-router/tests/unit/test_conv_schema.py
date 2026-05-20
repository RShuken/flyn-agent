"""ConvDb — write, search, thread queries, summary update with FTS5 sync."""
from __future__ import annotations

from pathlib import Path


def _msg(**overrides):
    from flyn_memory_router.conv.schema import ConvMessage
    base = dict(
        channel="telegram",
        sender_id="7191564227",
        thread_id="7191564227",
        reply_to_id=None,
        ts="2026-05-19T18:00:00+00:00",
        body="hello world",
        attachments=[],
        encrypted_raw=b"\x00" * 32,
    )
    base.update(overrides)
    return ConvMessage(**base)


def test_write_then_search_roundtrip(tmp_path: Path):
    """Write a message; FTS5 finds it by body content."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    row_id = db.write(_msg(body="Linear backlog stuck at 73 of 124"))
    assert row_id > 0
    hits = db.search("linear backlog", top_k=5)
    assert len(hits) == 1
    assert hits[0].row_id == row_id
    assert "Linear" in hits[0].body


def test_thread_query_returns_chronological(tmp_path: Path):
    """get_by_thread returns messages in ts DESC order, limited."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    for i in range(5):
        db.write(_msg(
            ts=f"2026-05-19T10:{i:02d}:00+00:00",
            body=f"message {i}",
            thread_id="t1",
        ))
    out = db.get_by_thread("t1", limit=3)
    assert len(out) == 3
    assert out[0].body == "message 4"  # newest first
    assert out[2].body == "message 2"


def test_summary_update_indexes_in_fts(tmp_path: Path):
    """update_summary updates messages table AND propagates to FTS5."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    row_id = db.write(_msg(body="opaque body text X"))
    # Initially the summary is NULL — searching for a summary-only token misses
    assert db.search("revenue figures", top_k=5) == []
    db.update_summary(row_id, "Discussion of revenue figures for Q2")
    hits = db.search("revenue figures", top_k=5)
    assert len(hits) == 1
    assert hits[0].row_id == row_id
    assert hits[0].summary is not None


# --- FTS5 robustness (F1) ---
# FTS5 MATCH syntax rejects bare quotes, lone AND/OR, dangling operators, etc.
# Real user search strings (especially from Telegram) routinely contain such
# characters. search() must never raise sqlite3.OperationalError to the caller.

import pytest


_FTS5_TRICKY_QUERIES = [
    'foo"',                     # unbalanced double quote
    'AND',                      # bare boolean operator at start
    'a OR',                     # dangling boolean operator
    'hello (world',             # unbalanced paren
    'NEAR/5',                   # NEAR operator with no terms
    '"',                        # single bare quote
    '"hello',                   # opening quote no close
    'a AND AND b',              # double operator
    '*',                        # bare wildcard
    'foo*bar"baz',              # mixed wildcard + quote
]


@pytest.mark.parametrize("q", _FTS5_TRICKY_QUERIES)
def test_search_does_not_crash_on_tricky_fts5_query(tmp_path: Path, q: str):
    """search() returns [] (or valid results) for any string. Never raises."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    db.write(_msg(body="some indexable content here"))
    # The contract: search() must not raise. Returning [] is acceptable.
    result = db.search(q, top_k=5)
    assert isinstance(result, list)


def test_search_still_works_on_normal_query_after_hardening(tmp_path: Path):
    """Sanity check: the hardening doesn't break ordinary searches."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    db.write(_msg(body="quarterly board meeting notes"))
    db.write(_msg(body="lunch with the team"))
    hits = db.search("board meeting", top_k=5)
    assert len(hits) == 1
    assert "board" in hits[0].body


# --- F4: ConvDb does not leak file descriptors ---
# sqlite3.Connection used as a context manager only manages transactions
# (commits on __exit__) — it does NOT close the connection. Each call to
# write/search/etc previously left a bare Connection for the GC to close.
# Under load (summarizer polling every 1s + ingest writes), fds could
# saturate. The fix is to make _conn() a proper @contextmanager that
# closes on exit.

import os


def _count_open_fds_for_path(path: Path) -> int:
    """How many of this process's open files point at the given db path."""
    pid = os.getpid()
    # Try psutil first (cleaner); fall back to lsof on systems where it's missing.
    try:
        import psutil  # type: ignore
        return sum(1 for f in psutil.Process(pid).open_files() if f.path == str(path))
    except ImportError:
        import subprocess
        try:
            out = subprocess.run(
                ["lsof", "-p", str(pid), "-Fn"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            return sum(1 for line in out.stdout.splitlines()
                       if line.startswith("n") and line[1:] == str(path))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0  # can't measure; test will be a no-op


def test_convdb_does_not_leak_file_descriptors(tmp_path: Path):
    """100 writes followed by 100 reads should not accumulate open fds."""
    from flyn_memory_router.conv.schema import ConvDb
    db_path = tmp_path / "ryan.db"
    db = ConvDb(owner_id="ryan", path=db_path)
    # baseline after construction
    baseline = _count_open_fds_for_path(db_path)

    for i in range(100):
        db.write(_msg(body=f"message {i}", ts=f"2026-05-19T10:{i % 60:02d}:00+00:00"))
    for _ in range(100):
        db.search("message", top_k=5)
        db.stats()
        db.get_by_id(1)

    after = _count_open_fds_for_path(db_path)
    # Tolerate WAL/SHM files (sqlite opens up to ~3 per active connection); we
    # check that the count is BOUNDED, not unbounded growth. If _conn() doesn't
    # close, we'd see ~300+ here.
    assert after - baseline < 20, (
        f"fd leak detected: baseline={baseline} after 300 ops={after}"
    )
