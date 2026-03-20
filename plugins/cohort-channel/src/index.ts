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
import { appendFileSync, mkdirSync } from "fs";
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

const config: ChannelConfig = {
  cohort_base_url: process.env.COHORT_BASE_URL ?? "http://localhost:5100",
  poll_interval_ms: parseInt(process.env.POLL_INTERVAL ?? "5000", 10),
  heartbeat_interval_ms: 10_000,
  session_id: `cohort-wq-${Date.now()}`,
};

const client = new CohortClient(config);

// Track current request so reply tools know what's active
let currentRequestId: string | null = null;
let currentRequestClaimedAt: number | null = null;

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const mcp = new Server(
  { name: "cohort-wq", version: "0.1.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
      tools: {},
    },
    instructions: [
      "You are an AI agent in the Cohort team chat system.",
      "When you receive a prompt via <channel source=\"cohort-wq\">, respond to",
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
          tokens_out_estimate: Math.ceil(content.length / 4),
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

async function pollLoop(): Promise<void> {
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
        currentRequestId = claim.id;
        currentRequestClaimedAt = Date.now();
        log("INFO", `Claimed ${claim.id} (agent=${claim.agent_id}, mode=${claim.response_mode})`);

        // Push the prompt into the Claude Code session
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
    await client.heartbeat();
    await new Promise((r) =>
      setTimeout(r, config.heartbeat_interval_ms)
    );
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await mcp.connect(transport);

  log("INFO", `Session ${config.session_id} connected. Log: ${LOG_FILE}`);

  // Start background loops (non-blocking)
  pollLoop().catch((e) =>
    log("FATAL", `Poll loop crashed: ${(e as Error).message}`)
  );
  heartbeatLoop().catch((e) =>
    log("FATAL", `Heartbeat loop crashed: ${(e as Error).message}`)
  );

  log("INFO", `Polling ${config.cohort_base_url} every ${config.poll_interval_ms}ms`);
}

main().catch((e) => {
  log("FATAL", (e as Error).message);
  process.exit(1);
});
