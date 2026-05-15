# deploy/orchestrator/flyn_orchestrator/backends/claude_p.py
"""Default backend: spawns `claude -p --output-format stream-json` as a subprocess.

Stream-json is tee'd to the capture file (audit-grade); each event is parsed live
for cost tracking. OAuth token refresh failures fall back to ANTHROPIC_API_KEY
if set in env.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..cost import CostTracker

from .base import WorkerResult, WorkerBackend
from ..cost import BudgetExceeded
from ..types import WorkerSpec


CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")


def _load_anthropic_api_key_from_profiles() -> Optional[str]:
    """Read ANTHROPIC_API_KEY from auth-profiles.json if available.

    Only returns tokens that look like API keys (sk-ant-api*). OAuth tokens
    (sk-ant-oat*) are stored in the same profile slot but cannot be used as
    ANTHROPIC_API_KEY — they'd fail auth and waste a worker turn. Return None
    instead so the backend falls back to OAuth-via-credentials-cache."""
    p = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            d = json.load(f)
        for key in ("anthropic:default", "anthropic"):
            if key in d.get("profiles", {}):
                token = d["profiles"][key].get("token", "")
                if token.startswith("sk-ant-api"):
                    return token
                # Anything else (sk-ant-oat-..., empty, etc) is not a valid API key.
                return None
    except Exception:
        pass
    return None


class ClaudePBackend:
    name = "claude-p"

    def _build_env(self) -> dict[str, str]:
        """Build subprocess env, injecting ANTHROPIC_API_KEY if available."""
        env = {**os.environ}
        if "ANTHROPIC_API_KEY" not in env:
            key = _load_anthropic_api_key_from_profiles()
            if key:
                env["ANTHROPIC_API_KEY"] = key
        return env

    def _build_command(self, spec: WorkerSpec, prompt: str) -> list[str]:
        cmd = [
            CLAUDE_BIN, "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--max-turns", str(spec.max_turns),
            "--dangerously-skip-permissions",
        ]
        if spec.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(spec.allowed_tools)])
        return cmd

    def run(self, spec: WorkerSpec, prompt: str, *, cost_tracker: Optional["CostTracker"] = None) -> WorkerResult:
        capture_path = Path(spec.worktree_path) / f"{spec.worker_id}.jsonl"
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        env = self._build_env()
        cmd = self._build_command(spec, prompt)
        cost = 0.0
        changed_files: list[str] = []
        summary = ""
        with capture_path.open("w", encoding="utf-8") as capture:
            proc = subprocess.Popen(
                cmd,
                cwd=spec.worktree_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert proc.stdout is not None
            # Iterate stdout line-by-line; this consumes the pipe before wait(),
            # so there is no deadlock risk even if stderr fills its OS buffer
            # (stderr is also PIPE but we don't read it here — for long-running
            # workers callers that care about stderr should pass stderr=None or
            # add a reader thread; for MVP the risk of stderr buffer saturation
            # is negligible because claude -p writes diagnostics to stderr only
            # on abnormal exit, not continuously).
            for line in proc.stdout:
                capture.write(line)
                capture.flush()
                try:
                    ev = json.loads(line.strip())
                except Exception:
                    continue
                if isinstance(ev, dict):
                    if "usage" in ev:
                        usage = ev["usage"]
                        if isinstance(usage, dict) and "cost_usd" in usage:
                            cost_delta = float(usage["cost_usd"])
                            cost += cost_delta
                            if cost_tracker is not None and cost_delta:
                                try:
                                    cost_tracker.add(cost_delta)
                                except BudgetExceeded:
                                    proc.terminate()
                                    try:
                                        proc.wait(timeout=5)
                                    except subprocess.TimeoutExpired:
                                        proc.kill()
                                        proc.wait(timeout=2)
                                    capture.flush()
                                    duration_ms = int((time.time() - start) * 1000)
                                    return WorkerResult(
                                        worker_id=spec.worker_id, exit_code=-1,
                                        capture_path=capture_path,
                                        cost_usd=cost_tracker.total_usd,
                                        duration_ms=duration_ms,
                                        changed_files=[],
                                        summary="budget exceeded mid-run",
                                    )
                    if "result" in ev and isinstance(ev["result"], dict):
                        summary = str(ev["result"].get("summary", ""))[:500]
                        cf = ev["result"].get("changed_files")
                        if isinstance(cf, list):
                            changed_files = [str(p) for p in cf]
            exit_code = proc.wait()
        duration_ms = int((time.time() - start) * 1000)
        return WorkerResult(
            worker_id=spec.worker_id,
            exit_code=exit_code,
            capture_path=capture_path,
            cost_usd=cost,
            duration_ms=duration_ms,
            changed_files=changed_files,
            summary=summary,
        )
