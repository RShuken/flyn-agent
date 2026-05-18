# Cookbook: Add a new ChannelAdapter

A ChannelAdapter is how Flyn talks to a communication system (Telegram, Email, Slack, Google Chat). It does three things: **receive** inbound messages and turn them into `InboundTaskRequest` records; **send** outbound text to a recipient; **render an approve button** for tasks awaiting human approval.

After this guide, you'll have a `<channel>.py` module under `flyn_orchestrator/adapters/channels/` that's ready to wire into the channel registry.

## When to add a ChannelAdapter

A new ChannelAdapter is justified when the channel has:

- **Inbound messaging** — users can DM/email/post-to Flyn and expect a task to start
- **Outbound messaging** — Flyn should be able to reply or notify on that channel
- **A distinct ingest path** — not just SMTP-into-the-existing-EmailChannelAdapter

Concrete examples worth adding:
- **Google Chat** — Phase 6.1 (waiting on Workspace OAuth)
- **Slack** — for teams that prefer Slack over Telegram
- **SMS** (via Twilio, Pinpoint, etc.)
- **Discord** — for community-facing deployments

Examples NOT worth adding:
- Another Telegram bot variant → just instantiate `TelegramChannelAdapter` with the new token
- "Email but with a different domain" → use the existing `EmailChannelAdapter`; configure `email:<domain>` in auth-profiles

## The contract

`flyn_orchestrator/adapters/base.py` defines:

```python
@runtime_checkable
class ChannelAdapter(Protocol):
    name: str
    def ingest(self, raw_message: dict[str, Any]) -> Optional[InboundTaskRequest]: ...
    def send(self, channel: str, body: str, attachments: Optional[list] = None) -> None: ...
    def approve_button(self, task_id: str, action: str) -> None: ...
```

**Three invariants:**

1. **`ingest` returns None on rejection.** Auth failures, malformed payloads, prompt-injection detections — all return None silently. The orchestrator treats None as "ignore this message". Never raise from `ingest`.

2. **`send` is best-effort.** When called on an unconfigured adapter (no token, no DNS), it no-ops without raising. The orchestrator emits a memory event on best-effort failure (currently MVP: the adapter swallows; later we'll wire a callback).

3. **`approve_button` may be a no-op.** Telegram supports inline keyboards; Email only supports tagged-reply convention. If your channel has no native button UX, document the alternate flow (e.g., "reply with `[FLYN-APPROVE:<task_id>]`").

## Build it — step by step

### 1. Adapter module

Create `deploy/orchestrator/flyn_orchestrator/adapters/channels/<channel>.py`. Reference implementations:

- `telegram.py` (158 lines) — full inbound (parse Update JSON) + outbound (Bot API) + approve_button (inline keyboard)
- `email.py` (~200 lines) — IMAP/SMTP with allowlist + injection detection + subject-tag parsing

**Pattern (mirrors telegram.py):**

```python
"""<Channel>ChannelAdapter — full inbound + outbound for <SYSTEM>.

Reads credentials from auth-profiles.json (slot `<channel>:default`) or
env vars. Stub-mode when credentials absent.

Inbound flow: ingest(raw_message) → verify identity/auth → detect
injection (if appropriate) → return InboundTaskRequest or None.
Outbound: send(channel_id, body) via the system's API.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from ...types import InboundTaskRequest, SenderRole


def _load_credentials() -> Optional[dict]:
    p = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if p.exists():
        try:
            d = json.load(open(p))
            for key in ("<channel>:default", "<channel>"):
                if key in d.get("profiles", {}):
                    prof = d["profiles"][key]
                    if all(prof.get(k) for k in ("token",)):  # whatever fields you need
                        return prof
        except Exception:
            pass
    # env fallback
    if os.environ.get("FLYN_<CHANNEL>_TOKEN"):
        return {"token": os.environ["FLYN_<CHANNEL>_TOKEN"]}
    return None


def _classify_sender(sender_id: str) -> SenderRole:
    """Owner / Teammate / Other based on hardcoded allowlist (MVP)
    or CONTACTS.md lookup (full). See KNOWLEDGE/19 — when CONTACTS becomes
    the source of truth, delete the hardcoded fallback."""
    # ...


class <Channel>ChannelAdapter:
    name = "<channel>"

    def __init__(
        self,
        credentials: Optional[dict] = None,
        sender: Optional[Callable[..., Any]] = None,  # injectable for tests
    ) -> None:
        self._creds = credentials if credentials is not None else _load_credentials()
        self._sender = sender

    @property
    def configured(self) -> bool:
        return self._creds is not None

    def ingest(self, raw_message: dict) -> Optional[InboundTaskRequest]:
        """raw_message shape depends on the channel. Document expected fields
        at the top of the method."""
        # 1. Validate basic structure
        sender_id = raw_message.get("from") or raw_message.get("user_id")
        if not sender_id:
            return None

        # 2. Verify identity / auth (if applicable)
        # 3. Detect prompt injection if the channel can receive arbitrary text
        # 4. Build InboundTaskRequest
        return InboundTaskRequest(
            channel=self.name,
            sender_identifier=str(sender_id),
            sender_role=_classify_sender(str(sender_id)),
            intent=raw_message.get("text", ""),
            external_message_id=raw_message.get("message_id", ""),
            raw_payload={
                "channel": self.name,
                # ... any channel-specific data the orchestrator might need later
            },
        )

    def send(self, channel: str, body: str, attachments: Optional[list] = None) -> None:
        if not self.configured:
            return
        try:
            if self._sender is not None:
                self._sender(channel=channel, body=body)
                return
            # Real API call — call the system's SDK or raw HTTP
            ...
        except Exception:
            return  # best-effort

    def approve_button(self, task_id: str, action: str) -> None:
        # If the channel supports inline buttons: post a message with a button.
        # If not: emit a message instructing the user to reply with a specific tag.
        return  # MVP: no-op
```

### 2. Auth/identity verification (if the channel has untrusted senders)

Telegram trusts the bot framework — the bot token authenticates inbound. Email doesn't trust SMTP — it requires SPF/DKIM verification via `email_auth.py`. Your channel falls somewhere on this spectrum.

**Decide upfront:**
- **Token-authenticated** (Telegram, Slack with signed-secret) → trust inbound, just classify sender by ID
- **Auth-via-domain** (Email, RSS) → verify signatures; allowlist override for trusted senders
- **No auth** (public webhooks, IRC) → reject anything not from a pre-known sender

For non-trivial cases, factor identity verification into its own module (like `email_auth.py`) so it's testable in isolation.

### 3. Prompt-injection detection (if appropriate)

Channels that can carry **arbitrary user-supplied text** (email body, Slack DM, etc.) need injection detection. Channels that carry **structured commands** (Telegram bot commands with strict parsing) don't.

If injection detection is needed, reuse `injection_detect.detect_injection(body) -> (suspicious, reasons)`. It covers:
- Instruction-override patterns ("ignore previous instructions")
- Role-reassignment ("you are now…")
- Role-confusion tags (`</user>`, `<system>`)
- Base64 smuggling (200+ char blobs)
- Zero-width Unicode
- Excessive whitespace

If your channel needs detection patterns beyond these (e.g., system-specific attack vectors), extend `INJECTION_PATTERNS` in `injection_detect.py` rather than re-implementing.

### 4. Tests

Create `tests/unit/test_<channel>_adapter.py`. Cover:
- `configured=False` when credentials absent → `send` no-op; `ingest` still works for allowlisted/trusted senders
- `ingest` happy path → returns `InboundTaskRequest` with the right `channel`, `sender_identifier`, `sender_role`
- `ingest` returns None on missing required fields
- `ingest` returns None on auth failure / injection (if applicable)
- `send` with injected sender callable → callable invoked with correct args
- `send` when not configured → no-op, no exception
- `send` when API raises → swallowed, no exception

If you wrote a separate auth/injection module, test that in its own file (`test_<channel>_auth.py`, `test_<channel>_injection.py`).

### 5. Register in the channel registry

`flyn_orchestrator/adapters/__init__.py` exposes `ChannelRegistry`. The orchestrator's bootstrap code (in `server.py` or wherever the registry is constructed) needs to instantiate your adapter and register it:

```python
from .adapters.channels.<channel> import <Channel>ChannelAdapter

registry = ChannelRegistry()
registry.register("telegram", TelegramChannelAdapter())
registry.register("email", EmailChannelAdapter())
registry.register("<channel>", <Channel>ChannelAdapter())  # ← your row
```

The registry's `get(channel_name)` is called from `_notify_originating_channel` and from `handle_approval` (for content/dev workflows).

### 6. Wire inbound (REST endpoint)

If users will POST to `:8300/api/tasks/inbound` with messages from your channel, no orchestrator changes needed — the existing endpoint routes by `payload.channel`. Just ensure your `raw_payload` dict in `ingest()` includes the `channel` key matching your adapter's `name`.

If your channel has its own webhook (e.g., Slack Events API), add a route in `server.py`:

```python
@app.post("/api/channels/<channel>/webhook")
def <channel>_webhook(payload: dict):
    adapter = channel_registry.get("<channel>")
    req = adapter.ingest(payload)
    if req is None:
        return {"status": "ignored"}
    task_id = router.accept(req)
    background_tasks.add_task(router.run_task, task_id)
    return {"status": "accepted", "task_id": task_id}
```

### 7. Ship checklist

- [ ] `adapters/channels/<channel>.py` adapter module
- [ ] Auth / injection / subject-parsing helpers in sibling modules if non-trivial
- [ ] `tests/unit/test_<channel>_adapter.py` + helper tests
- [ ] Registration in the channel registry bootstrap
- [ ] Inbound webhook route (if channel has one) in `server.py`
- [ ] If Phase 6 rubric criterion: update Phase 6 row + score
- [ ] `audit/_baseline.md` §Δ subsection if any new pattern surfaced
- [ ] Subject-line / message-format convention docs at `docs/<channel>-message-format.md` if users need to know how to talk to Flyn on this channel

## Anti-patterns to avoid

- **Raising from `ingest` on a malformed message.** Return None. The orchestrator decides what to do with rejected messages (currently: drop them silently).
- **Sending without injection detection on user-supplied content.** If the user can write a free-text body, scan it.
- **Hardcoded recipient lists in `send`.** The `channel` argument tells you where to send. Don't read CONTACTS.md inside the adapter.
- **`send` that raises on auth failure.** No. Swallow it. The orchestrator's job is to complete the deliverable; the side-channel notify is best-effort.
- **Synchronous polling in `ingest`.** Ingestion is event-driven. If your channel requires polling (IMAP), that lives in a separate scheduled job (`flyn-orchestrator-daily.sh`), not in `ingest`.

## See also

- `KNOWLEDGE/20-adapters-never-raise.md` — best-effort guarantee
- `flyn_orchestrator/adapters/base.py` — Protocol definition
- `flyn_orchestrator/adapters/channels/{telegram,email}.py` — reference implementations
- `docs/email-subject-tags.md` — example message-format convention doc
