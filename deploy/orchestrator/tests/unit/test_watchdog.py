"""Unit tests for flyn_orchestrator.watchdog.

All 11 specified test cases. No live Ollama calls — uses StubTriageBackend.
"""
from __future__ import annotations

import json
import time
import threading
import unittest
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from flyn_orchestrator.watchdog import (
    OllamaTriageBackend,
    TriageResult,
    Watchdog,
    WatchdogConfig,
    VALID_VERDICTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubTriageBackend:
    """Test double that returns a predetermined sequence of verdicts."""

    name = "stub"

    def __init__(self, verdicts: list[str], reason: str = "test") -> None:
        self._verdicts = list(verdicts)
        self._idx = 0
        self._reason = reason
        self.calls: list[dict] = []

    def classify(
        self,
        capture_tail: str,
        task_intent: str,
        elapsed_seconds: float,
    ) -> TriageResult:
        self.calls.append(
            {"tail": capture_tail, "intent": task_intent, "elapsed": elapsed_seconds}
        )
        if self._idx < len(self._verdicts):
            verdict = self._verdicts[self._idx]
            self._idx += 1
        else:
            verdict = self._verdicts[-1]
        return TriageResult(verdict=verdict, reason=self._reason, confidence=0.9)


def _make_watchdog(
    tmp_path: Path,
    verdicts: list[str],
    *,
    on_nudge=None,
    on_stuck=None,
    on_done=None,
    on_escalate=None,
    interval: float = 0.05,
    threshold: int = 2,
) -> tuple[Watchdog, Path]:
    """Create a Watchdog with StubTriageBackend and a populated capture file."""
    capture = tmp_path / "capture.jsonl"
    capture.write_text("progress line 1\nprogress line 2\n")

    backend = StubTriageBackend(verdicts)
    cfg = WatchdogConfig(
        poll_interval_seconds=interval,
        tail_bytes=4096,
        consecutive_stuck_threshold=threshold,
    )
    wd = Watchdog(
        capture_path=capture,
        task_id="T-test",
        task_intent="implement test feature",
        backend=backend,
        on_nudge=on_nudge,
        on_stuck=on_stuck,
        on_done=on_done,
        on_escalate=on_escalate,
        config=cfg,
    )
    return wd, capture


# ---------------------------------------------------------------------------
# OllamaTriageBackend unit tests
# ---------------------------------------------------------------------------


class TestOllamaBackend:
    def test_ollama_backend_parses_json_response(self):
        """Backend extracts verdict+reason from a well-formed Ollama response."""
        fake_body = json.dumps(
            {"response": json.dumps({"verdict": "STUCK", "reason": "no output"})}
        ).encode()

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = fake_body

        with patch("urllib.request.urlopen", return_value=mock_resp):
            backend = OllamaTriageBackend()
            result = backend.classify("some tail", "task intent", 120.0)

        assert result.verdict == "STUCK"
        assert result.reason == "no output"
        assert result.confidence == 0.7

    def test_ollama_backend_normalizes_unknown_verdict_to_fine(self):
        """Unknown verdict strings are coerced to FINE."""
        fake_body = json.dumps(
            {"response": json.dumps({"verdict": "UNKNOWN_VERDICT", "reason": "weird"})}
        ).encode()

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = fake_body

        with patch("urllib.request.urlopen", return_value=mock_resp):
            backend = OllamaTriageBackend()
            result = backend.classify("tail", "intent", 60.0)

        assert result.verdict == "FINE"

    def test_ollama_backend_falls_back_to_fine_on_error(self):
        """Network errors / timeouts silently default to FINE (never raise)."""
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            backend = OllamaTriageBackend()
            result = backend.classify("some tail", "task intent", 30.0)

        assert result.verdict == "FINE"
        assert result.confidence == 0.0
        assert "OSError" in result.reason

    def test_ollama_backend_falls_back_to_fine_on_json_parse_error(self):
        """Malformed JSON from Ollama also defaults to FINE."""
        fake_body = json.dumps({"response": "not json at all {{{"}).encode()

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = fake_body

        with patch("urllib.request.urlopen", return_value=mock_resp):
            backend = OllamaTriageBackend()
            result = backend.classify("tail", "intent", 60.0)

        assert result.verdict == "FINE"
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Watchdog behavioural tests
# ---------------------------------------------------------------------------


class TestWatchdogBehaviour:
    def test_watchdog_polls_capture_file(self, tmp_path: Path):
        """Watchdog calls backend.classify after one interval passes."""
        backend = StubTriageBackend(["FINE"])
        capture = tmp_path / "cap.jsonl"
        capture.write_text("some output\n")
        cfg = WatchdogConfig(poll_interval_seconds=0.05)

        wd = Watchdog(
            capture_path=capture,
            task_id="T-1",
            task_intent="test",
            backend=backend,
            config=cfg,
        )
        wd.start()
        time.sleep(0.15)
        wd.stop()

        assert len(backend.calls) >= 1
        assert backend.calls[0]["tail"] == "some output\n"

    def test_watchdog_acts_on_fine_verdict(self, tmp_path: Path):
        """FINE verdict → no callbacks fired, consecutive_stuck resets."""
        nudge_calls: list = []
        stuck_calls: list = []

        wd, _ = _make_watchdog(
            tmp_path,
            ["FINE"],
            on_nudge=lambda r: nudge_calls.append(r),
            on_stuck=lambda r: stuck_calls.append(r),
        )
        wd.start()
        time.sleep(0.15)
        wd.stop()

        assert nudge_calls == []
        assert stuck_calls == []
        assert len(wd.verdicts) >= 1
        assert wd.verdicts[0].verdict == "FINE"

    def test_watchdog_acts_on_nudge_verdict(self, tmp_path: Path):
        """NEEDS_NUDGE → on_nudge fires once per verdict."""
        nudge_calls: list = []

        wd, _ = _make_watchdog(
            tmp_path,
            ["NEEDS_NUDGE"],
            on_nudge=lambda r: nudge_calls.append(r),
        )
        wd.start()
        time.sleep(0.15)
        wd.stop()

        assert len(nudge_calls) >= 1
        assert nudge_calls[0].verdict == "NEEDS_NUDGE"

    def test_watchdog_acts_on_stuck_verdict_after_threshold(self, tmp_path: Path):
        """on_stuck fires only after consecutive_stuck_threshold STUCK verdicts, not before."""
        stuck_calls: list = []

        # threshold=2: first STUCK should NOT fire; second should
        wd, _ = _make_watchdog(
            tmp_path,
            ["STUCK", "STUCK", "STUCK"],
            on_stuck=lambda r: stuck_calls.append(r),
            interval=0.05,
            threshold=2,
        )
        wd.start()
        # Wait for first poll (1 STUCK, threshold not yet hit)
        time.sleep(0.08)
        assert stuck_calls == [], "on_stuck should not fire after only 1 STUCK verdict"

        # Wait for second poll (2nd STUCK, threshold hit)
        time.sleep(0.10)
        wd.stop()

        assert len(stuck_calls) >= 1

    def test_watchdog_acts_on_done_verdict(self, tmp_path: Path):
        """DONE verdict → on_done fires; on_stuck NOT fired."""
        done_calls: list = []
        stuck_calls: list = []

        wd, _ = _make_watchdog(
            tmp_path,
            ["DONE"],
            on_done=lambda r: done_calls.append(r),
            on_stuck=lambda r: stuck_calls.append(r),
        )
        wd.start()
        time.sleep(0.15)
        wd.stop()

        assert len(done_calls) >= 1
        assert done_calls[0].verdict == "DONE"
        assert stuck_calls == []

    def test_watchdog_acts_on_escalate_verdict(self, tmp_path: Path):
        """ESCALATE fires on_escalate immediately, bypassing consecutive threshold."""
        escalate_calls: list = []
        stuck_calls: list = []

        # threshold=2 but ESCALATE should NOT wait for 2 polls
        wd, _ = _make_watchdog(
            tmp_path,
            ["ESCALATE"],
            on_escalate=lambda r: escalate_calls.append(r),
            on_stuck=lambda r: stuck_calls.append(r),
            interval=0.05,
            threshold=2,
        )
        wd.start()
        time.sleep(0.12)
        wd.stop()

        assert len(escalate_calls) >= 1
        assert escalate_calls[0].verdict == "ESCALATE"
        # on_stuck should NOT be called for ESCALATE (separate callback)
        assert stuck_calls == []

    def test_watchdog_stop_joins_thread(self, tmp_path: Path):
        """stop() joins the daemon thread; subsequent start/stop are clean."""
        wd, _ = _make_watchdog(tmp_path, ["FINE"])
        wd.start()
        assert wd._thread is not None
        wd.stop()
        assert wd._thread is None

    def test_watchdog_handles_missing_capture_path(self, tmp_path: Path):
        """No exception raised when capture file does not exist yet."""
        backend = StubTriageBackend(["FINE"])
        non_existent = tmp_path / "does_not_exist.jsonl"
        cfg = WatchdogConfig(poll_interval_seconds=0.05)

        wd = Watchdog(
            capture_path=non_existent,
            task_id="T-missing",
            task_intent="test",
            backend=backend,
            config=cfg,
        )
        wd.start()
        time.sleep(0.12)
        wd.stop()

        # No calls should have been made (file doesn't exist)
        assert backend.calls == []

    def test_watchdog_handles_empty_capture_file(self, tmp_path: Path):
        """No classify() call when capture file is empty."""
        backend = StubTriageBackend(["FINE"])
        capture = tmp_path / "empty.jsonl"
        capture.write_bytes(b"")  # 0 bytes
        cfg = WatchdogConfig(poll_interval_seconds=0.05)

        wd = Watchdog(
            capture_path=capture,
            task_id="T-empty",
            task_intent="test",
            backend=backend,
            config=cfg,
        )
        wd.start()
        time.sleep(0.12)
        wd.stop()

        assert backend.calls == []

    def test_stuck_counter_resets_on_fine(self, tmp_path: Path):
        """A FINE verdict between two STUCKs resets the consecutive counter.

        Strategy: threshold=3 so we need 3 consecutive STUCKs to fire on_stuck.
        The stub returns STUCK→FINE→STUCK→FINE→... (alternating). Because FINE
        resets the counter, we should never accumulate 3 consecutive STUCKs.
        """
        stuck_calls: list = []

        # Alternating STUCK/FINE — counter never reaches threshold=3
        wd, _ = _make_watchdog(
            tmp_path,
            ["STUCK", "FINE", "STUCK", "FINE", "STUCK", "FINE"],
            on_stuck=lambda r: stuck_calls.append(r),
            interval=0.05,
            threshold=3,
        )
        wd.start()
        # Wait for ~6 polls
        time.sleep(0.40)
        wd.stop()

        # Counter was reset each time; never reached 3 consecutive
        assert stuck_calls == [], (
            "on_stuck should not fire when FINE repeatedly resets the consecutive counter"
        )

    def test_watchdog_verdicts_list_captures_all(self, tmp_path: Path):
        """All verdicts are appended to wd.verdicts regardless of action taken."""
        wd, _ = _make_watchdog(
            tmp_path,
            ["FINE", "STUCK", "DONE"],
            interval=0.05,
            threshold=10,  # prevent on_stuck from firing
        )
        wd.start()
        time.sleep(0.30)
        wd.stop()

        verdicts = [r.verdict for r in wd.verdicts]
        # At minimum we should have all 3 in order
        assert "FINE" in verdicts
        assert "STUCK" in verdicts
        assert "DONE" in verdicts
