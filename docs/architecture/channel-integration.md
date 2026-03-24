# Channel Integration Architecture

## The Definitive Model

A **Claude Code Channel** is NOT a server. It is an MCP subprocess that runs
inside a single Claude Code conversation. The channel plugin is a thin bridge
between a backend HTTP server and the Claude Code session's stdio transport.

```
[Cohort Server]  <--HTTP-->  [Channel Plugin (MCP subprocess)]  <--stdio-->  [Claude Code Session]
    (Python)                    (Bun/TypeScript)                               (Claude)
    port 5100                   no port, child process                         interactive or -p
```

## What Each Piece Does

### Cohort Server (Python, `cohort/server.py`)
- Long-running web service on port 5100
- Owns all business logic: agent routing, prompt construction, memory, chat
- Exposes `/api/channel/*` HTTP endpoints for the plugin to poll/claim/respond
- Manages the in-memory request queue (`channel_bridge.py`)
- Spawns and reaps Claude Code sessions on demand

### Channel Plugin (TypeScript, `plugins/cohort-channel/`)
- MCP server in the protocol sense (Claude Code spawns it as a child process)
- NOT a web server, NOT a standalone service
- Lives and dies with the Claude Code session
- Its entire job:
  1. Poll Cohort server for pending work
  2. Push prompts into Claude's conversation via `mcp.notification()`
  3. Expose reply tools (`cohort_respond`, `cohort_error`, `cohort_post`)
  4. POST results back to Cohort server
- No state management, no enrichment, no validation

### Claude Code Session
- Launched via `claude --dangerously-load-development-channels server:cohort-wq`
- Reads `.mcp.json` to discover and spawn the channel plugin
- Receives channel notifications as conversation turns
- Calls reply tools to send responses back through the plugin
- One session per Cohort chat channel (isolated context)

## Per-Channel Session Lifecycle

```
User sends message in #general with [CH] mode
  |
  v
agent_router: ensure_channel_session("general")
  |
  +-- Session exists and heartbeating? --> reuse it
  |
  +-- No session? --> subprocess.Popen("claude --dangerously-load-development-channels ...")
                       with CHANNEL_ID=general in env
                       |
                       +-- Claude starts, reads .mcp.json, spawns bun plugin
                       +-- Plugin starts heartbeating to Cohort server
                       +-- Bridge sees heartbeat, returns True
  |
  v
enqueue_channel_request(prompt, channel_id="general")
  |
  v
Plugin polls /api/channel/poll?channel_id=general
  --> claims request
  --> pushes prompt into Claude session
  --> Claude responds, calls cohort_respond tool
  --> Plugin POSTs response to /api/channel/{id}/respond
  |
  v
await_channel_response() unblocks, returns text
  |
  v
Response posted to Cohort chat
```

## Idle Reaper

Background thread (every 60s) checks each session's `last_activity` timestamp.
Sessions idle > 30 minutes are terminated. Next message in that channel spawns
a fresh session.

## Configuration

All configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `COHORT_CLAUDE_CMD` | `claude` | Path to Claude Code CLI |
| `COHORT_BASE_URL` | `http://localhost:5100` | Cohort server URL |
| `COHORT_CHANNEL_MODEL` | `sonnet` | Model for channel sessions |
| `CHANNEL_ID` | (none) | Scopes plugin to one channel |
| `CHANNEL_NAME` | `cohort-wq` | MCP server identity |
| `POLL_INTERVAL` | `5000` | Poll interval in ms |

## Key Files

| File | Role |
|------|------|
| `cohort/channel_bridge.py` | Request queue + session registry + spawn/reap |
| `cohort/server.py` | HTTP endpoints for plugin to poll/claim/respond |
| `cohort/agent_router.py` | Routes [CH] mode through bridge |
| `plugins/cohort-channel/src/index.ts` | MCP server (thin bridge) |
| `plugins/cohort-channel/src/cohort-client.ts` | HTTP client for Cohort API |
| `plugins/cohort-channel/system_prompt.md` | Instructions for channel sessions |
| `.mcp.json` | Plugin registration (Claude Code reads this) |
