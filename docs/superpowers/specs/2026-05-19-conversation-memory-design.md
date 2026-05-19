# Conversation Memory — Telegram Slice 1 Design

**Status:** Spec, awaiting user review
**Author:** Ryan Shuken + Claude (Opus 4.7, this session)
**Date:** 2026-05-19
**Relates to:** Memory-router unified design (`2026-05-16-flyn-memory-router-unified-design.md`); replaces the 2026-04-30 standalone-service plan tracked in auto-memory note `project_conversation_memory.md`.
**Build path:** Local autonomous stack — `superpowers:subagent-driven-development` executing the plan, with `outcomes_runner.py` + a new `CONV-MEMORY-SLICE-1-RUBRIC.md` for verification.

---

## Section 1 — Goals, non-goals, success criteria

### What this is

A new "conversation" tier inside the existing memory-router (`localhost:8400`) that captures every Telegram message Flyn sees, stores it in a per-owner SQLite database with a Keychain-encrypted raw payload and an asynchronously-generated summary, promotes it to Graphiti's temporal KG, and surfaces it through a new `conv_read` adapter alongside the 10 existing memory sources.

Slice 1 covers Telegram only. WhatsApp, iMessage, and email are explicit follow-ups; each adds a small hook for its channel and uses the same conv tier underneath.

### Why this exists

Today Flyn forgets across channels. A Telegram conversation with Beth on Monday doesn't inform Flyn's behavior on Wednesday because nothing writes it into long-term memory. The OpenClaw built-in memory captures *agent turns*, not raw inbound traffic; Graphiti captures *facts*, not the message stream that produced them; the memory-router has 10 read sources but none of them include actual conversation history.

This work closes the gap: every inbound message becomes searchable conversation memory, with security-grade encryption for replay and entity-graph promotion for cross-channel "when did X happen" queries.

### Goals (priority order)

1. **Every inbound Telegram message lands in conv.db within 200ms** of hitting the OpenClaw gateway. Async summarizer fills the summary within ~5s.
2. **Per-owner physical isolation** — Ryan's messages and Beth's messages live in separate SQLite files. Cross-owner reads require an explicit grant. Audit-logged.
3. **AES-GCM encrypted raw_payload** with per-owner keys in macOS Keychain. Raw is only decryptable via `flyn-mem conv replay`, audit-logged, never via REST.
4. **Single source of truth via memory-router** — conv tier reuses the existing dedup, redaction, queue, logging, and read-fan-out infrastructure.
5. **Cross-system retrieval via the existing query surface** — `flyn-mem query "..."` fans across 11 adapters now, and conv contributes via RRF.

### Non-goals

- **No outbound message mirroring** in slice 1. Flyn's replies don't get written to conv.db. Adding a `direction="outbound"` field is straightforward; we just defer it.
- **No multi-channel join** — a Telegram thread and an email thread aren't merged into one conversation in slice 1. `thread_id` is per-channel.
- **No conversation-level topic clustering** — no LDA / no embedding clustering / no "Beth always asks about Linear" detection. The per-message summary + Graphiti entity extraction are enough for slice 1.
- **No auto-promotion to wiki pages** — when "Beth has asked about X 3 times," nothing currently writes a wiki entry. Listed as a future drift category in `lint.py`.
- **No conversation deletion in slice 1** — disk-full guard warns at 500MB/owner but doesn't auto-delete. Add `flyn-mem conv archive --before <ts>` later if needed.
- **No streaming-results / SSE** on conv queries.

### Success criteria

A slice-1 ship is "done" when:

1. A live Telegram message to `@flyn_4c_bot` results in a row in `~/.flyn/memory-router/conv/ryan.db` within 200ms of receipt (measured via conv-writes log).
2. Within 10s, that row has a non-NULL `summary` populated by `gemma4:e4b`.
3. `flyn-mem query "<text from that message>"` returns a hit with `source="conv/telegram"` and the correct `metadata.msg_id`.
4. `flyn-mem conv replay <id>` decrypts the original payload and prints it, with the call audit-logged to `conv-replay-audit-*.jsonl`.
5. A Graphiti episode exists for that message under `group_id="flyn-ryan"`.
6. All 18 unit + integration tests pass + the live smoke procedure documented in the ship-gate playbook passes.

### Commitments inherited from existing memory-router design

- **REST + curl from exec only.** No MCP for the conv ingest path.
- **launchd-managed** — the memory-router service is unchanged; conv tier ships inside it. One new pulse for summarizer backfill.
- **File size caps** — soft 400 / hard 800 per file. This spec targets ≤200 per new file.
- **TDD for security-sensitive code** — encryption, owner resolution, schema. Live smoke for everything else.

---

## Section 2 — Architecture

The conversation tier is a 6th tier inside memory-router, sitting alongside the existing 5 (hot / warm / cool / cold / lesson). New module at `flyn_memory_router/conv/`, new write+read adapters at `flyn_memory_router/adapters/`. No new service, no new port, no new launchd unit (apart from one summarizer-backfill pulse).

```
Telegram message
   │
   ▼ (existing) OpenClaw gateway receives + dispatches to its agent
   │
   ▼ NEW: internal hook "flyn-conv-memory-tap"
   │     fires on every inbound Telegram message before openclaw agent processing
   │
   ▼ POST :8400/api/memory/ingest  (existing endpoint, branched on event_type)
   │   source="telegram"
   │   event_type="conversation_message"
   │   body=<msg text>            ← redacted by existing redact.py
   │   raw_payload=<full JSON>    ← will be AES-GCM sealed before write
   │   dedup_key="tg-<chat>-<msg_id>"
   │
   ▼ existing memory-router pipeline:
   │   ├─ dedup (existing) — drops duplicates from Telegram resend
   │   ├─ redact (existing) — strips secret patterns from body + raw_payload
   │   └─ NEW: conv_write adapter routes to conversation tier
   │      ├─ owner.resolve_from_chat("telegram", sender_id) → Owner("ryan")
   │      ├─ encrypted_raw.seal(redacted_raw_payload, owner_id) → AES-GCM bytes
   │      ├─ ConvDb("ryan").write(message_with_sealed_raw)
   │      ├─ enqueue summarize-job (uses existing disk queue dir)
   │      └─ POST Graphiti episode (existing httpx client, async, fire-and-forget)
   │   T+~150ms: HTTP 200 OK returned to the hook
   │
   ▼ async (within ~5s):
   │   summarizer worker thread pulls job
   │   → POST :11434/api/generate (gemma4:e4b, 30s timeout)
   │   → ConvDb.update_summary(row_id, summary_text)
   │
   ▼ at query time:
       flyn-mem query "..."
       → fans across 11 read adapters now
       → conv_read reads $USER's conv.db via FTS5 over body+summary
       → returns Hit(source="conv/telegram", ...)
       → RRF merges with other 10 sources
```

**Three-tier separation** matches the rest of memory-router:
- **Storage:** `conv/schema.py`, `conv/owner.py`, `conv/encrypted_raw.py` — pure modules, no FastAPI, no HTTP. Take typed inputs, return typed outputs.
- **Service:** `conv/summarizer.py` — the async worker thread + Ollama client.
- **Surface:** `adapters/conv_write.py` + `adapters/conv_read.py` — adapters that plug into memory-router's existing Protocol contracts.

Owner resolution happens at the adapter boundary (`conv_write` calls `owner.resolve_from_chat` before touching storage). Storage modules never know about HTTP requests or owner-grants logic — they just take `(owner_id, message)` and write.

---

## Section 3 — Components

Six new modules. All ≤200 lines.

### conv/schema.py

SQLite + FTS5 schema for per-owner conv.db.

```python
@dataclass(frozen=True)
class ConvMessage:
    channel: str
    sender_id: str
    thread_id: str | None
    reply_to_id: int | None
    ts: str                  # ISO 8601 UTC
    body: str                # already redacted
    attachments: list[dict]
    encrypted_raw: bytes


class ConvDb:
    def __init__(self, owner_id: str, path: Path) -> None: ...
    def write(self, msg: ConvMessage) -> int: ...                    # returns row id
    def update_summary(self, row_id: int, summary: str) -> None: ...
    def search(self, q: str, top_k: int = 30) -> list[StoredMessage]: ...
    def get_by_thread(self, thread_id: str, limit: int = 50) -> list[StoredMessage]: ...
    def get_by_id(self, row_id: int) -> StoredMessage | None: ...
    def stats(self) -> dict: ...                                     # row count, oldest, newest, summary_backlog
```

Schema:

```sql
CREATE TABLE IF NOT EXISTS messages (
  id            INTEGER PRIMARY KEY,
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

-- Triggers to keep FTS5 in sync
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, body, summary) VALUES (new.id, new.body, new.summary);
END;
CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, body, summary)
    VALUES('delete', old.id, old.body, old.summary);
  INSERT INTO messages_fts(rowid, body, summary) VALUES (new.id, new.body, new.summary);
END;
```

WAL mode. The `encrypted_raw` BLOB is NEVER indexed by FTS5 (FTS5 only sees the redacted body + summary).

### conv/owner.py

Owner registry + grants + audit logging. Shared `~/.flyn/memory-router/conv/owners.db`.

```python
@dataclass(frozen=True)
class Owner:
    id: str
    display_name: str
    chat_id_map: dict[str, str]


class OwnerRegistry:
    def __init__(self, owners_db_path: Path, principals_json: Path) -> None: ...
    def resolve_from_chat(self, channel: str, sender_id: str) -> Owner | None: ...
    def db_path_for(self, owner_id: str) -> Path: ...
    def viewer_can_read(self, viewer: str, owner_id: str) -> bool: ...
    def list_accessible_owners(self, viewer: str) -> set[str]: ...
    def grant(self, viewer: str, owned_by: str, granted_by: str, reason: str) -> None: ...
    def revoke(self, viewer: str, owned_by: str, revoked_by: str) -> None: ...
    def append_audit(self, viewer: str, owned_by: str, op: str, q: str | None) -> None: ...
```

Schema:

```sql
CREATE TABLE IF NOT EXISTS owners (
  id              TEXT PRIMARY KEY,
  display_name    TEXT NOT NULL,
  principals_json TEXT NOT NULL          -- {channel: external_id} mapping
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
  id        INTEGER PRIMARY KEY,
  ts        TEXT NOT NULL,
  viewer    TEXT NOT NULL,
  owned_by  TEXT NOT NULL,
  op        TEXT NOT NULL,                -- "read" | "replay" | "grant" | "revoke"
  q         TEXT                          -- query text or row_id, depending on op
);
```

Default-deny: `viewer_can_read("ryan", "ryan")` → True always. Cross-owner requires a row in `grants`. Every cross-owner read or replay writes to `audit_log`. Audit table never rotates.

Initial seed of owners comes from `~/.flyn/memory-router/conv/principals.json`:

```json
{
  "owners": [
    {"id": "ryan", "display_name": "Ryan Shuken",
     "principals": {"telegram": "7191564227"}}
  ]
}
```

### conv/encrypted_raw.py

Per-owner AES-GCM with keys in macOS Keychain. Subprocess to `security` CLI; no pyobjc dependency.

```python
def seal(plaintext: bytes, owner_id: str) -> bytes:
    """AES-GCM encrypt with per-owner key. Returns nonce(12) || ciphertext || tag(16)."""

def unseal(ciphertext: bytes, owner_id: str) -> bytes:
    """Decrypt. Raises KeychainLocked if security CLI can't unlock the keychain.
    Raises ValueError on auth-tag mismatch (tamper)."""

class KeychainLocked(Exception): pass
```

Implementation:

- Per-owner key stored as a generic password in the user's login keychain under the service name `flyn-conv-memory:<owner_id>`, account `aes-key`. Created on first `seal()` call if missing (16 bytes from `os.urandom`).
- Subprocess timeout for `security find-generic-password` is 2 seconds. Any non-zero exit raises `KeychainLocked`.
- AES-GCM via Python's `cryptography` library (already a transitive dep — verify in pyproject; if not, add it).
- The unsealed plaintext is held in memory only for the lifetime of the `unseal` return value. No on-disk cache.
- **No REST endpoint surfaces raw_payload** — only the `flyn-mem conv replay` CLI path, which the operator runs locally and which writes to the audit log.

### conv/summarizer.py

Background thread that pulls jobs from a disk queue and calls Ollama. Reuses memory-router's existing `queue.py` (the disk-persisted backpressure queue used by ingest dedup retries).

```python
SUMMARY_PROMPT_TEMPLATE = """Summarize this Telegram message in 1-2 sentences.
Focus on what the sender said, decided, or asked. Skip pleasantries.

Sender: {sender_id}
Body: {body}

Return JSON: {{"summary": "..."}}
"""


class SummarizerWorker:
    def __init__(self, queue_dir: Path, ollama_url: str, model: str,
                 timeout: float = 30.0) -> None: ...
    def start(self) -> None: ...           # spawns daemon thread
    def stop(self, timeout: float = 2.0) -> None: ...
```

Worker loop:
1. Walk `queue_dir/conv-summarize/*.json` sorted by mtime ascending.
2. For each job, load `{owner_id, db_path, row_id, body, sender_id}`.
3. POST `:11434/api/generate` with `gemma4:e4b`, `stream=false`, `format=json`.
4. On success: `ConvDb(owner_id, db_path).update_summary(row_id, parsed.summary)`. Delete job file.
5. On timeout / non-200 / unparseable JSON: leave the job in place. Will be retried on next poll. `TRACKER.record("conv-summarize", elapsed_ms=..., error=True)` per existing health-tracker pattern.
6. Poll interval: 1 second when queue is non-empty, 10 seconds when empty.

A daily pulse (`ai.flyn.pulse.conv-summarize-backfill`) scans `messages WHERE summary IS NULL AND ts < now() - 1h` and re-enqueues jobs that fell through the cracks (Ollama was down for hours, summarizer thread died, etc.).

### adapters/conv_write.py

Plugs into memory-router's existing `MemoryAdapter` Protocol. Triggered when `InboundEvent.event_type == "conversation_message"`.

```python
class ConvWriteAdapter:
    name: str = "conv.telegram"     # or just "conv" with channel in metadata

    def __init__(
        self,
        registry: OwnerRegistry,
        queue_dir: Path,
        graphiti_client: httpx.Client,
        group_id_template: str = "flyn-{owner}",
    ) -> None: ...

    def write(self, event: InboundEvent) -> WriteResult:
        # 1. Owner resolution
        owner = self._registry.resolve_from_chat(
            event.raw_payload["channel"],
            str(event.raw_payload["sender_id"]),
        )
        if owner is None:
            return WriteResult(target=self.name, ok=False, detail="unknown sender")

        # 2. Seal raw_payload
        try:
            sealed = encrypted_raw.seal(
                json.dumps(event.raw_payload).encode("utf-8"),
                owner.id,
            )
        except KeychainLocked:
            return WriteResult(target=self.name, ok=False, detail="keychain locked")

        # 3. Write to per-owner conv.db
        db = ConvDb(owner.id, self._registry.db_path_for(owner.id))
        msg = ConvMessage(
            channel=event.raw_payload["channel"],
            sender_id=str(event.raw_payload["sender_id"]),
            thread_id=str(event.raw_payload.get("thread_id") or ""),
            reply_to_id=event.raw_payload.get("reply_to_msg_id"),
            ts=event.raw_payload.get("ts") or event.valid_at.isoformat(),
            body=event.body,                              # already redacted upstream
            attachments=event.raw_payload.get("attachments", []),
            encrypted_raw=sealed,
        )
        row_id = db.write(msg)

        # 4. Enqueue summarize-job
        self._enqueue_summarize(owner.id, db.path, row_id, msg.body, msg.sender_id)

        # 5. POST Graphiti episode (fire-and-forget)
        self._post_graphiti(owner, msg, row_id)

        return WriteResult(target=self.name, ok=True, detail=f"row={row_id}")
```

All five steps each wrapped in their own try/except in the actual implementation. Any failure returns `WriteResult(ok=False, ...)` — never raises.

### adapters/conv_read.py

11th read adapter. Implements memory-router's `ReadAdapter` Protocol.

```python
class ConvReadAdapter:
    name: str = "conv"
    read_timeout: float = 1.5
    default_included: bool = True

    def __init__(self, registry: OwnerRegistry, viewer_id: str | None = None) -> None: ...

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        viewer = self._viewer_id or os.environ.get("USER", "ryan")
        accessible = self._registry.list_accessible_owners(viewer)
        hits: list[Hit] = []
        for owner_id in accessible:
            db = ConvDb(owner_id, self._registry.db_path_for(owner_id))
            for stored in db.search(q, top_k=top_k):
                hits.append(Hit(
                    text=stored.summary or stored.body[:500],
                    source="conv/telegram",
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
            if owner_id != viewer:
                self._registry.append_audit(viewer, owner_id, op="read", q=q)
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]
```

### server.py extensions

Two new pieces.

**1. Branch ingest by event_type** (existing `/api/memory/ingest` handler):

```python
@app.post("/api/memory/ingest")
def ingest(event: InboundEvent) -> EventResult:
    if event.event_type == "conversation_message":
        result = conv_write_adapter.write(event)
        return EventResult(
            accepted=result.ok,
            deduped=False,
            importance=event.importance,
            tiers_written=[Tier.CONV] if result.ok else [],
            notes=[result.detail] if not result.ok else [],
        )
    # fall through to existing tier-dispatch logic
    ...
```

**2. New `GET /api/memory/conv/owners`** — admin-only (checks `$USER` ∈ `cfg.owner_identifiers` from PR #23). Returns per-owner row counts + summary backlog.

---

## Section 4 — Data flow

### Write path (Telegram → conv.db)

```
t=0ms        User sends Telegram message in their @flyn_4c_bot chat
t≈100ms      OpenClaw gateway picks it up via getUpdates (existing)
t≈110ms      Internal hook "flyn-conv-memory-tap" fires
t≈115ms      POST :8400/api/memory/ingest
t≈115ms      server.py routes by event_type → conv_write
             │
t≈118ms      ├─ dedup check (by source + dedup_key)        [existing]
t≈120ms      ├─ redact.py runs on body + raw_payload       [existing]
             │
t≈122ms      ├─ owner.resolve_from_chat → Owner("ryan")
t≈125ms      ├─ encrypted_raw.seal(redacted_raw, "ryan")   ← Keychain unlock first call only
t≈130ms      ├─ ConvDb("ryan").write(msg)                  → row_id=42
t≈135ms      ├─ enqueue summarize-job                      → queue/conv-summarize/42.json
t≈140ms      └─ POST :8100/api/episodes  (async, fire-and-forget)
                  group_id="flyn-ryan", source_description="conv/telegram"
t≈150ms      200 OK ← hook ← openclaw

────────── async ──────────

t≈3-5s       summarizer thread pulls 42.json
             POST :11434/api/generate → gemma4:e4b
t≈5-8s       summary returned → db.update_summary(42, "...")
             FTS5 trigger updates the summary index
             delete 42.json
```

### Read path (`flyn-mem query` includes conv)

```
flyn-mem query "what did Beth say about Linear?" --top 5
   │
   ▼ POST :8400/api/memory/query
   │
   ▼ orchestrator fans across 11 read adapters (default-included set)
   │
   ▼ conv_read.query():
   │   viewer = $USER → "ryan"
   │   accessible = {"ryan"}  (+ any granted owners)
   │   for owner in accessible:
   │     ConvDb(owner).search("what did Beth say about Linear?", top_k=30)
   │       SELECT messages.* FROM messages
   │       JOIN messages_fts ON messages_fts.rowid = messages.id
   │       WHERE messages_fts MATCH ?
   │       ORDER BY rank LIMIT 30
   │     returns hits sorted by FTS5 rank
   │   if owner != viewer: append_audit(viewer, owner, "read", q)
   │
   ▼ RRF merge with the other 10 adapters' hits
   ▼ return ranked hits + citations
   ▼ p95 < 500ms total (conv adapter ~10-15ms)
```

### Performance budget

| Path | Target | Notes |
|---|---|---|
| Hook → ingest → 200 OK | p95 < 250ms | Section 3 walks ~150ms; +100ms headroom for Keychain cold-start |
| Summarizer roundtrip | 3-8s typical | Async; never blocks the hook |
| `conv_read.query` | p95 < 20ms | FTS5 on local SQLite |
| End-to-end query with conv | p95 < 500ms | Same cap as existing `/api/memory/query` |

### Failure modes

| Failure | Behavior |
|---|---|
| Owner can't be resolved | `WriteResult(ok=False, detail="unknown sender")`; row NOT stored; conv-writes log entry |
| Keychain locked | `WriteResult(ok=False, detail="keychain locked")`; row NOT stored; hook still acks 200 to openclaw; counter exposed via `flyn-mem conv health` |
| ConvDb.write fails (disk full / corruption) | `WriteResult(ok=False)`; logged; existing source-errors log gets a row |
| Summarizer timeout | Job stays in queue; row has summary=NULL; visible in backlog; auto-retried by daily pulse |
| Graphiti POST fails | Conv.db write still ok; logged; backfilled by future lint job (out of slice 1) |
| FTS5 corruption | Read returns `[]`; source_error logged; recoverable via `flyn-mem conv rebuild` |

---

## Section 5 — Error handling, logging, observability

### Logging contract

Adds two new JSONL streams alongside the three from PR #15.

| File | Content | Retention |
|---|---|---|
| `query-YYYY-MM-DD.jsonl` (existing) | Conv adapter is one source like any other; per-source elapsed_ms + hit count are already captured | 90 days |
| `conv-writes-YYYY-MM-DD.jsonl` (new) | One line per conversation message ingested: `{ts, owner, channel, msg_row_id, body_len, redacted_count, summarizer_ms?, graphiti_ok, encrypted_raw_bytes}` | 90 days |
| `conv-replay-audit-YYYY-MM-DD.jsonl` (new) | One line per `flyn-mem conv replay` call: `{ts, viewer, owner, row_id, was_cross_owner}` | **Never rotated, never deleted** — this is the access audit trail |

### Health visibility

- `GET /api/memory/sources` (existing) already shows the conv adapter's per-source stats from `health_tracker`.
- `GET /api/memory/conv/owners` (new) returns per-owner row counts + summary backlog + last-write-ts. Admin-only (checks `$USER` ∈ `cfg.owner_identifiers`).
- `flyn-mem conv health` (new CLI) renders the above in a human-readable table.

### CLI surface

```
flyn-mem conv health                                    # per-owner DB stats
flyn-mem conv search "<q>" [--since 7d] [--thread <id>] [--sender @x]
flyn-mem conv thread <thread_id> [--limit 50]
flyn-mem conv replay <row_id> [--owner <id>]            # decrypts + prints; audit-logged
flyn-mem conv grant <owner> --to <viewer> [--reason "..."]
flyn-mem conv revoke <owner> --from <viewer>
flyn-mem conv rebuild [--owner <id>]                    # rebuild FTS5
```

`flyn-mem query "..."` (existing) gets two effective new behaviors via the existing `--include` / `--exclude` flags applied to the new `conv` adapter name. No code changes needed; just docs.

### Discovery — auto-memory + workspace pointers

Install script appends to `~/.claude/projects/-Users-4c-AI/memory/MEMORY.md`:

```markdown
- [conversation memory](feedback_conv_memory.md) — flyn-mem conv search / thread / replay; per-owner SQLite under ~/.flyn/memory-router/conv/
```

And writes `feedback_conv_memory.md` with usage guidance: "for 'what did X say last week' or 'what was the decision on Y,' prefer `flyn-mem conv search` over generic grep. For exact unredacted original text, use `flyn-mem conv replay <id>` — audit-logged."

---

## Section 6 — Testing strategy

Targeted unit + integration coverage for security-sensitive code; live smoke for the rest.

### Tests (≤20 total)

| Layer | File | Count | Why |
|---|---|---|---|
| Encryption | `tests/unit/test_conv_encrypted_raw.py` | 4 | seal/unseal round-trip; wrong-key fail; keychain-unavailable; tamper-detection (auth-tag mismatch) |
| Owner registry | `tests/unit/test_conv_owner.py` | 3 | viewer==owner allow; default-deny cross-owner; cross-owner with grant + audit row written |
| Schema | `tests/unit/test_conv_schema.py` | 3 | write→search round-trip; thread query ordering; summary update propagates to FTS |
| Write adapter | `tests/unit/test_conv_write_adapter.py` | 3 | happy path; unknown sender returns ok=False; keychain-locked returns ok=False |
| Read adapter | `tests/unit/test_conv_read_adapter.py` | 2 | adapter Protocol compliance; cross-owner read writes audit row |
| Integration | `tests/integration/test_conv_ingest_roundtrip.py` | 3 | POST /ingest with conversation_message → conv.db row; summarizer eventually fills summary; /query returns the hit |
| Live smoke | `tests/smoke/test_conv_live_telegram.py` | manual | Send real Telegram message, verify within 5s |

Total automated: **18 tests**. Plus the manual smoke.

### Live smoke procedure (ship-gate)

Documented in `tests/e2e/test_conv_memory_slice_1_ship_gate.md`:

1. Verify `:8400` is up and conv adapter is registered: `flyn-mem sources | grep conv`.
2. Verify `ryan` owner exists in `principals.json` with your Telegram sender_id.
3. Send a unique-text message to `@flyn_4c_bot` from your phone.
4. Within 10 seconds, run `flyn-mem conv search "<that unique text>"`.
5. Verify a row is returned with the right `msg_id`, `ts`, and (within 30s) a non-NULL `summary`.
6. Run `flyn-mem conv replay <msg_id>` and verify the original Telegram JSON is printed.
7. Verify the replay shows up in `conv-replay-audit-*.jsonl`.
8. Verify a Graphiti episode exists: `curl :8100/api/episodes?group_id=flyn-ryan | jq '.results | length'`.

### TDD discipline

Tests are written first (red), then implementation (green), then commit — same pattern as the read-side work in PR #15. The plan will enumerate one task per test cluster + implementation.

---

## Section 7 — Discovery + cross-agent reach

The same auto-memory + workspace pointer pattern as the read-side (PR #15 install step). After ship:

- Any Claude Code session anywhere in `~/AI/` sees the `feedback_conv_memory.md` auto-memory entry → knows to prefer `flyn-mem conv search`.
- Flyn (the OpenClaw agent) sees the workspace `TOOLS.md` update telling it to query conv when answering "when did X say Y" questions.
- The orchestrator and other Flyn surfaces all reach conv via the same memory-router REST surface they already use. No new MCP, no new endpoints to learn.

---

## Section 8 — Integration with existing memory-router

This work folds into the same `flyn-memory-router` package; no new launchd unit (except summarizer-backfill pulse).

### Files modified outside `conv/` and `adapters/conv_*`

- `flyn_memory_router/types.py` — extend `Tier` enum with `CONV = "conv"`; extend `InboundEvent` validation to accept `event_type="conversation_message"` (already free-form; no change strictly required).
- `flyn_memory_router/config.py` — add `conv_root` property (defaults to `home / "conv"`) and `principals_json_path` (defaults to `conv_root / "principals.json"`).
- `flyn_memory_router/server.py` — branch `/api/memory/ingest` on event_type; add `GET /api/memory/conv/owners` route; wire conv_write_adapter and conv_read_adapter into the registries during `build_app`.
- `flyn_memory_router/cli.py` — add `conv` subcommand and its 7 sub-subcommands.
- `flyn_memory_router/discovery.py` — extend install script to write the auto-memory pointer + workspace pointer additions for conv.
- `deploy/memory-router/install.sh` — extend to ensure `conv_root` exists + seed `principals.json` with the current `$USER` as the default owner.

### New launchd unit

`ai.flyn.pulse.conv-summarize-backfill` — daily at e.g. 4 AM. Scans for `summary IS NULL AND ts < now()-1h` and re-enqueues.

### OpenClaw hook installation

Adds `flyn-conv-memory-tap` to `~/.openclaw/openclaw.json` under `hooks.internal.entries`. The hook config is small (label + script path). The hook itself is a tiny bash script in `deploy/hooks/flyn-conv-memory-tap.sh` that takes the message JSON on stdin and POSTs to `:8400/api/memory/ingest`. If memory-router is down, the hook logs to `/tmp/flyn-conv-memory-tap.log` and returns 0 (don't block openclaw's message processing).

**This hook is the one piece this design relies on OpenClaw exposing.** If the openclaw hook API doesn't provide raw Telegram message JSON to internal-hook scripts, we fall back to running a small sidecar daemon that subscribes to openclaw's WebSocket event stream. Verify before implementation.

---

## Section 9 — Execution model

Same pattern as the memory-router read-side (PR #15):

1. **Spec** (this document) → user approval
2. **Plan** in `docs/superpowers/plans/2026-05-20-conversation-memory-telegram-slice-1.md` via `writing-plans` skill
3. **Rubric** in `deploy/outcomes/CONV-MEMORY-SLICE-1-RUBRIC.md` for outcomes_runner verification
4. **Build** via `superpowers:subagent-driven-development` task-by-task
5. **Ship-gate** live smoke per Section 6

No Anthropic Managed Agents. Same reasons as memory-router: 20-iteration cap, requires API key, hosted container can't reach localhost:8100. Local autonomous stack uses OAuth + your subscription.

---

## Section 10 — Open questions and deferred decisions

Intentionally not decided in slice 1.

1. **OpenClaw hook API surface for inbound message JSON.** Need to verify the hook gets enough payload context before writing the hook script. If not, fallback is a small openclaw WebSocket subscriber daemon.
2. **Outbound message mirroring.** Flyn's replies aren't captured in slice 1. Adding `direction="outbound"` on the schema is small; a hook on the openclaw outbound path is the work.
3. **Cross-channel thread join.** Telegram thread + email thread + Slack thread → unified conversation. Not in slice 1.
4. **Conversation→wiki auto-promotion.** "Beth asked X 3 times → write a wiki entry." Add to `lint.py` as a new drift category later.
5. **Conversation deletion / TTL.** No auto-archive in slice 1. Disk-full guard warns at 500MB/owner.
6. **Embedding-based semantic search.** FTS5 is keyword-based. Slice 2 might add sqlite-vec over summaries for better recall on paraphrases.
7. **Multi-channel slice 2.** WhatsApp via `wacli.db` (poller already populates locally), iMessage via BlueBubbles, Email via Workspace OAuth. Each is a separate small hook for its channel; the conv tier underneath is shared.
8. **Schema migration story.** Slice 1 ships with one schema version. Future migrations need a `schema_version` table + migration runner in `conv/schema.py`. Bake the table in now to avoid retrofit.

---

## Section 11 — References

- Memory-router unified design: `docs/superpowers/specs/2026-05-16-flyn-memory-router-unified-design.md`
- Memory-router read-side implementation: PR #15 (34 commits, 146 tests)
- Owner-identifiers security fix (PR #23) — basis for cross-owner audit pattern
- Karpathy LLM Wiki gist (`karpathy/442a6bf555914893e9891c11519de94f`) — the reference vault that already lives in conv-adjacent space
- POSTMORTEM-2026-04-21 — no-MCP rule for orchestrator-adjacent surfaces
- 2026-04-30 auto-memory note: `~/.claude/projects/-Users-4c-AI/memory/project_conversation_memory.md` — the original standalone-service design that this spec supersedes
- Cormack/Clarke/Buettcher 2009 — RRF (reused from memory-router read-side)

---

## Spec self-review notes (this section will be deleted after user approval)

**Placeholder scan:** searched for TBD / TODO / XXX / FIXME / ??? / VERIFY — clean. Section 10 explicitly labels deferred decisions as deferred, not as placeholders. Section 8 has one VERIFY note about the OpenClaw hook API surface; that's an intentional pre-implementation check, not a placeholder.

**Internal consistency:** Section 2 (architecture) → Section 3 (components) → Section 4 (data flow) chain. Section 5 (observability) matches Section 6 (testing). Section 7 (discovery) matches the install steps in Section 8. The 11 user decisions from the brainstorm are all reflected: extend memory-router (§2), Telegram-first (§1 non-goals), per-owner SQLite (§3 schema), raw+parsed+summary (§3 ConvMessage), async summarizer (§3 SummarizerWorker), Graphiti per-message (§3 conv_write step 5), encrypted-raw vault (§3 encrypted_raw.py), conv_read 11th adapter (§3), OpenClaw hook (§8), dedicated conv/ module (§3), ≤20 tests (§6).

**Scope check:** This is one cohesive spec for one slice of one feature, integrating into one existing service. Folds cleanly into a single implementation plan. Slice 2 (WA/iMessage/email) is a separate future spec, not part of this scope.

**Ambiguity check:** Three intentional ambiguities, all flagged: OpenClaw hook surface (§8 — verify before implementation), summarizer worker poll cadence specifics (§3 says 1s / 10s; could be tuned), and adapter name (`conv` vs `conv.telegram` — chose `conv` for the read side since the channel lives in metadata, `conv.telegram` for the write target identifier).

**Boundary check:** Spec specifies the contracts between modules, not the internals of e.g. FTS5 query construction or exact Ollama prompt phrasing. Those land during TDD-driven implementation.
