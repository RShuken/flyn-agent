---
name: Graphiti + Neo4j + Local-LLM stack for OpenClaw — working recipe
description: Full working install for Graphiti temporal knowledge graph on 16GB Apple Silicon, Neo4j via Docker, entity extraction via local Ollama, embeddings via Gemini, exposed to OpenClaw via MCP.
type: feedback
originSessionId: b6add74d-697e-4ae2-a0e0-e9dfb6dbcc2f
---
End-to-end validated 2026-04-21 on Mac Mini 4C (OpenClaw 2026.4.15, Ollama 0.21.0, gemma4:e4b). All pieces below were tested live.

## Stack

- **Graph backend:** Neo4j 5.26 in Docker container `flyn-neo4j` (ports 7474/7687), 1 GB heap cap, persistent volumes under `~/.openclaw/workspace/memory/structured/neo4j/`. Memory footprint ~830 MiB steady-state.
- **Graphiti Python:** `graphiti-core[google-genai] 0.28.2` in venv at `~/.openclaw/workspace/memory/structured/graphiti-venv/`.
- **Graphiti MCP server:** cloned from `github.com/getzep/graphiti` tag `mcp-v1.0.2` to `~/.openclaw/workspace/memory/structured/graphiti-repo/`. Uses `uv` package manager. Must run `uv sync --extra providers` to enable Gemini embedder (default install does NOT include `google-genai`).
- **LLM for entity extraction:** local `ollama/gemma4:e4b` via OpenAI-compat at `http://localhost:11434/v1` — $0 inference cost.
- **Embeddings:** `gemini-embedding-001` stable (3072 dim) via `GeminiEmbedder`. Note: graphiti-core 0.28.2's `gemini` embedder model IDs lag vendor — `gemini-embedding-2-preview` may or may not parse cleanly; stable `001` works definitively.
- **Reranker/cross-encoder:** `OpenAIRerankerClient` with LLM config pointing at Ollama gemma4:e4b — keeps this call local too.

## Auth profiles pattern

Add to `~/.openclaw/agents/<id>/agent/auth-profiles.json`:
```json
"neo4j:default": {
  "type": "token",
  "provider": "neo4j",
  "user": "neo4j",
  "token": "<32-char-password>",
  "uri": "bolt://localhost:7687"
}
```
(Plus the existing `google:default`, `gemini:default`, `ollama:default`, etc.)

## Launcher wrapper

`~/.openclaw/workspace/memory/structured/flyn-graphiti-launch.sh` reads secrets from auth-profiles.json + execs `uv run main.py --transport stdio --config <flyn-config.yaml>`. This keeps secrets out of `openclaw.json` (which `openclaw mcp set` stores).

## MCP registration

```bash
openclaw mcp set flyn-graphiti '{"transport":"stdio","command":"/abs/path/to/flyn-graphiti-launch.sh","args":[]}'
```

## Validated end-to-end

Python smoke test (phase 6c): fed one prose episode "Flyn was deployed on 4C on 2026-04-21..." → Graphiti extracted 4 typed relationships (USES_AS_HEARTBEAT_MODEL, USES_FOR_EMBEDDINGS, DEPLOYED_ON, CONTEXT_ENGINE_FOR) with `valid_at` temporal anchors auto-inferred. Search query returned all 4 ranked.

## Gotchas

- **Graphiti core default cross-encoder uses OpenAI** — must pass `cross_encoder=OpenAIRerankerClient(config=ollama_llm_config)` explicitly when instantiating `Graphiti(...)`, else boot fails with `OpenAIError: api_key must be set`.
- **MCP server's `uv` env is separate** from the main graphiti-venv. Installing `google-genai` extra in the main venv does NOT make it available to the MCP server. Must `uv sync --extra providers` in `mcp_server/` directory.
- **Do NOT** set `OPENAI_API_URL` via global openclaw.json — it affects other code paths. Scope it to the MCP launcher's env only.
- **`openclaw mcp set` writes to openclaw.json as plaintext** — use a launcher shell script that reads secrets from auth-profiles.json at runtime rather than embedding them in the MCP `env` block.
- **Neo4j first-boot password** is set via `NEO4J_AUTH` env var at container start. Capture it (don't rely on shell history). If lost, `docker inspect <container> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep NEO4J_AUTH` retrieves it.

## When to use this

- Solo/small-team agent with 50+ entities where temporal queries matter ("what did I configure for client X last month")
- 16GB RAM host where Neo4j + Ollama + OpenClaw + gemma4 all need to coexist (confirmed fits)
- Want $0-variable-cost structured memory (all compute local except Gemini embedding calls)

## MCP tools exposed

`add_episode`, `search_nodes`, `search_facts`, `delete_entity_edge`, `delete_episode`, `get_entity_edge`, `get_episodes`, `clear_graph`, `get_status`

Agent needs the MCP tool names + purpose surfaced in `workspace/AGENTS.md` or system prompt for it to actually invoke them during turns. Registration alone doesn't cause use.
