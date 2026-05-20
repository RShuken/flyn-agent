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
  // openclaw's internal "message:received" hook context maps canonical sender
  // identity into ctx.metadata.senderId (numeric Telegram chat_id for direct
  // DMs). Top-level ctx.from is the display name and won't match principals.json.
  // Confirmed via source: dist/message-hook-mappers-7jVKerRx.js#toInternalMessageReceivedContext
  const metadata = (ctx.metadata as Record<string, unknown> | undefined) ?? {};
  const content = (ctx.content ?? ctx.body ?? ctx.text ?? "") as string;
  if (!content || typeof content !== "string") return null;
  const messageId = (ctx.messageId ?? ctx.message_id ?? "") as string;
  const threadId = (metadata.threadId ?? ctx.threadId ?? ctx.thread_id ?? ctx.conversationId ?? event.sessionKey ?? "") as string;
  // Canonical numeric sender id lives in metadata.senderId for direct DMs.
  // ctx.from is a display name and will fail principals.json lookup.
  const senderId = (metadata.senderId ?? ctx.senderId ?? ctx.sender_id ?? ctx.from ?? "") as string;
  const channelId = (ctx.channelId ?? ctx.channel ?? "telegram") as string;
  const subject = `${channelId}-${threadId || "unknown"}-${messageId || Date.now()}`;
  return {
    source: channelId,
    event_type: "conversation_message",
    subject,
    body: content,
    importance: "warm",
    dedup_key: `${channelId}:${direction}:${threadId || "x"}:${messageId || Date.now()}`,
    raw_payload: {
      direction,
      channel: channelId,
      thread_id: threadId,
      sender_id: senderId,
      // chat_id is the conv_write_adapter's fallback when sender_id is missing
      chat_id: senderId,
      message_id: messageId,
      session_key: event.sessionKey,
      sender_name: metadata.senderName ?? metadata.senderUsername ?? ctx.from,
      timestamp: event.timestamp instanceof Date ? event.timestamp.toISOString() : String(event.timestamp),
      // Trim noisy original_context to just keys the adapter doesn't already use
      original_metadata: metadata,
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

    // Confirmed via source trace: openclaw fires internal hooks as
    // type:action pairs (e.g., createInternalHookEvent("message", "received", ...))
    // not as the PluginHookName SDK strings (e.g., "message_received").
    // registerHook subscribes to internal hooks; the event name must use the
    // colon form to match the firing site in dispatch-*.js.
    api.registerHook(
      "message:received",
      async (event) => {
        safeLog(api.logger, "info", "conv-tap: message:received fired", {
          type: event.type,
          action: event.action,
          ctxKeys: Object.keys(event.context ?? {}),
        });
        const payload = buildPayload("inbound", event);
        if (!payload) return;
        await forwardToRouter(routerUrl, timeoutMs, payload, api.logger);
      },
      { name: "flyn-conv-tap-inbound", description: "Forward inbound channel message to conv tier" }
    );

    if (forwardOutbound) {
      api.registerHook(
        "message:sent",
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
