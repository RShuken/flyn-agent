"""SummarizerWorker behavior — Ollama call, job lifecycle, dead-letter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _stub_ollama_success(monkeypatch, summary: str = "Test summary"):
    from flyn_memory_router.conv import summarizer

    def fake_call_ollama(self, body: str, sender_id: str) -> str:
        return summary

    monkeypatch.setattr(summarizer.SummarizerWorker, "_call_ollama", fake_call_ollama)


def _make_job_file(queue_dir: Path, db_path: Path, row_id: int = 1) -> Path:
    from flyn_memory_router.conv.summarizer import SummarizeJob, enqueue
    job = SummarizeJob(
        owner_id="ryan",
        db_path=str(db_path),
        row_id=row_id,
        body="hello world",
        sender_id="7191564227",
    )
    return enqueue(queue_dir, job)


def _make_db_with_row(tmp_path: Path, row_id_target: int = 1):
    """Create a real ConvDb with one row so update_summary has something to update."""
    from flyn_memory_router.conv.schema import ConvDb, ConvMessage
    db_path = tmp_path / "ryan.db"
    db = ConvDb(owner_id="ryan", path=db_path)
    db.write(ConvMessage(
        channel="telegram", sender_id="7191564227", thread_id="t1",
        reply_to_id=None, ts="2026-05-19T18:00:00+00:00",
        body="hello world", attachments=[], encrypted_raw=b"\x00" * 32,
    ))
    return db_path


def test_summarizer_happy_path_unlinks_job(tmp_path: Path, monkeypatch):
    """Successful summary write → job file is deleted."""
    from flyn_memory_router.conv.summarizer import SummarizerWorker

    _stub_ollama_success(monkeypatch, summary="A clear summary")
    db_path = _make_db_with_row(tmp_path)
    job_path = _make_job_file(tmp_path / "q", db_path, row_id=1)

    w = SummarizerWorker(queue_dir=tmp_path / "q")
    w._dir.mkdir(parents=True, exist_ok=True)
    processed = w._tick()
    assert processed is True
    assert not job_path.exists(), "happy path should unlink job file"


def test_summarizer_persistent_db_error_moves_to_dead_letter(tmp_path: Path, monkeypatch):
    """When update_summary fails persistently, after N attempts the job is
    moved to dead-letter/ and the original file is unlinked. This prevents
    a tight 1s spin calling Ollama + DB forever."""
    from flyn_memory_router.conv import summarizer
    from flyn_memory_router.conv.summarizer import SummarizerWorker

    _stub_ollama_success(monkeypatch, summary="ignored")

    # Force every update_summary call to raise
    def bad_update(self, row_id, summary):
        raise RuntimeError("simulated persistent DB error")

    monkeypatch.setattr("flyn_memory_router.conv.schema.ConvDb.update_summary", bad_update)

    # Use a path that doesn't even need a real DB since update_summary is patched
    job_path = _make_job_file(tmp_path / "q", tmp_path / "fake.db", row_id=99)

    w = SummarizerWorker(queue_dir=tmp_path / "q")
    w._dir.mkdir(parents=True, exist_ok=True)

    # Tick MAX_RETRIES + 1 times. After the (MAX_RETRIES+1)th failure the job
    # should be in dead-letter and gone from the active queue.
    for _ in range(summarizer.MAX_SUMMARY_RETRIES + 2):
        w._tick()

    assert not job_path.exists(), "exhausted job should leave the queue"
    dead_letter_dir = (tmp_path / "q" / "conv-summarize" / "dead-letter")
    dead_files = list(dead_letter_dir.glob("*.json")) if dead_letter_dir.exists() else []
    assert len(dead_files) == 1, "exhausted job should move to dead-letter dir"

    # The dead-letter file records the attempt count for diagnosis
    payload = json.loads(dead_files[0].read_text())
    assert payload.get("_attempts", 0) >= summarizer.MAX_SUMMARY_RETRIES


def test_summarizer_retries_before_dead_letter(tmp_path: Path, monkeypatch):
    """A failing job stays in place (with attempt counter) until N exhausted."""
    from flyn_memory_router.conv import summarizer
    from flyn_memory_router.conv.summarizer import SummarizerWorker

    _stub_ollama_success(monkeypatch, summary="ignored")
    monkeypatch.setattr(
        "flyn_memory_router.conv.schema.ConvDb.update_summary",
        lambda self, row_id, summary: (_ for _ in ()).throw(RuntimeError("nope")),
    )

    job_path = _make_job_file(tmp_path / "q", tmp_path / "fake.db", row_id=42)

    w = SummarizerWorker(queue_dir=tmp_path / "q")
    w._dir.mkdir(parents=True, exist_ok=True)

    # First failure: job stays, counter at 1
    w._tick()
    assert job_path.exists()
    payload = json.loads(job_path.read_text())
    assert payload.get("_attempts") == 1

    # Second failure: counter at 2
    w._tick()
    payload = json.loads(job_path.read_text())
    assert payload.get("_attempts") == 2
