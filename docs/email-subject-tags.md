# Flyn Email Subject-Line Tagging Convention

> **Criterion 6.6** — Documents the `[FLYN-TAG]` subject prefix convention used by the `EmailChannelAdapter`.

---

## Why tagged subjects?

Email threading in IMAP clients relies on `In-Reply-To` / `References` headers,
not on subject lines alone. Flyn adds a structured prefix so that:

1. **Routing is unambiguous** — the orchestrator can classify an email as a new
   task request, a reply to an existing task, or an approval decision without
   parsing free-form text.
2. **Human senders need no special client** — anyone can send a tagged subject
   from any email client just by typing the prefix.
3. **Audit trail** — the tag and task-ID are preserved in `raw_payload` in the
   SQLite `channel_inbox`, making it trivial to correlate email threads with
   orchestrator task IDs.

---

## Format

```
[TAG] Free-form subject text
[TAG:TASK-ID] Free-form subject text
```

- The bracket group must start at the beginning of the subject (leading
  whitespace is stripped).
- `TAG` is uppercase A–Z and hyphens only.
- `TASK-ID` is the orchestrator task ID (e.g. `T-0042`); omit when creating
  a new task.
- Everything after the closing `]` (and optional whitespace) is the
  `clean_subject` passed into the intent field.

---

## Tag reference

| Tag | When to use | Task-ID required? | Example |
|---|---|---|---|
| `FLYN-TASK` | Start a new task | No | `[FLYN-TASK] Redesign the pricing page` |
| `FLYN-REPLY` | Reply or update on an existing task | Yes | `[FLYN-REPLY:T-0042] Here is the feedback` |
| `FLYN-APPROVE` | Approve a pending task or deliverable | Yes | `[FLYN-APPROVE:T-0042] Ship it` |
| `FLYN-REJECT` | Reject a pending task or request changes | Yes | `[FLYN-REJECT:T-0042] See comments in body` |

### Notes

- Unrecognised tags (e.g. `[FLYN-PING]`) are parsed as `tag=FLYN-PING` and
  treated as a new task intent by the router's default branch.
- A subject with no `[FLYN-*]` prefix is still accepted if the sender passes
  auth — the full subject becomes the intent and `tag=None`.

---

## Authentication requirements

Every inbound email must pass at least one of these two gates before it is
processed:

### Gate 1 — Sender in CONTACTS allowlist

Senders listed in `CONTACTS.md` under the `email:` field, or in the hardcoded
`DEFAULT_ALLOWLIST` in `email.py`, bypass SPF/DKIM checks. Currently:

- `ryanshuken@gmail.com` (Owner)
- `beth@cora.community` (Teammate)
- `eric@cora.community` (Teammate)

### Gate 2 — SPF/DKIM pass

For non-allowlisted senders, the `Authentication-Results` header (RFC 8601)
prepended by the receiving MX must show:

- Neither `spf` nor `dkim` is `fail`, **and**
- At least one of `spf` or `dkim` is `pass`.

Emails with no `Authentication-Results` header at all are **rejected** for
non-allowlisted senders.

---

## Injection detection

Every inbound body — regardless of sender auth status — is scanned by
`injection_detect.detect_injection()` before being passed to the orchestrator.
Emails with a suspicious body are **silently dropped** (no reply is sent, to
avoid oracle attacks).

### What is flagged

| Reason label | Example trigger |
|---|---|
| `instruction-override` | "ignore previous instructions" |
| `role-reassignment` | "you are now a different AI" |
| `system-prompt-reference` | "reveal your system prompt" |
| `instruction-injection` | "new instructions: ..." |
| `role-confusion-tag` | `</user><system>...` |
| `prompt-boundary-injection` | `BEGIN PROMPT` / `END PROMPT` |
| `zero-width-unicode` | invisible Unicode characters (U+200B etc.) |
| `base64-blob` | 200+ consecutive base64-alphabet characters |
| `excessive-whitespace` | 50+ consecutive whitespace/newline characters |

### False-positive handling

If a legitimate email is dropped due to a false positive (most likely
`base64-blob` in an auto-forwarded message with an attachment stub, or
`excessive-whitespace` in a heavily-quoted thread):

1. **Re-send from an allowlisted address** — allowlisted senders still have
   their bodies scanned, but you can trim the trigger content.
2. **Send via Telegram** — the Telegram adapter has no injection detector and
   is always available for quick task creation.
3. **File a KNOWLEDGE entry** — if the pattern needs adjustment, add a
   `KNOWLEDGE/<NN>-injection-detect-false-positive.md` entry and open a PR to
   tighten the regex threshold.

---

## Implementation pointers

| Component | File |
|---|---|
| Tag constants + parse/format helpers | `flyn_orchestrator/adapters/channels/email_subject.py` |
| Adapter (ingest / send / approve_button) | `flyn_orchestrator/adapters/channels/email.py` |
| SPF/DKIM verification | `flyn_orchestrator/adapters/channels/email_auth.py` |
| Injection detector | `flyn_orchestrator/adapters/channels/injection_detect.py` |
| Unit tests | `tests/unit/test_email_{auth,subject,adapter}.py` |
| Injection tests | `tests/unit/test_injection_detect.py` |
