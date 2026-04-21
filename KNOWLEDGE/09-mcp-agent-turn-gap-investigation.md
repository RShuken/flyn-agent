---
name: OpenClaw agent turns do NOT reliably invoke MCP tools from registered servers
description: Registering an MCP server via `openclaw mcp set` or `plugins.entries.acpx.config.mcpServers.*` makes it present in config, but agent turns (default, ACPX-enabled, or --local) don't actually invoke its tools — they hallucinate the call.
type: feedback
originSessionId: b6add74d-697e-4ae2-a0e0-e9dfb6dbcc2f
---
**Observed on 4C 2026-04-21, OpenClaw 2026.4.15:** flyn-graphiti MCP server was registered via both methods below, ACPX was enabled, gateway restarted. Multiple agent turns asked Flyn to "call flyn-graphiti add_episode tool" with specific episode bodies. Every turn:
1. Responded with plausible text ("Added", "Done", "episode is still processing")
2. Did NOT actually call the MCP tool (Neo4j episode count never incremented past the Python smoke-test baseline of 1)
3. Left ZERO MCP-related trace in the gateway log

**What's registered:**
- `mcp.servers.flyn-graphiti` (via `openclaw mcp set`) — stored, visible in `openclaw mcp list`
- `plugins.entries.acpx.config.mcpServers.flyn-graphiti` — stored after explicit ACPX enable
- Gateway restarted cleanly after each change

**What works independently of the integration:**
- Graphiti Python SDK end-to-end (phase 6c smoke test — ingested 1 episode, extracted 4 typed relationships with temporal anchors, search returned all 4)
- Graphiti MCP server boots cleanly via `flyn-graphiti-launch.sh`, logs confirm LLM + Embedder + Database + Transport all initialized

**Hypothesis (unverified):**
- OpenClaw's default agent runtime (Pi embedded harness) may not load MCP tools from either config path
- ACPX runtime may need additional config (permissions, enabled tools list) to surface MCP tools
- OR there's an undocumented per-agent allowlist for MCP tools

**Known-working workaround:** Call Graphiti from a Python cron job that reads daily session files and invokes `graphiti.add_episode()` directly (no MCP in the loop). The Python path is proven. This sacrifices in-session graph queries but preserves ingestion.

**Next investigation paths (for a fresh session):**
1. Switch `agents.defaults.embeddedHarness.runtime` from `auto` to explicit `codex` or another harness known to support MCP
2. Check `agents.defaults.tools` / allowlist schema for MCP inclusion flag
3. Look at OpenClaw's `channels` + `mcp serve` pattern — maybe agent MCP consumption is routed differently
4. Look at a known-working OpenClaw plugin that uses MCP internally (context7, etc.) to mirror its pattern
5. File a GitHub issue if the docs don't clarify the wiring

**Don't spin on this further without a concrete new lead. The Python path provides a working fallback.**

## Update 2026-04-21 — fuller investigation results

Tried 4 more specific paths after initial report, all still hallucinated tool use (Neo4j episode count never incremented past 1):

1. **Primary model swap** — `agents.defaults.model.primary` changed from `openai-codex/gpt-5.4` to `anthropic/claude-sonnet-4-6`. Rationale: GitHub openclaw/openclaw#53959 documents a codex-specific tool_use emission regression. Hypothesis: swap to Claude would bypass. Result: same hallucination. **So Codex tool_use regression was NOT our cause.**

2. **Explicit ACPX registration** — `plugins.entries.acpx.config.mcpServers.flyn-graphiti` + `plugins.entries.acpx.enabled = true`. PR openclaw/openclaw#39337 documents ACPX as the path. Result: ACPX config accepted, but zero `acpx` log activity during agent turns. Suggests `openclaw agent` doesn't actually go through ACPX harness.

3. **Switching embedded harness** — `agents.defaults.embeddedHarness.runtime = codex`. Reverted after no effect. Default `auto` and explicit `codex` both hallucinated.

4. **Community plugin `@aiwerk/openclaw-mcp-bridge` v0.13.5** — installed via `openclaw plugins install`, configured with the same flyn-graphiti server. Logs confirmed `[mcp-bridge] Plugin activated with 1 servers configured`. Result: still hallucinated. Plugin activates but doesn't visibly surface tools to the LLM's tool schema at turn time.

## Conclusion

**Across default harness, ACPX, codex harness, embedded `--local`, codex primary, Claude primary, and the mcp-bridge community plugin — `openclaw agent` does NOT invoke MCP server tools in any configuration we tried on OpenClaw 2026.4.15.** The MCP server boots correctly and exposes 9 tools via stdio, but OpenClaw's agent-runtime-to-tool-schema glue layer is not surfacing them to the LLM.

## Working alternative: shell-wrapped Python SDK

Skip MCP entirely for agent-in-turn calls. Wrap Graphiti's Python SDK in a shell script and let Flyn use OpenClaw's native exec capability (which IS emitting tool_use blocks correctly for other tools). Gives us:
- Agent-driven ingestion + queries (via shell tool)
- Python cron ingestion (scheduled)
- Direct Python SDK calls from any automation script

Architecture is equivalent to MCP for Flyn's use case; just a different transport. Proven to work end-to-end in phase 6c.
