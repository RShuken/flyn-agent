"""Watchdog — stuck-worker triage via cheap-LLM classification.

Spawned as a daemon thread by the dispatcher. Polls the worker's capture
file every poll_interval seconds, takes the last tail_bytes of output,
asks the triage LLM "is this worker FINE / NEEDS_NUDGE / STUCK / DONE / ESCALATE",
and acts on the verdict:

  FINE        → no-op
  NEEDS_NUDGE → emit a memory event; if a channel is wired, notify Ryan
  STUCK       → call on_stuck() (typically: SIGTERM the subprocess)
  DONE        → emit memory event; do NOT kill (worker may be cleaning up)
  ESCALATE    → call on_stuck() AND notify Ryan with capture-tail excerpt

Per spec criterion 1.8. Sanitized pattern lifted from
johba37/claude-code-supervisor (KNOWLEDGE/02 background routing).
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Triage backend Protocol
# ---------------------------------------------------------------------------

VALID_VERDICTS = frozenset({"FINE", "NEEDS_NUDGE", "STUCK", "DONE", "ESCALATE"})


@dataclass(frozen=True)
class TriageResult:
    verdict: str   # "FINE" | "NEEDS_NUDGE" | "STUCK" | "DONE" | "ESCALATE"
    reason: str
    confidence: float = 0.5


class TriageBackend(Protocol):
    name: str

    def classify(
        self,
        capture_tail: str,
        task_intent: str,
        elapsed_seconds: float,
    ) -> TriageResult: ...


# ---------------------------------------------------------------------------
# Default backend: Ollama
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_TRIAGE_MODEL = "gemma4:e4b"

TRIAGE_PROMPT_TEMPLATE = """You are a watchdog triaging a long-running worker subprocess.

The worker is executing this task: {intent}
Elapsed wall-clock: {elapsed:.0f}s
Recent capture-stream tail (last {tail_len} chars):
---
{tail}
---

Classify the worker's state. Return a JSON object with these exact fields:
  "verdict": one of FINE | NEEDS_NUDGE | STUCK | DONE | ESCALATE
  "reason": short string

Rules:
- FINE: worker is making progress (recent messages, tool calls, code edits)
- NEEDS_NUDGE: worker is slow/circling but still alive; suggest follow-up
- STUCK: worker has emitted nothing useful recently; no progress
- DONE: worker has clearly finished (final summary / success message)
- ESCALATE: catastrophic state — repeated auth failures, fatal exceptions, infinite loop

Output ONLY the JSON object, no prose."""


class OllamaTriageBackend:
    """Calls local Ollama with gemma4:e4b (or configured model) for triage."""

    name = "ollama"

    def __init__(
        self,
        url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_TRIAGE_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self._url = url
        self._model = model
        self._timeout = timeout

    def classify(
        self,
        capture_tail: str,
        task_intent: str,
        elapsed_seconds: float,
    ) -> TriageResult:
        prompt = TRIAGE_PROMPT_TEMPLATE.format(
            intent=task_intent[:300],
            elapsed=elapsed_seconds,
            tail_len=len(capture_tail),
            tail=capture_tail[-4000:],
        )
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._url,
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read())
            response_text = body.get("response", "").strip()
            parsed = json.loads(response_text)
            verdict = (parsed.get("verdict") or "FINE").upper()
            if verdict not in VALID_VERDICTS:
                verdict = "FINE"
            return TriageResult(
                verdict=verdict,
                reason=parsed.get("reason", ""),
                confidence=0.7,
            )
        except (socket.timeout, TimeoutError) as exc:
            # Transient: ollama was slow but is alive. Demote to debug so we don't
            # spam warn-level logs on every triage. The verdict still defaults to FINE
            # which is the safe choice (don't halt a worker on triage flakiness).
            logger.debug(
                "watchdog: ollama triage timed out after %.1fs; defaulting to FINE",
                self._timeout,
            )
            return TriageResult(
                verdict="FINE",
                reason="triage backend timeout",
                confidence=0.0,
            )
        except Exception as exc:
            # Real backend error (e.g. ollama not running, malformed response).
            # Keep at warn so it surfaces in logs and is debuggable.
            logger.warning(
                "watchdog: ollama triage failed (%s); defaulting to FINE",
                exc,
            )
            return TriageResult(
                verdict="FINE",
                reason=f"triage backend error: {type(exc).__name__}",
                confidence=0.0,
            )


# ---------------------------------------------------------------------------
# Watchdog configuration
# ---------------------------------------------------------------------------


@dataclass
class WatchdogConfig:
    poll_interval_seconds: float = 30.0
    tail_bytes: int = 4096
    # Number of consecutive STUCK verdicts required before on_stuck() fires.
    # ESCALATE bypasses this threshold entirely (fires immediately).
    consecutive_stuck_threshold: int = 2


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


class Watchdog:
    """Polling-based watchdog.

    Spawn as a daemon thread via start(); call stop() when the worker has
    exited (or been killed). The thread is daemon=True so it won't prevent
    process exit if the caller forgets to stop().

    Callbacks (all optional — default: no-op):
      on_nudge(result)    — fired once per NEEDS_NUDGE verdict
      on_stuck(result)    — fired when consecutive STUCK >= threshold
      on_done(result)     — fired once per DONE verdict (do NOT kill worker)
      on_escalate(result) — fired immediately on first ESCALATE verdict

    All verdicts are appended to self.verdicts (list[TriageResult]) for
    tests and post-mortem inspection.
    """

    def __init__(
        self,
        capture_path: Path,
        task_id: str,
        task_intent: str,
        backend: TriageBackend,
        on_nudge: Optional[Callable[[TriageResult], None]] = None,
        on_stuck: Optional[Callable[[TriageResult], None]] = None,
        on_done: Optional[Callable[[TriageResult], None]] = None,
        on_escalate: Optional[Callable[[TriageResult], None]] = None,
        config: Optional[WatchdogConfig] = None,
    ) -> None:
        self._capture_path = capture_path
        self._task_id = task_id
        self._task_intent = task_intent
        self._backend = backend
        self._on_nudge = on_nudge or _noop
        self._on_stuck = on_stuck or _noop
        self._on_done = on_done or _noop
        self._on_escalate = on_escalate or _noop
        self._config = config or WatchdogConfig()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_time: float = 0.0
        self._consecutive_stuck: int = 0
        # Public for tests/audit:
        self.verdicts: list[TriageResult] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog daemon thread. No-op if already started."""
        if self._thread is not None:
            return
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"watchdog-{self._task_id}",
        )
        self._thread.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        """Signal the watchdog to stop and wait up to join_timeout seconds."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        # First poll fires after one full interval — workers always look
        # STUCK at t=0 because the capture file is usually empty on start.
        while not self._stop_event.wait(self._config.poll_interval_seconds):
            try:
                self._poll_once()
            except Exception:
                logger.exception(
                    "watchdog: poll iteration failed for task %s; continuing",
                    self._task_id,
                )

    def _poll_once(self) -> None:
        if not self._capture_path.exists():
            return

        try:
            with self._capture_path.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - self._config.tail_bytes))
                tail = fh.read().decode("utf-8", errors="replace")
        except Exception:
            logger.debug(
                "watchdog: could not read capture file %s", self._capture_path
            )
            return

        if not tail:
            return

        elapsed = time.time() - self._start_time
        result = self._backend.classify(tail, self._task_intent, elapsed)
        self.verdicts.append(result)

        if result.verdict == "FINE":
            self._consecutive_stuck = 0

        elif result.verdict == "DONE":
            self._consecutive_stuck = 0
            self._on_done(result)

        elif result.verdict == "NEEDS_NUDGE":
            self._consecutive_stuck = 0
            self._on_nudge(result)

        elif result.verdict == "ESCALATE":
            # Bypass consecutive threshold — immediate action.
            self._consecutive_stuck = self._config.consecutive_stuck_threshold
            self._on_escalate(result)

        elif result.verdict == "STUCK":
            self._consecutive_stuck += 1
            if self._consecutive_stuck >= self._config.consecutive_stuck_threshold:
                self._on_stuck(result)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _noop(result: TriageResult) -> None:  # noqa: ANN001
    """Default no-op callback."""
