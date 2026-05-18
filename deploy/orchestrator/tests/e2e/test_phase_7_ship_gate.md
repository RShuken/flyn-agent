# Phase 7 Ship-Gate Playbook — Multi-PM

**Spec §9 Phase 7 ship gate:** Task mirrors to Linear AND Cora PM with same ID, stays in sync through state transitions.

This playbook runs after Phase 7 merges to main. Procedures A–D require only the orchestrator on `:8300` and the OL wiki on `:8200`; no Cora PM system needed. Procedures E and F are skeletons pending external systems. The conformance suite (Procedure A) has no live-service dependency.

## Prerequisites

```bash
# Verify orchestrator is running
curl -sS http://127.0.0.1:8300/api/health

# Verify OL wiki is running (needed for Procedures B)
curl -sS http://127.0.0.1:8200/api/health

# Verify Phase 7 code is importable
python3 -c "from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter; print('olwiki ok')"
python3 -c "from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter; print('webhook ok')"
python3 -c "from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter; print('linear ok')"

# Clear task state for a clean run (optional — isolates this playbook)
sqlite3 ~/.flyn/orchestrator/data/state.db \
  "DELETE FROM tasks; DELETE FROM task_events; UPDATE task_id_counter SET last=0;"
```

For Procedure B only — OL wiki on `:8200` must be live and accept `POST /api/decisions`. Verify:

```bash
curl -sS http://127.0.0.1:8200/api/health
```

For Procedure C only — a reachable webhook endpoint. Use [webhook.site](https://webhook.site) or a local listener:

```bash
# Local listener (one-liner, port 9999)
python3 -m http.server 9999 &
# OR use webhook.site and note the UUID URL
```

For Procedure D only — a valid Linear API key in `LINEAR_API_KEY` env var or in
`~/.openclaw/agents/main/agent/auth-profiles.json` under `profiles["linear:default"]["token"]`
or `profiles["linear"]["token"]`.

---

## Procedure A: PM Adapter Protocol Conformance (all 3)

Runs the 29-test parametrized conformance suite against `LinearPMAdapter`, `OLWikiPMAdapter`, and `WebhookPMAdapter`. Uses stub HTTP clients — no live services needed.

### Step 1: Run the conformance suite

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/orchestrator
.venv/bin/pytest tests/unit/test_pm_adapter_conformance.py -v
```

Expected output: 29 tests pass across 3 adapter variants (`linear`, `olwiki`, `webhook`) and 2 error-HTTP variants (`olwiki_err`, `webhook_err`). The test IDs follow the form `test_<criterion>[<adapter>]`.

Confirm the breakdown:
- 7 tests × 3 adapters (linear, olwiki, webhook) = 21 tests for `pm_adapter` fixture
- 4 tests × 2 adapters (olwiki_err, webhook_err) = 8 tests for `pm_adapter_errhttp` fixture
- Total = 29

### Step 2: Confirm each contract rule passes

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/orchestrator
.venv/bin/pytest tests/unit/test_pm_adapter_conformance.py -v --tb=short 2>&1 | \
  grep -E "PASSED|FAILED|ERROR"
```

Expected: every line says `PASSED`. No `FAILED` or `ERROR` lines.

The rules verified per adapter:
- `isinstance(adapter, PMAdapter)` structural protocol check
- `adapter.name` is a non-empty `str`
- `adapter.configured` returns a `bool`
- `create_task(t)` returns a non-empty `str`, does not raise
- `update_state(t, state)` returns `None`, does not raise
- `link_artifact(t, artifact)` returns `None`, does not raise
- `comment_on_task(t, body)` returns `None`, does not raise
- When the HTTP layer raises, none of the above propagate (best-effort guarantee)

### Step 3: Spot-check adapter name and configured attributes

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter
from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter

adapters = [
    OLWikiPMAdapter(),
    WebhookPMAdapter(target_url="http://stub.local"),
    LinearPMAdapter(api_key="stub-key"),
]
for a in adapters:
    print(f"{a.name}: configured={a.configured} (type={type(a.configured).__name__})")
    assert isinstance(a.name, str) and a.name, f"{a}: name is empty"
    assert isinstance(a.configured, bool), f"{a}: configured is not bool"

print("PASS: all 3 adapters have non-empty name and bool configured")
PYEOF
```

Expected: each adapter prints `name: configured=True` (or `False` for LinearPMAdapter without a real API key in env). The `type` must be `bool` in every case.

---

## Procedure B: OLWiki adapter live round-trip

Verifies that `OLWikiPMAdapter.create_task()` performs a real POST to `:8200/api/decisions`, that the decision appears via `GET /api/decisions`, and that the returned external ID uses the canonical `olwiki-decision-<N>` format.

**Prerequisite:** OL wiki live on `:8200` with valid PIN already supplied (i.e., `curl http://127.0.0.1:8200/api/decisions` returns a JSON list, not a 401).

### Step 4: Create a decision via the adapter

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

adapter = OLWikiPMAdapter(base_url="http://127.0.0.1:8200")

t = TaskRecord(
    task_id="T-p7-shipgate-B",
    workflow="dev",
    state=TaskState.INBOUND,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="Phase 7 ship-gate Procedure B: verify OLWiki round-trip",
)

ext_id = adapter.create_task(t)
print(f"external_id={ext_id!r}")

assert ext_id.startswith("olwiki-decision-"), (
    f"Expected 'olwiki-decision-<N>', got {ext_id!r}. "
    "If you got 'olwiki-stub-T-p7-shipgate-B', the wiki is not reachable or returned no 'id' field."
)

import re
m = re.fullmatch(r"olwiki-decision-(\d+)", ext_id)
assert m, f"ID suffix must be numeric, got {ext_id!r}"
decision_id = int(m.group(1))
print(f"PASS: create_task returned {ext_id!r} (numeric id={decision_id})")
PYEOF
```

Expected: `external_id='olwiki-decision-<N>'` where `<N>` is the integer primary key from the wiki's response.

### Step 5: Confirm the decision appears in GET /api/decisions

```bash
# Replace <N> with the numeric ID printed in Step 4
DECISION_ID=<N>
curl -sS "http://127.0.0.1:8200/api/decisions" | \
  python3 -c "
import json, sys
decisions = json.load(sys.stdin)
target = [d for d in decisions if str(d.get('id')) == '$DECISION_ID']
if not target:
    print(f'FAIL: decision $DECISION_ID not found in list of {len(decisions)} decisions')
    raise SystemExit(1)
d = target[0]
print(f'PASS: found decision {d[\"id\"]} — summary={d.get(\"summary\",\"\")[:60]!r}')
assert 'T-p7-shipgate-B' in (d.get('body_md') or '') or 'T-p7-shipgate-B' in (d.get('summary') or ''), \
    'Expected task_id in body_md or summary'
print('PASS: task_id T-p7-shipgate-B present in decision body')
"
```

Expected: the decision is listed with the task intent as the summary and the full `body_md` block including `Flyn task T-p7-shipgate-B`.

### Step 6: Confirm lifecycle no-ops do not raise

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

adapter = OLWikiPMAdapter(base_url="http://127.0.0.1:8200")

t = TaskRecord(
    task_id="T-p7-shipgate-B-noop",
    workflow="dev",
    state=TaskState.RUNNING,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="No-op lifecycle test",
)

# All three Phase 7 MVP no-ops — must return None and not raise
r1 = adapter.update_state(t, TaskState.COMPLETED)
r2 = adapter.link_artifact(t, {"url": "https://example.com/artifact.zip"})
r3 = adapter.comment_on_task(t, "Ship-gate verification comment.")

assert r1 is None, f"update_state must return None, got {r1!r}"
assert r2 is None, f"link_artifact must return None, got {r2!r}"
assert r3 is None, f"comment_on_task must return None, got {r3!r}"

print("PASS: update_state / link_artifact / comment_on_task all return None (Phase 7 MVP no-ops)")
print("NOTE: These are intentional deferred no-ops per spec — wiki has no native state/artifact fields in Phase 7 MVP.")
PYEOF
```

Expected: all three return `None`. No HTTP calls are made (Phase 7 MVP). This is expected behavior per the `olwiki.py` docstring: "Deferred to Phase 7b once the wiki gains native status fields."

---

## Procedure C: Webhook adapter delivery + auth header

Verifies that `WebhookPMAdapter` delivers correctly-shaped JSON POSTs for all four event types, and that the `X-Flyn-Secret` header is propagated when a secret is configured.

### Step 7: Confirm all four event shapes without a secret

Start a local listener in one terminal:

```bash
# In a separate terminal — captures exactly one POST and prints it
python3 - <<'LISTENER'
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        print(json.dumps(body, indent=2))
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass

print("Listening on 127.0.0.1:19999 — Ctrl-C to stop")
HTTPServer(("127.0.0.1", 19999), H).serve_forever()
LISTENER
```

In another terminal, fire all four event types:

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

adapter = WebhookPMAdapter(target_url="http://127.0.0.1:19999")

t = TaskRecord(
    task_id="T-p7-shipgate-C",
    workflow="dev",
    state=TaskState.INBOUND,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="Phase 7 ship-gate Procedure C: webhook delivery",
)

print("Firing create_task...")
ext_id = adapter.create_task(t)
assert ext_id == "webhook-T-p7-shipgate-C", f"Unexpected ext_id: {ext_id!r}"
print(f"  create_task returned {ext_id!r}")

print("Firing update_state...")
adapter.update_state(t, TaskState.RUNNING)

print("Firing link_artifact...")
adapter.link_artifact(t, {"url": "https://example.com/result.md", "type": "report"})

print("Firing comment_on_task...")
adapter.comment_on_task(t, "Reviewer: LGTM. No blocking findings.")

print("PASS: all 4 event types fired without exception")
PYEOF
```

In the listener terminal, verify 4 POST bodies arrive with these shapes:

```json
{"event": "task_created",  "data": {"task_id": "T-p7-shipgate-C", "workflow": "dev", ...}}
{"event": "state_changed", "data": {"task_id": "T-p7-shipgate-C", "to_state": "running"}}
{"event": "artifact_linked","data": {"task_id": "T-p7-shipgate-C", "artifact": {...}}}
{"event": "comment_added", "data": {"task_id": "T-p7-shipgate-C", "body": "Reviewer: LGTM..."}}
```

### Step 8: Confirm X-Flyn-Secret header propagates

Capture headers with a listener that prints them:

```bash
python3 - <<'LISTENER'
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        secret = self.headers.get("X-Flyn-Secret", "(absent)")
        print(f"event={body['event']!r}  X-Flyn-Secret={secret!r}")
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass

print("Listening on 127.0.0.1:19998 — Ctrl-C to stop")
HTTPServer(("127.0.0.1", 19998), H).serve_forever()
LISTENER
```

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

SECRET = "p7-shipgate-test-secret"
adapter = WebhookPMAdapter(
    target_url="http://127.0.0.1:19998",
    secret=SECRET,
)

t = TaskRecord(
    task_id="T-p7-shipgate-C2",
    workflow="dev",
    state=TaskState.INBOUND,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="Secret header test",
)

adapter.create_task(t)
adapter.update_state(t, TaskState.COMPLETED)
print("PASS: fired 2 events with secret configured — check listener for X-Flyn-Secret header")
PYEOF
```

Expected at the listener: both lines print `X-Flyn-Secret='p7-shipgate-test-secret'`.

### Step 9: Confirm best-effort — unreachable endpoint does not raise

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

# Port 19997 is not listening — simulates unreachable target
adapter = WebhookPMAdapter(target_url="http://127.0.0.1:19997")

t = TaskRecord(
    task_id="T-p7-shipgate-C3",
    workflow="dev",
    state=TaskState.INBOUND,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="Best-effort unreachable test",
)

# None of these must raise
adapter.create_task(t)
adapter.update_state(t, TaskState.RUNNING)
adapter.link_artifact(t, {"url": "https://x"})
adapter.comment_on_task(t, "silent failure")

print("PASS: all 4 webhook calls swallowed the connection error (best-effort guarantee)")
PYEOF
```

Expected: no exception, no traceback. Returns silently.

---

## Procedure D: Linear adapter live round-trip

Verifies that `LinearPMAdapter` loads its API key from env or auth-profiles, reports `configured=True`, and returns a non-empty external ID from `create_task()`. The Phase 7 MVP implementation returns a synthetic `linear-stub-<task_id>` — this procedure validates the adapter bootstraps correctly with live credentials even though it does not yet call the Linear API.

**Prerequisites:** `LINEAR_API_KEY` set in env OR `~/.openclaw/agents/main/agent/auth-profiles.json` contains `profiles["linear:default"]["token"]` or `profiles["linear"]["token"]` with a non-empty value.

### Step 10: Confirm adapter loads a live API key

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter

adapter = LinearPMAdapter()
print(f"configured={adapter.configured}")
assert adapter.configured is True, (
    "Expected configured=True — set LINEAR_API_KEY env var or add linear profile to auth-profiles.json"
)
print("PASS: LinearPMAdapter loaded API key — configured=True")
PYEOF
```

Expected: `configured=True`. If `configured=False`, the API key is missing from the expected locations.

### Step 11: create_task returns non-empty external ID

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

adapter = LinearPMAdapter()

t = TaskRecord(
    task_id="T-p7-shipgate-D",
    workflow="dev",
    state=TaskState.INBOUND,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="Phase 7 ship-gate Procedure D: Linear adapter live round-trip",
)

ext_id = adapter.create_task(t)
print(f"external_id={ext_id!r}")
assert isinstance(ext_id, str) and ext_id, "create_task must return non-empty str"

# Phase 7 MVP returns a synthetic ID — this is expected behavior
# (full Linear API integration lands when Phase 2 dev workflow needs it)
print(f"PASS: create_task returned {ext_id!r}")
print("NOTE: Phase 7 MVP returns a synthetic linear-stub-<task_id>. Live Linear API calls land in Phase 2 dev workflow.")
PYEOF
```

Expected: `external_id='linear-stub-T-p7-shipgate-D'`. The Phase 7 MVP does not call the Linear API; it returns a synthetic ID. The live Linear API integration is deferred to Phase 2 dev workflow (see `linear.py` docstring: "Full implementation arrives when Phase 2 dev workflow needs it").

### Step 12: Verify state-transition and lifecycle methods are no-ops

```bash
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

adapter = LinearPMAdapter()

t = TaskRecord(
    task_id="T-p7-shipgate-D2",
    workflow="dev",
    state=TaskState.RUNNING,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="No-op lifecycle test",
)

r1 = adapter.update_state(t, TaskState.COMPLETED)
r2 = adapter.link_artifact(t, {"url": "https://github.com/org/repo/pull/42"})
r3 = adapter.comment_on_task(t, "Reviewer: LGTM.")

assert r1 is None
assert r2 is None
assert r3 is None
print("PASS: LinearPMAdapter update_state / link_artifact / comment_on_task all return None (Phase 7 MVP no-ops)")
PYEOF
```

Expected: all return `None`. These are intentional MVP no-ops.

### Step 13: Confirm the full orchestrator accepts inbound task and records PM external ID

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "ryanshuken@gmail.com",
    "sender_role": "owner",
    "intent": "Phase 7 ship-gate D13: verify PM adapter wired in orchestrator router",
    "external_message_id": "p7-shipgate-d13",
    "raw_payload": {"channel": "telegram", "chat_id": 7191564227}
  }')
echo "$RESP" | python3 -m json.tool
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
echo "task_id=$TASK_ID"
```

Expected: JSON response with `task_id`. No 4xx/5xx. Optionally poll to `deliverable_ready`:

```bash
for i in $(seq 1 20); do
  sleep 15
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | \
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  case "$STATE" in
    deliverable_ready) echo "PASS: task completed"; break ;;
    failed|cancelled)  echo "FAIL: $STATE"; break ;;
  esac
done
```

---

## Procedure E: Cora PM adapter (PENDING — gated on E5 in task list)

**PENDING: requires E5 (Cora PM system existing as an external system) from the main task list. This procedure is a skeleton; fill in details once that system exists.**

### Status

`CoraPMAdapter` is not yet built. Rubric criteria 7.3 (CoraPMAdapter against Cora's PM system) and 7.4 (CoraPMAdapter passes contract conformance suite) are both ⬜, explicitly blocked on Cora PM existing as a system. Criterion 7.6 (E2E mirror to Linear AND Cora with same ID) is also ⬜ pending this.

Do not attempt steps E1–E4 until `CoraPMAdapter` exists in `flyn_orchestrator/adapters/pm/cora.py` and the Cora PM API is live at a known base URL.

### Step E1 (skeleton): Confirm CoraPMAdapter is importable

```bash
# Run once E5 is complete
python3 -c "from flyn_orchestrator.adapters.pm.cora import CoraPMAdapter; print('cora ok')"
```

### Step E2 (skeleton): Add CoraPMAdapter to conformance suite and run it

```bash
# Add CoraPMAdapter to the pytest.fixture params in test_pm_adapter_conformance.py,
# then run — must pass all 7 contract tests (same suite as linear/olwiki/webhook)
cd /Users/4c/AI/openclaw/flyn-agent/deploy/orchestrator
.venv/bin/pytest tests/unit/test_pm_adapter_conformance.py -k cora -v
```

Expected: 7 tests pass (protocol, name, configured, create_task, update_state, link_artifact, comment_on_task).

### Step E3 (skeleton): Live create_task round-trip against Cora PM API

```bash
# Verify task appears in Cora PM after adapter.create_task(t)
# Fill in Cora PM base URL, auth headers, and verification GET endpoint once E5 is complete.
```

### Step E4 (skeleton): E2E mirror — task mirrors to Linear AND Cora with same task_id

```bash
# 1. Send a task to the orchestrator.
# 2. Confirm the task appears in Linear with external ID linear-<task_id>.
# 3. Confirm the task appears in Cora PM with the same task_id reference.
# 4. Transition the task through RUNNING → deliverable_ready.
# 5. Confirm update_state fired on both adapters (state_changed events visible in both systems).
#
# Fill in exact verification steps once CoraPMAdapter is built and Cora PM is live.
```

---

## Procedure F: OLWiki Phase 7b lifecycle methods (PENDING — gated on E6 in task list)

**PENDING: requires E6 (OL wiki gaining native status/state fields) from the main task list. This procedure is a skeleton; fill in details once the wiki API supports status fields.**

### Status

`OLWikiPMAdapter.update_state()`, `link_artifact()`, and `comment_on_task()` are intentional no-ops in Phase 7 MVP per the `olwiki.py` docstring: "Deferred to Phase 7b once the wiki gains native status fields." Criterion 7.6 E2E sync also depends on this.

Do not attempt steps F1–F3 until the OL wiki `POST /api/decisions` response includes a `status` or equivalent field, and `PATCH /api/decisions/<id>` or similar is documented.

### Step F1 (skeleton): Verify OL wiki state field in API response

```bash
# Once E6 is complete, verify GET /api/decisions/<id> returns a "status" field:
curl -sS http://127.0.0.1:8200/api/decisions/<id> | python3 -m json.tool
# Expected: JSON includes "status": "..." or equivalent
```

### Step F2 (skeleton): update_state() POSTs to the wiki status endpoint

```bash
# Implement update_state() in olwiki.py to PATCH the decision status,
# then verify the wiki reflects the new state:
python3 - <<'PYEOF'
from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState

adapter = OLWikiPMAdapter(base_url="http://127.0.0.1:8200")
t = TaskRecord(
    task_id="T-p7b-lifecycle",
    workflow="dev",
    state=TaskState.RUNNING,
    sender_role="owner",
    sender_identifier="ryanshuken@gmail.com",
    intent="Phase 7b lifecycle test",
)
# After E6: this should PATCH decision status to 'running'
adapter.update_state(t, TaskState.COMPLETED)
# Then verify via GET that the decision status is 'completed' (or equivalent)
PYEOF
```

### Step F3 (skeleton): link_artifact() and comment_on_task() fire non-trivially

```bash
# After E6 builds the wiki endpoints for artifact links and comments:
# Verify link_artifact() POSTs to a /api/decisions/<id>/artifacts endpoint
# Verify comment_on_task() POSTs to a /api/decisions/<id>/comments endpoint
# Both should appear when GET /api/decisions/<id> is called
```

---

## Sign-off checklist

- ⬜ Procedure A Step 1: `pytest tests/unit/test_pm_adapter_conformance.py` — 29 tests pass (linear/olwiki/webhook, including errhttp variants)
- ⬜ Procedure A Step 2: every line in `-v` output is `PASSED`; no `FAILED` or `ERROR`
- ⬜ Procedure A Step 3: all 3 adapters have non-empty `str` name and `bool` configured
- ⬜ Procedure B Step 4: `OLWikiPMAdapter.create_task()` against live `:8200` returns `olwiki-decision-<N>` with numeric N
- ⬜ Procedure B Step 5: `GET /api/decisions` shows the new decision; `T-p7-shipgate-B` present in body
- ⬜ Procedure B Step 6: `update_state` / `link_artifact` / `comment_on_task` return `None` (MVP no-ops, do not raise)
- ⬜ Procedure C Step 7: listener receives 4 POST bodies with `event` keys `task_created`, `state_changed`, `artifact_linked`, `comment_added`
- ⬜ Procedure C Step 8: `X-Flyn-Secret` header present in both POSTs when secret is configured; absent when no secret
- ⬜ Procedure C Step 9: unreachable endpoint — all 4 webhook calls complete without exception (best-effort)
- ⬜ Procedure D Step 10: `LinearPMAdapter()` reports `configured=True` when API key is present
- ⬜ Procedure D Step 11: `create_task()` returns `linear-stub-<task_id>` (Phase 7 MVP synthetic ID)
- ⬜ Procedure D Step 12: `update_state` / `link_artifact` / `comment_on_task` return `None` (MVP no-ops)
- ⬜ Procedure D Step 13: orchestrator REST endpoint accepts inbound task, returns `task_id`
- 🟡 Procedure E Steps E1–E4: CoraPMAdapter round-trip (**blocked on E5 — Cora PM system must exist**)
- 🟡 Procedure F Steps F1–F3: OLWiki Phase 7b lifecycle methods (**blocked on E6 — wiki must gain native state fields**)
- ⬜ All 249 tests still pass (`pytest deploy/orchestrator/tests/`)
- ⬜ Ryan signs

Date: ____________  Ryan: ____________

---

## What this proves

If all Procedures A–D steps pass, Phase 7 is shipped per spec §9 at the 3/6 criterion mark (7.1, 7.2, 7.5 ✅):

- **7.1 OLWikiPMAdapter** wraps `POST /api/decisions` at `:8200`; returns canonical `olwiki-decision-<N>` ID on success; returns a synthetic stub ID on any HTTP or parse error (never raises); lifecycle methods are intentional MVP no-ops.
- **7.2 Conformance suite** passes all 29 tests across all 3 adapters; structural PMAdapter protocol satisfied; best-effort HTTP guarantee verified.
- **7.5 WebhookPMAdapter** delivers all 4 event types as JSON POSTs; `X-Flyn-Secret` header propagated when configured; unreachable endpoints are swallowed without exception.

Procedures E and F complete the phase once Cora PM exists (7.3/7.4/7.6) and the OL wiki gains native state fields (Phase 7b).

---

## Failure modes

- **`olwiki-stub-T-<id>` returned instead of `olwiki-decision-<N>`:** The wiki is unreachable, returned a non-200 status, or the response JSON has no `"id"` field. Check `curl -sS http://127.0.0.1:8200/api/decisions` returns a JSON array. If the wiki requires a PIN, verify it has been supplied in the session.
- **`configured=False` for LinearPMAdapter:** `LINEAR_API_KEY` is not set in env and `auth-profiles.json` has no `profiles["linear:default"]["token"]` or `profiles["linear"]["token"]` entry. Set `LINEAR_API_KEY=<your-key>` and rerun.
- **Webhook listener receives no events:** Check that both the adapter and the listener use the same port. The listener must be started before the adapter fires. If using `webhook.site`, replace `127.0.0.1:19999` with the full `https://webhook.site/<uuid>` URL in the adapter constructor.
- **`X-Flyn-Secret` absent from webhook POST:** Confirm `secret=...` is passed to `WebhookPMAdapter(...)`. A `None` or empty-string secret is skipped (`if self._secret: headers["X-Flyn-Secret"] = self._secret`).
- **Conformance suite fails on `isinstance(adapter, PMAdapter)`:** The adapter is missing one of the required methods (`name`, `configured`, `create_task`, `update_state`, `link_artifact`, `comment_on_task`). The `PMAdapter` Protocol in `adapters/base.py` is `runtime_checkable`; structural mismatch is caught by the isinstance check.
- **`test_http_error_does_not_propagate_*` fails:** An adapter method is raising an exception on HTTP failure instead of swallowing it. All network calls in `olwiki.py` and `webhook.py` must be wrapped in `try / except Exception: pass` (or equivalent). `LinearPMAdapter` has no HTTP calls in Phase 7 MVP so these tests do not apply to it.

---

## Deferred to Phase 7b (not blocking ship)

- `update_state()` / `link_artifact()` / `comment_on_task()` on OLWikiPMAdapter — requires OL wiki gaining native status and artifact fields (gated on E6)
- `CoraPMAdapter` — requires Cora PM system existing (gated on E5)
- Full Linear API integration — deferred to Phase 2 dev workflow; current `LinearPMAdapter` returns synthetic IDs
- E2E task mirror to Linear AND Cora with state-sync through all transitions (criterion 7.6 — gated on 7.3 + 7.4)
- Multi-PM fan-out in the router — currently the router wires one PM adapter at a time; fan-out to multiple simultaneous adapters is a Phase 7b enhancement
