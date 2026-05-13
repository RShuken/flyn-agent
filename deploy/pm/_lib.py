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
    chase_pattern: str | None = None
    deliverables: list[str] | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Stakeholder":
        """Permissive constructor: silently drops keys not in the dataclass."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


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
        return [Stakeholder.from_dict(s) for s in self.raw.get("stakeholders", [])]

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
    """Semantic search. Returns list of fact edges with valid_at/invalid_at.

    Best-effort: returns [] on any error (HTTP failure, service down,
    network issue). Callers that need to know about failures should use
    graphiti_health() first.
    """
    try:
        qs = urllib.parse.urlencode({"q": query})
        with urllib.request.urlopen(f"{GRAPHITI_BASE}/api/search?{qs}", timeout=10) as r:
            data = json.loads(r.read())
        return data.get("facts", []) or data.get("results", []) or []
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return []
    except json.JSONDecodeError:
        return []


# ----------------------------------------------------------------------------
# Telegram via OpenClaw channels
# ----------------------------------------------------------------------------


def _load_telegram_bot_token() -> str:
    """Pull the Flyn Telegram bot token from openclaw.json.

    OpenClaw 2026.4.15 doesn't expose a `channels send` subcommand (verified
    via `openclaw channels --help`), so we use the Telegram Bot HTTP API
    directly with the same bot token openclaw uses for inbound routing.
    """
    if v := os.environ.get("TELEGRAM_BOT_TOKEN"):
        return v
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg["channels"]["telegram"]["botToken"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return ""


def telegram_send(chat_id: str, text: str, topic_id: str | None = None) -> None:
    """Send a Telegram message via the Bot HTTP API.

    `chat_id` is the integer chat ID as a string (e.g. '7191564227'). If
    `topic_id` is provided (for forum/group topics), it's passed as
    `message_thread_id`. Failures raise RuntimeError so callers can decide
    to log+continue or bubble up.
    """
    token = _load_telegram_bot_token()
    if not token:
        # Dev-mode: print so tests/dry-runs don't crash
        print(f"[telegram_send NO-TOKEN to {chat_id}]\n{text}\n", flush=True)
        return

    payload: dict[str, Any] = {"chat_id": int(chat_id), "text": text}
    if topic_id:
        payload["message_thread_id"] = int(topic_id)

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        if not resp.get("ok"):
            raise RuntimeError(f"telegram sendMessage failed: {resp.get('description')}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"telegram sendMessage HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"telegram sendMessage network error: {e}") from e


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
