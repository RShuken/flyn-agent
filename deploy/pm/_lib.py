"""Shared helpers for Flyn's project-PM scripts.

Loads per-project config from ~/.openclaw/projects/<slug>/config.yaml, provides
a small Graphiti REST client and a Telegram sender that uses `openclaw channels send`.

Design notes:
- No external dependencies beyond stdlib + yaml (already installed for openclaw).
- Graphiti calls use the same curl-equivalent pattern documented in workspace/AGENTS.md
  (POST /api/episode is async-slow; GET endpoints are fast).
- Errors propagate. Callers catch and log; this module does not silently swallow.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml  # type: ignore[import]


PROJECTS_ROOT = Path.home() / ".openclaw" / "projects"
GRAPHITI_BASE = os.environ.get("FLYN_GRAPHITI_BASE", "http://localhost:8100")


@dataclass(frozen=True)
class Stakeholder:
    name: str
    role: str
    side: str  # "us" | "client"
    primary_channel: str
    email: str | None = None
    chat_id: str | None = None
    approval_gate: bool = False
    timeline_constraint: dict[str, Any] | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ProjectConfig:
    slug: str
    raw: dict[str, Any]

    @property
    def display_name(self) -> str:
        return self.raw.get("display_name", self.slug)

    @property
    def repo_path(self) -> Path:
        return Path(self.raw["repo"]["path"]).expanduser()

    @property
    def registry_path(self) -> Path:
        return self.repo_path / self.raw["source_of_truth"]["registry"]

    @property
    def stakeholders(self) -> list[Stakeholder]:
        return [Stakeholder(**s) for s in self.raw.get("stakeholders", [])]

    @property
    def current_sprint(self) -> int:
        return self.raw.get("contract", {}).get("current_sprint", 1)

    @property
    def current_sprint_ends(self) -> str | None:
        return self.raw.get("contract", {}).get("current_sprint_ends")

    def stakeholder(self, name: str) -> Stakeholder | None:
        for s in self.stakeholders:
            if s.name.lower() == name.lower():
                return s
        return None

    def approvers(self) -> list[Stakeholder]:
        return [s for s in self.stakeholders if s.approval_gate]


def load_project(slug: str) -> ProjectConfig:
    path = PROJECTS_ROOT / slug / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No project config at {path}. Run skill deploy-project-pm Step 2."
        )
    with path.open() as fh:
        raw = yaml.safe_load(fh)
    return ProjectConfig(slug=slug, raw=raw)


# ----------------------------------------------------------------------------
# Graphiti REST client
# ----------------------------------------------------------------------------


def graphiti_health() -> bool:
    """Return True if the local Graphiti REST is reachable."""
    try:
        with urllib.request.urlopen(f"{GRAPHITI_BASE}/api/health", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def graphiti_episode(body: str, name: str | None = None, timeout: int = 1800) -> dict:
    """POST a prose episode to Graphiti for entity + edge extraction.

    Blocks for 30-120s while gemma4:e4b runs locally. Default timeout = 1800s
    (30 min) matching deploy/kg/flyn-graphiti-api.py settings.
    """
    payload = json.dumps({"body": body, "name": name or "pm-episode"}).encode()
    req = urllib.request.Request(
        f"{GRAPHITI_BASE}/api/episode",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def graphiti_search(query: str) -> list[dict]:
    """Semantic search. Returns list of fact edges with valid_at/invalid_at."""
    qs = urllib.parse.urlencode({"q": query})
    with urllib.request.urlopen(f"{GRAPHITI_BASE}/api/search?{qs}", timeout=10) as r:
        data = json.loads(r.read())
    return data.get("facts", [])


# ----------------------------------------------------------------------------
# Telegram via OpenClaw channels
# ----------------------------------------------------------------------------


def telegram_send(chat_id: str, text: str, topic_id: str | None = None) -> None:
    """Send a Telegram message via `openclaw channels send`.

    Falls back to stdout print if openclaw is not on PATH (dev-mode dry runs).
    """
    cmd = ["openclaw", "channels", "send", "--platform", "telegram", "--chat", chat_id]
    if topic_id:
        cmd += ["--topic", topic_id]
    cmd += ["--text", text]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        print(f"[telegram_send DRYRUN to {chat_id}]\n{text}\n", flush=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"openclaw channels send failed: {e.stderr}") from e


# ----------------------------------------------------------------------------
# Git helpers (the repo is source of truth — Rule 1 from workspace/PROJECTS.md)
# ----------------------------------------------------------------------------


def git_pull(repo_path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), "pull", "--rebase", "--quiet"],
        check=True,
    )


def git_commit_and_push(repo_path: Path, paths: Iterable[str], message: str) -> str:
    """Stage specific paths, commit, push. Returns the new commit SHA.

    Raises if nothing to commit (caller should check beforehand).
    """
    paths = list(paths)
    subprocess.run(["git", "-C", str(repo_path), "add", *paths], check=True)
    # `git diff --cached --quiet` exits 1 if there's staged diff
    diff = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--cached", "--quiet"]
    )
    if diff.returncode == 0:
        raise RuntimeError("Nothing staged to commit")
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", message],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repo_path), "push", "origin", "HEAD"],
        check=True,
    )
    return sha
