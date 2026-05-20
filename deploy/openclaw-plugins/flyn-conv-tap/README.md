# flyn-conv-tap

OpenClaw plugin that captures every inbound channel message and forwards it
to `flyn-memory-router`'s `/api/memory/ingest` endpoint with
`event_type="conversation_message"`. Memory-router's conv tier then
encrypts, persists, summarizes via Ollama, and promotes to Graphiti.

## Why this plugin exists

The conv tier ([D1](../../docs/superpowers/specs/2026-05-19-conversation-memory-design.md))
needs every Telegram (and future channel) message to land in per-owner
SQLite. Openclaw doesn't auto-capture messages into memory by itself — its
`memory-core` plugin only registers search tools that the agent calls
explicitly. So we need an explicit forwarder.

Initial design tried a shell script in `hooks.internal.entries`. That was
wrong: that section is for openclaw lifecycle hooks (boot, compaction,
command-logger), not message interception. The canonical surface is the
`message_received` PluginHookName from `openclaw/plugin-sdk/plugin-entry`.

## How it works

1. Registers `definePluginEntry({ register: api => api.registerHook("message_received", ...) })`
2. On every inbound message: `POST http://localhost:8400/api/memory/ingest`
3. Memory-router's `conv_write_adapter` takes over (AES-GCM seal, SQLite write,
   enqueue summary job, promote to Graphiti)
4. Plugin never blocks the reply path — forward errors are logged and swallowed

## Build

```bash
npm install
npm run build  # → dist/index.js (~2.9 KB, esbuild bundled)
```

## Install

```bash
openclaw plugins install --link /Users/4c/AI/openclaw/flyn-agent/deploy/openclaw-plugins/flyn-conv-tap
# Restart gateway to pick up the plugin
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

Verify it loaded:

```bash
openclaw plugins list | grep flyn-conv-tap
# status should be "loaded", enabled true
```

## Config

In `~/.openclaw/openclaw.json` under `plugins.entries.flyn-conv-tap`:

```json
{
  "enabled": true,
  "routerUrl": "http://localhost:8400",
  "forwardOutbound": false,
  "timeoutMs": 1500
}
```

- `forwardOutbound`: set true to also capture Flyn's outbound replies
  via the `message_sent` hook (default off — inbound only)

## Verify end-to-end

1. Send a Telegram message to `@flyn_4c_bot` containing a unique phrase
2. Within ~5 seconds, check the owner DB:
   ```bash
   sqlite3 ~/.flyn/memory-router/conv/owners/ryan.db \
     "select id, ts, substr(body,1,80) from messages order by id desc limit 3"
   ```
3. Within ~30 seconds, summary should populate
4. Cross-source query should find it:
   ```bash
   /Users/4c/.flyn/memory-router/.venv/bin/flyn-mem query "<your phrase>"
   ```

## Failure modes

- Memory-router down/slow → forward errors logged, message processing continues
- Plugin disabled in config → no forwarding, no errors
- `message_received` hook only fires on **channel** messages (Telegram,
  Discord, etc), NOT on `openclaw agent -m ...` CLI invocations. To test,
  use a real channel message.
