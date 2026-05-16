---
name: discriminate-oauth-vs-api-key-tokens
description: Anthropic auth-profiles store both `sk-ant-oat-*` (OAuth) and `sk-ant-api-*` (API key) tokens in the same slot. Loaders must check the prefix — passing an OAuth token as ANTHROPIC_API_KEY fails every worker silently.
type: reference
---

# Discriminate OAuth tokens from API keys

Hotfix commit `2ea787d` during Phase 1 verification. The `_load_anthropic_api_key_from_profiles()` helper in `flyn_orchestrator/backends/claude_p.py` was returning ANY token stored under `anthropic:default` in `auth-profiles.json`. When that token was an OAuth refresh token (`sk-ant-oat-*`), it got passed to the headless `claude -p` worker as `ANTHROPIC_API_KEY`. Anthropic's API rejected it with an auth error; the worker exited 0-byte, the task happily advanced to `deliverable_ready`, and we wasted a turn.

## The discriminator

```python
def _load_anthropic_api_key_from_profiles() -> Optional[str]:
    """..."""
    p = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            d = json.load(f)
        for key in ("anthropic:default", "anthropic"):
            if key in d.get("profiles", {}):
                token = d["profiles"][key].get("token", "")
                if token.startswith("sk-ant-api"):
                    return token
                # Anything else (sk-ant-oat-..., empty, etc) is not a valid API key.
                return None
    except Exception:
        pass
    return None
```

The change is the prefix check: `sk-ant-api*` only. Returning `None` for OAuth tokens makes the backend fall back to OAuth-via-credentials-cache (`~/.claude/.credentials.json`), which is the correct path for OAuth-authenticated subscriptions.

## Why this lands silently

1. `claude -p` accepts `ANTHROPIC_API_KEY` from env without validating the prefix.
2. The auth call to Anthropic happens lazily at first message generation.
3. The auth failure surfaces as a generic "authentication error" in the worker's stream — but Phase 1's 0-byte capture guard (1b.1) was not yet in place, so the worker exited silently with no output.
4. The orchestrator's `run_task` happy path saw `exit_code=0` and advanced to `deliverable_ready` without producing any actual deliverable.

This is a "two bugs make a silent failure" pattern: the wrong-token bug alone would have shown up as a worker error; the missing 0-byte guard alone would have been fine for valid auth. Together they masked each other.

Phase 1b shipped both fixes (`1b.1` 0-byte guard + `1b.4` OAuth fallback discrimination) in the same PR. Now if EITHER fails, the task transitions to `FAILED` with a diagnostic.

## Companion knowledge

- Phase 1b's 0-byte capture guard (`dispatcher.py`): refuses to advance to `reviewed` if `result.capture_path` is < 100 bytes.
- The cost-tracking implication: API-key auth is per-token billed, not subscription. For Cora MVP usage (~$0.45/task) this is fine, but at scale the OAuth path saves real money. The fallback is intentional: prefer OAuth when available, fall back to API key only when explicitly configured.

## Inspecting your auth-profiles

```bash
python3 -c '
import json
from pathlib import Path
p = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
d = json.load(open(p))
for key, prof in d.get("profiles", {}).items():
    token = prof.get("token", "")
    kind = "API key" if token.startswith("sk-ant-api") else "OAuth" if token.startswith("sk-ant-oat") else "OTHER/empty"
    print(f"{key}: {kind} ({token[:12]}…)")
'
```

If your `anthropic:default` says `OAuth`, the headless workers will fall back to OAuth-via-credentials-cache — which competes with your interactive Claude Code session. Generate a real API key at `console.anthropic.com` and replace it for safe parallel use.
