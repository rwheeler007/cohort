#!/usr/bin/env bun
/**
 * Cohort Channel Plugin for Claude Code.
 *
 * This MCP server bridges Cohort's agent response pipeline with a persistent
 * Claude Code session. It polls Cohort for pending agent requests, pushes
 * prompts into the Claude session as channel events, and exposes reply tools
 * so Claude can deliver responses back to Cohort.
 *
 * All prompt construction, context enrichment, memory injection, and response
 * posting stay server-side in Cohort. This plugin is intentionally thin.
 *
 * Usage:
 *   claude --dangerously-load-development-channels server:cohort-wq
 *
 * Environment:
 *   COHORT_BASE_URL  -- Cohort server URL (default: http://localhost:5100)
 *   POLL_INTERVAL    -- Poll interval in ms (default: 5000)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { CohortClient } from "./cohort-client.js";
import type { ChannelConfig } from "./types.js";
import { appendFileSync, mkdirSync, writeFileSync, readFileSync, unlinkSync, existsSync } from "fs";
import { join, dirname } from "path";

// ---------------------------------------------------------------------------
// Dual logger: stderr (Claude Code captures) + file (we can tail)
// ---------------------------------------------------------------------------

const LOG_DIR = join(dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, "$1")), "..", "logs");
try { mkdirSync(LOG_DIR, { recursive: true }); } catch { /* exists */ }
const LOG_FILE = join(LOG_DIR, "channel.log");

function log(level: string, msg: string): void {
  const ts = new Date().toISOString();
  const line = `${ts} [${level}] ${msg}`;
  console.error(line);
  try { appendFileSync(LOG_FILE, line + "\n"); } catch { /* best-effort */ }
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const SERVER_NAME = process.env.CHANNEL_NAME ?? "cohort-wq";
const channelId = process.env.CHANNEL_ID;
const projectId = process.env.PROJECT_ID ?? "default";
console.error(`[cohort-wq] ENV: CHANNEL_ID=${channelId ?? "(unset)"} PROJECT_ID=${projectId} COHORT_BASE_URL=${process.env.COHORT_BASE_URL ?? "(unset)"}`);

const config: ChannelConfig = {
  cohort_base_url: process.env.COHORT_BASE_URL ?? "http://localhost:5100",
  poll_interval_ms: parseInt(process.env.POLL_INTERVAL ?? "5000", 10),
  heartbeat_interval_ms: 1_000,
  session_id: `${SERVER_NAME}-${Date.now()}`,
  channel_id: channelId,
};

const client = new CohortClient(config);

// Track current request so reply tools know what's active
let currentRequestId: string | null = null;
let currentRequestClaimedAt: number | null = null;
let requestCount: number = 0;

// Ready-gate: poll loop blocks until Claude calls cohort_ready, proving the
// channel is bidirectional and Claude is actually listening for notifications.
let resolveReady: (() => void) | null = null;
let startupPingTimer: ReturnType<typeof setInterval> | null = null;
const claudeReady = new Promise<void>((resolve) => {
  resolveReady = resolve;
});

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const mcp = new Server(
  { name: SERVER_NAME, version: "0.1.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
      tools: {},
    },
    instructions: [
      "You are an AI agent in the Cohort team chat system.",
      `When you receive a prompt via <channel source="${SERVER_NAME}">, respond to`,
      "the conversation following the persona and instructions in the prompt.",
      "",
      "Workflow for each request:",
      "1. Read the prompt carefully -- it contains the agent persona, grounding",
      "   rules, channel context, and the user's message",
      "2. Respond as the specified agent, in character",
      "3. Call cohort_respond with your response text",
      "4. If you cannot respond, call cohort_error with a description",
      "",
      "After responding, wait -- the next request will arrive automatically.",
    ].join("\n"),
  }
);

// ---------------------------------------------------------------------------
// Reply Tools -- Claude calls these to communicate back to Cohort
// ---------------------------------------------------------------------------

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "cohort_respond",
      description:
        "Deliver your agent response back to the Cohort channel. Call this " +
        "after composing your response to the user's message.",
      inputSchema: {
        type: "object" as const,
        properties: {
          request_id: {
            type: "string",
            description: "The request ID from the channel event meta",
          },
          content: {
            type: "string",
            description: "Your response text (in the agent's voice)",
          },
        },
        required: ["request_id", "content"],
      },
    },
    {
      name: "cohort_post",
      description:
        "Post a message to a Cohort channel as a specific agent. Use this " +
        "during multi-round roundtable discussions to post each agent's " +
        "contribution as a separate message. This lets you drive collaborative " +
        "discussions with multiple rounds without waiting for Cohort to prompt you.",
      inputSchema: {
        type: "object" as const,
        properties: {
          channel: {
            type: "string",
            description: "The channel ID to post to (e.g., 'rt-think-skill-design')",
          },
          sender: {
            type: "string",
            description: "The agent ID to post as (e.g., 'python_developer')",
          },
          content: {
            type: "string",
            description: "The message content (in the agent's voice)",
          },
          thread_id: {
            type: "string",
            description: "Optional thread ID to post as a reply",
          },
        },
        required: ["channel", "sender", "content"],
      },
    },
    {
      name: "cohort_error",
      description:
        "Report that you cannot complete the request. Call this if the " +
        "prompt is unclear, the agent persona is missing, or you encounter " +
        "an unrecoverable issue.",
      inputSchema: {
        type: "object" as const,
        properties: {
          request_id: {
            type: "string",
            description: "The request ID from the channel event meta",
          },
          error: {
            type: "string",
            description: "Description of what went wrong",
          },
        },
        required: ["request_id", "error"],
      },
    },
    {
      name: "cohort_ready",
      description:
        "Signal that this Claude Code session is initialized and ready to " +
        "receive channel requests. Call this ONCE at the very start of each " +
        "session, before doing anything else. This unblocks prompt delivery -- " +
        "no requests will be pushed until this is called.",
      inputSchema: { type: "object" as const, properties: {}, required: [] },
    },
  ],
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;

  switch (name) {
    case "cohort_respond": {
      const requestId = (args as { request_id: string }).request_id;
      const content = (args as { content: string }).content;

      try {
        const elapsed = currentRequestClaimedAt
          ? (Date.now() - currentRequestClaimedAt) / 1000
          : undefined;
        await client.respond(requestId, content, {
          ...(elapsed !== undefined && { elapsed_seconds: Math.round(elapsed * 100) / 100 }),
          tokens_out: Math.ceil(content.length / 4),
        });
        currentRequestId = null;
        currentRequestClaimedAt = null;
        return {
          content: [
            {
              type: "text",
              text: `Response delivered for ${requestId}. Waiting for next request...`,
            },
          ],
        };
      } catch (e) {
        return {
          content: [
            {
              type: "text",
              text: `Failed to deliver response: ${(e as Error).message}`,
            },
          ],
          isError: true,
        };
      }
    }

    case "cohort_post": {
      const channel = (args as { channel: string }).channel;
      const sender = (args as { sender: string }).sender;
      const postContent = (args as { content: string }).content;
      const threadId = (args as { thread_id?: string }).thread_id;

      try {
        const result = await client.postMessage(channel, sender, postContent, threadId);
        log("INFO", `Posted as ${sender} to #${channel} (msg=${result.message_id})`);
        return {
          content: [
            {
              type: "text",
              text: `Posted as ${sender} to #${channel}. Message ID: ${result.message_id}`,
            },
          ],
        };
      } catch (e) {
        return {
          content: [
            {
              type: "text",
              text: `Failed to post: ${(e as Error).message}`,
            },
          ],
          isError: true,
        };
      }
    }

    case "cohort_error": {
      const requestId = (args as { request_id: string }).request_id;
      const errorMsg = (args as { error: string }).error;
      await client.error(requestId, errorMsg);
      currentRequestId = null;
      currentRequestClaimedAt = null;
      return {
        content: [
          {
            type: "text",
            text: `Error reported for ${requestId}. Waiting for next request...`,
          },
        ],
      };
    }

    case "cohort_ready": {
      if (resolveReady) {
        resolveReady();
        resolveReady = null;
        if (startupPingTimer) { clearInterval(startupPingTimer); startupPingTimer = null; }
        log("INFO", "Claude signaled ready -- prompt delivery unblocked");
      }
      return {
        content: [{ type: "text", text: "Ready acknowledged. Waiting for requests..." }],
      };
    }

    default:
      return {
        content: [{ type: "text", text: `Unknown tool: ${name}` }],
        isError: true,
      };
  }
});

// ---------------------------------------------------------------------------
// Poll Loop -- checks Cohort for pending requests, pushes prompts to Claude
// ---------------------------------------------------------------------------

const READY_TIMEOUT_MS = 90_000; // 90s -- covers slow resume sessions
const STARTUP_PING_INTERVAL_MS = 4_000; // re-ping if Claude hasn't called cohort_ready yet

async function pollLoop(): Promise<void> {
  // Claude won't spontaneously call cohort_ready -- it needs a prompt.
  // Send a startup ping every few seconds until it responds.
  log("INFO", "Sending startup ping to Claude...");

  // Once the notification is successfully injected, mark it consumed so the
  // retry interval doesn't duplicate it while Claude is still processing the
  // first one (schema fetch + tool call can take several seconds).
  let startupInjected = false;

  const sendStartupPing = async () => {
    try {
      await mcp.notification({
        method: "notifications/claude/channel",
        params: {
          content: "Session started. Your first action must be to call `cohort_ready` with no arguments.",
          meta: { request_id: "startup", type: "startup" },
        },
      });
      startupInjected = true; // Consumed -- don't re-send unless this failed
    } catch { /* best-effort */ }
  };

  // Wait for Claude Code to finish initializing its channel listener.
  // The MCP handshake completes before Claude is ready to receive
  // notifications, so firing immediately gets swallowed silently.
  await new Promise((r) => setTimeout(r, 2000));
  await sendStartupPing();
  startupPingTimer = setInterval(async () => {
    if (resolveReady === null) { if (startupPingTimer) { clearInterval(startupPingTimer); startupPingTimer = null; } return; }
    // Keep retrying -- even if the send "succeeded" (no throw), Claude may
    // not have been listening yet.  Only stop when cohort_ready arrives.
    log("INFO", "Re-sending startup ping (waiting for cohort_ready)...");
    await sendStartupPing();
  }, STARTUP_PING_INTERVAL_MS);

  // Block until Claude calls cohort_ready, proving the channel is live.
  const timeout = new Promise<never>((_, reject) =>
    setTimeout(() => reject(new Error("Claude ready timeout after 90s")), READY_TIMEOUT_MS)
  );
  await Promise.race([claudeReady, timeout]);
  if (startupPingTimer) { clearInterval(startupPingTimer); startupPingTimer = null; }
  log("INFO", "Claude is ready -- starting poll loop");

  let consecutiveFailures = 0;
  const MAX_BACKOFF_MS = 30_000;

  while (true) {
    try {
      // Don't poll while a request is active
      if (currentRequestId !== null) {
        await new Promise((r) => setTimeout(r, config.poll_interval_ms));
        continue;
      }

      const pollResult = await client.poll();
      if (consecutiveFailures > 0) {
        log("INFO", "Connection restored after " + consecutiveFailures + " failures");
      }
      consecutiveFailures = 0;

      if (pollResult.request) {
        const requestId = pollResult.request.id;
        log("INFO", `Request found: ${requestId}`);

        // Claim the request (get the full prompt)
        const claim = await client.claim(requestId);
        requestCount++;
        log("INFO", `Claimed ${claim.id} (agent=${claim.agent_id}, mode=${claim.response_mode}, request #${requestCount})`);

        // Guard: skip if server returned an empty prompt
        if (!claim.prompt) {
          log("WARN", `Claim ${claim.id} has empty prompt -- skipping`);
          continue;
        }

        currentRequestId = claim.id;
        currentRequestClaimedAt = Date.now();

        // Push the prompt into the Claude Code session
        try {
          await mcp.notification({
            method: "notifications/claude/channel",
            params: {
              content: claim.prompt,
              meta: {
                request_id: claim.id,
                agent_id: claim.agent_id,
                channel_id: claim.channel_id,
                response_mode: claim.response_mode,
              },
            },
          });
        } catch (notifyErr) {
          log("ERR", `Failed to push notification for ${claim.id}: ${(notifyErr as Error).message}`);
          // Reset so the next poll cycle can retry
          currentRequestId = null;
          currentRequestClaimedAt = null;
        }
      }
    } catch (e) {
      consecutiveFailures++;
      const msg = (e as Error).message;
      log("ERR", `Poll error (attempt ${consecutiveFailures}): ${msg}`);

      if (consecutiveFailures === 3) {
        log("WARN", "Cohort server unreachable for 3 consecutive polls. Backing off.");
      }

      // Exponential backoff: 5s, 10s, 20s, capped at 30s
      const backoff = Math.min(
        config.poll_interval_ms * Math.pow(2, consecutiveFailures - 1),
        MAX_BACKOFF_MS
      );
      await new Promise((r) => setTimeout(r, backoff));
      continue;
    }

    await new Promise((r) => setTimeout(r, config.poll_interval_ms));
  }
}

// ---------------------------------------------------------------------------
// Heartbeat -- keeps Cohort informed that this session is alive
// ---------------------------------------------------------------------------

async function heartbeatLoop(): Promise<void> {
  while (true) {
    await client.heartbeat({
      requests_served: requestCount,
      current_request_active: currentRequestId !== null,
    });
    await new Promise((r) =>
      setTimeout(r, config.heartbeat_interval_ms)
    );
  }
}

// ---------------------------------------------------------------------------
// PID lockfile -- prevents multiple instances from competing for same work
// ---------------------------------------------------------------------------

// Lock file is per-project + per-channel so multiple projects don't collide.
// e.g. cohort-wq-cohort-vscode-general.lock
const LOCK_FILE = join(
  LOG_DIR,
  `${SERVER_NAME}-${projectId}${channelId ? `-${channelId}` : ""}.lock`
);

function isPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e: any) {
    // EPERM = process exists but we can't signal it (still alive)
    // ESRCH = no such process (dead)
    return e?.code === "EPERM";
  }
}

function acquireLock(): boolean {
  try {
    if (existsSync(LOCK_FILE)) {
      const content = readFileSync(LOCK_FILE, "utf-8").trim();
      const existingPid = parseInt(content, 10);
      if (!isNaN(existingPid) && existingPid !== process.pid) {
        if (isPidAlive(existingPid)) {
          return false; // Another live instance holds the lock
        }
        log("WARN", `Stale lockfile for PID ${existingPid}, taking over`);
      }
    }
    writeFileSync(LOCK_FILE, String(process.pid), "utf-8");
    return true;
  } catch (e) {
    log("WARN", `Lock check failed: ${(e as Error).message}, proceeding anyway`);
    return true; // Fail open
  }
}

function releaseLock(): void {
  try {
    if (existsSync(LOCK_FILE)) {
      const content = readFileSync(LOCK_FILE, "utf-8").trim();
      if (content === String(process.pid)) {
        unlinkSync(LOCK_FILE);
      }
    }
  } catch { /* best-effort */ }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  // Only acquire lock and start poll/heartbeat when spawned for a specific channel.
  // Normal Claude Code sessions (no CHANNEL_ID) get the MCP tools but don't poll.
  const isChannelSession = !!channelId;

  if (isChannelSession) {
    if (!acquireLock()) {
      log("WARN", `Another ${SERVER_NAME} instance is already running. Exiting.`);
      process.exit(0);
    }

    process.on("exit", releaseLock);
    process.on("SIGINT", () => { releaseLock(); process.exit(0); });
    process.on("SIGTERM", () => { releaseLock(); process.exit(0); });
  }

  const transport = new StdioServerTransport();
  await mcp.connect(transport);

  log("INFO", `Session ${config.session_id} connected (PID ${process.pid}). Log: ${LOG_FILE}`);

  if (isChannelSession) {
    // Register with the server so ensure-session knows we're alive
    try {
      const regResult = await client.register();
      if (!regResult.ok) {
        log("FATAL", `Registration rejected: ${regResult.error} (limit=${regResult.limit}, active=${regResult.active})`);
        process.exit(1);
      }
      if (regResult.warn) {
        log("WARN", `Session count at warning threshold (${regResult.active}/${regResult.limit})`);
      }
      log("INFO", `Registered with server (active=${regResult.active}/${regResult.limit})`);
    } catch (e) {
      log("WARN", `Registration failed: ${(e as Error).message} -- continuing anyway`);
    }

    // Start background loops -- only for spawned channel sessions
    pollLoop().catch((e) =>
      log("FATAL", `Poll loop crashed: ${(e as Error).message}`)
    );
    heartbeatLoop().catch((e) =>
      log("FATAL", `Heartbeat loop crashed: ${(e as Error).message}`)
    );

    log("INFO", `Polling ${config.cohort_base_url} every ${config.poll_interval_ms}ms (channel=#${channelId})`);
  } else {
    log("INFO", "Passive mode -- tools available but no poll/heartbeat (no CHANNEL_ID set)");
  }
}

main().catch((e) => {
  log("FATAL", (e as Error).message);
  if (channelId) releaseLock();
  process.exit(1);
});
