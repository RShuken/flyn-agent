"""Thin wrapper around the `gh` CLI for PR operations.

Three operations: create, comment, merge. All swallow stdout/stderr to
strings rather than streaming — PR operations are short and atomic. Errors
raise PRError with the stderr content.
"""
from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path
from typing import Literal


class PRError(Exception):
    pass


GH_BIN = os.environ.get("GH_BIN", "gh")


def _run(args: list[str], cwd: Path) -> tuple[str, str, int]:
    res = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)
    return res.stdout, res.stderr, res.returncode


def create_pr(*, repo_path: Path, title: str, body: str, base: str, head: str) -> str:
    """Create a PR. Returns the PR URL on success."""
    stdout, stderr, rc = _run(
        [GH_BIN, "pr", "create", "--title", title, "--body", body, "--base", base, "--head", head],
        cwd=repo_path,
    )
    if rc != 0:
        raise PRError(f"gh pr create failed: {stderr.strip()}")
    # gh emits the PR URL as the last line of stdout
    url = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if not url.startswith("http"):
        raise PRError(f"gh pr create returned unexpected output: {stdout!r}")
    return url


def comment_pr(*, repo_path: Path, pr_number: int, body: str) -> None:
    stdout, stderr, rc = _run(
        [GH_BIN, "pr", "comment", str(pr_number), "--body", body],
        cwd=repo_path,
    )
    if rc != 0:
        raise PRError(f"gh pr comment {pr_number} failed: {stderr.strip()}")


def merge_pr(*, repo_path: Path, pr_number: int, method: Literal["merge", "squash", "rebase"] = "merge") -> bool:
    method_flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}[method]
    stdout, stderr, rc = _run(
        [GH_BIN, "pr", "merge", str(pr_number), method_flag, "--delete-branch=false"],
        cwd=repo_path,
    )
    return rc == 0


def pr_number_from_url(url: str) -> int:
    m = re.search(r"/pull/(\d+)", url)
    if not m:
        raise PRError(f"could not parse PR number from {url!r}")
    return int(m.group(1))
