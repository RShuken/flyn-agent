---
name: Flyn structured memory — REST + curl pattern (the one that actually works)
description: Don't register structured-memory Graphiti as an MCP server with OpenClaw — wrap it in a local Flask REST on localhost:8100 and have the agent reach it via curl from the exec/shell tool. Same pattern Edge uses in production.
type: feedback
originSessionId: b6add74d-697e-4ae2-a0e0-e9dfb6dbcc2f
---
**Validated 2026-04-21 on Mac Mini 4C.** After exhausting 6 MCP registration paths — all resulted in hallucinated tool calls where the agent claimed success but never actually invoked — switching to a local REST API called via curl from OpenClaw's exec tool produced real end-to-end ingestion on the first try.

## The pattern

```
OpenClaw agent turn
   │ model emits real tool_use for exec tool (ALWAYS works — no MCP plumbing gap)
   ▼
OpenClaw exec/shell → runs: curl -sS -X POST http://localhost:8100/api/episode -d '{...}'
   ▼
Flyn Graphiti REST (Flask, ~8KB Python at ~/.openclaw/workspace/kg/flyn-graphiti-api.py)
   │ wraps graphiti-core 0.28.2
   ▼
Neo4j Docker (flyn-neo4j, 1GB heap, localhost:7687)
```

**Why this works and MCP didn't:** OpenClaw's default agent harness surfaces `exec` and a handful of other built-in tools to the model's tool list correctly. It does NOT surface MCP-registered servers' tools (despite the config being valid across all 3+ registration paths). `curl` is just a shell command; Codex / Claude both emit real tool_use blocks for `exec`.

## File layout on 4C

- `~/.openclaw/workspace/kg/flyn-graphiti-api.py` — Flask app, loads Graphiti, exposes 5 endpoints
- `~/.openclaw/workspace/memory/structured/graphiti-venv/` — dedicated Python venv (graphiti-core[google-genai] + flask + deps)
- `~/Library/LaunchAgents/ai.flyn.graphiti-api.plist` — launchd service, auto-start at boot, auto-restart on crash, throttle 30s
- Secrets read at runtime from `~/.openclaw/agents/main/agent/auth-profiles.json` profiles: `neo4j:default`, `google:default`. Never embedded in plist or source.

## Endpoints exposed

- `GET /api/health` — liveness + Neo4j connectivity
- `POST /api/episode` body `{body, name?, source?, valid_at?}` — ingest, Graphiti auto-extracts typed entities + edges
- `GET /api/search?q=...` — semantic + graph search over facts (edges with valid_at/invalid_at)
- `GET /api/temporal?q=...&from=ISO&to=ISO` — temporal-filtered fact search
- `GET /api/episodes?limit=N` — raw episode list

## Gotchas

- `POST /api/episode` blocks 30–120s while local gemma4:e4b runs the 4-step entity-extraction pipeline. Set client timeout > 300s. My original 120s timeout caused `TimeoutError` with empty error string; bumped to 600s.
- Neo4j `DateTime` is not JSON-serializable by Flask default. `_coerce()` helper recursively converts `.isoformat()` on anything with that method.
- launchd plist needs explicit `HOME` env var and the graphiti-venv bin on PATH — inherited shell env isn't enough.
- `group_id` is hardcoded to `flyn` in the API. Every ingest lands there. If multi-group needed later, make it a query param.

## How Flyn actually calls it (from AGENTS.md)

The agent's system prompt now contains curl snippets as literal shell patterns. The model generates `exec` tool_use → OpenClaw runs the bash → REST returns JSON → agent reads and formats. Same pattern as any other shell tool call.

## Why not use graphiti-mcp-server

We installed it, registered it 3 different ways (`mcp.servers.*`, `plugins.entries.acpx.config.mcpServers.*`, `@aiwerk/openclaw-mcp-bridge`), enabled ACPX explicitly — agent STILL hallucinated every tool call. See `feedback_openclaw_agent_mcp_invocation_gap.md` for the full investigation. Edge (Dan Caruso's production agent, same OpenClaw version) ALSO doesn't use MCP for this — they run a standalone MCP server + a REST wrapper and both are called via shell, not registered with OpenClaw. That reference confirmed REST was the pattern, not an architectural compromise.

## Evidence it works

End-to-end: sent `openclaw agent -m "POST to http://localhost:8100/api/episode with body ... then GET /api/search ..."`. Neo4j episode count went 1 → 2 in real time; new episode name matches the one specified in the prompt. Gateway log showed real shell exec tool_use blocks. First time across ~8 test attempts that an agent turn actually modified the KG.
