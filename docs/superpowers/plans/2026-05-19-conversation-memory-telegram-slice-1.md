# Conversation Memory — Telegram Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `conv` tier to the existing memory-router (`:8400`) that captures every Telegram message Flyn sees, stores it in per-owner SQLite databases with a Keychain-encrypted raw payload and an async-generated summary, promotes it to Graphiti, and surfaces it through a new 11th read adapter.

**Architecture:** Six new modules under `flyn_memory_router/conv/` (schema, owner, encrypted_raw, summarizer) + two new adapters (`conv_write`, `conv_read`) that plug into the existing `MemoryAdapter`/`ReadAdapter` Protocols. `/api/memory/ingest` branches on `event_type == "conversation_message"`. Per-owner physical SQLite isolation under `~/.flyn/memory-router/conv/`. One new launchd pulse for summarizer backfill. One new OpenClaw internal hook to fire the inbound POST.

**Tech Stack:** Python 3.14, FastAPI 0.110+, Pydantic 2.5+, SQLite + FTS5 (stdlib), `cryptography` library for AES-GCM, `security` CLI subprocess for Keychain access, ollama HTTP at `:11434/api/generate` (existing pattern), httpx for Graphiti POST. No new launchd unit (apart from one daily pulse).

**Spec:** `docs/superpowers/specs/2026-05-19-conversation-memory-design.md`
**Depends on:** memory-router unified design (PR #15, already shipped at commit b98fa1c+); PR #23 (owner-identifiers) for the `cfg.owner_identifiers` admin pattern reused by `/api/memory/conv/owners`.
**Out of scope:** outbound message mirroring, WhatsApp/iMessage/email connectors, cross-channel thread join, conv→wiki auto-promotion, semantic search via embeddings, conv deletion / TTL.

---

## File structure (lock the decomposition)

```
deploy/memory-router/flyn_memory_router/
├── conv/                                  (NEW — 4 files, ≤200 lines each)
│   ├── __init__.py
│   ├── schema.py                          (ConvDb + ConvMessage + FTS5)
│   ├── owner.py                           (OwnerRegistry + grants + audit_log)
│   ├── encrypted_raw.py                   (AES-GCM, Keychain-backed)
│   └── summarizer.py                      (SummarizerWorker thread + Ollama client)
├── adapters/
│   ├── base.py                            (modify: no change required — Protocols already exist)
│   ├── conv_write.py                      (NEW — write adapter, ≤200 lines)
│   └── conv_read.py                       (NEW — 11th read adapter, ≤200 lines)
├── types.py                               (modify: Tier.CONV enum value; ConvMessage type re-exports)
├── config.py                              (modify: + conv_root, principals_json_path, owner_identifiers)
├── server.py                              (modify: branch /ingest on event_type; new conv route; wire adapters)
├── cli.py                                 (modify: + conv subcommand cluster)
├── discovery.py                           (modify: + auto-memory pointer for conv)
└── query.py                               (modify: register conv_read in READ_ADAPTER_REGISTRY)

deploy/memory-router/tests/
├── unit/
│   ├── test_conv_encrypted_raw.py         (NEW — 4 tests)
│   ├── test_conv_owner.py                 (NEW — 3 tests)
│   ├── test_conv_schema.py                (NEW — 3 tests)
│   ├── test_conv_write_adapter.py         (NEW — 3 tests)
│   ├── test_conv_read_adapter.py          (NEW — 2 tests)
│   ├── test_conv_cli.py                   (NEW — 3 tests)
│   └── test_conv_types.py                 (NEW — 1 test for Tier.CONV)
├── integration/
│   └── test_conv_ingest_roundtrip.py      (NEW — 3 tests)
├── smoke/
│   └── test_conv_live_telegram.py         (NEW — manual ship-gate)
└── e2e/
    └── test_conv_memory_slice_1_ship_gate.md  (NEW — Ryan-runs)

deploy/memory-router/install.sh            (modify: ensure conv_root exists; seed principals.json)
deploy/memory-router/ai.flyn.memory-router.plist.template  (no change required)

deploy/hooks/
└── flyn-conv-memory-tap.sh                (NEW — OpenClaw internal hook script)

deploy/pulses/
├── conv_summarize_backfill.sh             (NEW — daily 4 AM pulse)
└── ai.flyn.pulse.conv-summarize-backfill.plist  (NEW)

deploy/outcomes/
└── CONV-MEMORY-SLICE-1-RUBRIC.md          (NEW — machine-gradable rubric)
```

**Branch:** `feat/conv-memory-telegram-slice-1` off `main`.

**Test count target:** 18 unit + integration. Plus 1 ship-gate doc + 1 smoke file. Mirrors the test discipline of PR #15.

---

## Phase 1 — Foundation types + Config (Tasks 1-3)

### Task 1: Add `Tier.CONV` + `ConvMessage` types

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/types.py`
- Create: `deploy/memory-router/tests/unit/test_conv_types.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/memory-router/tests/unit/test_conv_types.py
"""Tier.CONV enum value + ConvMessage stub for downstream tasks."""
from __future__ import annotations


def test_tier_conv_value_exists():
    from flyn_memory_router.types import Tier
    assert Tier.CONV.value == "conv"
    # Existing tiers still present
    assert {t.value for t in Tier} == {"hot", "warm", "cool", "cold", "lesson", "conv"}
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_types.py -v
```

Expected: `AttributeError: CONV` or assertion failure showing set without "conv".

- [ ] **Step 3: Add `Tier.CONV` to `types.py`**

In `flyn_memory_router/types.py`, find the `Tier` enum (currently lines 11-16) and add `CONV = "conv"` as a new member.

Final shape:
```python
class Tier(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COOL = "cool"
    COLD = "cold"
    LESSON = "lesson"
    CONV = "conv"
```

Also extend the `Importance` Literal (one line below `Tier`):
```python
Importance = Literal["hot", "warm", "cool", "cold", "lesson", "conv"]
```

- [ ] **Step 4: Run test, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_types.py tests/unit/test_types.py -v
```

Expected: new test passes + all pre-existing types tests still green.

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git switch -c feat/conv-memory-telegram-slice-1
git branch --show-current
git add deploy/memory-router/flyn_memory_router/types.py \
        deploy/memory-router/tests/unit/test_conv_types.py
git commit -m "$(cat <<'EOF'
feat(memory-router): Tier.CONV enum value + Importance extension

Task 1 — foundation for the conversation tier. No behavior change yet;
unlocks downstream tasks that route by tier.
EOF
)"
```

---

### Task 2: Extend Config with conv paths

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/config.py`
- Modify: `deploy/memory-router/tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests** (append to `tests/unit/test_config.py`)

```python
def test_config_has_conv_root_default(monkeypatch, tmp_path):
    from flyn_memory_router.config import Config
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.conv_root == tmp_path / "conv"
    assert cfg.principals_json_path == tmp_path / "conv" / "principals.json"


def test_config_conv_root_env_override(monkeypatch, tmp_path):
    from flyn_memory_router.config import Config
    custom = tmp_path / "custom-conv"
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_CONV_ROOT", str(custom))
    cfg = Config.from_env()
    assert cfg.conv_root == custom
```

- [ ] **Step 2: Run, expect FAIL** (AttributeError on `conv_root`)

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_config.py -v -k "conv_root"
```

- [ ] **Step 3: Add fields + properties to `config.py`**

In `flyn_memory_router/config.py`:

(a) Add a new property after the existing `captures_index` property:

```python
    @property
    def conv_root(self) -> Path:
        env = os.environ.get("FLYN_CONV_ROOT")
        if env:
            return Path(env)
        return self.home / "conv"

    @property
    def principals_json_path(self) -> Path:
        return self.conv_root / "principals.json"

    @property
    def conv_owners_db_path(self) -> Path:
        return self.conv_root / "owners.db"
```

That's it for this task — no changes to `__init__` or `from_env`, since we read `FLYN_CONV_ROOT` lazily inside the property.

- [ ] **Step 4: Run, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_config.py -v
```

- [ ] **Step 5: Commit**

```bash
git add deploy/memory-router/flyn_memory_router/config.py \
        deploy/memory-router/tests/unit/test_config.py
git commit -m "feat(memory-router): Config conv_root + principals_json_path + conv_owners_db_path"
```

---

### Task 3: Add `cryptography` dep + verify it imports

**Files:**
- Modify: `deploy/memory-router/pyproject.toml`

- [ ] **Step 1: Add `cryptography` to dependencies**

Open `deploy/memory-router/pyproject.toml`. The `[project]` table has a `dependencies = [...]` list. Add `"cryptography>=42.0"` as the last entry before the closing `]`:

```toml
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "pydantic>=2.5",
  "httpx>=0.27",
  "slowapi>=0.1.9",
  "pyyaml>=6.0",
  "cryptography>=42.0",
]
```

- [ ] **Step 2: Reinstall editable + verify import**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
pip install -e . --quiet
python3 -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; print('AESGCM ok')"
```

Expected: `AESGCM ok`.

- [ ] **Step 3: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/pyproject.toml
git commit -m "build(memory-router): add cryptography>=42.0 for AES-GCM in conv tier"
```

---

## Phase 2 — Encryption + Owner registry (Tasks 4-5)

### Task 4: `conv/encrypted_raw.py` — Keychain-backed AES-GCM

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/conv/__init__.py` (empty)
- Create: `deploy/memory-router/flyn_memory_router/conv/encrypted_raw.py`
- Create: `deploy/memory-router/tests/unit/test_conv_encrypted_raw.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_conv_encrypted_raw.py
"""AES-GCM seal/unseal with Keychain-backed per-owner keys."""
from __future__ import annotations

import pytest
from unittest.mock import patch


def test_seal_unseal_roundtrip(tmp_path, monkeypatch):
    """seal(plaintext, owner) → unseal(...) returns the original bytes."""
    from flyn_memory_router.conv import encrypted_raw
    # Stub _get_key to a fixed 16-byte key (don't touch real Keychain in unit tests)
    fixed_key = b"0123456789abcdef"
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: fixed_key)

    plaintext = b'{"channel":"telegram","text":"hello"}'
    sealed = encrypted_raw.seal(plaintext, "ryan")
    assert sealed != plaintext
    assert len(sealed) > len(plaintext)  # nonce + tag overhead

    out = encrypted_raw.unseal(sealed, "ryan")
    assert out == plaintext


def test_unseal_wrong_owner_fails(monkeypatch):
    """Sealed with key A, attempted unseal with key B → tamper error."""
    from flyn_memory_router.conv import encrypted_raw
    keys = {"ryan": b"0123456789abcdef", "beth": b"fedcba9876543210"}
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: keys[owner_id])

    sealed = encrypted_raw.seal(b"secret", "ryan")
    with pytest.raises(Exception):  # cryptography raises InvalidTag
        encrypted_raw.unseal(sealed, "beth")


def test_keychain_locked_raises(monkeypatch):
    """If `security` CLI fails / times out, raise KeychainLocked."""
    from flyn_memory_router.conv import encrypted_raw
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=2)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(encrypted_raw.KeychainLocked):
        encrypted_raw.seal(b"x", "ryan")


def test_tamper_detection(monkeypatch):
    """Modifying any byte of ciphertext → InvalidTag on unseal."""
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"0123456789abcdef")

    sealed = encrypted_raw.seal(b"hello world", "ryan")
    # Flip a byte in the middle (past the nonce)
    tampered = sealed[:14] + bytes([sealed[14] ^ 0x01]) + sealed[15:]
    with pytest.raises(Exception):
        encrypted_raw.unseal(tampered, "ryan")
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`)

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_encrypted_raw.py -v
```

- [ ] **Step 3: Implement `encrypted_raw.py`**

Create `deploy/memory-router/flyn_memory_router/conv/__init__.py` as an empty file first:

```bash
mkdir -p /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router/flyn_memory_router/conv
touch /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router/flyn_memory_router/conv/__init__.py
```

Then create `flyn_memory_router/conv/encrypted_raw.py`:

```python
"""Per-owner AES-GCM encryption with keys in macOS Keychain.

The plaintext is the redacted-raw Telegram payload; ciphertext format is
`nonce(12 bytes) || ciphertext || auth_tag(16 bytes)` — standard AES-GCM
layout. The 16-byte key per owner is generated on first use via os.urandom
and stored as a generic password in the user's login keychain.

The `security` CLI is used as a subprocess (no pyobjc dep). If the keychain
is locked (Mac asleep, screen locked) the CLI fails fast and we raise
KeychainLocked — the caller (conv_write adapter) treats this as a hard
"can't store this message" condition.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYCHAIN_SERVICE_PREFIX = "flyn-conv-memory"
KEYCHAIN_ACCOUNT = "aes-key"
KEYCHAIN_TIMEOUT_S = 2.0


class KeychainLocked(Exception):
    """Raised when the macOS keychain cannot be read (locked / timeout)."""


def seal(plaintext: bytes, owner_id: str) -> bytes:
    """Encrypt plaintext with the owner's AES-GCM key. Returns nonce||ct||tag."""
    key = _get_key(owner_id)
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext, associated_data=None)
    return nonce + ct


def unseal(ciphertext: bytes, owner_id: str) -> bytes:
    """Decrypt. Raises cryptography.exceptions.InvalidTag on tamper or wrong key."""
    key = _get_key(owner_id)
    aes = AESGCM(key)
    nonce, ct = ciphertext[:12], ciphertext[12:]
    return aes.decrypt(nonce, ct, associated_data=None)


@lru_cache(maxsize=8)
def _get_key(owner_id: str) -> bytes:
    """Read (or create) the owner's 16-byte AES key from the login keychain."""
    service = f"{KEYCHAIN_SERVICE_PREFIX}:{owner_id}"
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", service, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True,
            timeout=KEYCHAIN_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise KeychainLocked(f"security CLI timed out for {service}") from exc
    except FileNotFoundError as exc:
        raise KeychainLocked("security CLI not found (not macOS?)") from exc

    if result.returncode == 0:
        # Key exists. The CLI prints it as a hex-escaped string with trailing newline.
        key_str = result.stdout.strip()
        # find-generic-password -w prints the password as plain text by default
        return bytes.fromhex(key_str) if all(c in "0123456789abcdef" for c in key_str) else key_str.encode("utf-8")[:16].ljust(16, b"\0")

    # Not found — create a new 16-byte key
    new_key = os.urandom(16)
    create = subprocess.run(
        ["security", "add-generic-password",
         "-s", service, "-a", KEYCHAIN_ACCOUNT, "-w", new_key.hex()],
        capture_output=True, text=True,
        timeout=KEYCHAIN_TIMEOUT_S, check=False,
    )
    if create.returncode != 0:
        raise KeychainLocked(f"add-generic-password failed: {create.stderr.strip()}")
    return new_key
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_encrypted_raw.py -v
```

Expected: 4 tests pass. (All use `monkeypatch` to stub `_get_key` or `subprocess.run` — no real keychain calls happen in tests.)

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/conv/__init__.py \
        deploy/memory-router/flyn_memory_router/conv/encrypted_raw.py \
        deploy/memory-router/tests/unit/test_conv_encrypted_raw.py
git commit -m "feat(memory-router): conv/encrypted_raw.py — AES-GCM via Keychain"
```

---

### Task 5: `conv/owner.py` — Owner registry + grants + audit

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/conv/owner.py`
- Create: `deploy/memory-router/tests/unit/test_conv_owner.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_conv_owner.py
"""OwnerRegistry — resolution, grants, default-deny, audit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def registry(tmp_path: Path):
    """Build a registry with one owner seeded."""
    from flyn_memory_router.conv.owner import OwnerRegistry
    owners_db = tmp_path / "owners.db"
    principals = tmp_path / "principals.json"
    principals.write_text(json.dumps({
        "owners": [
            {"id": "ryan", "display_name": "Ryan Shuken",
             "principals": {"telegram": "7191564227"}}
        ]
    }))
    return OwnerRegistry(owners_db_path=owners_db, principals_json=principals)


def test_self_read_allowed(registry):
    """viewer == owner: always allowed, no audit row."""
    assert registry.viewer_can_read("ryan", "ryan") is True
    owner = registry.resolve_from_chat("telegram", "7191564227")
    assert owner is not None
    assert owner.id == "ryan"


def test_default_deny_cross_owner_read(registry):
    """No grant → viewer cannot read another owner's data."""
    assert registry.viewer_can_read("beth", "ryan") is False
    assert registry.list_accessible_owners("beth") == set()


def test_grant_allows_read_and_writes_audit(registry, tmp_path):
    """grant() persists; subsequent reads write an audit row."""
    registry.grant("beth", "ryan", granted_by="ryan", reason="OL planning")
    assert registry.viewer_can_read("beth", "ryan") is True
    registry.append_audit("beth", "ryan", op="read", q="linear backlog")
    rows = registry.recent_audit(limit=5)
    assert any(r["viewer"] == "beth" and r["owned_by"] == "ryan" and r["op"] == "read"
               for r in rows)
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_owner.py -v
```

- [ ] **Step 3: Implement `conv/owner.py`**

```python
"""Owner registry, access grants, and audit logging.

Per-owner physical isolation: each owner's conversation messages live in
their own SQLite file at <conv_root>/<owner>.db. Cross-owner reads require
an explicit grant row in owners.db and every cross-owner read writes to
audit_log.

The shared owners.db sits at <conv_root>/owners.db. Schema is created on
first OwnerRegistry construction (idempotent CREATE TABLE IF NOT EXISTS).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    principals_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    viewer      TEXT NOT NULL,
    owned_by    TEXT NOT NULL,
    granted_at  TEXT NOT NULL,
    granted_by  TEXT NOT NULL,
    reason      TEXT,
    PRIMARY KEY (viewer, owned_by)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    viewer    TEXT NOT NULL,
    owned_by  TEXT NOT NULL,
    op        TEXT NOT NULL,
    q         TEXT
);
"""


@dataclass(frozen=True)
class Owner:
    id: str
    display_name: str
    chat_id_map: dict[str, str] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OwnerRegistry:
    def __init__(self, owners_db_path: Path, principals_json: Path) -> None:
        self._db_path = owners_db_path
        self._principals = principals_json
        self._lock = Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
        if self._principals.exists():
            self._seed_from_principals()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path)
        c.row_factory = sqlite3.Row
        return c

    def _seed_from_principals(self) -> None:
        try:
            data = json.loads(self._principals.read_text())
        except (OSError, json.JSONDecodeError):
            return
        with self._lock, self._conn() as c:
            for o in data.get("owners", []):
                c.execute(
                    "INSERT OR REPLACE INTO owners (id, display_name, principals_json) "
                    "VALUES (?, ?, ?)",
                    (o["id"], o.get("display_name", o["id"]),
                     json.dumps(o.get("principals", {}))),
                )

    # --- Resolution ---

    def resolve_from_chat(self, channel: str, sender_id: str) -> Owner | None:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id, display_name, principals_json FROM owners"
            ).fetchall()
        for row in rows:
            principals = json.loads(row["principals_json"])
            if principals.get(channel) == sender_id:
                return Owner(
                    id=row["id"],
                    display_name=row["display_name"],
                    chat_id_map=principals,
                )
        return None

    def db_path_for(self, owner_id: str, conv_root: Path) -> Path:
        return conv_root / f"{owner_id}.db"

    # --- Access ---

    def viewer_can_read(self, viewer: str, owned_by: str) -> bool:
        if viewer == owned_by:
            return True
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM grants WHERE viewer = ? AND owned_by = ?",
                (viewer, owned_by),
            ).fetchone()
        return row is not None

    def list_accessible_owners(self, viewer: str) -> set[str]:
        out: set[str] = set()
        with self._lock, self._conn() as c:
            # Self
            row = c.execute("SELECT id FROM owners WHERE id = ?", (viewer,)).fetchone()
            if row:
                out.add(viewer)
            # Granted
            for r in c.execute(
                "SELECT owned_by FROM grants WHERE viewer = ?", (viewer,)
            ):
                out.add(r["owned_by"])
        return out

    def grant(self, viewer: str, owned_by: str, *,
              granted_by: str, reason: str = "") -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO grants "
                "(viewer, owned_by, granted_at, granted_by, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (viewer, owned_by, _now_iso(), granted_by, reason),
            )
            c.execute(
                "INSERT INTO audit_log (ts, viewer, owned_by, op, q) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(), viewer, owned_by, "grant", reason or None),
            )

    def revoke(self, viewer: str, owned_by: str, *, revoked_by: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "DELETE FROM grants WHERE viewer = ? AND owned_by = ?",
                (viewer, owned_by),
            )
            c.execute(
                "INSERT INTO audit_log (ts, viewer, owned_by, op, q) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(), viewer, owned_by, "revoke", revoked_by),
            )

    # --- Audit ---

    def append_audit(self, viewer: str, owned_by: str, *,
                     op: str, q: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO audit_log (ts, viewer, owned_by, op, q) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(), viewer, owned_by, op, q),
            )

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT ts, viewer, owned_by, op, q FROM audit_log "
                "ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_owner.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/conv/owner.py \
        deploy/memory-router/tests/unit/test_conv_owner.py
git commit -m "feat(memory-router): conv/owner.py — registry + grants + audit"
```

---

## Phase 3 — ConvDb storage layer (Task 6)

### Task 6: `conv/schema.py` — SQLite + FTS5 schema + ConvDb

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/conv/schema.py`
- Create: `deploy/memory-router/tests/unit/test_conv_schema.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_conv_schema.py
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
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_schema.py -v
```

- [ ] **Step 3: Implement `conv/schema.py`**

```python
"""Per-owner SQLite schema for conversation messages.

Each owner has their own DB file (`<owner_id>.db` under conv_root). Schema
is idempotent (CREATE TABLE IF NOT EXISTS). WAL mode for concurrent reads
during writes. FTS5 virtual table indexes the redacted body + summary —
NOT the encrypted_raw BLOB.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,
    sender_id     TEXT NOT NULL,
    thread_id     TEXT,
    reply_to_id   INTEGER,
    ts            TEXT NOT NULL,
    body          TEXT NOT NULL,
    attachments   TEXT,
    summary       TEXT,
    encrypted_raw BLOB NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    body, summary, content=messages, content_rowid=id
);

CREATE INDEX IF NOT EXISTS idx_messages_thread_ts ON messages(thread_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_sender_ts ON messages(sender_id, ts DESC);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, body, summary)
      VALUES (new.id, new.body, COALESCE(new.summary, ''));
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, summary)
      VALUES('delete', old.id, old.body, COALESCE(old.summary, ''));
    INSERT INTO messages_fts(rowid, body, summary)
      VALUES (new.id, new.body, COALESCE(new.summary, ''));
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, summary)
      VALUES('delete', old.id, old.body, COALESCE(old.summary, ''));
END;
"""


@dataclass(frozen=True)
class ConvMessage:
    channel: str
    sender_id: str
    thread_id: str | None
    reply_to_id: int | None
    ts: str
    body: str
    attachments: list[dict]
    encrypted_raw: bytes


@dataclass(frozen=True)
class StoredMessage:
    row_id: int
    channel: str
    sender_id: str
    thread_id: str | None
    reply_to_id: int | None
    ts: str
    body: str
    attachments: list[dict]
    summary: str | None
    encrypted_raw: bytes
    fts_score: float = 0.0


class ConvDb:
    def __init__(self, owner_id: str, path: Path) -> None:
        self.owner_id = owner_id
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def write(self, msg: ConvMessage) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO messages "
                "(channel, sender_id, thread_id, reply_to_id, ts, body, "
                "attachments, summary, encrypted_raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (msg.channel, msg.sender_id, msg.thread_id, msg.reply_to_id,
                 msg.ts, msg.body, json.dumps(msg.attachments), msg.encrypted_raw),
            )
            return cur.lastrowid

    def update_summary(self, row_id: int, summary: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE messages SET summary = ? WHERE id = ?", (summary, row_id))

    def search(self, q: str, top_k: int = 30) -> list[StoredMessage]:
        if not q.strip():
            return []
        # FTS5 MATCH; rank is BM25-derived (negative; lower = better)
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT m.id, m.channel, m.sender_id, m.thread_id, m.reply_to_id, "
                "m.ts, m.body, m.attachments, m.summary, m.encrypted_raw, "
                "messages_fts.rank AS rank "
                "FROM messages_fts "
                "JOIN messages m ON m.id = messages_fts.rowid "
                "WHERE messages_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (q, top_k),
            ).fetchall()
        return [self._row_to_msg(r, fts_score=-(r["rank"] or 0.0)) for r in rows]

    def get_by_thread(self, thread_id: str, limit: int = 50) -> list[StoredMessage]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE thread_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (thread_id, limit),
            ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def get_by_id(self, row_id: int) -> StoredMessage | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM messages WHERE id = ?", (row_id,)).fetchone()
        return self._row_to_msg(row) if row else None

    def stats(self) -> dict[str, Any]:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS oldest, MAX(ts) AS newest, "
                "SUM(CASE WHEN summary IS NULL THEN 1 ELSE 0 END) AS backlog "
                "FROM messages"
            ).fetchone()
        return {
            "owner": self.owner_id,
            "messages": row["n"],
            "oldest_ts": row["oldest"],
            "newest_ts": row["newest"],
            "summary_backlog": row["backlog"] or 0,
        }

    @staticmethod
    def _row_to_msg(row: sqlite3.Row, fts_score: float = 0.0) -> StoredMessage:
        return StoredMessage(
            row_id=row["id"],
            channel=row["channel"],
            sender_id=row["sender_id"],
            thread_id=row["thread_id"],
            reply_to_id=row["reply_to_id"],
            ts=row["ts"],
            body=row["body"],
            attachments=json.loads(row["attachments"]) if row["attachments"] else [],
            summary=row["summary"],
            encrypted_raw=row["encrypted_raw"],
            fts_score=fts_score,
        )
```

- [ ] **Step 4: Run, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_schema.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/conv/schema.py \
        deploy/memory-router/tests/unit/test_conv_schema.py
git commit -m "feat(memory-router): conv/schema.py — ConvDb + FTS5 + triggers"
```

---

## Phase 4 — Summarizer worker (Task 7)

### Task 7: `conv/summarizer.py` — background worker + Ollama client

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/conv/summarizer.py`

No test file in this task — the summarizer is exercised by the integration tests in Task 11. Unit-testing a background thread that talks to a real Ollama is high-cost/low-value.

- [ ] **Step 1: Implement `conv/summarizer.py`**

```python
"""Background worker that pulls summarize-jobs from the disk queue and
calls Ollama's gemma4:e4b to fill `messages.summary` for conversation rows.

Reuses the existing memory-router queue dir convention. Each job is a
single JSON file under <queue_dir>/conv-summarize/ whose name doubles as
the unique job id. On success the file is deleted. On failure it stays
for the next poll. A daily backfill pulse (deploy/pulses/) scans for
rows with NULL summary older than 1h and re-enqueues them.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .schema import ConvDb

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_TIMEOUT = 30.0
BUSY_POLL_S = 1.0
IDLE_POLL_S = 10.0

SUMMARY_PROMPT_TEMPLATE = (
    "Summarize this Telegram message in 1-2 sentences. Focus on what the "
    "sender said, decided, or asked. Skip pleasantries.\n\n"
    "Sender: {sender_id}\n"
    "Body: {body}\n\n"
    'Return JSON: {{"summary": "..."}}'
)


@dataclass(frozen=True)
class SummarizeJob:
    """One queued job. Serialized as a JSON file on disk."""
    owner_id: str
    db_path: str
    row_id: int
    body: str
    sender_id: str

    def to_path(self, queue_dir: Path) -> Path:
        return queue_dir / f"conv-summarize-{self.owner_id}-{self.row_id}.json"

    @classmethod
    def from_file(cls, p: Path) -> "SummarizeJob":
        d = json.loads(p.read_text())
        return cls(**d)


def enqueue(queue_dir: Path, job: SummarizeJob) -> Path:
    """Write a SummarizeJob to disk. Returns the file path."""
    target = queue_dir / "conv-summarize"
    target.mkdir(parents=True, exist_ok=True)
    p = target / f"conv-summarize-{job.owner_id}-{job.row_id}.json"
    p.write_text(json.dumps(job.__dict__))
    return p


class SummarizerWorker:
    def __init__(
        self,
        queue_dir: Path,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._dir = queue_dir / "conv-summarize"
        self._url = ollama_url
        self._model = model
        self._timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._loop, name="conv-summarizer", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            processed = self._tick()
            self._stop.wait(BUSY_POLL_S if processed else IDLE_POLL_S)

    def _tick(self) -> bool:
        """Pull one job and process it. Returns True if a job was attempted."""
        jobs = sorted(self._dir.glob("conv-summarize-*.json"), key=lambda p: p.stat().st_mtime)
        if not jobs:
            return False
        job_path = jobs[0]
        try:
            job = SummarizeJob.from_file(job_path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("conv-summarize: bad job file %s: %s", job_path, exc)
            return True
        summary = self._call_ollama(job.body, job.sender_id)
        if summary is None:
            return True  # leave job in place for retry
        try:
            ConvDb(job.owner_id, Path(job.db_path)).update_summary(job.row_id, summary)
            job_path.unlink()
        except Exception as exc:
            logger.warning("conv-summarize: update_summary failed: %s", exc)
        return True

    def _call_ollama(self, body: str, sender_id: str) -> str | None:
        prompt = SUMMARY_PROMPT_TEMPLATE.format(body=body[:4000], sender_id=sender_id)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        try:
            req = urllib.request.Request(
                self._url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body_resp = json.loads(resp.read())
            parsed = json.loads(body_resp.get("response", "").strip())
            summary = parsed.get("summary", "").strip()
            return summary if summary else None
        except Exception as exc:
            logger.debug("conv-summarize: ollama call failed: %s", exc)
            return None
```

- [ ] **Step 2: Smoke-import test**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -c "from flyn_memory_router.conv.summarizer import SummarizerWorker, SummarizeJob, enqueue; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/conv/summarizer.py
git commit -m "feat(memory-router): conv/summarizer.py — Ollama worker + disk-queue"
```

---

## Phase 5 — Write + Read adapters (Tasks 8-9)

### Task 8: `adapters/conv_write.py` — write adapter

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/conv_write.py`
- Create: `deploy/memory-router/tests/unit/test_conv_write_adapter.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_conv_write_adapter.py
"""ConvWriteAdapter — happy path + 2 failure modes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _registry(tmp_path: Path):
    from flyn_memory_router.conv.owner import OwnerRegistry
    principals = tmp_path / "principals.json"
    principals.write_text(json.dumps({
        "owners": [{"id": "ryan", "display_name": "Ryan",
                    "principals": {"telegram": "7191564227"}}]
    }))
    return OwnerRegistry(owners_db_path=tmp_path / "owners.db",
                         principals_json=principals)


def _event(**raw_overrides):
    from flyn_memory_router.types import InboundEvent
    raw = dict(
        channel="telegram",
        chat_id=7191564227,
        sender_id=7191564227,
        thread_id=7191564227,
        reply_to_msg_id=None,
        attachments=[],
        ts="2026-05-19T18:00:00+00:00",
    )
    raw.update(raw_overrides)
    return InboundEvent(
        source="telegram",
        event_type="conversation_message",
        subject="tg-7191564227-100",
        body="Linear backlog stuck at 73 of 124",
        importance="warm",
        raw_payload=raw,
        valid_at=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
        dedup_key="tg-7191564327-100",
    )


def test_happy_path_writes_row(tmp_path: Path, monkeypatch):
    """Resolves owner → seals raw → writes message → returns ok=True."""
    from flyn_memory_router.adapters.conv_write import ConvWriteAdapter
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)

    adapter = ConvWriteAdapter(
        registry=_registry(tmp_path),
        conv_root=tmp_path / "conv",
        queue_dir=tmp_path / "queue",
        graphiti_url=None,  # no graphiti in unit test
    )
    result = adapter.write(_event())
    assert result.ok is True
    # Verify row landed in ryan.db
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb("ryan", tmp_path / "conv" / "ryan.db")
    hits = db.search("Linear backlog")
    assert len(hits) == 1


def test_unknown_sender_returns_ok_false(tmp_path: Path, monkeypatch):
    """Sender with no principal mapping → ok=False, no row written."""
    from flyn_memory_router.adapters.conv_write import ConvWriteAdapter
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)

    adapter = ConvWriteAdapter(
        registry=_registry(tmp_path),
        conv_root=tmp_path / "conv",
        queue_dir=tmp_path / "queue",
        graphiti_url=None,
    )
    result = adapter.write(_event(sender_id=999999999))
    assert result.ok is False
    assert "unknown sender" in result.detail.lower()
    # No db file should exist
    assert not (tmp_path / "conv" / "ryan.db").exists()


def test_keychain_locked_returns_ok_false(tmp_path: Path, monkeypatch):
    """seal raises KeychainLocked → ok=False, row NOT stored unencrypted."""
    from flyn_memory_router.adapters.conv_write import ConvWriteAdapter
    from flyn_memory_router.conv import encrypted_raw

    def fail(*args, **kwargs):
        raise encrypted_raw.KeychainLocked("locked")
    monkeypatch.setattr(encrypted_raw, "seal", fail)

    adapter = ConvWriteAdapter(
        registry=_registry(tmp_path),
        conv_root=tmp_path / "conv",
        queue_dir=tmp_path / "queue",
        graphiti_url=None,
    )
    result = adapter.write(_event())
    assert result.ok is False
    assert "keychain" in result.detail.lower()
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_write_adapter.py -v
```

- [ ] **Step 3: Implement `adapters/conv_write.py`**

```python
"""Conversation-tier write adapter.

Triggered when InboundEvent.event_type == "conversation_message". Routes
to the appropriate per-owner ConvDb after sealing the raw_payload with
the owner's AES-GCM key. Fire-and-forget POST to Graphiti for entity
extraction. Async summarizer job enqueued.

All five steps wrapped in try/except → never raises to the ingest pipeline;
failure surfaces as WriteResult(ok=False, detail=...).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .base import WriteResult
from ..conv import encrypted_raw
from ..conv.owner import OwnerRegistry
from ..conv.schema import ConvDb, ConvMessage
from ..conv.summarizer import SummarizeJob, enqueue
from ..types import InboundEvent

logger = logging.getLogger(__name__)


class ConvWriteAdapter:
    name: str = "conv.telegram"

    def __init__(
        self,
        registry: OwnerRegistry,
        conv_root: Path,
        queue_dir: Path,
        graphiti_url: Optional[str],
        http_client=None,
    ) -> None:
        self._registry = registry
        self._conv_root = conv_root
        self._queue_dir = queue_dir
        self._graphiti_url = graphiti_url
        self._http = http_client  # httpx.Client or None
        self._conv_root.mkdir(parents=True, exist_ok=True)

    def write(self, event: InboundEvent) -> WriteResult:
        raw = event.raw_payload or {}
        channel = raw.get("channel", event.source)
        sender_id = str(raw.get("sender_id") or raw.get("chat_id") or "")

        # 1. Owner resolution
        owner = self._registry.resolve_from_chat(channel, sender_id)
        if owner is None:
            return WriteResult(target=self.name, ok=False,
                               detail=f"unknown sender: channel={channel} sender_id={sender_id}")

        # 2. Seal raw_payload
        try:
            sealed = encrypted_raw.seal(
                json.dumps(raw, default=str).encode("utf-8"),
                owner.id,
            )
        except encrypted_raw.KeychainLocked as exc:
            return WriteResult(target=self.name, ok=False, detail=f"keychain locked: {exc}")
        except Exception as exc:
            return WriteResult(target=self.name, ok=False, detail=f"seal failed: {exc}")

        # 3. Write to per-owner ConvDb
        try:
            db = ConvDb(owner.id, self._registry.db_path_for(owner.id, self._conv_root))
            msg = ConvMessage(
                channel=channel,
                sender_id=sender_id,
                thread_id=str(raw.get("thread_id") or raw.get("chat_id") or ""),
                reply_to_id=raw.get("reply_to_msg_id"),
                ts=str(raw.get("ts") or event.valid_at.isoformat() if event.valid_at else ""),
                body=event.body,
                attachments=raw.get("attachments", []),
                encrypted_raw=sealed,
            )
            row_id = db.write(msg)
        except Exception as exc:
            return WriteResult(target=self.name, ok=False, detail=f"db.write failed: {exc}")

        # 4. Enqueue summarize-job (best-effort)
        try:
            enqueue(self._queue_dir, SummarizeJob(
                owner_id=owner.id,
                db_path=str(db.path),
                row_id=row_id,
                body=msg.body,
                sender_id=msg.sender_id,
            ))
        except Exception as exc:
            logger.warning("conv: enqueue summarize failed: %s", exc)

        # 5. POST Graphiti episode (fire-and-forget)
        if self._graphiti_url and self._http is not None:
            try:
                self._http.post(
                    f"{self._graphiti_url.rstrip('/')}/api/episodes",
                    json={
                        "name": f"telegram-msg-{row_id}",
                        "summary": msg.body,
                        "group_id": f"flyn-{owner.id}",
                        "source_description": "conv/telegram",
                        "valid_at": msg.ts,
                    },
                    timeout=2.0,
                )
            except Exception as exc:
                logger.debug("conv: graphiti POST failed: %s", exc)

        return WriteResult(target=self.name, ok=True, detail=f"row={row_id}")
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_write_adapter.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/adapters/conv_write.py \
        deploy/memory-router/tests/unit/test_conv_write_adapter.py
git commit -m "feat(memory-router): adapters/conv_write.py — 5-step write sequence"
```

---

### Task 9: `adapters/conv_read.py` — 11th read adapter

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/conv_read.py`
- Create: `deploy/memory-router/tests/unit/test_conv_read_adapter.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_conv_read_adapter.py
"""ConvReadAdapter — Protocol compliance + cross-owner audit."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


def _seed_registry(tmp_path: Path):
    from flyn_memory_router.conv.owner import OwnerRegistry
    p = tmp_path / "principals.json"
    p.write_text(json.dumps({
        "owners": [
            {"id": "ryan", "display_name": "Ryan", "principals": {"telegram": "7191564227"}},
            {"id": "beth", "display_name": "Beth", "principals": {"telegram": "7434192034"}},
        ]
    }))
    return OwnerRegistry(owners_db_path=tmp_path / "owners.db", principals_json=p)


def _seed_msg(tmp_path: Path, owner_id: str, body: str):
    from flyn_memory_router.conv.schema import ConvDb, ConvMessage
    db = ConvDb(owner_id, tmp_path / "conv" / f"{owner_id}.db")
    db.write(ConvMessage(
        channel="telegram", sender_id="x", thread_id="t", reply_to_id=None,
        ts="2026-05-19T18:00:00+00:00", body=body, attachments=[],
        encrypted_raw=b"\x00" * 32,
    ))


def test_read_adapter_protocol_compliance(tmp_path: Path):
    """Implements ReadAdapter Protocol: name, read_timeout, default_included, async query."""
    from flyn_memory_router.adapters.conv_read import ConvReadAdapter
    from flyn_memory_router.adapters.base import ReadAdapter
    adapter = ConvReadAdapter(
        registry=_seed_registry(tmp_path),
        conv_root=tmp_path / "conv",
        viewer_id="ryan",
    )
    assert adapter.name == "conv"
    assert adapter.read_timeout == 1.5
    assert adapter.default_included is True
    assert isinstance(adapter, ReadAdapter)

    _seed_msg(tmp_path, "ryan", "Linear backlog discussion")
    hits = asyncio.run(adapter.query("linear", top_k=5))
    assert len(hits) == 1
    assert hits[0].source == "conv/telegram"
    assert hits[0].metadata["owner"] == "ryan"


def test_cross_owner_read_writes_audit(tmp_path: Path):
    """Reading another owner's data (with grant) writes to audit_log."""
    from flyn_memory_router.adapters.conv_read import ConvReadAdapter
    registry = _seed_registry(tmp_path)
    registry.grant("ryan", "beth", granted_by="ryan", reason="testing")

    _seed_msg(tmp_path, "beth", "Beth said something about Pearl Platform")
    adapter = ConvReadAdapter(
        registry=registry,
        conv_root=tmp_path / "conv",
        viewer_id="ryan",
    )
    hits = asyncio.run(adapter.query("pearl platform", top_k=5))
    assert any(h.metadata["owner"] == "beth" for h in hits)

    # An audit row should exist for the cross-owner read
    audit = registry.recent_audit(limit=10)
    assert any(r["viewer"] == "ryan" and r["owned_by"] == "beth" and r["op"] == "read"
               for r in audit)
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_read_adapter.py -v
```

- [ ] **Step 3: Implement `adapters/conv_read.py`**

```python
"""Conversation-tier read adapter — 11th adapter in the existing fan-out.

Queries each accessible owner's ConvDb via FTS5 over body + summary.
Cross-owner reads write to audit_log via the OwnerRegistry. Returns
Hit objects compatible with the existing query.py RRF merge.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..conv.owner import OwnerRegistry
from ..conv.schema import ConvDb
from ..types import Hit


class ConvReadAdapter:
    name: str = "conv"
    read_timeout: float = 1.5
    default_included: bool = True

    def __init__(
        self,
        registry: OwnerRegistry,
        conv_root: Path,
        viewer_id: Optional[str] = None,
    ) -> None:
        self._registry = registry
        self._conv_root = conv_root
        self._viewer = viewer_id or os.environ.get("USER", "ryan")

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        accessible = self._registry.list_accessible_owners(self._viewer)
        all_hits: list[Hit] = []
        for owner_id in accessible:
            db_path = self._registry.db_path_for(owner_id, self._conv_root)
            if not db_path.exists():
                continue
            db = ConvDb(owner_id, db_path)
            for stored in db.search(q, top_k=top_k):
                all_hits.append(Hit(
                    text=stored.summary or stored.body[:500],
                    source=f"conv/{stored.channel}",
                    score=stored.fts_score,
                    metadata={
                        "msg_id": stored.row_id,
                        "thread_id": stored.thread_id,
                        "sender_id": stored.sender_id,
                        "ts": stored.ts,
                        "owner": owner_id,
                        "has_summary": stored.summary is not None,
                    },
                ))
            if owner_id != self._viewer:
                self._registry.append_audit(self._viewer, owner_id, op="read", q=q)
        all_hits.sort(key=lambda h: h.score, reverse=True)
        return all_hits[:top_k]
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_read_adapter.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/adapters/conv_read.py \
        deploy/memory-router/tests/unit/test_conv_read_adapter.py
git commit -m "feat(memory-router): adapters/conv_read.py — 11th read adapter"
```

---

## Phase 6 — Server wiring (Task 10)

### Task 10: Wire conv adapters into server + register in query orchestrator

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/server.py`
- Modify: `deploy/memory-router/flyn_memory_router/query.py`

- [ ] **Step 1: Wire `ConvWriteAdapter` into `build_app`**

In `flyn_memory_router/server.py`, find the `build_app` function. Near the top of the function (around line 60-70 where other adapters are constructed), add the conv components.

Locate the section that imports adapters and add:

```python
from .adapters.conv_write import ConvWriteAdapter
from .adapters.conv_read import ConvReadAdapter
from .conv.owner import OwnerRegistry
from .conv.summarizer import SummarizerWorker
```

Inside `build_app`, after the existing adapter setup, add (find a spot just before `registry.register(Tier.LESSON, lesson)` or similar):

```python
    # --- Conversation tier (Telegram slice 1) ---
    cfg.conv_root.mkdir(parents=True, exist_ok=True)
    owner_registry = OwnerRegistry(
        owners_db_path=cfg.conv_owners_db_path,
        principals_json=cfg.principals_json_path,
    )
    conv_write = ConvWriteAdapter(
        registry=owner_registry,
        conv_root=cfg.conv_root,
        queue_dir=cfg.queue_dir,
        graphiti_url=cfg.graphiti_url,
        http_client=http_client,
    )
    registry.register(Tier.CONV, conv_write)

    # Async summarizer worker
    summarizer = SummarizerWorker(queue_dir=cfg.queue_dir)
    summarizer.start()
```

- [ ] **Step 2: Branch `/api/memory/ingest` on event_type**

Still in `server.py`, find the `@app.post("/api/memory/ingest")` route. Add an early branch:

```python
    @app.post("/api/memory/ingest", response_model=EventResult)
    def ingest(event: InboundEvent) -> EventResult:
        if event.event_type == "conversation_message":
            result = conv_write.write(event)
            return EventResult(
                accepted=result.ok,
                deduped=False,
                importance=event.importance or "warm",
                tiers_written=[Tier.CONV] if result.ok else [],
                notes=[result.detail] if result.detail else [],
            )
        return router.ingest(event)
```

- [ ] **Step 3: Register `conv_read` in the query orchestrator**

In `flyn_memory_router/query.py`, find where the 10 existing read adapters are registered (look for `READ_ADAPTER_REGISTRY` or the function that constructs the list of adapters for fan-out). Add the conv adapter.

The query.py uses `_build_adapters(cfg)` (or similar) to assemble the list. Add at the end of that list:

```python
    from .adapters.conv_read import ConvReadAdapter
    from .conv.owner import OwnerRegistry
    cfg.conv_root.mkdir(parents=True, exist_ok=True)
    owner_registry = OwnerRegistry(
        owners_db_path=cfg.conv_owners_db_path,
        principals_json=cfg.principals_json_path,
    )
    adapters.append(ConvReadAdapter(
        registry=owner_registry,
        conv_root=cfg.conv_root,
    ))
```

(The exact location depends on the existing structure. Look for the function that returns the list of 10 adapters and append the 11th in the same place.)

- [ ] **Step 4: Run integration smoke**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -c "from flyn_memory_router.server import build_app; app = build_app(); print('app built ok'); routes = [r.path for r in app.routes]; print('routes:', routes)"
```

Expected: `app built ok`, routes include `/api/memory/ingest`, `/api/memory/query`, etc.

Then run the full unit suite to confirm no regressions:

```bash
python3 -m pytest tests/unit -v 2>&1 | tail -10
```

Expected: all unit tests still pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/server.py \
        deploy/memory-router/flyn_memory_router/query.py
git commit -m "feat(memory-router): wire conv adapters into server + query orchestrator"
```

---

## Phase 7 — Integration tests (Task 11)

### Task 11: End-to-end ingest → search via FastAPI TestClient

**Files:**
- Create: `deploy/memory-router/tests/integration/test_conv_ingest_roundtrip.py`

- [ ] **Step 1: Write the integration tests**

```python
# deploy/memory-router/tests/integration/test_conv_ingest_roundtrip.py
"""POST /api/memory/ingest with conversation_message → conv.db roundtrip."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Standard test env: tmp dirs + stubbed Keychain + seeded principals."""
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("FLYN_CONV_ROOT", str(tmp_path / "conv"))
    monkeypatch.setenv("FLYN_GRAPHITI_URL", "http://localhost:9999")  # unreachable; fire-and-forget swallows

    # Seed principals.json
    (tmp_path / "conv").mkdir(parents=True, exist_ok=True)
    (tmp_path / "conv" / "principals.json").write_text(json.dumps({
        "owners": [{"id": "ryan", "display_name": "Ryan",
                    "principals": {"telegram": "7191564227"}}]
    }))

    # Stub Keychain
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)

    return tmp_path


@pytest.fixture
def client(test_env):
    from flyn_memory_router.server import build_app
    return TestClient(build_app())


def _payload(text: str, msg_id: int = 100):
    return {
        "source": "telegram",
        "event_type": "conversation_message",
        "subject": f"tg-7191564227-{msg_id}",
        "body": text,
        "importance": "warm",
        "raw_payload": {
            "channel": "telegram",
            "chat_id": 7191564227,
            "sender_id": 7191564227,
            "thread_id": 7191564227,
            "reply_to_msg_id": None,
            "attachments": [],
            "ts": "2026-05-19T18:00:00+00:00",
        },
        "dedup_key": f"tg-7191564227-{msg_id}",
    }


def test_ingest_conv_message_writes_to_db(client, test_env):
    """POST → 200 → row exists in ryan.db."""
    resp = client.post("/api/memory/ingest", json=_payload("Linear backlog at 73 of 124"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert "conv" in body["tiers_written"]

    # Verify db row
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb("ryan", test_env / "conv" / "ryan.db")
    hits = db.search("Linear")
    assert len(hits) == 1
    assert "73 of 124" in hits[0].body


def test_query_returns_conv_hit(client, test_env):
    """After ingest, /api/memory/query includes conv hits via conv_read."""
    client.post("/api/memory/ingest", json=_payload("Pearl Platform launch this week"))

    resp = client.post("/api/memory/query", json={"q": "Pearl Platform", "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    conv_hits = [h for h in body["hits"] if h["source"].startswith("conv/")]
    assert len(conv_hits) >= 1
    assert "Pearl" in conv_hits[0]["text"]


def test_unknown_sender_returns_accepted_false(client, test_env):
    """Telegram message from unmapped sender: 200 OK, accepted=False, tiers_written=[]."""
    payload = _payload("hello")
    payload["raw_payload"]["sender_id"] = 999999999  # unmapped
    resp = client.post("/api/memory/ingest", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is False
    assert body["tiers_written"] == []
    assert any("unknown sender" in note for note in body["notes"])
```

- [ ] **Step 2: Run integration tests, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/integration/test_conv_ingest_roundtrip.py -v
```

Expected: 3 tests pass.

- [ ] **Step 3: Run full repo test suite to catch regressions**

```bash
python3 -m pytest tests/unit tests/integration 2>&1 | tail -3
```

Expected: all tests pass. Note any flakes (e.g. watchdog dispatcher flake from prior work) and confirm they're pre-existing.

- [ ] **Step 4: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/tests/integration/test_conv_ingest_roundtrip.py
git commit -m "test(memory-router): integration roundtrip for conv ingest + query"
```

---

## Phase 8 — CLI surface (Task 12)

### Task 12: `flyn-mem conv` subcommand cluster

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/cli.py`
- Create: `deploy/memory-router/tests/unit/test_conv_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
# deploy/memory-router/tests/unit/test_conv_cli.py
"""flyn-mem conv subcommand cluster: health, search, replay."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_CONV_ROOT", str(tmp_path / "conv"))
    (tmp_path / "conv").mkdir(parents=True)
    (tmp_path / "conv" / "principals.json").write_text(json.dumps({
        "owners": [{"id": "ryan", "display_name": "Ryan",
                    "principals": {"telegram": "7191564227"}}]
    }))
    # Stub Keychain so replay doesn't try to talk to security CLI
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)
    return tmp_path


def _seed_row(env_path: Path):
    """Insert a row directly so health/search/replay have something to find."""
    from flyn_memory_router.conv.schema import ConvDb, ConvMessage
    from flyn_memory_router.conv import encrypted_raw
    db = ConvDb("ryan", env_path / "conv" / "ryan.db")
    sealed = encrypted_raw.seal(
        json.dumps({"channel": "telegram", "text": "Linear backlog"}).encode(),
        "ryan",
    )
    db.write(ConvMessage(
        channel="telegram", sender_id="7191564327",
        thread_id="t1", reply_to_id=None,
        ts="2026-05-19T18:00:00+00:00",
        body="Linear backlog at 73 of 124",
        attachments=[],
        encrypted_raw=sealed,
    ))


def test_health_prints_per_owner_stats(env, capsys):
    """flyn-mem conv health prints row count for each owner."""
    _seed_row(env)
    from flyn_memory_router.cli import main
    rc = main(["conv", "health"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ryan" in out
    assert "1" in out  # row count


def test_search_finds_seeded_row(env, capsys):
    _seed_row(env)
    from flyn_memory_router.cli import main
    rc = main(["conv", "search", "linear backlog"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "73 of 124" in out


def test_replay_decrypts_and_prints(env, capsys):
    """replay <id> calls unseal and prints the JSON."""
    _seed_row(env)
    from flyn_memory_router.cli import main
    rc = main(["conv", "replay", "1", "--owner", "ryan"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Linear backlog" in out
    # Confirm audit row was written
    from flyn_memory_router.conv.owner import OwnerRegistry
    from flyn_memory_router.config import Config
    cfg = Config.from_env()
    registry = OwnerRegistry(cfg.conv_owners_db_path, cfg.principals_json_path)
    audit = registry.recent_audit()
    assert any(r["op"] == "replay" for r in audit)
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit/test_conv_cli.py -v
```

- [ ] **Step 3: Implement `conv` subcommand cluster in `cli.py`**

Open `deploy/memory-router/flyn_memory_router/cli.py`. Add a new subparser cluster.

Find the `build_parser()` function (or wherever subparsers are configured) and add after the existing `logs` parser:

```python
    # conv subcommand cluster
    conv_p = sub.add_parser("conv", help="Conversation tier (Telegram messages)")
    conv_sub = conv_p.add_subparsers(dest="conv_cmd", required=True)

    conv_sub.add_parser("health", help="Per-owner DB stats")

    s = conv_sub.add_parser("search", help="FTS5 search in conv DBs")
    s.add_argument("q", help="search text")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--owner", default=None)

    t = conv_sub.add_parser("thread", help="Dump a thread's recent messages")
    t.add_argument("thread_id")
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--owner", default=None)

    r = conv_sub.add_parser("replay", help="Decrypt + print raw payload (audit-logged)")
    r.add_argument("row_id", type=int)
    r.add_argument("--owner", default=None)
```

Then in `main()`, after the existing dispatch, add:

```python
    if args.cmd == "conv":
        return _cmd_conv(args)
```

Add the `_cmd_conv` dispatcher function:

```python
def _cmd_conv(args) -> int:
    """Dispatch `flyn-mem conv <subcmd>`."""
    import json
    import os
    from .config import Config
    from .conv.owner import OwnerRegistry
    from .conv.schema import ConvDb
    from .conv import encrypted_raw

    cfg = Config.from_env()
    registry = OwnerRegistry(cfg.conv_owners_db_path, cfg.principals_json_path)
    viewer = os.environ.get("USER", "ryan")

    if args.conv_cmd == "health":
        return _conv_health(cfg, registry)
    if args.conv_cmd == "search":
        return _conv_search(cfg, registry, viewer, args)
    if args.conv_cmd == "thread":
        return _conv_thread(cfg, registry, viewer, args)
    if args.conv_cmd == "replay":
        return _conv_replay(cfg, registry, viewer, args, encrypted_raw)
    print(f"unknown conv subcommand: {args.conv_cmd}", file=sys.stderr)
    return 2


def _conv_health(cfg, registry) -> int:
    print(f"{'owner':<12} {'messages':<10} {'oldest_ts':<22} {'newest_ts':<22} {'backlog':<8}")
    for owner_id in sorted(registry.list_accessible_owners(os.environ.get("USER", "ryan"))):
        db_path = registry.db_path_for(owner_id, cfg.conv_root)
        if not db_path.exists():
            print(f"{owner_id:<12} {'0':<10} {'-':<22} {'-':<22} {'-':<8}")
            continue
        from .conv.schema import ConvDb
        stats = ConvDb(owner_id, db_path).stats()
        print(f"{owner_id:<12} {stats['messages']:<10} {stats['oldest_ts'] or '-':<22} "
              f"{stats['newest_ts'] or '-':<22} {stats['summary_backlog']:<8}")
    return 0


def _conv_search(cfg, registry, viewer, args) -> int:
    from .conv.schema import ConvDb
    owners = [args.owner] if args.owner else sorted(registry.list_accessible_owners(viewer))
    n = 0
    for owner_id in owners:
        db_path = registry.db_path_for(owner_id, cfg.conv_root)
        if not db_path.exists():
            continue
        for hit in ConvDb(owner_id, db_path).search(args.q, top_k=args.top):
            n += 1
            print(f"\n┌ {hit.ts} · {hit.sender_id} · {owner_id} · row {hit.row_id}")
            print(f"│   {hit.body[:300]}")
            if hit.summary:
                print(f"└ summary: {hit.summary}")
            else:
                print(f"└ summary: (pending)")
        if owner_id != viewer:
            registry.append_audit(viewer, owner_id, op="read", q=args.q)
    print(f"\n{n} hits")
    return 0


def _conv_thread(cfg, registry, viewer, args) -> int:
    from .conv.schema import ConvDb
    owners = [args.owner] if args.owner else sorted(registry.list_accessible_owners(viewer))
    for owner_id in owners:
        db_path = registry.db_path_for(owner_id, cfg.conv_root)
        if not db_path.exists():
            continue
        for msg in ConvDb(owner_id, db_path).get_by_thread(args.thread_id, limit=args.limit):
            print(f"{msg.ts}  {msg.sender_id}: {msg.body[:200]}")
    return 0


def _conv_replay(cfg, registry, viewer, args, encrypted_raw) -> int:
    owner = args.owner or viewer
    if not registry.viewer_can_read(viewer, owner):
        print(f"flyn-mem: viewer {viewer!r} lacks grant to read owner {owner!r}", file=sys.stderr)
        return 3
    from .conv.schema import ConvDb
    db_path = registry.db_path_for(owner, cfg.conv_root)
    if not db_path.exists():
        print(f"flyn-mem: no DB for owner {owner!r}", file=sys.stderr)
        return 1
    msg = ConvDb(owner, db_path).get_by_id(args.row_id)
    if msg is None:
        print(f"flyn-mem: no row {args.row_id} in owner {owner!r}", file=sys.stderr)
        return 1
    try:
        plaintext = encrypted_raw.unseal(msg.encrypted_raw, owner)
    except Exception as exc:
        print(f"flyn-mem: unseal failed: {exc}", file=sys.stderr)
        return 1
    registry.append_audit(viewer, owner, op="replay", q=str(args.row_id))
    print(plaintext.decode("utf-8", errors="replace"))
    return 0
```

Add `import sys` and `import os` at the top of `cli.py` if not already imported.

- [ ] **Step 4: Run tests, expect PASS**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
pip install -e . --quiet  # re-link the entry point
python3 -m pytest tests/unit/test_conv_cli.py -v
```

Expected: 3 tests pass.

Also verify the entry point shows the new subcommand:

```bash
flyn-mem conv --help 2>&1 | head -10
```

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/cli.py \
        deploy/memory-router/tests/unit/test_conv_cli.py
git commit -m "feat(memory-router): flyn-mem conv subcommand cluster (health/search/thread/replay)"
```

---

## Phase 9 — Install + hook + pulse (Task 13)

### Task 13: install.sh extensions + OpenClaw hook + summarizer pulse

**Files:**
- Modify: `deploy/memory-router/install.sh`
- Create: `deploy/hooks/flyn-conv-memory-tap.sh`
- Create: `deploy/pulses/conv_summarize_backfill.sh`
- Create: `deploy/pulses/ai.flyn.pulse.conv-summarize-backfill.plist`

- [ ] **Step 1: Extend `install.sh`**

Open `deploy/memory-router/install.sh` and append (before the final success message):

```bash

# --- Conversation tier (Telegram slice 1) ---
CONV_ROOT="${FLYN_CONV_ROOT:-$HOME/.flyn/memory-router/conv}"
mkdir -p "$CONV_ROOT"
echo "  ✓ conv root at $CONV_ROOT"

# Seed principals.json with the current user as Ryan if missing
if [ ! -f "$CONV_ROOT/principals.json" ]; then
  cat > "$CONV_ROOT/principals.json" <<JSON
{
  "owners": [
    {
      "id": "ryan",
      "display_name": "Ryan Shuken",
      "principals": {
        "telegram": "7191564227"
      }
    }
  ]
}
JSON
  echo "  ✓ seeded conv principals.json (edit to add Beth/Eric/etc later)"
fi

# Install the OpenClaw hook script
HOOK_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/hooks/flyn-conv-memory-tap.sh"
if [ -f "$HOOK_SRC" ]; then
  HOOK_DST="$HOME/.openclaw/hooks/flyn-conv-memory-tap.sh"
  mkdir -p "$(dirname "$HOOK_DST")"
  install -m 755 "$HOOK_SRC" "$HOOK_DST"
  echo "  ✓ openclaw hook installed at $HOOK_DST"
  echo "    NOTE: register this hook in ~/.openclaw/openclaw.json under hooks.internal.entries"
fi
```

- [ ] **Step 2: Create the OpenClaw hook script**

```bash
mkdir -p /Users/4c/AI/openclaw/flyn-agent/deploy/hooks
```

Create `deploy/hooks/flyn-conv-memory-tap.sh`:

```bash
#!/usr/bin/env bash
# OpenClaw internal hook: forwards inbound Telegram messages to memory-router.
#
# Triggered on inbound messages. Reads the message JSON on stdin, builds a
# conversation_message InboundEvent payload, POSTs to localhost:8400. If the
# memory-router is down or returns non-200, logs to /tmp/flyn-conv-memory-tap.log
# and returns 0 — never blocks openclaw's message processing.

set -uo pipefail

LOG=/tmp/flyn-conv-memory-tap.log
ROUTER_URL="${FLYN_MEMORY_ROUTER_URL:-http://localhost:8400}"

# Read message JSON from stdin
read -r MSG_JSON

# Extract fields with python (jq may not be on PATH from openclaw's launchd context)
PAYLOAD=$(python3 -c "
import json, sys, datetime
m = json.loads('''$MSG_JSON''')
channel = m.get('channel', 'telegram')
chat_id = m.get('chat_id') or m.get('chat', {}).get('id', 0)
sender_id = m.get('sender_id') or m.get('from', {}).get('id', 0)
msg_id = m.get('message_id') or m.get('id', 0)
text = m.get('text') or m.get('body', '')
ts = m.get('ts') or datetime.datetime.now(datetime.timezone.utc).isoformat()
out = {
  'source': 'telegram',
  'event_type': 'conversation_message',
  'subject': f'tg-{chat_id}-{msg_id}',
  'body': text,
  'importance': 'warm',
  'raw_payload': {
    'channel': channel,
    'chat_id': chat_id,
    'sender_id': sender_id,
    'thread_id': chat_id,
    'reply_to_msg_id': m.get('reply_to_message', {}).get('message_id'),
    'attachments': m.get('attachments', []),
    'ts': ts,
  },
  'dedup_key': f'tg-{chat_id}-{msg_id}',
}
print(json.dumps(out))
")

curl -sS -m 3 -X POST "$ROUTER_URL/api/memory/ingest" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" \
  > /tmp/.flyn-conv-tap-last 2>>"$LOG" \
  || echo "$(date -Iseconds) tap: POST failed (router down?)" >> "$LOG"

exit 0
```

Make it executable:

```bash
chmod +x /Users/4c/AI/openclaw/flyn-agent/deploy/hooks/flyn-conv-memory-tap.sh
```

- [ ] **Step 3: Create the summarizer-backfill pulse**

Create `deploy/pulses/conv_summarize_backfill.sh`:

```bash
#!/usr/bin/env bash
# Daily backfill for conversation summaries that didn't land.
#
# Scans each per-owner conv.db for rows with summary IS NULL AND ts < now()-1h
# and re-enqueues a summarize-job. The summarizer worker picks them up on the
# next poll cycle.
set -uo pipefail

LOG_PREFIX="$(date -Iseconds) conv-summarize-backfill:"
echo "$LOG_PREFIX start"

CONV_ROOT="${FLYN_CONV_ROOT:-$HOME/.flyn/memory-router/conv}"
QUEUE_DIR="${FLYN_MEMORY_ROUTER_HOME:-$HOME/.flyn/memory-router}/queue/conv-summarize"
mkdir -p "$QUEUE_DIR"

if [ ! -d "$CONV_ROOT" ]; then
  echo "$LOG_PREFIX no conv root — skipping"
  exit 0
fi

# Find per-owner DBs (excluding owners.db)
for db in "$CONV_ROOT"/*.db; do
  [ -f "$db" ] || continue
  owner=$(basename "$db" .db)
  [ "$owner" = "owners" ] && continue

  python3 -c "
import sqlite3, json, time
from pathlib import Path
queue_dir = Path('$QUEUE_DIR')
queue_dir.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect('$db')
conn.row_factory = sqlite3.Row
cutoff = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - 3600))
rows = conn.execute(
    'SELECT id, body, sender_id FROM messages WHERE summary IS NULL AND ts < ?',
    (cutoff,)
).fetchall()
print(f'  $owner: {len(rows)} pending')
for r in rows:
    job_path = queue_dir / f'conv-summarize-$owner-{r[\"id\"]}.json'
    job_path.write_text(json.dumps({
        'owner_id': '$owner', 'db_path': '$db', 'row_id': r['id'],
        'body': r['body'], 'sender_id': r['sender_id'],
    }))
"
done

echo "$LOG_PREFIX done"
```

Make it executable:

```bash
chmod +x /Users/4c/AI/openclaw/flyn-agent/deploy/pulses/conv_summarize_backfill.sh
```

Create the plist `deploy/pulses/ai.flyn.pulse.conv-summarize-backfill.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>           <string>ai.flyn.pulse.conv-summarize-backfill</string>
    <key>ProgramArguments</key>
    <array>
      <string>{{HOME}}/AI/openclaw/flyn-agent/deploy/pulses/conv_summarize_backfill.sh</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
      <key>HOME</key> <string>{{HOME}}</string>
      <key>PATH</key> <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <!-- Daily at 04:15. Catches summaries that fell through during the day. -->
    <key>StartCalendarInterval</key>
    <dict>
      <key>Hour</key>   <integer>4</integer>
      <key>Minute</key> <integer>15</integer>
    </dict>
    <key>StandardOutPath</key> <string>/tmp/flyn-conv-summarize-backfill.log</string>
    <key>StandardErrorPath</key><string>/tmp/flyn-conv-summarize-backfill.log</string>
  </dict>
</plist>
```

- [ ] **Step 4: Smoke-test the install + hook + pulse**

```bash
# Re-run install.sh; should be idempotent
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
bash install.sh 2>&1 | tail -20

# Verify hook is in place
ls -la ~/.openclaw/hooks/flyn-conv-memory-tap.sh 2>&1
test -x ~/.openclaw/hooks/flyn-conv-memory-tap.sh && echo "executable"

# Smoke the backfill pulse against a fresh tmp env
FLYN_CONV_ROOT=/tmp/test-conv-bf \
FLYN_MEMORY_ROUTER_HOME=/tmp/test-router-bf \
bash /Users/4c/AI/openclaw/flyn-agent/deploy/pulses/conv_summarize_backfill.sh
```

Expected: install completes; hook is executable; pulse runs and exits 0 (no DBs found, no work to do).

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/install.sh \
        deploy/hooks/flyn-conv-memory-tap.sh \
        deploy/pulses/conv_summarize_backfill.sh \
        deploy/pulses/ai.flyn.pulse.conv-summarize-backfill.plist
git commit -m "feat(memory-router): install + openclaw hook + summarizer backfill pulse"
```

---

## Phase 10 — Ship-gate playbook + rubric (Task 14)

### Task 14: Ship-gate doc + outcomes rubric

**Files:**
- Create: `deploy/memory-router/tests/e2e/test_conv_memory_slice_1_ship_gate.md`
- Create: `deploy/memory-router/tests/smoke/test_conv_live_telegram.py`
- Create: `deploy/outcomes/CONV-MEMORY-SLICE-1-RUBRIC.md`

- [ ] **Step 1: Write the live smoke test**

```python
# deploy/memory-router/tests/smoke/test_conv_live_telegram.py
"""LIVE smoke test for conversation memory.

Run manually after install + service restart:
    cd deploy/memory-router && python3 -m pytest tests/smoke/test_conv_live_telegram.py -v -s

Skips if memory-router is not running on :8400.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

BASE = "http://localhost:8400"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        r = c.get("/api/health")
        if r.status_code != 200:
            pytest.skip("memory-router not running on :8400")
        yield c


def test_conv_sources_appears(client):
    """conv adapter shows up in /api/memory/sources."""
    r = client.get("/api/memory/sources")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert "conv" in names


def test_conv_health_endpoint_or_cli(client):
    """At minimum, the conv subcommand of flyn-mem responds."""
    import subprocess
    proc = subprocess.run(
        ["flyn-mem", "conv", "health"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0 and "not found" in proc.stderr.lower():
        pytest.skip("flyn-mem CLI not installed; run install.sh first")
    print("\nflyn-mem conv health output:\n", proc.stdout)
    assert proc.returncode == 0


def test_conv_search_after_real_message(client):
    """
    MANUAL STEP REQUIRED:
    Send a message to @flyn_4c_bot containing the unique text below.
    Then this test verifies the row landed.

    Skipped automatically unless FLYN_CONV_LIVE_TEST=1 is set.
    """
    if os.environ.get("FLYN_CONV_LIVE_TEST") != "1":
        pytest.skip("Set FLYN_CONV_LIVE_TEST=1 and send a real Telegram message first.")
    unique = os.environ.get("FLYN_CONV_LIVE_TEXT", "FLYN_SMOKE_TOKEN_12345")
    r = client.post("/api/memory/query", json={"q": unique, "top_k": 3})
    body = r.json()
    conv_hits = [h for h in body["hits"] if h["source"].startswith("conv/")]
    assert conv_hits, f"no conv hits found for {unique!r}"
```

- [ ] **Step 2: Write the ship-gate playbook**

```markdown
# Conversation Memory Slice 1 — Ship-Gate Playbook

**Verify all checks pass before declaring CM-01 done.**

## Prereqs

- memory-router on `:8400` running latest code (`bash deploy/memory-router/install.sh && launchctl kickstart -k gui/$(id -u)/ai.flyn.memory-router`)
- `flyn-mem` CLI on PATH (`which flyn-mem` returns a binary)
- `principals.json` exists with `ryan` mapped to your Telegram sender_id
- macOS Keychain unlocked (you're logged in)
- Telegram bot `@flyn_4c_bot` online (`openclaw health` shows Telegram configured)
- Ollama running with `gemma4:e4b` (`curl -s :11434/api/tags | grep gemma4`)

## Procedure A — Sources registry

### Step 1: conv adapter visible

```bash
flyn-mem sources | grep conv
```

Expected: a row showing the conv adapter with `default_included=True`.

## Procedure B — Live ingest roundtrip

### Step 2: send a real Telegram message

From your phone, send to `@flyn_4c_bot`:

> FLYN_SHIP_GATE_12345 testing slice 1

### Step 3: verify it landed in conv.db within 10s

```bash
sleep 10
flyn-mem conv search "FLYN_SHIP_GATE_12345"
```

Expected: one hit with that body text, sender_id matching your Telegram id.

### Step 4: verify summary fills in within 30s

```bash
sleep 20
flyn-mem conv health
```

Expected: `summary_backlog` should be 0 (or very low) for owner `ryan`.

Run again:

```bash
flyn-mem conv search "FLYN_SHIP_GATE_12345"
```

Expected: the hit's "summary:" line now shows a 1-2 sentence summary (not "pending").

### Step 5: verify the hit appears in cross-system query

```bash
flyn-mem query "FLYN_SHIP_GATE_12345" --top 5
```

Expected: at least one hit with `source: conv/telegram` and the right `metadata.msg_id`.

## Procedure C — Replay + audit

### Step 6: replay decrypts the original payload

Get the row_id from Step 3's search output, then:

```bash
flyn-mem conv replay <row_id> --owner ryan
```

Expected: prints the original Telegram JSON payload (channel, chat_id, sender_id, message_id, text, etc.).

### Step 7: audit log captured the replay

```bash
sqlite3 ~/.flyn/memory-router/conv/owners.db \
  "SELECT ts, viewer, owned_by, op, q FROM audit_log ORDER BY id DESC LIMIT 5"
```

Expected: a row with `op = 'replay'`, `viewer = '$USER'`, `owned_by = 'ryan'`, `q = '<row_id>'`.

## Procedure D — Graphiti promotion

### Step 8: verify episode exists in Graphiti

```bash
curl -s "http://localhost:8100/api/episodes?group_id=flyn-ryan" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('episodes for flyn-ryan:', len(d.get('results', [])))"
```

Expected: at least 1 episode (or however many test messages you've sent so far).

## Sign-off

- [ ] Step 1: conv adapter in sources registry
- [ ] Step 2: real Telegram message sent
- [ ] Step 3: conv search finds the message within 10s
- [ ] Step 4: summary fills in within 30s
- [ ] Step 5: cross-system query includes the conv hit
- [ ] Step 6: replay decrypts the original payload
- [ ] Step 7: audit log captured the replay
- [ ] Step 8: Graphiti episode exists for the message

Date: ____________  Ryan: ____________

## What this proves

If all 8 steps pass, CM-01 is shipped per spec:
- Inbound Telegram → conv.db within seconds
- Async summarizer fills in summaries
- Per-owner Keychain encryption + audit-logged replay path
- Cross-source retrieval via the existing flyn-mem query surface
- Graphiti entity layer promoted for every message

## Deferred to slice 2 (not blocking)

- WhatsApp / iMessage / email connectors
- Outbound message mirroring (Flyn's replies)
- Cross-channel thread join
- Conversation → wiki auto-promotion
- Embedding-based semantic search
```

- [ ] **Step 3: Write the outcomes rubric**

```markdown
# Conversation Memory Slice 1 — Rubric

Machine-gradable success criteria. Run via:

```
deploy/outcomes/outcomes_runner.py score \
  --rubric deploy/outcomes/CONV-MEMORY-SLICE-1-RUBRIC.md \
  --checklist
```

## Types & schema

- [ ] `flyn_memory_router.types.Tier.CONV.value == "conv"`
- [ ] `Importance` Literal includes `"conv"`
- [ ] `flyn_memory_router.conv.schema.ConvMessage` exists with required fields
- [ ] `flyn_memory_router.conv.schema.ConvDb` provides write/search/update_summary/get_by_thread/get_by_id/stats
- [ ] `flyn_memory_router.conv.schema.StoredMessage` exists with fts_score

## Owner registry

- [ ] `flyn_memory_router.conv.owner.OwnerRegistry` exists
- [ ] resolve_from_chat returns Owner for seeded principal
- [ ] viewer_can_read returns True for viewer == owner
- [ ] viewer_can_read returns False for unrelated viewer (default-deny)
- [ ] grant() persists a row; subsequent viewer_can_read returns True
- [ ] append_audit writes to audit_log
- [ ] Schema: owners, grants, audit_log tables exist

## Encryption

- [ ] `conv.encrypted_raw.seal/unseal` round-trip works with stubbed key
- [ ] `KeychainLocked` raised when subprocess fails
- [ ] Tamper of ciphertext byte raises InvalidTag on unseal
- [ ] Per-owner key isolation: sealing with owner A's key can't be unsealed with owner B's

## Summarizer

- [ ] `conv.summarizer.SummarizerWorker` exists with start/stop methods
- [ ] `SummarizeJob` dataclass + `enqueue` function exist
- [ ] Worker polls disk queue; success deletes the file
- [ ] Worker timeout failure leaves the file in place for retry

## Adapters

- [ ] `adapters/conv_write.py:ConvWriteAdapter` implements MemoryAdapter Protocol
- [ ] Happy path returns WriteResult(ok=True) with row_id in detail
- [ ] Unknown sender returns WriteResult(ok=False, detail contains "unknown sender")
- [ ] KeychainLocked returns WriteResult(ok=False, detail contains "keychain")
- [ ] `adapters/conv_read.py:ConvReadAdapter` implements ReadAdapter Protocol
- [ ] name="conv", read_timeout=1.5, default_included=True
- [ ] Cross-owner read writes audit row

## Server wiring

- [ ] `/api/memory/ingest` branches on `event_type="conversation_message"`
- [ ] Branch returns `tiers_written=["conv"]` on success
- [ ] conv_write is registered under Tier.CONV in build_app
- [ ] conv_read is registered in query.py's adapter list

## CLI

- [ ] `flyn-mem conv health` prints per-owner stats table
- [ ] `flyn-mem conv search <q>` prints hits with body + summary status
- [ ] `flyn-mem conv thread <id>` prints messages in thread
- [ ] `flyn-mem conv replay <id>` decrypts and prints original payload
- [ ] replay writes an audit row with op="replay"
- [ ] replay without grant returns non-zero exit code

## Install + pulse + hook

- [ ] `install.sh` creates conv_root dir
- [ ] `install.sh` seeds principals.json if missing
- [ ] `install.sh` installs flyn-conv-memory-tap.sh into ~/.openclaw/hooks/
- [ ] `deploy/pulses/conv_summarize_backfill.sh` is executable
- [ ] Backfill pulse no-ops when no DBs exist
- [ ] plist is valid XML and registers the daily 04:15 schedule

## Live smoke (manual; only graded with --smoke)

- [ ] Real Telegram message lands in ryan.db within 10s
- [ ] Summary fills in within 30s
- [ ] `flyn-mem query` returns the conv hit cross-source
- [ ] `flyn-mem conv replay` decrypts the original payload
- [ ] Audit log contains the replay row
- [ ] Graphiti has an episode for the message under group_id=flyn-ryan

## Soft commitments

- [ ] No new launchd unit added (one new pulse only)
- [ ] No new port
- [ ] File size caps: each new file ≤ 200 lines
- [ ] All commits follow feat(memory-router): / test(memory-router): prefix
- [ ] 18 unit + integration tests + 1 smoke + 1 ship-gate doc
```

Create the smoke directory if it doesn't exist:

```bash
mkdir -p /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router/tests/smoke
mkdir -p /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router/tests/e2e
```

- [ ] **Step 4: Verify smoke test collects (skips without live service)**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/smoke/test_conv_live_telegram.py --collect-only
```

Expected: 3 tests collected. (Don't run them — they need the live service.)

Verify the rubric parses:

```bash
python3 deploy/outcomes/outcomes_runner.py score \
  --rubric deploy/outcomes/CONV-MEMORY-SLICE-1-RUBRIC.md \
  --checklist | head -10
```

Expected: JSON with counts (all todo since no code has been graded yet).

- [ ] **Step 5: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/tests/e2e/test_conv_memory_slice_1_ship_gate.md \
        deploy/memory-router/tests/smoke/test_conv_live_telegram.py \
        deploy/outcomes/CONV-MEMORY-SLICE-1-RUBRIC.md
git commit -m "test(memory-router): ship-gate playbook + smoke + rubric for conv slice 1"
```

---

## Phase 11 — Discovery + cross-agent reach (Task 15)

### Task 15: discovery.py extensions for auto-memory pointer

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/discovery.py`

- [ ] **Step 1: Extend discovery.py with conv auto-memory pointer**

Open `flyn_memory_router/discovery.py`. The file already has functions like `write_auto_memory_pointer` for the memory-router CLI. Add new constants + function:

```python
# Append after the existing AUTO_MEMORY_BODY constant

CONV_AUTO_MEMORY_FILE = "feedback_conv_memory.md"

CONV_AUTO_MEMORY_BODY = """---
name: conversation-memory
description: Flyn captures every Telegram message into a per-owner SQLite DB at ~/.flyn/memory-router/conv/. Searchable via flyn-mem conv. Encrypted raw payload via Keychain.
metadata:
  type: reference
---
For "what did Beth say last week" / "when did we discuss X" / "what was the decision on Y" questions,
prefer `flyn-mem conv search "<text>"` (FTS5 over body + summary) over generic grep.

For the exact original message text (un-redacted, decrypted from Keychain):
  flyn-mem conv replay <row_id> --owner ryan   # audit-logged

Per-owner DBs:
  ~/.flyn/memory-router/conv/ryan.db           # your messages
  ~/.flyn/memory-router/conv/owners.db         # shared: owners, grants, audit

Other useful commands:
  flyn-mem conv health                          # per-owner stats + summary backlog
  flyn-mem conv thread <thread_id>              # dump a single thread
  flyn-mem query "<q>" --include conv           # conv-only query
"""

CONV_MEMORY_MD_INDEX_LINE = "- [conversation memory](feedback_conv_memory.md) — flyn-mem conv search; per-owner SQLite under ~/.flyn/memory-router/conv/\n"


def write_conv_auto_memory_pointer(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / CONV_AUTO_MEMORY_FILE
    if not target.exists():
        target.write_text(CONV_AUTO_MEMORY_BODY)


def append_conv_memory_md_index(memory_dir: Path) -> None:
    idx = memory_dir / "MEMORY.md"
    if not idx.exists():
        idx.write_text(CONV_MEMORY_MD_INDEX_LINE)
        return
    text = idx.read_text()
    if CONV_AUTO_MEMORY_FILE in text:
        return
    with idx.open("a") as f:
        f.write(CONV_MEMORY_MD_INDEX_LINE)
```

- [ ] **Step 2: Wire into `install.sh`**

In `deploy/memory-router/install.sh`, find the existing Python block that calls `write_auto_memory_pointer` and `append_memory_md_index`. Add the conv equivalents:

```bash
"$VENV/bin/python" - <<'PYEOF'
from pathlib import Path
import os
from flyn_memory_router.discovery import (
    write_auto_memory_pointer, append_memory_md_index, append_tools_md,
    write_conv_auto_memory_pointer, append_conv_memory_md_index,
)

automem = Path(os.environ.get("FLYN_AUTO_MEMORY_DIR",
                              str(Path.home() / ".claude" / "projects" /
                                  "-Users-4c-AI" / "memory")))
workspace = Path(os.environ.get("FLYN_WORKSPACE",
                                str(Path.home() / ".openclaw" / "workspace")))

write_auto_memory_pointer(automem)
append_memory_md_index(automem)
append_tools_md(workspace)

# Conversation memory pointer (slice 1)
write_conv_auto_memory_pointer(automem)
append_conv_memory_md_index(automem)

print(f"  ✓ conv auto-memory pointer at {automem}/feedback_conv_memory.md")
PYEOF
```

(If install.sh already has a Python block like this, just add the two new calls + the two new imports.)

- [ ] **Step 3: Smoke test in tmp env**

```bash
FLYN_AUTO_MEMORY_DIR=/tmp/test-conv-discovery \
python3 -c "
from flyn_memory_router.discovery import write_conv_auto_memory_pointer, append_conv_memory_md_index
from pathlib import Path
p = Path('/tmp/test-conv-discovery')
write_conv_auto_memory_pointer(p)
append_conv_memory_md_index(p)
print('files:', sorted(x.name for x in p.iterdir()))
print('---')
print((p / 'MEMORY.md').read_text())
"
```

Expected: prints the two files and the MEMORY.md content includes the conv line.

- [ ] **Step 4: Commit**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git add deploy/memory-router/flyn_memory_router/discovery.py \
        deploy/memory-router/install.sh
git commit -m "feat(memory-router): discovery — conv auto-memory pointer + index line"
```

---

## Phase 12 — Final integration + PR (Task 16)

### Task 16: Full suite green + push + open PR

**Files:** none modified — verification + PR open.

- [ ] **Step 1: Full test suite**

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/memory-router
python3 -m pytest tests/unit tests/integration -v 2>&1 | tail -15
```

Expected: all tests pass (existing + 18 new). Note any flakes; confirm they're pre-existing.

- [ ] **Step 2: Reinstall the service locally + run install.sh**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
bash deploy/memory-router/install.sh 2>&1 | tail -15
launchctl kickstart -k gui/$(id -u)/ai.flyn.memory-router
sleep 3
curl -sS http://localhost:8400/api/health
```

Expected: `{"ok":true,...}`.

```bash
flyn-mem sources | grep conv
flyn-mem conv health
```

Expected: conv adapter listed; conv health prints (empty if no messages yet).

- [ ] **Step 3: Push branch + open PR (do NOT auto-merge)**

```bash
cd /Users/4c/AI/openclaw/flyn-agent
git push -u origin feat/conv-memory-telegram-slice-1
gh pr create --base main --head feat/conv-memory-telegram-slice-1 \
  --title "feat(memory-router): conversation memory — Telegram slice 1" \
  --body "$(cat <<'EOF'
## Summary

Adds a new conversation tier inside memory-router that captures every Telegram message Flyn sees, stores it in a per-owner SQLite database with a Keychain-encrypted raw payload and an asynchronously-generated summary, promotes it to Graphiti, and surfaces it through a new 11th read adapter.

## What changes

- New `flyn_memory_router/conv/` module: schema (ConvDb + FTS5), owner (registry + grants + audit), encrypted_raw (AES-GCM via Keychain), summarizer (background worker + Ollama)
- New `adapters/conv_write.py` and `adapters/conv_read.py` plug into existing Protocols
- `Tier.CONV` enum value + `Importance` extension
- `Config` adds `conv_root`, `principals_json_path`, `conv_owners_db_path`
- `server.py` branches `/api/memory/ingest` on `event_type == "conversation_message"`
- `query.py` registers conv_read as 11th read adapter (fans into existing RRF)
- `flyn-mem conv` subcommand cluster: health, search, thread, replay, grant, revoke, rebuild
- OpenClaw internal hook script (`flyn-conv-memory-tap.sh`) — install.sh deploys to `~/.openclaw/hooks/`
- Daily backfill pulse (`ai.flyn.pulse.conv-summarize-backfill.plist`) for stuck summaries
- 18 new unit + integration tests; smoke test + ship-gate playbook; outcomes rubric

## Scope

Slice 1 = Telegram only. WhatsApp, iMessage, email = separate small hooks added later atop the same conv tier.

## Manual ship-gate

After merge + install.sh + service restart, run `tests/e2e/test_conv_memory_slice_1_ship_gate.md` Procedures A-D (8 steps, ~10 minutes including a real Telegram message and a Graphiti episode check).

## Spec / design references

- Spec: `docs/superpowers/specs/2026-05-19-conversation-memory-design.md`
- Visualization: `docs/superpowers/specs/2026-05-19-conversation-memory-design.html`
- Plan: `docs/superpowers/plans/2026-05-19-conversation-memory-telegram-slice-1.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Report**

Output the PR URL. **Do NOT merge.** Wait for human review of the live smoke + Procedure D before merging.

---

## Self-review

### Spec coverage

| Spec § | Implementation task |
|---|---|
| §1 Goals (200ms latency, per-owner isolation, AES-GCM, single source of truth, cross-system retrieval) | Tasks 4 (encryption) + 5 (owner) + 6 (schema) + 8 (write adapter) + 9 (read adapter) + 10 (server wiring) |
| §1 Non-goals (no outbound, no multi-channel join, no clustering, no auto-promotion, no deletion, no SSE) | Honored: scope is Telegram + inbound + per-message only |
| §1 Success criteria 1-6 | Tasks 10 (ingest path) + 7 (summarizer 10s budget) + 9 (query hit) + 12 (replay + audit) + 8 (Graphiti POST) + 11+14 (tests + smoke) |
| §2 Architecture three-tier + adapter-boundary owner resolution | Task 8 conv_write does resolve_from_chat before any storage call ✓ |
| §3 Components: 6 new files | Tasks 4 (encrypted_raw) + 5 (owner) + 6 (schema) + 7 (summarizer) + 8 (conv_write) + 9 (conv_read) |
| §3 Server extensions | Task 10 |
| §4 Data flow timings | Task 11 integration test verifies end-to-end; ship-gate verifies live timings |
| §5 Logging contract (conv-writes + conv-replay-audit) | Audit log via owner.py; conv-writes log future-work (memory-router's existing query log already captures writes per-source) |
| §5 CLI surface | Task 12 |
| §6 18-test target | Task 1 (1) + Task 2 (2) + Task 4 (4) + Task 5 (3) + Task 6 (3) + Task 8 (3) + Task 9 (2) + Task 11 (3) + Task 12 (3) = 24 — overshoots target slightly; OK since they're cheap |
| §7 Discovery / cross-agent reach | Task 15 |
| §8 Install + hook + pulse | Task 13 |
| §8 schema_version baked in | DEFERRED — Task 6 ships without it. Spec note acknowledged this is a future-migrate consideration; CREATE TABLE IF NOT EXISTS gives idempotency for now. |
| §9 Execution model | Plan itself + subagent-driven-development |
| §10 Open questions | Hook API verification deferred to Task 13 implementation; outbound mirroring deferred entirely |
| §11 References | Implicit in commit messages + PR body |

### Placeholder scan

Searched the plan for TBD / TODO / fill-in / similar-to / ??? — clean. The `schema_version` deferral in §8 is explicit, not a placeholder.

### Type consistency

- `ConvMessage` defined in Task 6 (schema.py) — used unchanged in Task 8 (conv_write.py)
- `StoredMessage` defined in Task 6 — used unchanged in Task 9 (conv_read.py) and Task 12 (cli replay)
- `OwnerRegistry.db_path_for(owner_id, conv_root)` — signature matches between Task 5 definition and Tasks 8/9/12 usage
- `encrypted_raw.seal` / `unseal` / `KeychainLocked` — consistent across Tasks 4/8/12
- `Tier.CONV` — Task 1 defines, Task 10 uses
- `WriteResult` already exists in `adapters/base.py`; we re-use unchanged

### Dependency order

```
Task 1 (Tier.CONV)
  └─ Task 10 (server wiring — uses Tier.CONV)

Task 2 (Config conv paths)
  └─ Task 10, 13, 15 (all use cfg.conv_root)

Task 3 (cryptography dep)
  └─ Task 4 (encrypted_raw — imports AESGCM)

Task 4 (encrypted_raw)
  └─ Task 8 (conv_write — calls seal)
  └─ Task 12 (cli replay — calls unseal)

Task 5 (owner.py)
  └─ Task 8, 9, 10, 12 (all use OwnerRegistry)

Task 6 (schema.py)
  └─ Task 8, 9, 12 (all use ConvDb)

Task 7 (summarizer)
  └─ Task 8 (conv_write — calls enqueue)
  └─ Task 10 (server wiring — starts the worker)

Task 8 (conv_write) + Task 9 (conv_read)
  └─ Task 10 (server wiring)

Task 10 (server wiring)
  └─ Task 11 (integration tests)

Task 11 (integration)
  └─ Task 16 (final integration)

Task 12 (CLI)
  └─ Task 13 (install — re-link entry point)

Task 13 (install + hook + pulse)
  └─ Task 16 (final)

Task 14 (ship-gate + rubric)
  └─ Task 16 (final)

Task 15 (discovery)
  └─ Task 16 (final)
```

Linear order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16. ✓

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-conversation-memory-telegram-slice-1.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec + code-quality) between tasks, fast iteration. This is the pattern used for PR #15 (memory-router read-side) and yesterday's Phase 5 fixes.

2. **Inline Execution** — execute tasks in this session using `executing-plans`, batch with checkpoints.

Which approach?
