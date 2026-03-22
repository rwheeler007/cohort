"""@mention-triggered agent response pipeline for Cohort.

Pipeline flow:
  message posted -> mentions extracted -> agents validated ->
  queue with priority -> background thread processes ->
  rate limiting + loop detection + depth limiting ->
  load prompt -> invoke Claude Code CLI -> post response ->
  chain routing if response contains @mentions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import json
import sys
import tempfile

from cohort.api import (
    parse_mentions, AgentStore, truncate_context, load_persona,
    resolve_permissions, get_central_permissions, ResolvedPermissions,
)
from cohort.local.config import classify_confidence
from cohort.agent_context import load_agent_context, load_project_memory, load_user_profile_block

logger = logging.getLogger(__name__)

# =====================================================================
# Configuration constants
# =====================================================================

RATE_LIMIT_SECONDS = 5
MAX_RESPONSES_PER_MINUTE = 5
MAX_CONVERSATION_DEPTH = 5
RESPONSE_TIMEOUT = 300  # 5 min timeout for Claude CLI
CONTEXT_HISTORY_LIMIT = int(os.environ.get("COHORT_HISTORY_LIMIT", "50"))
CIRCUIT_BREAKER_CHAR_LIMIT = int(os.environ.get("COHORT_CIRCUIT_BREAKER", "240000"))

# =====================================================================
# Credential injection for agent subprocess environments
# =====================================================================

# Canonical mapping: service_type -> env var mappings.
# 'key' always maps to the primary credential. Extra fields map to their env var names.
_SERVICE_ENV_MAP: dict[str, dict[str, str]] = {
    "anthropic":    {"key": "ANTHROPIC_API_KEY"},
    "github":       {"key": "GITHUB_TOKEN"},
    "youtube":      {"key": "YOUTUBE_API_KEY"},
    "openai":       {"key": "OPENAI_API_KEY"},
    "cloudflare":   {"key": "CLOUDFLARE_API_TOKEN"},
    "resend":       {"key": "RESEND_API_KEY"},
    "slack":        {"key": "SLACK_WEBHOOK_URL"},
    "discord":      {"key": "DISCORD_WEBHOOK_URL"},
    "email_smtp":   {
        "key": "SMTP_PASS",
        "SMTP_HOST": "SMTP_HOST",
        "SMTP_PORT": "SMTP_PORT",
        "SMTP_USER": "SMTP_USER",
    },
    "email_imap":   {
        "key": "IMAP_PASS",
        "IMAP_HOST": "IMAP_HOST",
        "IMAP_PORT": "IMAP_PORT",
        "IMAP_USER": "IMAP_USER",
    },
    "aws":          {
        "key": "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION": "AWS_DEFAULT_REGION",
    },
    "google":       {
        "key": "GOOGLE_CLOUD_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS": "GOOGLE_APPLICATION_CREDENTIALS",
    },
    "linkedin":     {
        "key": "LINKEDIN_CLIENT_ID",
        "LINKEDIN_CLIENT_SECRET": "LINKEDIN_CLIENT_SECRET",
    },
    "twitter":      {
        "key": "TWITTER_API_KEY",
        "TWITTER_API_SECRET": "TWITTER_API_SECRET",
        "TWITTER_BEARER_TOKEN": "TWITTER_BEARER_TOKEN",
    },
    "serpapi":      {"key": "SERPAPI_API_KEY"},
    "serper":       {"key": "SERPER_API_KEY"},
    "reddit":       {
        "key": "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET": "REDDIT_CLIENT_SECRET",
    },
    "webhook":      {
        "key": "WEBHOOK_API_KEY",
        "WEBHOOK_URL": "WEBHOOK_URL",
    },
    "rss":          {"key": "RSS_API_KEY"},
    "custom":       {"key": "CUSTOM_API_KEY"},
}


def _load_agent_credentials(agent_id: str) -> dict[str, str]:
    """Load credentials for services an agent is permitted to access.

    Reads settings.json, decrypts secrets, checks agent_permissions,
    and returns a dict of env var name -> value for injection into subprocess env.
    """
    if _settings_path is None or not _settings_path.exists():
        return {}

    try:
        with open(_settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    # Decrypt secrets in-place (same as server._load_settings)
    from cohort.secret_store import decrypt_settings_secrets  # proprietary: cohort_app
    decrypt_settings_secrets(settings)

    service_keys = settings.get("service_keys", [])
    permissions = settings.get("agent_permissions", {})
    agent_perms = permissions.get(agent_id, {})

    # Build service lookup: id -> service dict
    svc_by_id = {svc.get("id", ""): svc for svc in service_keys}

    env_vars: dict[str, str] = {}

    for svc_id, allowed in agent_perms.items():
        if not allowed:
            continue
        svc = svc_by_id.get(svc_id)
        if not svc:
            continue

        svc_type = svc.get("type", "custom")
        mapping = _SERVICE_ENV_MAP.get(svc_type)
        if not mapping:
            continue

        main_key = svc.get("key", "")
        # Parse extra JSON for multi-field services
        extra: dict[str, str] = {}
        extra_raw = svc.get("extra", "")
        if extra_raw:
            try:
                extra = json.loads(extra_raw)
            except (json.JSONDecodeError, TypeError):
                pass

        for field_key, env_name in mapping.items():
            if field_key == "key":
                if main_key:
                    env_vars[env_name] = main_key
            else:
                val = extra.get(field_key, "")
                if val:
                    env_vars[env_name] = val

    return env_vars



CLAUDE_CMD = os.environ.get("COHORT_CLAUDE_CMD", "claude")

# Force all agent responses through Claude Code (bypass local Ollama)
_force_claude_code: bool = False

# Dev mode: enables CLI subprocess path for internal testing.
# When False (default for distribution), only cloud API path is available.
_dev_mode: bool = False

# Channel mode: use persistent Claude Code session via MCP Channels
# instead of spawning ephemeral CLI subprocesses.
_channel_mode_enabled: bool = False

# Cached cloud settings (updated via apply_settings)
_cloud_settings: dict = {}

# Cache Claude CLI availability for dev mode
_claude_cli_available: bool | None = None  # None = not checked yet


def check_claude_cli_available() -> bool:
    """Check if Claude CLI is installed and runnable.

    Caches the result after first check. Call reset_claude_cli_cache()
    after settings changes to re-check.
    """
    global _claude_cli_available  # noqa: PLW0603
    if _claude_cli_available is not None:
        return _claude_cli_available

    import shutil

    cmd = CLAUDE_CMD
    # Check explicit path first
    if os.path.isfile(cmd):
        _claude_cli_available = True
        return True
    # Check PATH
    found = shutil.which(cmd)
    _claude_cli_available = found is not None
    if found:
        logger.info("[OK] Claude CLI detected: %s", found)
    else:
        logger.info("[*] Claude CLI not found -- Smartest mode unavailable")
    return _claude_cli_available


def reset_claude_cli_cache() -> None:
    """Reset the cached CLI availability check."""
    global _claude_cli_available  # noqa: PLW0603
    _claude_cli_available = None


# =====================================================================
# Grounding rules (anti-hallucination, adapted from BOSS/SMACK)
# =====================================================================

GROUNDING_RULES = """
GROUNDING RULES (apply to ALL responses):
- You are an AI agent in the Cohort multi-agent system. Your team consists ONLY of other AI agents in this system.
- DO NOT invent, reference, or delegate to human team members (no fake names like "David", "Sarah", etc.).
- DO NOT reference external tools, channels, or platforms that are not part of the Cohort system.
- When delegating or coordinating, refer ONLY to agents by their actual agent IDs.
- If you do not know who should handle something, say so -- do not fabricate organizational structures.

DESIGN SIMPLICITY:
- Prefer concrete implementations over abstractions.
- Solve the immediate problem simply. Generalize only when forced by a real second use case.
- Each agent's contribution should solve a real problem, not add scaffolding for hypothetical futures.
"""

# Known human senders -- never route to these
HUMAN_USERS = frozenset({
    "admin", "user", "human", "system",
})

# =====================================================================
# Tool permission helpers
# =====================================================================


def _build_tool_awareness(perms: ResolvedPermissions) -> str:
    """Build a tool awareness section for the agent prompt."""
    lines = [
        "=== AVAILABLE TOOLS ===",
        f"You have access to the following tools: {', '.join(perms.allowed_tools)}",
    ]

    tool_set = set(perms.allowed_tools)

    if {"Read", "Glob", "Grep"} & tool_set:
        lines.append("- File tools: Use Read to view files, Glob to find files by pattern, Grep to search file contents")

    if {"Write", "Edit"} & tool_set:
        lines.append("- Edit tools: Use Edit for surgical changes to existing files, Write for new files")

    if "Bash" in tool_set:
        lines.append("- Bash: Execute shell commands (git, tests, builds, etc.)")

    if {"WebSearch", "WebFetch"} & tool_set:
        lines.append("- Web tools: Search and fetch web content for research")

    if perms.mcp_servers:
        server_names = [s.get("name", "unknown") for s in perms.mcp_servers]
        lines.append(f"- MCP servers: {', '.join(server_names)}")
        if any(s.get("name") == "local_llm" for s in perms.mcp_servers):
            lines.append("  - local_llm: Delegate sub-tasks to local Ollama models (fast, free)")
        if any(s.get("name") == "cohort" for s in perms.mcp_servers):
            lines.append("  - cohort: Read/post messages in team chat channels")

    lines.append(f"- Max turns: {perms.max_turns}")
    lines.append("=== END TOOLS ===\n")

    return "\n".join(lines)


def _write_mcp_config(mcp_servers: list[dict]) -> Path | None:
    """Write a temporary MCP config JSON for Claude CLI --mcp-config flag.

    Returns path to temp file, or None on failure.
    Caller is responsible for cleanup.
    """
    try:
        config: dict[str, Any] = {"mcpServers": {}}
        for server in mcp_servers:
            name = server.get("name", "")
            if name:
                config["mcpServers"][name] = {
                    "command": server["command"],
                    "args": server.get("args", []),
                }
        fd, path = tempfile.mkstemp(suffix=".json", prefix="cohort_mcp_")
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
        return Path(path)
    except Exception:
        logger.debug("[*] Failed to write MCP config temp file")
        return None


# =====================================================================
# Agent alias map
# =====================================================================

AGENT_ALIASES: dict[str, str] = {
    # Leadership
    "boss": "cohort_orchestrator",
    "supervisor": "supervisor_agent",
    "coding": "coding_orchestrator",
    "orch": "coding_orchestrator",
    # Core developers
    "python": "python_developer",
    "py": "python_developer",
    "pydev": "python_developer",
    "pd": "python_developer",
    "web": "web_developer",
    "webdev": "web_developer",
    "frontend": "web_developer",
    "js": "javascript_developer",
    "javascript": "javascript_developer",
    "jsdev": "javascript_developer",
    "syscoder": "system_coder",
    "cpp": "cpp_developer",
    # Specialists
    "db": "database_developer",
    "database": "database_developer",
    "dbdev": "database_developer",
    "qa": "qa_agent",
    "testing": "qa_agent",
    "security": "security_agent",
    "sec": "security_agent",
    "archeo": "code_archaeologist",
    "archaeologist": "code_archaeologist",
    "devops": "devops_agent",
    # Support
    "docs": "documentation_agent",
    "documentation": "documentation_agent",
    "educator": "sdk_educator_research",
    "sdk": "sdk_educator_research",
}


# =====================================================================
# In-memory state
# =====================================================================

@dataclass
class _RouterState:
    """Mutable singleton holding queue and rate-limit state."""

    queue: list[dict[str, Any]] = field(default_factory=list)
    queue_lock: threading.Lock = field(default_factory=threading.Lock)
    processor_running: bool = False
    processor_started_at: float = 0.0  # epoch time when processor last started

    # Rate limiting: agent_id -> epoch timestamp
    last_response_time: dict[str, float] = field(default_factory=dict)

    # Loop detection: (channel_id, agent_id) -> [epoch timestamps]
    recent_responses: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    # Conversation depth: message_id -> depth
    conversation_depth: dict[str, int] = field(default_factory=dict)

    # Escalation rate limiter: [epoch timestamps] of 35B calls
    escalation_calls: list[float] = field(default_factory=list)


_state = _RouterState()


def _check_escalation_rate() -> tuple[bool, str]:
    """Check if the 35B escalation model can be called (hourly rate limit).

    Returns:
        (allowed, reason)
    """
    from cohort.local.config import get_budget_limits
    limits = get_budget_limits()
    max_per_hour = limits.get("escalation_per_hour", 30)
    if max_per_hour <= 0:
        return True, "Escalation rate limiting disabled"

    now = time.time()
    cutoff = now - 3600
    _state.escalation_calls = [t for t in _state.escalation_calls if t > cutoff]

    if len(_state.escalation_calls) >= max_per_hour:
        return False, f"Escalation rate limit ({len(_state.escalation_calls)}/{max_per_hour} per hour)"
    return True, "OK"


def _record_escalation_call() -> None:
    """Record a 35B escalation call for rate limiting."""
    _state.escalation_calls.append(time.time())

# =====================================================================
# References to Cohort subsystems (set during setup)
# =====================================================================

_chat: Any = None
_sio: Any = None
_orchestrator: Any = None
_event_loop: asyncio.AbstractEventLoop | None = None
_agent_store: AgentStore | None = None
AGENTS_ROOT: Path | None = None
_settings_path: Path | None = None


def setup_agent_router(
    chat: Any,
    sio: Any,
    agents_root: str | Path,
    orchestrator: Any = None,
    store: AgentStore | None = None,
    settings_path: Path | None = None,
) -> None:
    """Initialize the agent router.  Called once from server.py create_app()."""
    global _chat, _sio, _state, AGENTS_ROOT, _orchestrator, _event_loop, _agent_store, _settings_path  # noqa: PLW0603
    _chat = chat
    _sio = sio
    AGENTS_ROOT = Path(agents_root)
    _orchestrator = orchestrator
    _agent_store = store
    _settings_path = settings_path
    _state = _RouterState()

    # Capture the running event loop so the background thread can schedule coroutines
    try:
        _event_loop = asyncio.get_running_loop()
    except RuntimeError:
        _event_loop = None

    logger.info("[OK] Agent router initialised (AGENTS_ROOT=%s)", AGENTS_ROOT)


def set_orchestrator(orch: Any) -> None:
    """Wire the roundtable orchestrator after lazy init."""
    global _orchestrator  # noqa: PLW0603
    _orchestrator = orch


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the ASGI event loop for the sync->async bridge."""
    global _event_loop  # noqa: PLW0603
    _event_loop = loop


def apply_settings(settings: dict) -> None:
    """Hot-reload settings from the settings.json store.

    Called on startup and whenever the user saves settings from the UI.
    """
    global CLAUDE_CMD, RESPONSE_TIMEOUT, AGENTS_ROOT, _force_claude_code, _dev_mode, _channel_mode_enabled, _cloud_settings  # noqa: PLW0603

    if "force_to_claude_code" in settings:
        _force_claude_code = bool(settings["force_to_claude_code"])
        logger.info("[OK] Force Claude Code: %s", _force_claude_code)

    if "dev_mode" in settings:
        _dev_mode = bool(settings["dev_mode"])
        logger.info("[OK] Dev mode: %s", _dev_mode)

    if "channel_mode" in settings:
        _channel_mode_enabled = bool(settings["channel_mode"])
        logger.info("[OK] Channel mode: %s", _channel_mode_enabled)

    if settings.get("claude_cmd"):
        CLAUDE_CMD = settings["claude_cmd"]
        reset_claude_cli_cache()  # Re-check availability with new path
        logger.info("[OK] Claude CLI path updated: %s", CLAUDE_CMD)

    if settings.get("response_timeout"):
        try:
            RESPONSE_TIMEOUT = int(settings["response_timeout"])
        except (TypeError, ValueError):
            pass

    if settings.get("agents_root"):
        new_root = Path(settings["agents_root"])
        if new_root.exists():
            AGENTS_ROOT = new_root
            logger.info("[OK] AGENTS_ROOT updated: %s", AGENTS_ROOT)

    # Cloud backend settings (for Smartest tier in distribution mode)
    for key in ("cloud_provider", "cloud_api_key", "cloud_model", "cloud_base_url"):
        if key in settings:
            _cloud_settings[key] = settings[key]
    if _cloud_settings.get("cloud_provider"):
        logger.info("[OK] Cloud provider: %s", _cloud_settings["cloud_provider"])


# =====================================================================
# Dynamic agent type helpers
# =====================================================================

def _find_orchestrator_agent() -> str | None:
    """Find the first registered orchestrator agent by type, not by name.

    Returns the agent_id of the first agent with agent_type='orchestrator',
    or None if none exists.
    """
    if _agent_store is None:
        return None
    for agent in _agent_store.list_agents():
        if agent.agent_type == "orchestrator" and agent.status == "active":
            return agent.agent_id
    return None


def _get_agent_type(agent_id: str) -> str | None:
    """Look up an agent's type from the AgentStore."""
    if _agent_store is None:
        return None
    config = _agent_store.get(agent_id)
    return config.agent_type if config else None


# =====================================================================
# Agent resolution
# =====================================================================

def resolve_agent_id(mention: str) -> str | None:
    """Resolve a mention string to a canonical agent_id.

    Checks AgentStore first, then alias map, then filesystem as fallback.
    Returns None if the mention does not match any known agent.
    """
    normalized = mention.lower().replace("-", "_").replace(" ", "_")

    # AgentStore lookup (preferred -- file-backed agent configs)
    if _agent_store is not None:
        config = _agent_store.get_by_alias(normalized)
        if config:
            return config.agent_id

    # Legacy alias lookup
    if normalized in AGENT_ALIASES:
        return AGENT_ALIASES[normalized]

    # Direct match -- the mention IS the canonical id
    if AGENTS_ROOT and (AGENTS_ROOT / "agents" / normalized / "agent_prompt.md").exists():
        return normalized

    # Try original casing
    if AGENTS_ROOT and (AGENTS_ROOT / "agents" / mention / "agent_prompt.md").exists():
        return mention

    return None


def get_agent_prompt_path(agent_id: str) -> Path | None:
    """Return the absolute path to agent_prompt.md, or None."""
    # Check AgentStore first (Cohort-managed agents)
    if _agent_store is not None:
        store_path = _agent_store.get_prompt_path(agent_id)
        if store_path:
            return store_path

    # Fallback to agents root filesystem
    if AGENTS_ROOT is None:
        return None
    p = AGENTS_ROOT / "agents" / agent_id / "agent_prompt.md"
    return p if p.exists() else None


# =====================================================================
# Rate limiting helpers
# =====================================================================

def _is_rate_limited(agent_id: str) -> bool:
    if agent_id in _state.last_response_time:
        elapsed = time.time() - _state.last_response_time[agent_id]
        return elapsed < RATE_LIMIT_SECONDS
    return False


def _check_response_loop(channel_id: str, agent_id: str) -> bool:
    """Return True if agent should be blocked (too many recent responses)."""
    key = (channel_id, agent_id)
    now = time.time()

    if key not in _state.recent_responses:
        _state.recent_responses[key] = []

    # Prune entries older than 60 seconds
    _state.recent_responses[key] = [
        ts for ts in _state.recent_responses[key] if now - ts < 60
    ]

    return len(_state.recent_responses[key]) >= MAX_RESPONSES_PER_MINUTE


def _record_response(agent_id: str, channel_id: str) -> None:
    now = time.time()
    _state.last_response_time[agent_id] = now

    key = (channel_id, agent_id)
    if key not in _state.recent_responses:
        _state.recent_responses[key] = []
    _state.recent_responses[key].append(now)


def _get_conversation_depth(message_id: str) -> int:
    return _state.conversation_depth.get(message_id, 0)


def _set_conversation_depth(message_id: str, parent_id: str | None) -> None:
    if parent_id and parent_id in _state.conversation_depth:
        _state.conversation_depth[message_id] = _state.conversation_depth[parent_id] + 1
    else:
        _state.conversation_depth[message_id] = 1


# =====================================================================
# Async-sync bridge
# =====================================================================

def _emit_sync(event: str, data: dict) -> None:
    """Schedule a Socket.IO emit from the sync background thread."""
    if _sio is None:
        return
    try:
        loop = _event_loop or asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_sio.emit(event, data), loop)
        else:
            loop.run_until_complete(_sio.emit(event, data))
    except RuntimeError:
        logger.debug("No event loop for emit_sync: %s", event)


# =====================================================================
# Queue management
# =====================================================================

def queue_agent_response(
    agent_id: str,
    message_content: str,
    channel_id: str,
    thread_id: str | None = None,
    priority: int = 50,
    response_mode: str = "smarter",
    project_path: str | None = None,
) -> None:
    """Add an agent response to the priority queue."""
    with _state.queue_lock:
        # Dedup -- skip if this agent is already queued for this channel
        for entry in _state.queue:
            if entry["agent_id"] == agent_id and entry["channel_id"] == channel_id:
                logger.info("[*] Skipping duplicate queue entry: %s in #%s", agent_id, channel_id)
                return

        item: dict = {
            "agent_id": agent_id,
            "message_content": message_content,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "priority": priority,
            "queued_at": time.time(),
            "response_mode": response_mode,
        }
        if project_path:
            item["project_path"] = project_path
        _state.queue.append(item)
        # Sort: lower priority number = responds first
        _state.queue.sort(key=lambda x: x["priority"])

    logger.info("[>>] Queued %s response (priority %d) in #%s", agent_id, priority, channel_id)
    _start_queue_processor()


def _start_queue_processor() -> None:
    """Spawn background daemon thread if not already running."""
    if _state.processor_running:
        # Safety: if processor has been "running" for over 10 minutes, it's stuck
        elapsed = time.time() - _state.processor_started_at
        if elapsed > 600:
            logger.warning("[!] Processor stuck for %.0fs, resetting flag", elapsed)
            _state.processor_running = False
        else:
            return
    thread = threading.Thread(target=_process_queue, daemon=True)
    thread.start()


def _process_queue() -> None:
    """Background thread: drain the queue with safety checks."""
    _state.processor_running = True
    _state.processor_started_at = time.time()
    try:
        while True:
            with _state.queue_lock:
                if not _state.queue:
                    break
                item = _state.queue.pop(0)

            agent_id = item["agent_id"]
            channel_id = item["channel_id"]

            # Rate limiting
            if _is_rate_limited(agent_id):
                wait = RATE_LIMIT_SECONDS - (time.time() - _state.last_response_time[agent_id])
                if wait > 0:
                    logger.info("[...] Rate-limiting %s (%.1fs)", agent_id, wait)
                    time.sleep(wait)

            # Loop detection
            if _check_response_loop(channel_id, agent_id):
                logger.warning("[!] Loop detected for %s in #%s, skipping", agent_id, channel_id)
                continue

            # Conversation depth
            if item["thread_id"]:
                depth = _get_conversation_depth(item["thread_id"])
                if depth >= MAX_CONVERSATION_DEPTH:
                    logger.warning(
                        "[!] Max depth (%d) for %s, skipping", depth, agent_id
                    )
                    continue

            # Invoke the agent
            try:
                _invoke_agent_sync(item)
            except Exception:
                logger.exception("[X] Error invoking %s", agent_id)

            _record_response(agent_id, channel_id)
    finally:
        _state.processor_running = False


# =====================================================================
# Agent invocation (ported from route_to_claude_code_sync)
# =====================================================================

def build_channel_context(channel_id: str) -> str:
    """Build a context string with recent channel messages."""
    if _chat is None:
        return ""

    channel = _chat.get_channel(channel_id)
    if not channel:
        return ""

    parts = [
        "=== CHANNEL CONTEXT ===",
        f"Channel: #{channel.name}",
    ]
    if channel.description:
        parts.append(f"Description: {channel.description}")
    if channel.topic:
        parts.append(f"Topic: {channel.topic}")

    messages = _chat.get_channel_messages(channel_id, limit=CONTEXT_HISTORY_LIMIT)
    if messages:
        # D6: Apply sliding window truncation to stay within char budget
        messages = truncate_context(messages)
        parts.append("\n--- Recent Messages ---")
        for msg in messages:
            ts = msg.timestamp.split("T")[1][:8] if "T" in msg.timestamp else msg.timestamp
            # System messages (tool context, instructions) get higher limit
            # to avoid clipping injected knowledge
            limit = 6000 if msg.sender == "system" else 2000
            parts.append(f"[{ts}] {msg.sender}: {msg.content[:limit]}")

    parts.append("=== END CHANNEL CONTEXT ===\n")
    return "\n".join(parts)


def _build_thread_context(thread_id: str, channel_id: str) -> str:
    """Build context from parent thread messages."""
    if _chat is None or not thread_id:
        return ""

    messages = _chat.get_channel_messages(channel_id, limit=50)
    thread_msgs = [
        m for m in messages
        if m.id == thread_id or m.thread_id == thread_id
    ]
    if not thread_msgs:
        return ""

    parts = ["--- Thread Context ---"]
    for msg in thread_msgs:
        parts.append(f"{msg.sender}: {msg.content[:2000]}")
    parts.append("--- End Thread ---\n")
    return "\n".join(parts)


def _invoke_smartest_pipeline(
    agent_id: str,
    user_message: str,
    local_prompt: str,
    agent_temperature: float | None,
) -> tuple[str | None, dict[str, Any]]:
    """Execute the Smartest 3-phase pipeline: Qwen reason -> distill -> Claude.

    Returns:
        (response_content, response_metadata). Content is None if pipeline failed entirely.
        On Phase 3 failure, returns Qwen draft with degraded metadata.

    Never raises exceptions.
    """
    metadata: dict[str, Any] = {}
    t0 = time.monotonic()

    # Phase 1: Qwen reasoning pass
    try:
        from cohort.api import LocalRouter

        router = LocalRouter()
        task_type = "code" if any(
            kw in user_message.lower()
            for kw in ("code", "implement", "function", "class", "debug")
        ) else "general"

        phase1_result = router.route(
            local_prompt,
            task_type=task_type,
            temperature=agent_temperature,
            response_mode="smarter",
        )

        if phase1_result is None or not phase1_result.text.strip():
            logger.warning("[!] Smartest Phase 1 failed for %s", agent_id)
            return None, {}

        qwen_draft = phase1_result.text
        logger.info("[OK] Smartest Phase 1: %s (%d/%d tok)",
                    agent_id, phase1_result.tokens_in, phase1_result.tokens_out)
    except Exception:
        logger.exception("[X] Smartest Phase 1 exception for %s", agent_id)
        return None, {}

    # Phase 2: Distillation
    try:
        distilled = router.distill(qwen_draft)

        if not distilled:
            logger.warning("[!] Smartest Phase 2 failed for %s, truncating raw draft", agent_id)
            distilled = qwen_draft[:2000]
        else:
            logger.info("[OK] Smartest Phase 2: %s (%d chars)", agent_id, len(distilled))
    except Exception:
        logger.exception("[X] Smartest Phase 2 exception for %s", agent_id)
        distilled = qwen_draft[:2000]

    # Phase 3: Local 35B -> Cloud API -> CLI subprocess (dev mode)
    # Configurable via tier_settings.json: primary + fallback
    try:
        from cohort.api import SMARTEST_CLAUDE_PROMPT

        # Load lightweight persona (not full agent_prompt.md)
        persona = ""
        if _agent_store is not None:
            config = _agent_store.get(agent_id)
            if config and config.persona_text:
                persona = config.persona_text
        if not persona:
            persona = load_persona(agent_id) or f"You are the {agent_id} agent."

        # Inject user profile into Phase 3
        _phase3_profile = load_user_profile_block()
        _phase3_grounding = GROUNDING_RULES
        if _phase3_profile:
            _phase3_grounding = _phase3_profile + "\n" + GROUNDING_RULES

        # Build the system prompt and user message for Phase 3
        system_prompt = (
            f"You are responding as the {agent_id} agent.\n\n"
            f"Follow this persona exactly:\n---\n{persona}\n---\n\n"
            f"{_phase3_grounding}"
        )
        phase3_user_message = (
            "A local AI model has analyzed the conversation and produced this briefing:\n\n"
            f"--- ANALYSIS BRIEFING ---\n{distilled}\n--- END BRIEFING ---\n\n"
            "Now respond to the user's message. Use the briefing as your research/context, "
            "but write your response in your own voice. Do not reference the briefing or "
            "the local model's analysis.\n\n"
            f"User message:\n{user_message}"
        )

        # Also build the flat prompt for CLI fallback (dev mode)
        claude_prompt = SMARTEST_CLAUDE_PROMPT.format(
            agent_id=agent_id,
            persona=persona,
            grounding_rules=_phase3_grounding,
            distilled_briefing=distilled,
            user_message=user_message,
        )

        response_text = None
        actual_tokens_in = 0
        actual_tokens_out = 0
        phase3_model = "cloud"

        # Read tier settings to determine Phase 3 model order
        from cohort.local.config import get_smartest_model, get_smartest_fallback
        smartest_primary = get_smartest_model()
        smartest_fallback = get_smartest_fallback()

        # Try local 35B model first (if configured as primary)
        if smartest_primary not in ("cloud_api", "local") and not response_text:
            _esc_allowed, _esc_reason = _check_escalation_rate()
            if not _esc_allowed:
                logger.warning("[!] %s -- skipping local escalation for %s", _esc_reason, agent_id)
            try:
                from cohort.api import LocalRouter
                _p3_router = LocalRouter()
                if _esc_allowed and _p3_router._ensure_client():
                    _p3_prompt = f"{system_prompt}\n\n{phase3_user_message}"
                    _p3_result = _p3_router._client.generate(
                        model=smartest_primary,
                        prompt=_p3_prompt,
                        temperature=0.3,
                        think=True,
                        keep_alive="0",
                        options={"num_predict": 8192},
                    )
                    if _p3_result is not None and _p3_result.text.strip():
                        response_text = _p3_result.text.strip()
                        actual_tokens_in = _p3_result.tokens_in
                        actual_tokens_out = _p3_result.tokens_out
                        phase3_model = smartest_primary
                        _record_escalation_call()
                        logger.info("[OK] Smartest Phase 3 (local/%s): %s", smartest_primary, agent_id)
            except Exception:
                logger.exception("[!] Smartest Phase 3 local %s failed for %s", smartest_primary, agent_id)

        # Try cloud API (if configured as primary or fallback, and not yet resolved)
        _try_cloud = (
            not response_text
            and (smartest_primary == "cloud_api" or smartest_fallback == "cloud_api")
        )
        if _try_cloud:
            # Budget check: query accumulated token spend before allowing cloud call
            _cloud_allowed = True
            if _chat is not None and hasattr(_chat, "check_token_budget"):
                try:
                    from cohort.local.config import get_budget_limits
                    _limits = get_budget_limits()
                    _cloud_allowed, _remaining, _budget_reason = _chat.check_token_budget(
                        daily_limit=_limits["daily_token_limit"],
                        monthly_limit=_limits["monthly_token_limit"],
                    )
                    if not _cloud_allowed:
                        logger.warning("[!] Cloud API budget exceeded for %s: %s", agent_id, _budget_reason)
                except Exception:
                    pass  # Budget check failure should not block the call

            if not _cloud_allowed:
                logger.info("[*] Skipping cloud API (budget limit) -- will use Qwen draft for %s", agent_id)
            else:
                from cohort.local.cloud import get_cloud_backend
                cloud = get_cloud_backend(_cloud_settings)
                if cloud is not None:
                    try:
                        cr = cloud.complete(system_prompt, phase3_user_message)
                        response_text = cr.text.strip()
                        actual_tokens_in = cr.tokens_in
                        actual_tokens_out = cr.tokens_out
                        phase3_model = cr.model
                        logger.info("[OK] Smartest Phase 3 (cloud/%s): %s", cr.model, agent_id)
                    except Exception:
                        logger.exception("[!] Smartest Phase 3 cloud failed for %s", agent_id)

        # Channel mode: persistent Claude Code session via MCP Channels
        if not response_text and _channel_mode_enabled:
            from cohort.channel_bridge import channel_mode_active
            if channel_mode_active():
                try:
                    from cohort.channel_bridge import (
                        enqueue_channel_request,
                        await_channel_response,
                    )

                    logger.info("[>>] Smartest Phase 3 (channel): %s", agent_id)
                    request_id = enqueue_channel_request(
                        prompt=claude_prompt,
                        agent_id=agent_id,
                        channel_id="smartest",
                        response_mode="smartest",
                    )
                    ch_content, ch_meta = await_channel_response(
                        request_id, timeout=RESPONSE_TIMEOUT,
                    )
                    if ch_content:
                        response_text = ch_content
                        actual_tokens_in = len(claude_prompt) // 4
                        actual_tokens_out = len(response_text) // 4
                        phase3_model = "claude_code_channel"
                        logger.info("[OK] Smartest Phase 3 (channel): %s", agent_id)
                except Exception:
                    logger.exception("[!] Smartest Phase 3 channel failed for %s", agent_id)

        # Dev mode: CLI subprocess with response harvesting (internal testing)
        if not response_text and _dev_mode and check_claude_cli_available():
            logger.info("[>>] Smartest Phase 3 dev-mode CLI fallback for %s", agent_id)
            env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
            env.update(_load_agent_credentials(agent_id))

            cli_cmd = [CLAUDE_CMD, "-p", "-"]
            if sys.platform == "win32":
                cli_cmd = ["cmd", "/c"] + cli_cmd

            result = subprocess.run(
                cli_cmd,
                input=claude_prompt,
                capture_output=True,
                text=True,
                cwd=str(AGENTS_ROOT) if AGENTS_ROOT else None,
                timeout=RESPONSE_TIMEOUT,
                shell=False,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            if result.returncode == 0 and result.stdout.strip():
                response_text = result.stdout.strip()
                actual_tokens_in = len(claude_prompt) // 4  # estimated
                actual_tokens_out = len(response_text) // 4
                phase3_model = "claude_code"
                logger.info("[OK] Smartest Phase 3 (dev CLI): %s", agent_id)

        # Handoff mode: open in Claude Code, user takes over (no response harvesting)
        if not response_text and not _dev_mode and check_claude_cli_available():
            logger.info("[>>] Smartest Phase 3 Claude Code handoff for %s", agent_id)
            env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
            env.update(_load_agent_credentials(agent_id))

            cli_cmd = [CLAUDE_CMD, "-p", "-", "--output-format", "json"]
            if sys.platform == "win32":
                cli_cmd = ["cmd", "/c"] + cli_cmd

            try:
                result = subprocess.run(
                    cli_cmd,
                    input=claude_prompt,
                    capture_output=True,
                    text=True,
                    cwd=str(AGENTS_ROOT) if AGENTS_ROOT else None,
                    timeout=RESPONSE_TIMEOUT,
                    shell=False,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
                session_id = ""
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        cli_json = json.loads(result.stdout)
                        session_id = cli_json.get("session_id", "")
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("[!] Could not parse Claude Code JSON output")

                elapsed = round(time.monotonic() - t0, 1)
                handoff_metadata = {
                    "tier": 6,
                    "model": f"{phase1_result.model}+claude_code",
                    "pipeline": "smartest-handoff",
                    "confidence": "high",
                    "elapsed_seconds": elapsed,
                    "tokens_in": phase1_result.tokens_in + (len(claude_prompt) // 4),
                    "tokens_out": phase1_result.tokens_out,
                    "claude_code_handoff": {
                        "session_id": session_id,
                        "status": "handed_off",
                    },
                }
                logger.info("[OK] Smartest Phase 3 (handoff): %s session=%s", agent_id, session_id)
                return None, handoff_metadata
            except subprocess.TimeoutExpired:
                logger.error("[X] Claude Code handoff timeout for %s", agent_id)
            except Exception:
                logger.exception("[X] Claude Code handoff failed for %s", agent_id)

        elapsed = round(time.monotonic() - t0, 1)

        if response_text:
            metadata = {
                "tier": 6,
                "model": f"{phase1_result.model}+{phase3_model}",
                "pipeline": "smartest",
                "confidence": "high",
                "elapsed_seconds": elapsed,
                "tokens_in": phase1_result.tokens_in + actual_tokens_in,
                "tokens_out": phase1_result.tokens_out + actual_tokens_out,
            }
            logger.info("[OK] Smartest Phase 3: %s (%.1fs total)", agent_id, elapsed)
            return response_text, metadata

        logger.warning("[!] Smartest Phase 3 failed for %s, returning Qwen draft", agent_id)
    except subprocess.TimeoutExpired:
        logger.error("[X] Smartest Phase 3 timeout for %s", agent_id)
    except Exception:
        logger.exception("[X] Smartest Phase 3 exception for %s", agent_id)

    # Degraded: return Qwen's draft answer
    elapsed = round(time.monotonic() - t0, 1)
    confidence = classify_confidence(
        prompt=user_message, pipeline="smartest-degraded",
        tier=phase1_result.tier,
    )
    metadata = {
        "tier": phase1_result.tier,
        "model": phase1_result.model,
        "pipeline": "smartest-degraded",
        "confidence": confidence,
        "elapsed_seconds": elapsed,
        "tokens_in": phase1_result.tokens_in,
        "tokens_out": phase1_result.tokens_out,
    }
    return qwen_draft, metadata


def _invoke_agent_sync(item: dict) -> None:
    """Load agent prompt, call Claude CLI, post response back to chat."""
    agent_id = item["agent_id"]
    channel_id = item["channel_id"]
    thread_id = item["thread_id"]
    message_content = item["message_content"]

    # Response mode: "smarter" (default), "smart" (fast), or "smartest" (Qwen+Claude)
    response_mode = item.get("response_mode", "smarter")

    # Env override for always-full-prompt
    if os.environ.get("COHORT_FULL_PROMPT", "").strip() == "1":
        response_mode = "smarter"

    # Smarter/Smartest/Channel = full prompt; Smart = lightweight persona
    use_full_prompt = (response_mode in ("smarter", "smartest", "channel"))

    # Load agent prompt -- persona first (light mode), full prompt as fallback
    agent_prompt: str | None = None

    if not use_full_prompt:
        # Light mode: try persona from AgentStore, then from personas/ loader
        if _agent_store is not None:
            config = _agent_store.get(agent_id)
            if config and config.persona_text:
                agent_prompt = config.persona_text
        if not agent_prompt:
            agent_prompt = load_persona(agent_id)

    if not agent_prompt:
        # Heavy mode or persona not found: load full agent_prompt.md
        prompt_path = get_agent_prompt_path(agent_id)
        if not prompt_path:
            logger.warning("[!] No prompt for %s, skipping", agent_id)
            return
        try:
            full_text = prompt_path.read_text(encoding="utf-8")
        except Exception:
            logger.exception("[X] Failed to read prompt for %s", agent_id)
            return
        if use_full_prompt:
            agent_prompt = full_text
        else:
            # Truncated fallback: first 1000 chars of agent_prompt.md
            agent_prompt = full_text[:1000]

    # Build context
    channel_context = build_channel_context(channel_id)
    thread_context = _build_thread_context(thread_id, channel_id) if thread_id else ""

    # Resolve tool permissions (needed for both prompt injection and CLI flags)
    agent_cfg_for_perms = _agent_store.get(agent_id) if _agent_store else None
    perms = resolve_permissions(agent_id, agent_cfg_for_perms, get_central_permissions())

    # Build tool awareness section (only for Claude CLI path -- local tool
    # calling gets real schemas, not text descriptions)
    tool_awareness = ""
    if perms and perms.allowed_tools:
        tool_awareness = _build_tool_awareness(perms)

    # Build collaboration awareness: who else is in this channel
    _collab_section = ""
    if _chat is not None:
        channel_obj = _chat.get_channel(channel_id)
        if channel_obj and hasattr(channel_obj, "members") and channel_obj.members:
            other_members = [m for m in channel_obj.members if m != agent_id and m not in HUMAN_USERS]
            if other_members:
                member_list = ", ".join(f"@{m}" for m in other_members)
                _collab_section = (
                    "=== COLLABORATION ===\n"
                    f"Other agents in this channel: {member_list}\n"
                    "When your response would benefit from another agent's expertise, "
                    "tag them with @agent_name to bring them into the conversation. "
                    "For example, tag @cohort_orchestrator to coordinate multi-agent work, "
                    "or tag a specialist agent for domain-specific analysis.\n"
                    "=== END COLLABORATION ===\n\n"
                )

    # Load agent memory (learned facts, tasks, collaborators)
    _agent_memory_block = load_agent_context(
        agent_id, query=message_content, agent_store=_agent_store,
    )

    # Load per-project memory (working memory scoped to this workspace)
    _project_path = item.get("project_path")
    _project_memory_block = load_project_memory(
        agent_id, project_path=_project_path, query=message_content,
    ) if _project_path else ""

    # Load user profile (core paragraph + adaptation rules + distilled traits)
    _user_profile_block = load_user_profile_block(
        conversation_context=channel_context + thread_context,
    )

    # Construct prompts: one with tool awareness (Claude CLI), one without (local tools)
    _base_prompt = (
        f"You are responding as the {agent_id} agent in Cohort team chat.\n\n"
        f"Follow this agent prompt exactly:\n\n"
        f"---\n{agent_prompt}\n---\n\n"
        f"{_user_profile_block}\n"
        f"{_agent_memory_block}\n"
        f"{_project_memory_block}\n"
        f"{GROUNDING_RULES}\n"
        f"{_collab_section}"
    )
    _context_suffix = (
        f"{channel_context}"
        f"{thread_context}"
        f"Now respond to this message:\n{message_content}"
    )
    # Full prompt with tool awareness text (for Claude CLI fallback)
    full_prompt = _base_prompt + tool_awareness + _context_suffix
    # Clean prompt without tool awareness (for local tool calling -- model gets real schemas)
    _local_prompt = _base_prompt + _context_suffix

    # D5: Circuit breaker -- reject oversized prompts
    prompt_byte_len = len(full_prompt.encode("utf-8"))
    if prompt_byte_len > CIRCUIT_BREAKER_CHAR_LIMIT:
        error_msg = (
            f"[!] Prompt for {agent_id} exceeds safety limit "
            f"({prompt_byte_len:,} bytes > {CIRCUIT_BREAKER_CHAR_LIMIT:,}). "
            f"Use `cohort clear` to reset context or set COHORT_FULL_PROMPT=1 "
            f"for verbose mode."
        )
        logger.error(error_msg)
        if _chat is not None:
            err_response = _chat.post_message(
                channel_id=channel_id,
                sender="system",
                content=error_msg,
                thread_id=thread_id,
            )
            _emit_sync("new_message", err_response.to_dict())
        return

    # Emit typing indicator
    _emit_sync("user_typing", {"sender": agent_id, "typing": True, "channel_id": channel_id})

    # D5: Try local router first, fallback to Claude CLI transparently
    response_content: str | None = None
    response_metadata: dict[str, Any] = {}

    # Load per-agent temperature from agent_config.json (if available)
    agent_temperature: float | None = None
    if _agent_store is not None:
        agent_cfg = _agent_store.get(agent_id)
        if agent_cfg and agent_cfg.model_params:
            agent_temperature = agent_cfg.model_params.get("temperature")

    # Skip local router entirely when force_to_claude_code is enabled
    if _force_claude_code:
        logger.info("[>>] Force Claude Code enabled, skipping local router for %s", agent_id)

    # Smartest pipeline: Qwen reasoning -> distill -> Cloud API (or CLI in dev mode)
    elif response_mode == "smartest":
        from cohort.local.cloud import check_cloud_available
        from cohort.channel_bridge import channel_mode_active as _ch_active
        _smartest_available = (
            check_cloud_available(_cloud_settings)
            or (_channel_mode_enabled and _ch_active())
            or (_dev_mode and check_claude_cli_available())
            or check_claude_cli_available()  # Handoff mode (no harvest)
        )
        if _smartest_available:
            response_content, response_metadata = _invoke_smartest_pipeline(
                agent_id=agent_id,
                user_message=message_content,
                local_prompt=_local_prompt,
                agent_temperature=agent_temperature,
            )
            if response_content:
                logger.info("[OK] Smartest pipeline handled %s in #%s", agent_id, channel_id)
            elif response_metadata and response_metadata.get("pipeline") == "smartest-handoff":
                # Handoff to Claude Code -- no response text, but that's intentional
                _handoff = response_metadata.get("claude_code_handoff", {})
                _session_id = _handoff.get("session_id", "")
                response_content = (
                    f"Opened in Claude Code. "
                    f"Session: `{_session_id}`" if _session_id
                    else "Opened in Claude Code."
                )
                logger.info("[OK] Smartest handoff for %s in #%s (session=%s)",
                            agent_id, channel_id, _session_id)
            else:
                logger.warning("[!] Smartest pipeline failed for %s, falling back to smarter",
                               agent_id)
                response_mode = "smarter"  # Degrade for fallback below
        else:
            logger.warning("[!] No cloud API or dev CLI available, falling back to smarter for %s",
                           agent_id)
            response_mode = "smarter"

    # Channel mode: route through per-channel persistent Claude Code session
    if response_mode == "channel" and not response_content:
        if _channel_mode_enabled:
            from cohort.channel_bridge import ensure_channel_session, enqueue_channel_request, await_channel_response
            if ensure_channel_session(channel_id):
                try:
                    request_id = enqueue_channel_request(
                        prompt=full_prompt,
                        agent_id=agent_id,
                        channel_id=channel_id,
                        thread_id=thread_id,
                        response_mode="channel",
                    )
                    ch_content, ch_meta = await_channel_response(
                        request_id, timeout=RESPONSE_TIMEOUT,
                    )
                    if ch_content:
                        response_content = ch_content
                        response_metadata = ch_meta or {}
                        logger.info("[OK] Channel mode: %s in #%s", agent_id, channel_id)
                except Exception:
                    logger.exception("[!] Channel mode failed for %s, degrading to smarter", agent_id)
                    response_mode = "smarter"
            else:
                logger.warning("[!] Could not start channel session for #%s, degrading to smarter", channel_id)
                response_mode = "smarter"
        else:
            response_mode = "smarter"

    # Standard local routing (smart / smarter modes)
    if not response_content and not _force_claude_code:
        try:
            from cohort.api import LocalRouter

            router = LocalRouter()

            if perms and perms.allowed_tools:
                # Tool-enabled local routing: use /api/chat with native tool calling
                from cohort.api import build_tool_schemas, execute_tool

                tool_schemas = build_tool_schemas(perms.allowed_tools)
                if tool_schemas:
                    messages = [{"role": "user", "content": _local_prompt}]
                    _resolved_file_perms = perms.file_permissions or []

                    def _tool_executor(name: str, args: dict) -> str:
                        return execute_tool(
                            name, args,
                            agents_root=AGENTS_ROOT or Path.cwd(),
                            file_permissions=_resolved_file_perms,
                        )

                    route_result = router.route_with_tools(
                        messages=messages,
                        tools=tool_schemas,
                        tool_executor=_tool_executor,
                        temperature=agent_temperature or 0.4,
                        max_turns=min(perms.max_turns, 15),  # Cap local tool turns
                        response_mode=response_mode,
                    )
                    if route_result is not None and route_result.text:
                        response_content = route_result.text
                        response_metadata = {
                            "tier": route_result.tier,
                            "model": route_result.model,
                            "confidence": route_result.confidence,
                            "elapsed_seconds": route_result.elapsed_seconds,
                            "tokens_in": route_result.tokens_in,
                            "tokens_out": route_result.tokens_out,
                        }
                        logger.info("[OK] Local tool loop handled %s in #%s (%s, %d/%d tok)",
                                    agent_id, channel_id, route_result.model,
                                    route_result.tokens_in, route_result.tokens_out)
            else:
                # No tools -- single-shot text generation
                task_type = "code" if any(kw in message_content.lower() for kw in ["code", "implement", "function", "class", "debug"]) else "general"
                route_result = router.route(
                    full_prompt,
                    task_type=task_type,
                    temperature=agent_temperature,
                    response_mode=response_mode,
                )
                if route_result is not None and route_result.text:
                    response_content = route_result.text
                    response_metadata = {
                        "tier": route_result.tier,
                        "model": route_result.model,
                        "confidence": route_result.confidence,
                        "elapsed_seconds": route_result.elapsed_seconds,
                        "tokens_in": route_result.tokens_in,
                        "tokens_out": route_result.tokens_out,
                    }
                    logger.info("[OK] Local router handled %s in #%s (%s, %d/%d tok)",
                                agent_id, channel_id, route_result.model,
                                route_result.tokens_in, route_result.tokens_out)
        except Exception:
            # Local routing failed -- fall through to Claude CLI
            logger.debug("[*] Local router unavailable for %s, using Claude CLI", agent_id)

    # Fallback: cloud API (distribution) or CLI subprocess (dev mode)
    if not response_content:
        logger.info("[>>] Invoking cloud/CLI fallback for %s in #%s", agent_id, channel_id)
        cli_t0 = time.monotonic()
        mcp_config_path: Path | None = None

        # Try cloud API first (distribution path)
        from cohort.local.cloud import get_cloud_backend
        cloud = get_cloud_backend(_cloud_settings)
        if cloud is not None:
            try:
                # Split prompt into system/user for cloud API
                cr = cloud.complete(
                    system_prompt="You are a helpful AI assistant.",
                    user_message=full_prompt,
                )
                if cr.text.strip():
                    response_content = cr.text.strip()
                    response_metadata = {
                        "tier": 5,
                        "model": cr.model,
                        "confidence": "high",
                        "elapsed_seconds": cr.elapsed_seconds,
                        "tokens_in": cr.tokens_in,
                        "tokens_out": cr.tokens_out,
                    }
                    logger.info("[OK] Cloud fallback for %s (%s, %d/%d tok)",
                                agent_id, cr.model, cr.tokens_in, cr.tokens_out)
            except Exception:
                logger.exception("[!] Cloud fallback failed for %s", agent_id)

        # Channel mode: persistent Claude Code session via MCP Channels
        if not response_content and _channel_mode_enabled:
            from cohort.channel_bridge import channel_mode_active
            if channel_mode_active():
                try:
                    from cohort.channel_bridge import (
                        enqueue_channel_request,
                        await_channel_response,
                    )

                    logger.info("[>>] Channel mode for %s in #%s", agent_id, channel_id)
                    ch_t0 = time.monotonic()
                    # Channel session already has conversation history in
                    # its context window -- strip channel context to avoid
                    # double-loading (saves ~2-15K tokens per request).
                    _channel_prompt = _base_prompt + (
                        f"Now respond to this message in #{channel_id}:\n"
                        f"{message_content}"
                    )
                    request_id = enqueue_channel_request(
                        prompt=_channel_prompt,
                        agent_id=agent_id,
                        channel_id=channel_id,
                        thread_id=thread_id,
                        response_mode=response_mode,
                    )
                    ch_content, ch_meta = await_channel_response(
                        request_id, timeout=RESPONSE_TIMEOUT,
                    )
                    if ch_content:
                        response_content = ch_content
                        ch_elapsed = round(time.monotonic() - ch_t0, 1)
                        response_metadata = ch_meta or {}
                        response_metadata.setdefault("elapsed_seconds", ch_elapsed)
                        logger.info(
                            "[OK] Channel response for %s in #%s (%.1fs)",
                            agent_id, channel_id, ch_elapsed,
                        )
                    else:
                        logger.warning(
                            "[!] Channel mode returned empty for %s: %s",
                            agent_id, ch_meta.get("error", "unknown"),
                        )
                except Exception:
                    logger.exception("[!] Channel mode failed for %s", agent_id)
            else:
                logger.debug("[*] Channel session not active, skipping for %s", agent_id)

        # Dev mode: CLI subprocess fallback (not available in distribution)
        if not response_content and _dev_mode:
            try:
                # Strip CLAUDECODE env vars so Claude CLI doesn't refuse to start
                # when the server is launched from within a Claude Code session.
                env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
                # Inject permitted service credentials into agent environment
                env.update(_load_agent_credentials(agent_id))

                if perms and perms.allowed_tools:
                    # Tool-enabled invocation (follows BOSS code queue worker pattern)
                    cli_cmd = [CLAUDE_CMD, "-p"]

                    if perms.permission_mode:
                        cli_cmd.extend(["--permission-mode", perms.permission_mode])

                    cli_cmd.extend(["--allowedTools", ",".join(perms.allowed_tools)])
                    cli_cmd.extend(["--max-turns", str(perms.max_turns)])
                    cli_cmd.extend(["--output-format", "text"])

                    # MCP server config (temporary file)
                    if perms.mcp_servers:
                        mcp_config_path = _write_mcp_config(perms.mcp_servers)
                        if mcp_config_path:
                            cli_cmd.extend(["--mcp-config", str(mcp_config_path)])

                    cli_cmd.append("-")  # read from stdin

                    # Windows: use cmd /c for .cmd files (proven BOSS pattern)
                    if sys.platform == "win32":
                        cli_cmd = ["cmd", "/c"] + cli_cmd

                    logger.info("[>>] Dev-mode tool-enabled CLI: %s (profile=%s, tools=%s)",
                                agent_id, perms.profile_name, ",".join(perms.allowed_tools))

                    result = subprocess.run(
                        cli_cmd,
                        input=full_prompt,
                        capture_output=True,
                        text=True,
                        cwd=str(AGENTS_ROOT) if AGENTS_ROOT else None,
                        timeout=RESPONSE_TIMEOUT,
                        shell=False,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                    )
                else:
                    # No tools -- simple CLI path
                    cli_cmd = [CLAUDE_CMD, "-p", "-"]
                    if sys.platform == "win32":
                        cli_cmd = ["cmd", "/c"] + cli_cmd

                    result = subprocess.run(
                        cli_cmd,
                        input=full_prompt,
                        capture_output=True,
                        text=True,
                        cwd=str(AGENTS_ROOT) if AGENTS_ROOT else None,
                        timeout=RESPONSE_TIMEOUT,
                        shell=False,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                    )

                response_content = result.stdout.strip()
                if result.returncode != 0 and not response_content:
                    response_content = f"[Error] Agent {agent_id} failed: {result.stderr.strip()[:200]}"
                    logger.error("[X] Claude CLI error for %s: %s", agent_id, result.stderr[:200])
                else:
                    cli_elapsed = round(time.monotonic() - cli_t0, 1)
                    est_in = len(full_prompt) // 4
                    est_out = len(response_content) // 4 if response_content else 0
                    response_metadata = {
                        "tier": 5,
                        "model": "claude_code",
                        "confidence": "high",
                        "elapsed_seconds": cli_elapsed,
                        "tokens_in": est_in,
                        "tokens_out": est_out,
                    }

            except subprocess.TimeoutExpired:
                response_content = f"[Timeout] Agent {agent_id} response timed out after {RESPONSE_TIMEOUT}s"
                logger.error("[X] Claude CLI timeout for %s", agent_id)
            except Exception as exc:
                response_content = f"[Error] Agent {agent_id} invocation failed: {exc}"
                logger.exception("[X] Claude CLI exception for %s", agent_id)
            finally:
                # Clean up temporary MCP config file
                if mcp_config_path and mcp_config_path.exists():
                    try:
                        mcp_config_path.unlink()
                    except OSError:
                        pass

    # Stop typing indicator
    _emit_sync("user_typing", {"sender": agent_id, "typing": False, "channel_id": channel_id})

    if not response_content:
        logger.warning("[!] Empty response from %s, skipping post", agent_id)
        return

    # Post agent response back to chat
    if _chat is None:
        return

    response_msg = _chat.post_message(
        channel_id=channel_id,
        sender=agent_id,
        content=response_content,
        thread_id=thread_id,
        metadata=response_metadata if response_metadata else None,
    )

    # Broadcast to all connected clients
    _emit_sync("new_message", response_msg.to_dict())

    logger.info("[OK] %s responded in #%s (msg %s)", agent_id, channel_id, response_msg.id)

    # Record to agent memory (if store is available)
    if _agent_store is not None:
        try:
            from cohort.api import WorkingMemoryEntry
            from cohort.api import MemoryManager

            mm = MemoryManager(_agent_store)
            mm.add_working_memory(agent_id, WorkingMemoryEntry(
                timestamp=response_msg.timestamp,
                channel=channel_id,
                input=message_content[:500],
                response=response_content[:500],
            ))
        except Exception:
            logger.debug("[!] Failed to record working memory for %s", agent_id)

    # Learn from conversation (async, non-blocking)
    try:
        from cohort.learning import maybe_learn_async
        maybe_learn_async(agent_id, channel_id, message_content, response_content, _agent_store)
    except Exception:
        pass  # Never break chat for learning failures

    # Track conversation depth
    _set_conversation_depth(response_msg.id, thread_id)

    # Chain routing: if the agent's response contains @mentions, queue those too
    channel = _chat.get_channel(channel_id) if _chat else None
    has_active_session = channel and getattr(channel, "meeting_context", None) is not None

    if not has_active_session:
        chain_mentions = parse_mentions(response_content)
        for mentioned in chain_mentions:
            # Skip self-mentions and the original sender
            if mentioned.lower() == agent_id.lower():
                continue
            if mentioned.lower() in HUMAN_USERS:
                continue

            resolved = resolve_agent_id(mentioned)
            if not resolved:
                continue
            if not get_agent_prompt_path(resolved):
                continue
            if _check_response_loop(channel_id, resolved):
                logger.info("[*] Skipping chain @%s -- loop detected", resolved)
                continue

            depth = _get_conversation_depth(response_msg.id)
            if depth >= MAX_CONVERSATION_DEPTH:
                logger.info("[*] Skipping chain @%s -- max depth", resolved)
                break

            queue_agent_response(
                agent_id=resolved,
                message_content=response_content,
                channel_id=channel_id,
                thread_id=response_msg.id,
                priority=50,
                response_mode="smarter",  # Chain responses use smarter mode
            )


# =====================================================================
# Multi-agent roundtable via channel session
# =====================================================================

def _route_roundtable_to_channel(
    message: Any,
    mentions: list[str],
    *,
    response_mode: str = "smarter",
) -> None:
    """Bundle 3+ agent mentions into a single channel roundtable request.

    Instead of queuing N independent agent requests, we send ONE request
    to the Claude channel session with all agent personas loaded.  Claude
    orchestrates the multi-round discussion internally and posts each
    agent's response back via the ``cohort_post`` MCP tool.

    The channel session's system prompt contains roundtable orchestration
    patterns (round structure, cross-pollination, convergence, synthesis).
    """
    from cohort.channel_bridge import enqueue_channel_request, await_channel_response

    channel_id = getattr(message, "channel_id", "")
    message_content = getattr(message, "content", "")

    # Resolve and load personas for each mentioned agent
    agent_personas: list[tuple[str, str]] = []  # (agent_id, persona_text)
    for mention in mentions:
        mention_lower = mention.lower()
        if mention_lower in HUMAN_USERS or mention_lower == "claude":
            continue

        resolved = resolve_agent_id(mention)
        if not resolved:
            continue

        # Load persona (lightweight) or full prompt
        persona: str | None = None
        if _agent_store is not None:
            config = _agent_store.get(resolved)
            if config and config.persona_text:
                persona = config.persona_text
        if not persona:
            persona = load_persona(resolved)
        if not persona:
            prompt_path = get_agent_prompt_path(resolved)
            if prompt_path:
                try:
                    persona = prompt_path.read_text(encoding="utf-8")[:2000]
                except Exception:
                    continue
        if persona:
            agent_personas.append((resolved, persona))

    if len(agent_personas) < 2:
        logger.warning("[!] Roundtable needs 2+ agents, falling back to individual routing")
        # Fall through to normal routing handled by caller
        return

    # Build the roundtable prompt
    agent_list = ", ".join(aid for aid, _ in agent_personas)
    logger.info("[>>] Channel roundtable: %s in #%s", agent_list, channel_id)

    persona_blocks = []
    for agent_id, persona in agent_personas:
        persona_blocks.append(
            f"=== {agent_id} ===\n{persona}\n=== END {agent_id} ==="
        )

    roundtable_prompt = (
        f"# Roundtable Discussion in #{channel_id}\n\n"
        f"You are orchestrating a multi-agent roundtable discussion.\n"
        f"The following agents are participating:\n\n"
        + "\n\n".join(persona_blocks)
        + f"\n\n## The Seed Message\n\n{message_content}\n\n"
        f"## Instructions\n\n"
        f"Run a multi-round collaborative discussion following your roundtable "
        f"orchestration training. For EACH agent response, call `cohort_post` "
        f"with the agent's ID as sender and #{channel_id} as channel.\n\n"
        f"Structure:\n"
        f"- Round 1: Each agent gives their initial position (150-200 words)\n"
        f"- Round 2: Agents respond to each other -- build on, challenge, or extend (100-150 words)\n"
        f"- Round 3: Convergence -- final positions incorporating insights (80-120 words)\n"
        f"- Synthesis: Post as 'system' with consensus, tensions, recommendations, action items\n\n"
        f"Post each response as a separate `cohort_post` call so they appear as "
        f"individual messages in the chat. Do NOT batch them.\n\n"
        f"After posting all messages, call `cohort_respond` to signal completion."
    )

    # Emit typing indicator for first agent
    _emit_sync("user_typing", {
        "sender": agent_personas[0][0],
        "typing": True,
        "channel_id": channel_id,
    })

    def _run_roundtable() -> None:
        try:
            t0 = time.monotonic()
            request_id = enqueue_channel_request(
                prompt=roundtable_prompt,
                agent_id="roundtable",
                channel_id=channel_id,
                response_mode=response_mode,
            )
            # Long timeout -- roundtable produces many messages
            content, meta = await_channel_response(
                request_id, timeout=max(RESPONSE_TIMEOUT * 3, 600),
            )
            elapsed = round(time.monotonic() - t0, 1)
            logger.info(
                "[OK] Channel roundtable complete in #%s (%.1fs, %d agents)",
                channel_id, elapsed, len(agent_personas),
            )
        except Exception:
            logger.exception("[X] Channel roundtable failed in #%s", channel_id)

    # Run in background thread (same pattern as queue processor)
    thread = threading.Thread(target=_run_roundtable, daemon=True)
    thread.start()


# =====================================================================
# Entry point (called from socketio_events.py)
# =====================================================================

def route_mentions(
    message: Any,
    mentions: list[str],
    *,
    response_mode: str = "smarter",
    project_path: str | None = None,
) -> None:
    """Route @mentions from a posted message to agent response queue.

    This is the main entry point, called after a message is posted and broadcast.

    Args:
        message: The posted message object.
        mentions: List of @mentioned agent IDs.
        response_mode: "smart", "smarter" (default), or "smartest".
        project_path: Workspace path for per-project memory injection.
    """
    if not mentions or _chat is None:
        return

    # Check conversation depth on the parent
    if message.thread_id:
        depth = _get_conversation_depth(message.thread_id)
        if depth >= MAX_CONVERSATION_DEPTH:
            logger.warning("[!] Max depth reached, not routing mentions")
            return

    # Multi-agent roundtable: if 3+ agents mentioned and channel session is
    # alive, bundle into a single roundtable request.  Claude orchestrates
    # the multi-round discussion internally and posts each agent's response
    # via cohort_post.
    if len(mentions) >= 3 and _channel_mode_enabled:
        from cohort.channel_bridge import channel_mode_active
        if channel_mode_active():
            _route_roundtable_to_channel(message, mentions, response_mode=response_mode)
            return

    sender = getattr(message, "sender", "")
    channel_id = getattr(message, "channel_id", "")

    # Check session gating (scoring engine gates agents when a session is active)
    channel = _chat.get_channel(channel_id) if channel_id else None
    has_active_session = channel and getattr(channel, "meeting_context", None) is not None

    priority_boost = 0  # first mentioned agent gets a boost
    for mention in mentions:
        mention_lower = mention.lower()

        # Skip humans, claude, self
        if mention_lower in HUMAN_USERS:
            continue
        if mention_lower == "claude":
            continue
        if mention_lower == sender.lower():
            continue

        # Handle @all -> route to the orchestrator agent (discovered dynamically)
        if mention_lower == "all":
            mention = _find_orchestrator_agent() or "cohort_orchestrator"

        # Resolve alias
        resolved = resolve_agent_id(mention)
        if not resolved:
            logger.info("[*] Skipping @%s -- no agent found", mention)
            continue

        # Validate prompt exists (file on disk or persona in memory)
        if not get_agent_prompt_path(resolved):
            # Gateway agents may have persona_text in memory but no disk file
            agent_cfg = _agent_store.get(resolved) if _agent_store else None
            if not (agent_cfg and agent_cfg.persona_text):
                logger.info("[*] Skipping @%s -- no prompt file or persona", resolved)
                continue

        # Session gating (if orchestrator is wired up and session is active)
        if has_active_session and _orchestrator:
            session = _orchestrator.get_session_for_channel(channel_id)
            if session:
                should_respond, reason = _orchestrator.should_agent_respond(
                    session.session_id, resolved, message.content,
                )
                if not should_respond:
                    logger.info("[*] Gated %s in roundtable: %s", resolved, reason)
                    continue

        # Loop detection
        if _check_response_loop(channel_id, resolved):
            logger.info("[*] Skipping @%s -- loop detected", resolved)
            continue

        # Calculate priority (first mentioned = highest priority)
        priority = 50 - priority_boost
        priority_boost = max(priority_boost, 10)  # subsequent agents get lower priority

        # Orchestrator agents get priority boost for coordination messages
        agent_type = _get_agent_type(resolved)
        if agent_type == "orchestrator":
            content_lower = message.content.lower()
            is_first_mention = mentions[0].lower().replace("-", "_").replace(" ", "_") == resolved
            has_coordination_keywords = any(w in content_lower for w in (
                "coordinate", "route", "task", "plan", "workflow",
                "delegate", "assign", "orchestrate",
            ))

            if is_first_mention and has_coordination_keywords:
                priority = 1
            elif is_first_mention:
                priority = 2
            elif has_coordination_keywords:
                priority = 5

        queue_agent_response(
            agent_id=resolved,
            message_content=message.content,
            channel_id=channel_id,
            thread_id=message.id,
            priority=priority,
            response_mode=response_mode,
            project_path=project_path,
        )

    # Track depth for the triggering message
    _set_conversation_depth(message.id, message.thread_id)
