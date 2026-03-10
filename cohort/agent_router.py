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

from cohort.chat import parse_mentions
from cohort.agent_store import AgentStore
from cohort.context_window import truncate_context
from cohort.personas import load_persona
from cohort.tool_permissions import resolve_permissions, get_central_permissions, ResolvedPermissions

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
    from cohort.secret_store import decrypt_settings_secrets
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

# Cache Claude CLI availability for Smartest mode
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


_state = _RouterState()

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
    global CLAUDE_CMD, RESPONSE_TIMEOUT, AGENTS_ROOT, _force_claude_code  # noqa: PLW0603

    if "force_to_claude_code" in settings:
        _force_claude_code = bool(settings["force_to_claude_code"])
        logger.info("[OK] Force Claude Code: %s", _force_claude_code)

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
) -> None:
    """Add an agent response to the priority queue."""
    with _state.queue_lock:
        # Dedup -- skip if this agent is already queued for this channel
        for entry in _state.queue:
            if entry["agent_id"] == agent_id and entry["channel_id"] == channel_id:
                logger.info("[*] Skipping duplicate queue entry: %s in #%s", agent_id, channel_id)
                return

        _state.queue.append({
            "agent_id": agent_id,
            "message_content": message_content,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "priority": priority,
            "queued_at": time.time(),
            "response_mode": response_mode,
        })
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
        from cohort.local import LocalRouter

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

    # Phase 3: Claude CLI
    try:
        from cohort.local.config import SMARTEST_CLAUDE_PROMPT

        # Load lightweight persona (not full agent_prompt.md)
        persona = ""
        if _agent_store is not None:
            config = _agent_store.get(agent_id)
            if config and config.persona_text:
                persona = config.persona_text
        if not persona:
            persona = load_persona(agent_id) or f"You are the {agent_id} agent."

        claude_prompt = SMARTEST_CLAUDE_PROMPT.format(
            agent_id=agent_id,
            persona=persona,
            grounding_rules=GROUNDING_RULES,
            distilled_briefing=distilled,
            user_message=user_message,
        )

        # Strip CLAUDECODE env vars
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
        # Inject permitted service credentials
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

        elapsed = round(time.monotonic() - t0, 1)

        if result.returncode == 0 and result.stdout.strip():
            response_text = result.stdout.strip()
            est_claude_in = len(claude_prompt) // 4
            est_claude_out = len(response_text) // 4
            metadata = {
                "tier": 6,
                "model": f"{phase1_result.model}+claude",
                "pipeline": "smartest",
                "elapsed_seconds": elapsed,
                "tokens_in": phase1_result.tokens_in + est_claude_in,
                "tokens_out": phase1_result.tokens_out + est_claude_out,
            }
            logger.info("[OK] Smartest Phase 3: %s (%.1fs total)", agent_id, elapsed)
            return response_text, metadata

        # Claude CLI failed -- return Qwen draft
        logger.warning("[!] Smartest Phase 3 failed for %s (rc=%d), returning Qwen draft",
                       agent_id, result.returncode)
    except subprocess.TimeoutExpired:
        logger.error("[X] Smartest Phase 3 timeout for %s", agent_id)
    except Exception:
        logger.exception("[X] Smartest Phase 3 exception for %s", agent_id)

    # Degraded: return Qwen's draft answer
    elapsed = round(time.monotonic() - t0, 1)
    metadata = {
        "tier": phase1_result.tier,
        "model": phase1_result.model,
        "pipeline": "smartest-degraded",
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

    # Smarter/Smartest = full prompt; Smart = lightweight persona
    use_full_prompt = (response_mode in ("smarter", "smartest"))

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

    # Construct prompts: one with tool awareness (Claude CLI), one without (local tools)
    _base_prompt = (
        f"You are responding as the {agent_id} agent in Cohort team chat.\n\n"
        f"Follow this agent prompt exactly:\n\n"
        f"---\n{agent_prompt}\n---\n\n"
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

    # Smartest pipeline: Qwen reasoning -> distill -> Claude CLI
    elif response_mode == "smartest":
        if check_claude_cli_available():
            response_content, response_metadata = _invoke_smartest_pipeline(
                agent_id=agent_id,
                user_message=message_content,
                local_prompt=_local_prompt,
                agent_temperature=agent_temperature,
            )
            if response_content:
                logger.info("[OK] Smartest pipeline handled %s in #%s", agent_id, channel_id)
            else:
                logger.warning("[!] Smartest pipeline failed for %s, falling back to smarter",
                               agent_id)
                response_mode = "smarter"  # Degrade for fallback below
        else:
            logger.warning("[!] Claude CLI unavailable, falling back to smarter for %s", agent_id)
            response_mode = "smarter"

    # Standard local routing (smart / smarter modes)
    if not response_content and not _force_claude_code:
        try:
            from cohort.local import LocalRouter

            router = LocalRouter()

            if perms and perms.allowed_tools:
                # Tool-enabled local routing: use /api/chat with native tool calling
                from cohort.local.tools import build_tool_schemas, execute_tool

                tool_schemas = build_tool_schemas(perms.allowed_tools)
                if tool_schemas:
                    messages = [{"role": "user", "content": _local_prompt}]

                    def _tool_executor(name: str, args: dict) -> str:
                        return execute_tool(name, args, agents_root=AGENTS_ROOT or Path.cwd())

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

    # Fallback to Claude CLI if local routing failed or returned None
    if not response_content:
        logger.info("[>>] Invoking Claude CLI for %s in #%s", agent_id, channel_id)
        cli_t0 = time.monotonic()
        mcp_config_path: Path | None = None

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

                logger.info("[>>] Tool-enabled CLI: %s (profile=%s, tools=%s)",
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
                # Estimate tokens from char counts (Claude CLI doesn't expose usage)
                est_in = len(full_prompt) // 4
                est_out = len(response_content) // 4 if response_content else 0
                response_metadata = {
                    "tier": 5,
                    "model": "claude_code",
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
            from cohort.agent import WorkingMemoryEntry
            from cohort.memory_manager import MemoryManager

            mm = MemoryManager(_agent_store)
            mm.add_working_memory(agent_id, WorkingMemoryEntry(
                timestamp=response_msg.timestamp,
                channel=channel_id,
                input=message_content[:500],
                response=response_content[:500],
            ))
        except Exception:
            logger.debug("[!] Failed to record working memory for %s", agent_id)

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
# Entry point (called from socketio_events.py)
# =====================================================================

def route_mentions(message: Any, mentions: list[str], *, response_mode: str = "smarter") -> None:
    """Route @mentions from a posted message to agent response queue.

    This is the main entry point, called after a message is posted and broadcast.

    Args:
        message: The posted message object.
        mentions: List of @mentioned agent IDs.
        response_mode: "smart", "smarter" (default), or "smartest".
    """
    if not mentions or _chat is None:
        return

    # Check conversation depth on the parent
    if message.thread_id:
        depth = _get_conversation_depth(message.thread_id)
        if depth >= MAX_CONVERSATION_DEPTH:
            logger.warning("[!] Max depth reached, not routing mentions")
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
        )

    # Track depth for the triggering message
    _set_conversation_depth(message.id, message.thread_id)
