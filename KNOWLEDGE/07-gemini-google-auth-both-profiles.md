---
name: OpenClaw Gemini auth uses provider id "google", not "gemini"
description: Gemini embedding provider advertises id "gemini" but the auth store looks up tokens under provider id "google". Store the key under both profile keys.
type: feedback
originSessionId: b6add74d-697e-4ae2-a0e0-e9dfb6dbcc2f
---
When wiring Google Gemini into OpenClaw (2026.4.15+), the embedding capability advertises provider id `gemini` in `openclaw capability embedding providers` output, but the actual runtime auth lookup asks for a key under provider id `google`.

**Why:** discovered 2026-04-20 on Mac Mini 4C while wiring Flyn's Gemini embedding stack. First stored the API key as `gemini:default` in `auth-profiles.json`; the provider list showed `configured: true`, but an embedding create call failed with: `Error: No API key found for provider "google"`. Adding a `google:default` profile with the same token resolved it.

**How to apply:**
- When adding a Gemini/Google API key to `~/.openclaw/agents/<id>/agent/auth-profiles.json`, write BOTH profiles:
  ```json
  "gemini:default": { "type": "token", "provider": "gemini", "token": "AIza..." },
  "google:default": { "type": "token", "provider": "google", "token": "AIza..." }
  ```
- Belt-and-suspenders — some OpenClaw call paths reference one, others the other. Having both avoids chasing lookup mismatches later.
- If this mismatch gets patched upstream, `google:default` alone should be sufficient (it's the one the runtime actually reads).
- Verify with a live smoke test: `openclaw capability embedding create --provider gemini --text "test" --json` — expect `"ok": true`.
