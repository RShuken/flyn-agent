"""ocw_mem read: runs `openclaw memory search --json` via subprocess.run.

Uses asyncio.to_thread to keep the adapter async-friendly without
calling raw process-spawn APIs. subprocess.run with a list argv is
shell-safe (no shell=True).
"""
from __future__ import annotations

import asyncio
import json
import subprocess

from ..types import Hit


class OCWMemRead:
    """Read OpenClaw's built-in memory search via `openclaw memory search --json`.

    Expected stdout shape (pinned for forward-compat detection — see
    tests/smoke/test_ocw_mem_live.py):

        {
          "results": [
            {
              "text":  str,   # snippet text (required, non-empty)
              "score": float, # similarity 0..1 (optional, defaults to 0.5)
              "file":  str,   # absolute path of source file (optional)
              "line":  int    # 1-based line number (optional)
            },
            ...
          ]
        }

    If `openclaw memory search` ever changes this contract, the live smoke
    test will fail loudly and this adapter will need updating.
    """
    name = "ocw_mem"
    read_timeout = 3.0
    default_included = False

    def __init__(self, binary: str = "openclaw") -> None:
        self._bin = binary

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        argv = [self._bin, "memory", "search",
                "--query", q, "--limit", str(top_k), "--json"]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                argv,
                capture_output=True,
                text=True,
                timeout=self.read_timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        try:
            data = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return []

        hits: list[Hit] = []
        for rec in data.get("results", []):
            text = rec.get("text") or ""
            if not text:
                continue
            hits.append(Hit(
                text=text,
                source="ocw_mem",
                score=float(rec.get("score", 0.5)),
                metadata={
                    "file": rec.get("file"),
                    "line": rec.get("line"),
                },
            ))
        return hits
