# OL Wiki MCP Server

MCP server wrapping the [OL wiki backend](../wiki-backend/) so:

- **Flyn** (via openclaw exec/curl — see TOOLS.md) can drive PM operations from a Telegram conversation
- **Claude Code sessions** (Ryan, Eric, Beth) can mutate the project state via MCP tools without leaving the chat

## Tools exposed (8)

| Tool | Auth | Purpose |
|---|---|---|
| `list_questions` | open | Filter by owner/status/bucket/section/sprint/free-text |
| `get_question` | open | Fetch one question by id |
| `list_decisions` | open | All decisions, most recent first |
| `stats` | open | Aggregate counts (by status, owner, sprint, bucket) |
| `answer_question` | X-API-Key | Flip status → answered, record answer + audit |
| `reassign_question` | X-API-Key | Change owner, record reason + audit |
| `create_decision` | X-API-Key | Log a decision (mirrors RESOLVED.md entries) |
| `list_audit` | X-API-Key | Recent mutation events |

## Install

### Claude Code

```bash
claude mcp add ol-wiki --scope user -- \
  /Users/4c/AI/flyn-agent/deploy/wiki-mcp/.venv/bin/python \
  /Users/4c/AI/flyn-agent/deploy/wiki-mcp/server.py
```

### openclaw (Flyn)

Currently Flyn uses the HTTP API directly via `curl` from the exec tool (see
`workspace/TOOLS.md`). This is the proven pattern per POSTMORTEM-2026-04-21.md.
If openclaw's MCP client support is now stable, the same server.py can be
registered as an MCP source.

## Configuration

- `OL_WIKI_API_BASE` — defaults to `http://127.0.0.1:8200` (local 4C backend)
- `OL_WIKI_API_KEY` — defaults to loading from `~/.openclaw/agents/main/agent/auth-profiles.json` under `ol_wiki_api:default`
