"""Alternate backend: spawns `codex exec --json` as a subprocess.

Mirror of ClaudePBackend with the codex CLI shape. OpenAI subscription
OAuth (ChatGPT Plus/Pro) is the default auth; OPENAI_API_KEY env or
auth-profiles fallback covers the cases where OAuth refresh fails or
the operator wants per-token billing.

Reference: https://developers.openai.com/codex/noninteractive
"""
from __future__ import annotations
import json as _json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .base import WorkerResult, WorkerBackend
from ..types import WorkerSpec


CODEX_BIN = os.environ.get("CODEX_BIN", "codex")


def _load_openai_api_key_from_profiles() -> Optional[str]:
    """Read OPENAI_API_KEY from auth-profiles.json if available."""
    p = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if not p.exists():
        return None
    try:
        d = _json.load(open(p))
        for key in ("openai:default", "openai"):
            if key in d.get("profiles", {}):
                return d["profiles"][key].get("token")
    except Exception:
        pass
    return None


class CodexExecBackend:
    name = "codex-exec"

    def _build_env(self) -> dict[str, str]:
        """Build subprocess env, injecting OPENAI_API_KEY if available."""
        env = {**os.environ}
        if "OPENAI_API_KEY" not in env:
            key = _load_openai_api_key_from_profiles()
            if key:
                env["OPENAI_API_KEY"] = key
        return env

    def _build_command(self, spec: WorkerSpec, prompt: str) -> list[str]:
        return [
            CODEX_BIN, "exec",
            "--json",
            "--sandbox", "workspace-write",
            prompt,
        ]

    def run(self, spec: WorkerSpec, prompt: str) -> WorkerResult:
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
                cmd, cwd=spec.worktree_path, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                capture.write(line)
                capture.flush()
                try:
                    ev = _json.loads(line.strip())
                except Exception:
                    continue
                if isinstance(ev, dict):
                    # Defensive: codex may emit cost in usage.cost_usd, top-level cost_usd, or neither
                    usage = ev.get("usage")
                    if isinstance(usage, dict) and "cost_usd" in usage:
                        cost += float(usage["cost_usd"])
                    elif "cost_usd" in ev:
                        try:
                            cost += float(ev["cost_usd"])
                        except (TypeError, ValueError):
                            pass
                    if "summary" in ev and isinstance(ev["summary"], str):
                        summary = ev["summary"][:500]
                    if "changed_files" in ev and isinstance(ev["changed_files"], list):
                        changed_files = [str(p) for p in ev["changed_files"]]
            exit_code = proc.wait()
        duration_ms = int((time.time() - start) * 1000)
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=exit_code,
            capture_path=capture_path, cost_usd=cost, duration_ms=duration_ms,
            changed_files=changed_files, summary=summary,
        )
