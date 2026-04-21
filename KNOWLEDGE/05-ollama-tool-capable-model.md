---
name: Ollama local heartbeat model must support tool/function schemas
description: OpenClaw sends tool schemas with every inference call including heartbeat. Models without tool support (gemma3:4b) are rejected by Ollama with 400 and heartbeat falls back to cloud primary.
type: feedback
originSessionId: b6add74d-697e-4ae2-a0e0-e9dfb6dbcc2f
---
When picking a local Ollama model to route OpenClaw heartbeat / background / agent-turn calls to, **the model must support tool/function calling**. OpenClaw's request schema includes a tool/function definition on every call. Models that can't parse that schema get rejected by Ollama with HTTP 400 "provider rejected the request schema or tool payload," and the model-fallback system falls through the ladder back to the cloud primary (Codex/Anthropic).

**Why:** 2026-04-21 on Mac Mini 4C — configured `agents.defaults.heartbeat.model = ollama/gemma3:4b`, added `ollama:default` auth profile. Heartbeat DID route to Ollama first (log confirms `requestedProvider: "ollama"`). But gemma3:4b returned 400 format-reject, and OpenClaw fell back to `openai-codex/gpt-5.4-mini`. So we were paying cloud cost despite the intent to stay local.

**How to apply:**
- Pick Ollama models with native tool/function calling for anything OpenClaw routes through: heartbeat, background, compaction summarization, agent turns. Validated-good candidates: `qwen2.5:7b` / `qwen2.5:14b`, `llama3.2:3b`, `llama3.1:8b`. `qwen3:8b` was cited in prior research for stable tool-call formats.
- **Bad picks for heartbeat:** `gemma3:4b`, `gemma3:1b` (limited/no tool-calling), `embeddinggemma` (embedding-only), `phi3:*` (variable), any quantized Q2/Q3 variant of a larger model (tool-call formatting degrades).
- **Memory budget note:** on 16GB RAM hosts, qwen2.5:7b (~4.7GB) is the sweet spot for tool-capable local inference. Leaves ~10GB for OS + OpenClaw + other services.
- **Auth requirement (counterintuitive):** even though Ollama is local and needs no auth, OpenClaw requires an entry in `~/.openclaw/agents/<id>/agent/auth-profiles.json` under `ollama:default`. Without it, OpenClaw errors `401 No API key found for provider "ollama"` before even attempting the call.
  ```json
  "ollama:default": { "type": "token", "provider": "ollama", "token": "local" }
  ```
- **Verify via log, not CLI probe:** `openclaw capability model run --model X` has its own fallback quirks; the real routing behavior is visible in `/tmp/openclaw/openclaw-YYYY-MM-DD.log` under subsystem `model-fallback/decision`. Look for `requestedProvider`, `reason`, `nextCandidateProvider` to see what actually happened.
