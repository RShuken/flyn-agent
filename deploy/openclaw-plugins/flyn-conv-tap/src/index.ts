/**
 * flyn-conv-tap: openclaw plugin that captures every inbound channel message
 * and POSTs it to flyn-memory-router's /api/memory/ingest with the
 * conversation_message event_type. The router's conv_write_adapter takes it
 * from there (encrypt with AES-GCM, write SQLite row, enqueue summary job,
 * promote to Graphiti).
 *
 * Hook: `message_received` (canonical PluginHookName from openclaw plugin SDK).
 *
 * Failure mode: forward errors are logged and swallowed — never block the
 * agent's reply path. The router being down or slow must not break Flyn.
 */

// Canonical SDK subpath as of openclaw 2026.5.18 (the bare `openclaw/plugin-sdk`
// path is the compat shim and is deprecated for new plugins).
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — resolved at runtime against openclaw/plugin-sdk/plugin-entry
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

type InternalHookEvent = {
  type: string;
  action: string;
  sessionKey: string;
  context: Record<string, unknown>;
  timestamp: Date;
  messages: string[];
};

type PluginConfig = {
  enabled?: boolean;
  routerUrl?: string;
  forwardOutbound?: boolean;
  timeoutMs?: number;
};

type Logger = {
  info: (msg: string, meta?: Record<string, unknown>) => void;
  warn: (msg: string, meta?: Record<string, unknown>) => void;
  debug: (msg: string, meta?: Record<string, unknown>) => void;
};

type HookOpts = { name?: string; description?: string };

type PluginApi = {
  registerHook: (
    events: string | string[],
    handler: (event: InternalHookEvent) => Promise<void> | void,
    opts?: HookOpts
  ) => void;
  pluginConfig?: PluginConfig;
  logger?: Logger;
};

function safeLog(logger: Logger | undefined, level: "info" | "warn" | "debug", msg: string, meta?: Record<string, unknown>) {
  if (logger && typeof logger[level] === "function") {
    logger[level](msg, meta);
  } else {
    // Fallback so dev/test still see signal
    // eslint-disable-next-line no-console
    console[level === "debug" ? "log" : level](`[flyn-conv-tap] ${msg}`, meta ?? "");
  }
}

async function forwardToRouter(
  routerUrl: string,
  timeoutMs: number,
  payload: Record<string, unknown>,
  logger: Logger | undefined
): Promise<void> {
  const url = `${routerUrl.replace(/\/+$/, "")}/api/memory/ingest`;
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) {
      safeLog(logger, "warn", `conv-tap forward got ${resp.status}`, {
        status: resp.status,
        url,
      });
    } else {
      safeLog(logger, "debug", "conv-tap forwarded", { subject: payload.subject });
    }
  } catch (err) {
    safeLog(logger, "warn", "conv-tap forward failed", {
      url,
      error: err instanceof Error ? err.message : String(err),
    });
  } finally {
    clearTimeout(t);
  }
}

function buildPayload(direction: "inbound" | "outbound", event: InternalHookEvent): Record<string, unknown> | null {
  const ctx = event.context ?? {};
  // openclaw fills these from PluginHookMessageReceivedEvent / MessageSentEvent.
  // We accept any of the common shapes since exact field names varied across
  // openclaw versions; the receiver tolerates missing fields.
  const content = (ctx.content ?? ctx.body ?? ctx.text ?? "") as string;
  if (!content || typeof content !== "string") return null;
  const messageId = (ctx.messageId ?? ctx.message_id ?? "") as string;
  const threadId = (ctx.threadId ?? ctx.thread_id ?? ctx.conversationId ?? event.sessionKey ?? "") as string;
  const senderId = (ctx.senderId ?? ctx.from ?? ctx.sender_id ?? "") as string;
  const channelId = (ctx.channelId ?? ctx.channel ?? "telegram") as string;
  const subject = `${channelId}-${threadId || "unknown"}-${messageId || Date.now()}`;
  return {
    source: channelId,
    event_type: "conversation_message",
    subject,
    body: content,
    importance: "warm",
    raw_payload: {
      direction,
      channel: channelId,
      thread_id: threadId,
      sender_id: senderId,
      message_id: messageId,
      session_key: event.sessionKey,
      timestamp: event.timestamp instanceof Date ? event.timestamp.toISOString() : String(event.timestamp),
      original_context: ctx,
    },
  };
}

const DEFAULT_ROUTER_URL = "http://localhost:8400";
const DEFAULT_TIMEOUT_MS = 1500;

export default definePluginEntry({
  id: "flyn-conv-tap",
  name: "Flyn Conv-Tap",
  description:
    "Forwards every inbound channel message to flyn-memory-router's conv tier.",
  register: (api: PluginApi) => {
    const cfg = api.pluginConfig ?? {};
    const enabled = cfg.enabled !== false;
    if (!enabled) {
      safeLog(api.logger, "info", "flyn-conv-tap: disabled by config");
      return;
    }
    const routerUrl = cfg.routerUrl || DEFAULT_ROUTER_URL;
    const timeoutMs = cfg.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const forwardOutbound = !!cfg.forwardOutbound;

    api.registerHook(
      "message_received",
      async (event) => {
        const payload = buildPayload("inbound", event);
        if (!payload) return;
        await forwardToRouter(routerUrl, timeoutMs, payload, api.logger);
      },
      { name: "flyn-conv-tap-inbound", description: "Forward inbound message to conv tier" }
    );

    if (forwardOutbound) {
      api.registerHook(
        "message_sent",
        async (event) => {
          const payload = buildPayload("outbound", event);
          if (!payload) return;
          await forwardToRouter(routerUrl, timeoutMs, payload, api.logger);
        },
        { name: "flyn-conv-tap-outbound", description: "Forward outbound reply to conv tier" }
      );
    }

    safeLog(api.logger, "info", "flyn-conv-tap: registered", {
      routerUrl,
      timeoutMs,
      forwardOutbound,
    });
  },
});
