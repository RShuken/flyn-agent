#!/usr/bin/env bash
# Seed Flyn's Graphiti KG with core architectural + operator facts so queries
# return something useful from day 1.
#
# Usage:
#   ./seed-flyn-graphiti.sh          # ingest all episodes (blocks, ~15-25 min total)
#   ./seed-flyn-graphiti.sh --check  # show what would be ingested, no POST
#
# Each POST blocks 30-120s during local gemma4:e4b entity extraction. Normal.
# Runs serially (concurrent POSTs would fight the model). If interrupted, safe
# to re-run — Graphiti will just add duplicate episodes, which is cheap to
# deduplicate later via a prune script.

set -euo pipefail

KG_API="http://localhost:8100"
MODE="ingest"
[[ "${1:-}" == "--check" ]] && MODE="check"

log() { printf "\033[1;34m▶\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m!\033[0m %s\n" "$*"; }

# Preflight
if ! curl -sSf --max-time 5 "${KG_API}/api/health" | grep -q '"status":"ok"'; then
  warn "Graphiti REST at ${KG_API} not healthy. Start the launchd service first:"
  echo "  launchctl kickstart -k gui/\$(id -u)/ai.flyn.graphiti-api"
  exit 1
fi

post_ep() {
  local name="$1"
  local body="$2"
  if [ "$MODE" = "check" ]; then
    printf '  [%s]\n    %s\n' "$name" "$(echo "$body" | head -c 120)…"
    return 0
  fi
  log "ingesting: $name"
  RESP="$(curl -sS --max-time 600 -X POST "${KG_API}/api/episode" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; n,b=sys.argv[1:]; print(json.dumps({"name":n,"body":b}))' "$name" "$body")" \
    2>&1)" || { warn "POST failed: $RESP"; return 1; }
  if echo "$RESP" | grep -q '"ok":true'; then
    ok "$name"
  else
    warn "unexpected response: $RESP"
  fi
}

# ---------- SEED EPISODES ----------
#
# Each episode is written as natural prose. Graphiti extracts typed entities
# and edges with temporal anchors. Dates in the prose help Graphiti infer
# `valid_at` correctly.

post_ep "flyn-core-identity" \
"Flyn is the primary OpenClaw agent deployed on Mac Mini 4C as of 2026-04-21. Flyn is the CEO of its machine — it owns strategy, orchestration, execution, and interactive turns on 4C. Flyn is fully autonomous within Ryan's approval gates. Flyn is not subordinate to any other agent."

post_ep "flyn-primary-model" \
"Flyn uses openai-codex/gpt-5.4 as its primary LLM for interactive user-facing turns. Auth is via OpenAI OAuth subscription (flat-rate, not pay-per-token). This is the cost-controlled path. Do not switch primary to Anthropic Claude without explicit Ryan approval — Anthropic lacks a subscription tier and would flip Flyn to per-token billing."

post_ep "flyn-background-model" \
"Flyn uses ollama/gemma4:e4b as its local background model for heartbeat, cron, fact extraction, and any non-user-facing inference. The model is ~9.6 GB on disk and ~11 GB in Metal memory when loaded. It supports native tool calling (unlike gemma3:4b which does not). All background work routes here; frontier cloud is reserved for user-chat turns only."

post_ep "flyn-structured-memory-architecture" \
"Flyn's structured memory is a Graphiti knowledge graph on Neo4j, wrapped by a local Flask REST API at http://localhost:8100. The agent reaches the API via curl from the exec shell tool — NOT via OpenClaw's MCP registration, which does not reliably surface tools to the agent's tool list on OpenClaw 2026.4.15. The REST pattern is the same one production operators (e.g., the Edge/Crusoe-Ventures deployment) use."

post_ep "flyn-context-engine" \
"Flyn's context engine is the Lossless Claw plugin v0.9.2 by Martian-Engineering, occupying plugins.slots.contextEngine. Lossless Claw preserves every conversation message in a SQLite DAG and uses summarization rather than truncation, giving Flyn zero-loss context management. Recovery tools: lcm_grep, lcm_describe, lcm_expand."

post_ep "flyn-embedding-provider" \
"Flyn uses gemini-embedding-001 (Google Gemini, cloud) as its primary embedding provider, with EmbeddingGemma 300M as a local air-gap fallback. The Gemini API key is stored under BOTH gemini:default and google:default profile IDs in auth-profiles.json — OpenClaw's embedding provider ID is gemini but runtime auth lookup uses google, and both entries are required."

post_ep "flyn-neo4j-backend" \
"Flyn's Neo4j is a Docker container named flyn-neo4j running Neo4j 5.26 with a 1 GB heap cap. Ports 7474 (HTTP) and 7687 (Bolt) are bound to 127.0.0.1 only (loopback trust boundary). Persistent volumes mount from ~/.openclaw/workspace/memory/structured/neo4j/. Steady-state memory footprint is approximately 830 MiB."

post_ep "flyn-rest-api-service" \
"The flyn-graphiti-api Flask service runs as a launchd agent named ai.flyn.graphiti-api. It auto-starts at boot, auto-restarts on crash with a 30-second throttle, and logs to /tmp/flyn-graphiti-api.log. Endpoints exposed: GET /api/health, POST /api/episode, GET /api/search, GET /api/temporal, GET /api/episodes. The group_id is hardcoded to 'flyn'."

post_ep "flyn-approval-gates" \
"Flyn requires explicit Ryan approval for: (1) external communication (email, DMs, public posts); (2) spending or subscription changes; (3) writes to production systems including Cora, Railway live, and any third-party API that mutates state; (4) destructive operations (delete, rollback, kill non-Flyn processes, force-push); (5) out-of-domain writes outside Flyn's own 4C scope; (6) auth changes including re-auth, provider setup, and Keychain migration."

post_ep "flyn-hard-nos" \
"Flyn will NEVER: send email or DMs without explicit Ryan approval; spend money or upgrade subscriptions without approval; claim work is done that isn't; auto-migrate auth secrets to macOS Keychain under launch-agent setup (64-hour outage precedent); use Anthropic Claude models in default routing for background work; default to running background-process tasks unless instructed or scheduled."

post_ep "ryan-operator-profile" \
"Ryan Shuken is Flyn's operator. He runs an OpenClaw consulting + building operation solo, based in Mountain Time (America/Denver, pending confirmation). Primary communication channel is Telegram, async preferred over synchronous. Decision-making style: give a range of options with a recommendation and the main tradeoff. Values low ongoing cost, fast iteration, research-first discipline, and honest reporting. Deep technical depth in TypeScript, Node, Cloudflare Workers, Supabase, SQLite, Apple Silicon stacks, and OpenClaw internals."

post_ep "ryan-cora-project" \
"Cora is Ryan's primary product, live at getcora.io. Cora's database runs on Supabase (Firebase is legacy, not used). Cora is deployed on Railway as of 2026-03-25 (migrated from Vercel). Services in the same Railway project can crash each other — verify all services after any deployment. Always verify locally first, then develop branch, then only with explicit go-ahead to production."

post_ep "openclaw-base-upstream" \
"openclaw-base at github.com/RShuken/openclaw-base is the public source-of-truth skill library that flyn-agent forks from. It contains the skill definitions, audit research, canonical templates for IDENTITY/SOUL/HEARTBEAT/etc, and install runbooks. flyn-agent carries Flyn-specific overrides and the POSTMORTEM. Library updates flow from openclaw-base upstream to flyn-agent via git fetch + merge."

post_ep "memory-routing-hierarchy" \
"Flyn's memory routing order, fastest/cheapest first: (1) MEMORY.md Hot tier (always in context, free); (2) Graphiti REST for typed temporal predicate queries via curl to localhost:8100; (3) openclaw memory search for fuzzy semantic recall over workspace/memory/*.md via sqlite-vec + Gemini embeddings; (4) Lossless Claw lcm_grep/lcm_describe/lcm_expand for exact recovery of specific turns from compacted conversation history. Never use frontier cloud to remember something already in one of these four layers."

post_ep "known-gotcha-node-25-tls" \
"CRITICAL known gotcha: Homebrew Node 25 has a TLS fingerprint that Cloudflare blocks, causing Codex LLM calls to fail with DNS-lookup errors. OpenClaw 2026.4.14 also had a models.json corruption regression. The fix: tarball Node 22 LTS installed to ~/.local/node and OpenClaw 2026.4.12+ with a clean models.json. Ian Ferguson had a 14-hour outage from this combination on 2026-04-17. Always verify `node --version` returns v22.x on a fresh install."

post_ep "known-gotcha-mcp-hallucination" \
"Known gotcha on OpenClaw 2026.4.15: registering an MCP server with openclaw mcp set or plugins.entries.<plugin>.config.mcpServers does NOT surface the MCP tools to the agent's tool list at turn time. The agent hallucinates the tool call (emits 'Added' text without actually invoking the tool, with zero MCP trace in the gateway log). Investigation exhausted six registration paths including the @aiwerk/openclaw-mcp-bridge community plugin. The working pattern instead is REST + curl from the exec shell tool."

post_ep "flyn-deploy-bootstrap" \
"Flyn was successfully deployed and bootstrapped on Mac Mini 4C on 2026-04-21 by Ryan. The deployment validated: Lossless Claw context engine, gemma4:e4b local heartbeat, Gemini 2 embeddings with EmbeddingGemma local fallback, Graphiti + Neo4j via REST on localhost:8100, and the curl-from-exec pattern for agent-driven structured memory access. The flyn-agent git repo (RShuken/flyn-agent, private) carries the idempotent install script, postmortem, and portable KNOWLEDGE directory."

# ---------- FINAL ----------

if [ "$MODE" = "check" ]; then
  echo
  ok "check mode — 16 episodes would be ingested"
  exit 0
fi

echo
ok "seed complete — verify with:"
echo "  curl -sS 'http://localhost:8100/api/episodes?limit=20' | python3 -m json.tool | head -20"
echo "  curl -sS 'http://localhost:8100/api/search?q=Flyn' | python3 -m json.tool | head -40"
