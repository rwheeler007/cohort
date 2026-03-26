"""Cohort MCP Server -- Claude Code tool integration.

Provides native MCP tool access to cohort channels, messages, and
checklists.  Returns compact, pre-filtered responses to minimise
token usage in iterative conversations.

Supports two modes:

* **Full mode** -- connects to a running Cohort web app via HTTP
  (CohortClient).  All features including live agent routing,
  session management, briefings, and work queue.
* **Lite mode** -- file-backed storage only (LiteBackend).  Works
  standalone without the web app.  Channel CRUD, messages, agent
  profiles, checklists, and search.

Mode is auto-detected on startup by probing the Cohort server.

Usage::

    python -m cohort.mcp.server          # stdio transport (default)
    fastmcp dev cohort/mcp/server.py     # MCP inspector UI
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from cohort.mcp.client import CohortClient, DEFAULT_URL, _request

logger = logging.getLogger(__name__)

# =====================================================================
# Server
# =====================================================================

mcp = FastMCP("cohort_mcp")

CHARACTER_LIMIT = 25000

_STOP_WORDS = frozenset(
    "a an the and or but is are was were be been being have has had do does "
    "did will would shall should may might can could of in to for on with at "
    "by from as into through during before after above below between out off "
    "over under again further then once here there when where why how all "
    "each every both few more most other some such no nor not only own same "
    "so than too very that this these those it its i me my we our you your "
    "he him his she her they them their what which who whom".split()
)


def _create_backend():
    """Auto-detect whether the Cohort server is running and pick a backend.

    Returns a CohortClient (full mode) or LiteBackend (lite mode).
    """
    import httpx

    base_url = os.environ.get("COHORT_SERVER_URL", DEFAULT_URL)
    try:
        resp = httpx.get(f"{base_url}/health", timeout=2.0)
        if resp.status_code == 200:
            logger.info("[*] MCP: connected to Cohort server at %s", base_url)
            return CohortClient(base_url=base_url)
    except Exception:
        pass

    # Server not reachable -- use lite file-backed mode
    from cohort.mcp.lite_backend import LiteBackend

    data_dir = os.environ.get("COHORT_DATA_DIR", "data")
    agents_dir = os.environ.get("COHORT_AGENTS_DIR")
    logger.info("[*] MCP: lite mode (file-backed, data_dir=%s)", data_dir)
    return LiteBackend(data_dir=data_dir, agents_dir=agents_dir)


# Backend instance -- auto-detected on import
_client = _create_backend()


# =====================================================================
# Shared helpers
# =====================================================================

def _extract_keywords(texts: List[str], top_n: int = 10) -> List[str]:
    words: Counter[str] = Counter()
    for text in texts:
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())
        for token in tokens:
            if token not in _STOP_WORDS and len(token) > 2:
                words[token] += 1
    return [word for word, _ in words.most_common(top_n)]


def _error_msg(service_down: bool = False) -> str:
    if service_down:
        return "Error: Cannot connect to chat server. Is it running?"
    return "Error: Unexpected error communicating with chat server."


# =====================================================================
# Input models
# =====================================================================

class ReadChannelInput(BaseModel):
    """Input for reading messages from a channel."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel: str = Field(
        ..., description="Channel ID (e.g. 'general')",
        min_length=1, max_length=100,
    )
    limit: int = Field(
        20, description="Max messages to return (1-100). Default 20.",
        ge=1, le=100,
    )
    exclude_system: bool = Field(
        True, description="Exclude system/status messages. Default True.",
    )


class PostMessageInput(BaseModel):
    """Input for posting a message to a channel."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel: str = Field(
        ..., description="Channel ID to post to (e.g. 'general')",
        min_length=1, max_length=100,
    )
    sender: str = Field(
        ..., description="Sender name",
        min_length=1, max_length=100,
    )
    message: str = Field(
        ..., description="Message content (markdown supported)",
        min_length=1,
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional metadata (model, tokens, elapsed_seconds, etc.)",
    )


class ListChannelsInput(BaseModel):
    """Input for listing channels."""
    model_config = ConfigDict(extra="forbid")

    include_archived: bool = Field(
        False, description="Include archived channels. Default False.",
    )


class ChannelSummaryInput(BaseModel):
    """Input for getting a compact channel activity summary."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel: str = Field(
        ..., description="Channel ID (e.g. 'general')",
        min_length=1, max_length=100,
    )
    depth: int = Field(
        20, description="Number of recent messages to analyse (1-50). Default 20.",
        ge=1, le=50,
    )


class CondenseChannelInput(BaseModel):
    """Input for condensing a channel."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel: str = Field(
        ..., description="Channel ID to condense",
        min_length=1, max_length=100,
    )
    keep_last: int = Field(
        5, description="Number of recent messages to keep (default 5).",
        ge=1, le=50,
    )


class ChecklistStatus(str, Enum):
    ALL = "all"
    PENDING = "pending"
    DONE = "done"


class ChecklistPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GetChecklistInput(BaseModel):
    """Input for reading the to-do checklist."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: ChecklistStatus = Field(
        ChecklistStatus.ALL, description="Filter by status: all, pending, or done.",
    )
    assignee: Optional[str] = Field(
        None, description="Filter by assignee name.",
    )
    priority: Optional[ChecklistPriority] = Field(
        None, description="Filter by priority: high, medium, or low.",
    )


class ChecklistAction(str, Enum):
    ADD = "add"
    COMPLETE = "complete"
    REMOVE = "remove"


class UpdateChecklistInput(BaseModel):
    """Input for adding, completing, or removing a checklist item."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: ChecklistAction = Field(
        ..., description="Action: 'add', 'complete', or 'remove'.",
    )
    content: str = Field(
        ..., description="Task text (for add) or search text (for complete/remove).",
        min_length=1,
    )
    priority: Optional[ChecklistPriority] = Field(
        None, description="Priority for new tasks (add only).",
    )
    assignee: Optional[str] = Field(
        None, description="Assignee for new tasks (add only).",
    )
    category: Optional[str] = Field(
        None, description="Category for new tasks (add only).",
    )


# =====================================================================
# Tool 1: read_channel
# =====================================================================

@mcp.tool(
    name="read_channel",
    annotations={
        "title": "Read Channel Messages",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def read_channel(params: ReadChannelInput) -> str:
    """Read recent messages from a channel with compact output."""
    messages = await _client.get_messages(params.channel, limit=params.limit)
    if messages is None:
        return _error_msg(service_down=True)
    if not messages:
        return f"No messages in #{params.channel}."
    if params.exclude_system:
        messages = [
            m for m in messages
            if m.get("message_type", "chat") not in ("system", "status")
        ]
    if not messages:
        return f"No non-system messages in #{params.channel}."

    lines = [f"## #{params.channel} ({len(messages)} messages)"]
    for m in messages:
        sender = m.get("sender", "unknown")
        content = m.get("content", "")
        ts = m.get("timestamp", "")
        if "T" in ts:
            ts = ts.split("T")[1][:8]
        lines.append(f"**{sender}** ({ts}): {content}")

    result = "\n\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 2: post_message
# =====================================================================

@mcp.tool(
    name="post_message",
    annotations={
        "title": "Post Message to Channel",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def post_message(params: PostMessageInput) -> str:
    """Post a message to a channel."""
    result = await _client.post_message(params.channel, params.sender, params.message, metadata=params.metadata)
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        return f"Posted to #{params.channel} (id: {result.get('message_id', '?')})"
    return f"Error posting to #{params.channel}: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 3: list_channels
# =====================================================================

@mcp.tool(
    name="list_channels",
    annotations={
        "title": "List Channels",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def list_channels(params: ListChannelsInput) -> str:
    """List all channels with compact output."""
    channels = await _client.get_channels()
    if channels is None:
        return _error_msg(service_down=True)
    if not channels:
        return "No channels found."
    if not params.include_archived:
        channels = [c for c in channels if not c.get("is_archived", False)]

    lines = [f"## Channels ({len(channels)})", ""]
    lines.append("| Channel | Description | Members |")
    lines.append("|---------|-------------|---------|")
    for c in channels:
        cid = c.get("id", "?")
        desc = c.get("description", "")[:60]
        members = c.get("members", [])
        count = len(members) if isinstance(members, list) else 0
        lines.append(f"| #{cid} | {desc} | {count} |")

    result = "\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 3b: create_channel
# =====================================================================

class CreateChannelInput(BaseModel):
    """Input for creating a new channel."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        ..., description="Channel name/ID (e.g. 'design-review', 'proj-api')",
        min_length=1, max_length=100,
    )
    description: str = Field(
        "", description="Channel description.",
    )
    members: List[str] = Field(
        default_factory=list, description="Initial member agent IDs.",
    )
    is_private: bool = Field(
        False, description="Whether the channel is private. Default False.",
    )
    topic: str = Field(
        "", description="Channel topic.",
    )


@mcp.tool(
    name="cohort_create_channel",
    annotations={
        "title": "Create Channel",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_create_channel(params: CreateChannelInput) -> str:
    """Create a new chat channel with optional members and topic."""
    result = await _client.create_channel(
        name=params.name,
        description=params.description,
        members=params.members,
        is_private=params.is_private,
        topic=params.topic,
    )
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        ch = result.get("channel", {})
        member_count = len(ch.get("members", []))
        return (
            f"Created channel **#{params.name}**"
            + (f": {params.description}" if params.description else "")
            + (f" ({member_count} members)" if member_count else "")
        )
    return f"Error creating channel: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 4: channel_summary
# =====================================================================

@mcp.tool(
    name="channel_summary",
    annotations={
        "title": "Channel Activity Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def channel_summary(params: ChannelSummaryInput) -> str:
    """Get a compact summary of recent channel activity."""
    messages = await _client.get_messages(params.channel, limit=params.depth)
    if messages is None:
        return _error_msg(service_down=True)
    if not messages:
        return f"No messages in #{params.channel}."

    senders: Counter[str] = Counter()
    types: Counter[str] = Counter()
    contents: list[str] = []
    for m in messages:
        senders[m.get("sender", "unknown")] += 1
        types[m.get("message_type", "chat")] += 1
        contents.append(m.get("content", ""))

    timestamps = [m.get("timestamp", "") for m in messages if m.get("timestamp")]
    last = max(timestamps) if timestamps else "unknown"
    first = min(timestamps) if timestamps else "unknown"
    keywords = _extract_keywords(contents, top_n=8)

    lines = [f"## #{params.channel} Summary ({len(messages)} messages)", ""]
    participant_strs = [f"{name} ({count})" for name, count in senders.most_common()]
    lines.append(f"**Participants**: {', '.join(participant_strs)}")
    lines.append(f"**Activity**: {first} to {last}")
    if len(types) > 1 or "chat" not in types:
        lines.append(f"**Message types**: {', '.join(f'{t}: {c}' for t, c in types.most_common())}")
    if keywords:
        lines.append(f"**Topics**: {', '.join(keywords)}")
    return "\n".join(lines)


# =====================================================================
# Tool 5: condense_channel
# =====================================================================

@mcp.tool(
    name="condense_channel",
    annotations={
        "title": "Condense Channel",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def condense_channel(params: CondenseChannelInput) -> str:
    """Condense a channel: summarise old messages and archive originals."""
    result = await _client.condense_channel(params.channel, keep_last=params.keep_last)
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        archived = result.get("archived_count", 0)
        archive_ch = result.get("archive_channel", "?")
        preview = result.get("summary_preview", "")
        if archived == 0:
            return result.get("message") or f"Nothing to condense in #{params.channel}."
        return (
            f"Condensed #{params.channel}: {archived} messages archived to #{archive_ch}\n\n"
            f"**Summary preview**: {preview}"
        )
    return f"Error condensing #{params.channel}: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 6: get_checklist
# =====================================================================

@mcp.tool(
    name="get_checklist",
    annotations={
        "title": "Get Checklist",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def get_checklist(params: GetChecklistInput) -> str:
    """Read the to-do checklist with optional filtering."""
    data = await _client.read_checklist()
    if data is None:
        return "Error: Could not read checklist file."

    items = data.get("items", [])
    if not items:
        return "Checklist is empty."

    if params.status == ChecklistStatus.PENDING:
        items = [i for i in items if not i.get("checked", False)]
    elif params.status == ChecklistStatus.DONE:
        items = [i for i in items if i.get("checked", False)]
    if params.assignee:
        items = [i for i in items if (i.get("assignee") or "").lower() == params.assignee.lower()]
    if params.priority:
        items = [i for i in items if (i.get("priority") or "").lower() == params.priority.value]
    if not items:
        return "No tasks match the filters."

    lines = [f"## Checklist ({len(items)} tasks)"]
    for item in items:
        check = "[x]" if item.get("checked") else "[ ]"
        content = item.get("content", "?")
        parts = []
        if item.get("priority"):
            parts.append(item["priority"].upper())
        if item.get("assignee"):
            parts.append(f"@{item['assignee']}")
        suffix = f" ({', '.join(parts)})" if parts else ""
        lines.append(f"- {check} {content}{suffix}")
    return "\n".join(lines)


# =====================================================================
# Tool 7: update_checklist
# =====================================================================

@mcp.tool(
    name="update_checklist",
    annotations={
        "title": "Update Checklist",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def update_checklist(params: UpdateChecklistInput) -> str:
    """Add, complete, or remove a task on the to-do checklist."""
    data = await _client.read_checklist()
    if data is None:
        return "Error: Could not read checklist file."

    items = data.get("items", [])

    if params.action == ChecklistAction.ADD:
        new_item = {
            "id": str(uuid.uuid4())[:8],
            "content": params.content,
            "checked": False,
            "priority": params.priority.value if params.priority else "medium",
            "category": params.category or "",
            "assignee": params.assignee,
            "due_date": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "mcp",
            "completed_at": None,
        }
        items.append(new_item)
        data["items"] = items
        if await _client.write_checklist(data):
            return f"Added: {params.content} (id: {new_item['id']})"
        return "Error: Failed to write checklist."

    search = params.content.lower()
    match_idx = None
    for idx, item in enumerate(items):
        if search in item.get("content", "").lower():
            match_idx = idx
            break

    if match_idx is None:
        return f"No task matching '{params.content}' found."

    matched = items[match_idx]

    if params.action == ChecklistAction.COMPLETE:
        matched["checked"] = True
        matched["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["items"] = items
        if await _client.write_checklist(data):
            return f"Completed: {matched['content']}"
        return "Error: Failed to write checklist."

    if params.action == ChecklistAction.REMOVE:
        removed = items.pop(match_idx)
        data["items"] = items
        if await _client.write_checklist(data):
            return f"Removed: {removed['content']}"
        return "Error: Failed to write checklist."

    return "Error: Unknown action."


# =====================================================================
# Tool 8: cohort_list_agents
# =====================================================================

class ListAgentsInput(BaseModel):
    """Input for listing agents."""
    model_config = ConfigDict(extra="forbid")

    include_hidden: bool = Field(
        False, description="Include hidden/inactive agents. Default False.",
    )


@mcp.tool(
    name="cohort_list_agents",
    annotations={
        "title": "List Agents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_list_agents(params: ListAgentsInput) -> str:
    """List all agents with ID, role, status, and skills."""
    raw = await _client.list_agents()
    if raw is None:
        return _error_msg(service_down=True)
    # /api/agents returns {"agents": [...], ...} wrapper
    agents = raw.get("agents", raw) if isinstance(raw, dict) else raw
    if not agents:
        return "No agents registered."

    if not params.include_hidden:
        agents = [a for a in agents if a.get("status", "active") != "hidden"]

    lines = [f"## Agents ({len(agents)})", ""]
    lines.append("| ID | Name | Role | Status | Skills |")
    lines.append("|-----|------|------|--------|--------|")
    for a in agents:
        aid = a.get("agent_id", "?")
        name = a.get("name", aid)
        role = (a.get("role") or "")[:30]
        status = a.get("status", "active")
        edu = a.get("education", {})
        skills = edu.get("skill_levels", {}) if isinstance(edu, dict) else {}
        skill_str = ", ".join(f"{k}:{v}" for k, v in list(skills.items())[:3]) if skills else "-"
        lines.append(f"| {aid} | {name} | {role} | {status} | {skill_str} |")

    result = "\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 9: cohort_get_agent
# =====================================================================

class GetAgentInput(BaseModel):
    """Input for getting a single agent's config."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ..., description="Agent ID or alias (e.g. 'python_developer', 'pd')",
        min_length=1, max_length=100,
    )


@mcp.tool(
    name="cohort_get_agent",
    annotations={
        "title": "Get Agent Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_agent(params: GetAgentInput) -> str:
    """Get full agent configuration by ID or alias."""
    data = await _client.get_agent(params.agent_id)
    if data is None:
        return f"Agent '{params.agent_id}' not found or server unavailable."
    if "error" in data:
        return f"Error: {data['error']}"

    lines = [f"## {data.get('name', params.agent_id)}"]
    lines.append(f"**ID**: {data.get('agent_id', '?')}")
    lines.append(f"**Role**: {data.get('role', '-')}")
    lines.append(f"**Status**: {data.get('status', 'active')}")
    if data.get("primary_task"):
        lines.append(f"**Task**: {data['primary_task']}")
    if data.get("capabilities"):
        lines.append(f"**Capabilities**: {', '.join(data['capabilities'])}")
    if data.get("domain_expertise"):
        lines.append(f"**Expertise**: {', '.join(data['domain_expertise'])}")
    if data.get("triggers"):
        lines.append(f"**Triggers**: {', '.join(data['triggers'])}")
    edu = data.get("education", {})
    if isinstance(edu, dict) and edu.get("skill_levels"):
        skills = ", ".join(f"{k}: {v}" for k, v in edu["skill_levels"].items())
        lines.append(f"**Skills**: {skills}")

    return "\n".join(lines)


# =====================================================================
# Tool 10: cohort_get_agent_memory
# =====================================================================

class GetAgentMemoryInput(BaseModel):
    """Input for getting an agent's memory."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ..., description="Agent ID (e.g. 'python_developer')",
        min_length=1, max_length=100,
    )
    section: Optional[str] = Field(
        None, description="Optional section: 'working_memory', 'learned_facts', or None for all.",
    )


@mcp.tool(
    name="cohort_get_agent_memory",
    annotations={
        "title": "Get Agent Memory",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_agent_memory(params: GetAgentMemoryInput) -> str:
    """Get an agent's memory (working memory, learned facts)."""
    data = await _client.get_agent_memory(params.agent_id)
    if data is None:
        return f"Memory for '{params.agent_id}' not found or server unavailable."
    if "error" in data:
        return f"Error: {data['error']}"

    lines = [f"## Memory: {params.agent_id}"]

    working = data.get("working_memory", [])
    facts = data.get("learned_facts", [])
    lines.append(f"**Working memory**: {len(working)} entries | **Learned facts**: {len(facts)}")

    if params.section in (None, "learned_facts") and facts:
        lines.append("")
        lines.append("### Learned Facts")
        for f in facts[-20:]:
            conf = f.get("confidence", "medium")
            lines.append(f"- [{conf}] {f.get('fact', '?')} (from: {f.get('learned_from', '?')})")

    if params.section in (None, "working_memory") and working:
        lines.append("")
        lines.append("### Recent Working Memory")
        for w in working[-10:]:
            ts = w.get("timestamp", "")
            ch = w.get("channel", "?")
            inp = (w.get("input") or "")[:80]
            lines.append(f"- [{ts}] #{ch}: {inp}")

    result = "\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 11: cohort_create_agent
# =====================================================================

class CreateAgentInput(BaseModel):
    """Input for creating a new agent."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Agent display name (e.g. 'Python Developer')", min_length=1)
    role: str = Field(..., description="Agent role (e.g. 'Senior Python Engineer')", min_length=1)
    primary_task: str = Field("", description="Primary task description")
    agent_type: str = Field("specialist", description="Type: specialist, orchestrator, supervisor, infrastructure, utility")
    capabilities: List[str] = Field(default_factory=list, description="List of capabilities")
    domain_expertise: List[str] = Field(default_factory=list, description="List of domain expertise areas")
    personality: str = Field("", description="Personality description")
    triggers: List[str] = Field(default_factory=list, description="Trigger words for routing")
    avatar: str = Field("", description="Two-letter avatar code")
    color: str = Field("#95A5A6", description="Hex color code")
    group: str = Field("Agents", description="Agent group/category")


@mcp.tool(
    name="cohort_create_agent",
    annotations={
        "title": "Create Agent",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_create_agent(params: CreateAgentInput) -> str:
    """Create a new agent with directory structure, config, prompt, and memory."""
    result = await _client.create_agent(params.model_dump())
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        aid = result.get("agent_id", "?")
        return f"Created agent **{params.name}** (id: `{aid}`)"
    return f"Error creating agent: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 12: cohort_clean_memory
# =====================================================================

class CleanMemoryInput(BaseModel):
    """Input for cleaning an agent's working memory."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ..., description="Agent ID (e.g. 'python_developer')",
        min_length=1, max_length=100,
    )
    keep_last: int = Field(
        10, description="Number of recent working memory entries to keep (default 10).",
        ge=1, le=100,
    )
    dry_run: bool = Field(
        False, description="If true, report what would be cleaned without actually cleaning.",
    )


@mcp.tool(
    name="cohort_clean_memory",
    annotations={
        "title": "Clean Agent Memory",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_clean_memory(params: CleanMemoryInput) -> str:
    """Trim an agent's working memory, archiving old entries."""
    result = await _client.clean_agent_memory(
        params.agent_id, keep_last=params.keep_last, dry_run=params.dry_run,
    )
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        removed = result.get("working_memory_removed", 0)
        kept = result.get("working_memory_kept", 0)
        prefix = "[DRY RUN] " if params.dry_run else ""
        if removed == 0:
            return f"{prefix}No trimming needed for {params.agent_id} ({kept} entries)."
        return (
            f"{prefix}Cleaned {params.agent_id}: removed {removed}, kept {kept} entries."
        )
    return f"Error: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 13: cohort_add_fact
# =====================================================================

class AddFactInput(BaseModel):
    """Input for adding a learned fact to an agent's memory."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ..., description="Agent ID (e.g. 'python_developer')",
        min_length=1, max_length=100,
    )
    fact: str = Field(
        ..., description="The fact or knowledge to record",
        min_length=1,
    )
    learned_from: str = Field(
        "mcp", description="Source of the knowledge (e.g. 'teacher', 'session', 'mcp')",
    )
    confidence: str = Field(
        "medium", description="Confidence level: high, medium, or low",
    )


@mcp.tool(
    name="cohort_add_fact",
    annotations={
        "title": "Add Learned Fact",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_add_fact(params: AddFactInput) -> str:
    """Add a learned fact to an agent's memory."""
    result = await _client.add_agent_fact(
        params.agent_id,
        {
            "fact": params.fact,
            "learned_from": params.learned_from,
            "confidence": params.confidence,
        },
    )
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        return f"Added fact to {params.agent_id}: \"{params.fact}\" [{params.confidence}]"
    return f"Error: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 14: cohort_search_messages
# =====================================================================

class SearchMessagesInput(BaseModel):
    """Input for searching messages across channels."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ..., description="Search string (case-insensitive substring match).",
        min_length=1, max_length=500,
    )
    channel: Optional[str] = Field(
        None, description="Optional channel ID to restrict search. None = all channels.",
        max_length=100,
    )


@mcp.tool(
    name="cohort_search_messages",
    annotations={
        "title": "Search Messages",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_search_messages(params: SearchMessagesInput) -> str:
    """Search messages across channels by keyword."""
    matches = await _client.search_messages(
        params.query, channel=params.channel, limit=50,
    )
    if matches is None:
        return _error_msg(service_down=True)
    if not matches:
        scope = f"#{params.channel}" if params.channel else "all channels"
        return f"No messages matching '{params.query}' in {scope}."

    lines = [f"## Search: '{params.query}' ({len(matches)} results)"]
    for m in matches:
        sender = m.get("sender", "unknown")
        content = (m.get("content") or "")[:200]
        ts = m.get("timestamp", "")
        if "T" in ts:
            ts = ts.split("T")[1][:8]
        ch = m.get("_channel") or m.get("channel_id", "?")
        lines.append(f"**{sender}** in #{ch} ({ts}): {content}")

    result = "\n\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 15: cohort_get_mentions
# =====================================================================

class GetMentionsInput(BaseModel):
    """Input for getting @mentions for an agent."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ..., description="Agent ID to look up mentions for (e.g. 'python_developer').",
        min_length=1, max_length=100,
    )
    limit: int = Field(
        20, description="Max mentions to return (1-100). Default 20.",
        ge=1, le=100,
    )


@mcp.tool(
    name="cohort_get_mentions",
    annotations={
        "title": "Get Agent Mentions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_mentions(params: GetMentionsInput) -> str:
    """Get messages where an agent was @mentioned."""
    matches = await _client.get_mentions(params.agent_id, limit=params.limit)
    if matches is None:
        return _error_msg(service_down=True)
    if not matches:
        return f"No @mentions found for {params.agent_id}."

    lines = [f"## @{params.agent_id} mentions ({len(matches)} found)"]
    for m in matches:
        sender = m.get("sender", "unknown")
        content = (m.get("content") or "")[:200]
        ts = m.get("timestamp", "")
        if "T" in ts:
            ts = ts.split("T")[1][:8]
        ch = m.get("_channel", "?")
        lines.append(f"**{sender}** in #{ch} ({ts}): {content}")

    result = "\n\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 16: cohort_get_work_queue (sequential execution queue)
# =====================================================================

class GetWorkQueueInput(BaseModel):
    """Input for reading the sequential work queue."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: Optional[str] = Field(
        None,
        description=(
            "Filter by status: 'queued', 'active', 'completed', 'failed', "
            "'cancelled', or None for all."
        ),
    )


@mcp.tool(
    name="cohort_get_work_queue",
    annotations={
        "title": "Get Work Queue",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_work_queue(params: GetWorkQueueInput) -> str:
    """Read the sequential work queue.

    Items execute one at a time (FIFO with priority ordering).
    Only one item can be 'active' at a time.
    Status lifecycle: queued -> active -> completed/failed/cancelled.
    """
    items = await _client.get_work_queue(status=params.status)
    if items is None:
        return _error_msg(service_down=True)
    if not items:
        scope = f"(status={params.status})" if params.status else ""
        return f"Work queue is empty {scope}".strip() + "."

    active_count = sum(1 for i in items if i.get("status") == "active")
    queued_count = sum(1 for i in items if i.get("status") == "queued")
    lines = [f"## Work Queue ({len(items)} items, {active_count} active, {queued_count} queued)"]

    for pos, item in enumerate(items, 1):
        item_id = item.get("id", "?")
        status = item.get("status", "?")
        priority = (item.get("priority") or "medium").upper()
        desc = (item.get("description") or "")[:120]
        agent = item.get("agent_id")
        agent_str = f" @{agent}" if agent else ""
        deps = item.get("depends_on", [])
        dep_str = f" (depends: {', '.join(deps)})" if deps else ""

        marker = " [RUNNING]" if status == "active" else ""
        lines.append(
            f"- **{item_id}** [{status}]{marker}{agent_str} ({priority}) "
            f"-- {desc}{dep_str}"
        )

    result = "\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 16b: cohort_get_tasks (parallel agent tasks)
# =====================================================================

class GetTasksInput(BaseModel):
    """Input for reading the parallel task queue."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: Optional[str] = Field(
        None,
        description=(
            "Filter by task status: 'briefing', 'assigned', 'in_progress', "
            "'complete', or None for all."
        ),
    )


@mcp.tool(
    name="cohort_get_tasks",
    annotations={
        "title": "Get Agent Tasks",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_tasks(params: GetTasksInput) -> str:
    """Read agent tasks (parallel, lifecycle-driven).

    Unlike the work queue (sequential), tasks run concurrently.
    Status lifecycle: briefing -> assigned -> in_progress -> complete.
    """
    tasks = await _client.get_task_queue(status=params.status)
    if tasks is None:
        return _error_msg(service_down=True)
    if not tasks:
        scope = f"(status={params.status})" if params.status else ""
        return f"No agent tasks {scope}".strip() + "."

    lines = [f"## Agent Tasks ({len(tasks)} tasks)"]
    for t in tasks:
        task_id = t.get("task_id", "?")
        agent = t.get("agent_id", "unassigned")
        priority = (t.get("priority") or "medium").upper()
        status = t.get("status", "?")
        desc = (t.get("description") or "")[:120]
        created = t.get("created_at", "")
        if "T" in created:
            created = created.split("T")[0]

        review = t.get("review")
        review_str = ""
        if review:
            verdict = review.get("verdict", "?")
            review_str = f" | review: {verdict}"

        lines.append(
            f"- **{task_id}** [{status}] @{agent} ({priority}) "
            f"-- {desc} (created: {created}){review_str}"
        )

    result = "\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 16c: cohort_enqueue_item
# =====================================================================

class EnqueueItemInput(BaseModel):
    """Input for adding an item to the sequential work queue."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: str = Field(
        ..., description="What the work item should accomplish.",
        min_length=5, max_length=2000,
    )
    requester: str = Field(
        "claude_code", description="Who is submitting the item.",
        max_length=100,
    )
    priority: str = Field(
        "medium", description="Priority: 'critical', 'high', 'medium', or 'low'.",
    )
    agent_id: Optional[str] = Field(
        None, description="Optional agent to assign the item to.",
        max_length=100,
    )
    depends_on: Optional[List[str]] = Field(
        None, description="Optional list of work queue item IDs that must complete first.",
    )


@mcp.tool(
    name="cohort_enqueue_item",
    annotations={
        "title": "Enqueue Work Item",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_enqueue_item(params: EnqueueItemInput) -> str:
    """Add an item to the sequential work queue.

    Items are executed one at a time in priority + FIFO order.
    Use depends_on to block an item until other items complete.
    """
    result = await _client.enqueue_work_item(
        description=params.description,
        requester=params.requester,
        priority=params.priority,
        agent_id=params.agent_id,
        depends_on=params.depends_on,
    )
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        item = result.get("item", {})
        item_id = item.get("id", "?")
        deps = item.get("depends_on", [])
        dep_str = f", depends_on: {', '.join(deps)}" if deps else ""
        return (
            f"Enqueued: {item_id} (priority: {params.priority}, "
            f"status: queued{dep_str})"
        )
    return f"Error: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 16d: cohort_claim_next
# =====================================================================

@mcp.tool(
    name="cohort_claim_next",
    annotations={
        "title": "Claim Next Work Item",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_claim_next() -> str:
    """Claim the next item from the sequential work queue.

    Atomically transitions the highest-priority queued item to 'active'.
    Only one item can be active at a time -- returns an error if an
    item is already running.
    """
    result = await _client.claim_work_item()
    if result is None:
        return _error_msg(service_down=True)
    if "error" in result:
        active = result.get("active_item", {})
        active_id = active.get("id", "?")
        return (
            f"Cannot claim: {result['error']}. "
            f"Active item: {active_id} -- complete or cancel it first."
        )
    item = result.get("item")
    if item is None:
        return "Work queue is empty -- nothing to claim."
    item_id = item.get("id", "?")
    desc = (item.get("description") or "")[:100]
    priority = (item.get("priority") or "medium").upper()
    return f"Claimed: {item_id} ({priority}) -- {desc}"


# =====================================================================
# Tool 17: cohort_get_outputs
# =====================================================================

@mcp.tool(
    name="cohort_get_outputs",
    annotations={
        "title": "Get Outputs for Review",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_outputs() -> str:
    """Get completed tasks awaiting human review.

    Returns task outputs (diffs, content, summaries) that need
    approval or rejection before being finalized.
    """
    outputs = await _client.get_outputs_for_review()
    if outputs is None:
        return _error_msg(service_down=True)
    if not outputs:
        return "No outputs pending review."

    lines = [f"## Outputs Pending Review ({len(outputs)} tasks)"]
    for t in outputs:
        task_id = t.get("task_id", "?")
        agent = t.get("agent_id", "?")
        desc = (t.get("description") or "")[:100]
        output = t.get("output") or {}
        # Show a preview of the output
        preview = (
            output.get("summary")
            or output.get("content", "")[:200]
            or output.get("diff", "")[:200]
            or "(no output content)"
        )
        lines.append(f"\n### {task_id} (@{agent})")
        lines.append(f"**Task**: {desc}")
        lines.append(f"**Output preview**: {preview}")

    result = "\n".join(lines)
    if len(result) > CHARACTER_LIMIT:
        result = result[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return result


# =====================================================================
# Tool 17b: cohort_assign_task
# =====================================================================

class AssignTaskInput(BaseModel):
    """Input for assigning a task to an agent."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ..., description="Agent ID to assign the task to (e.g. 'python_developer').",
        min_length=1, max_length=100,
    )
    description: str = Field(
        ..., description="What the task should accomplish.",
        min_length=1,
    )
    priority: str = Field(
        "medium", description="Priority: 'high', 'medium', or 'low'.",
    )
    trigger_type: str = Field(
        "mcp",
        description=(
            "How the task was triggered: 'manual', 'scheduled', 'event', or 'mcp'. "
            "Defaults to 'mcp' for MCP-originated tasks."
        ),
    )
    trigger_source: str = Field(
        "mcp_tool",
        description="Source identifier for the trigger (e.g. 'user:rwhee', 'sched_abc').",
    )
    tool: Optional[str] = Field(
        None,
        description=(
            "The tool or action the task should use (e.g. 'generate_report', "
            "'fetch_rss', 'run_script'). Set during briefing if omitted."
        ),
    )
    success_criteria: Optional[str] = Field(
        None,
        description=(
            "How we know the task succeeded (e.g. 'RSS data fetched and stored'). "
            "Set during briefing if omitted."
        ),
    )


@mcp.tool(
    name="cohort_assign_task",
    annotations={
        "title": "Assign Task",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_assign_task(params: AssignTaskInput) -> str:
    """Assign a task to an agent via the Work Queue.

    Creates a task in 'briefing' status. The agent will receive the
    task and begin a conversational briefing to confirm scope before
    starting work. Task lifecycle: briefing -> assigned -> in_progress -> complete.

    Supports the trigger-action-outcome triad: specify trigger_type,
    trigger_source, tool, and success_criteria for full traceability.
    """
    result = await _client.create_task(
        agent_id=params.agent_id,
        description=params.description,
        priority=params.priority,
        trigger_type=params.trigger_type,
        trigger_source=params.trigger_source,
        tool=params.tool,
        success_criteria=params.success_criteria,
    )
    if result is None:
        return _error_msg(service_down=True)
    if result.get("success"):
        task = result.get("task", {})
        task_id = task.get("task_id", "?")
        return (
            f"Task assigned to @{params.agent_id} (id: {task_id}, "
            f"priority: {params.priority}, status: briefing)"
        )
    return f"Error assigning task: {result.get('error', 'Unknown')}"


# =====================================================================
# Tool 18: cohort_discussion (was cohort_roundtable)
# =====================================================================

class DiscussionInput(BaseModel):
    """Input for running a multi-agent discussion."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel: str = Field(
        ..., description="Channel ID to run the discussion in.",
        min_length=1, max_length=100,
    )
    agents: List[str] = Field(
        ..., description="List of agent IDs to solicit responses from.",
        min_length=1,
    )
    prompt: str = Field(
        ..., description="The question or topic to pose to all agents.",
        min_length=1,
    )
    sender: str = Field(
        "claude_code", description="Who is asking the question (default 'claude_code').",
        max_length=100,
    )
    timeout_per_agent: int = Field(
        90, description="Max seconds to wait for each agent's response (default 90, max 300).",
        ge=10, le=300,
    )


# Deprecated alias
RoundtableInput = DiscussionInput


@mcp.tool(
    name="cohort_discussion",
    annotations={
        "title": "Multi-Agent Discussion",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_discussion(params: DiscussionInput) -> str:
    """Run a discussion across multiple agents.

    Posts the prompt with @mentions to the channel, starts a session
    via the Cohort server, and returns the session details.
    Agents respond sequentially through the server's orchestrator.
    """
    import asyncio

    # Post the prompt with @mentions
    mentions = " ".join(f"@{a}" for a in params.agents)
    full_message = f"{mentions}\n\n{params.prompt}"
    post_result = await _client.post_message(
        params.channel, params.sender, full_message,
    )
    if post_result is None:
        return _error_msg(service_down=True)

    # Start the session
    result = await _client.start_session(
        channel=params.channel,
        agents=params.agents,
        prompt=params.prompt,
        sender=params.sender,
    )
    if result is None:
        return _error_msg(service_down=True)
    if not result.get("success") and not result.get("session_id"):
        return f"Error starting session: {result.get('error', 'Unknown')}"

    session_id = result.get("session_id", "?")

    # Poll for completion (simplified -- check a few times)
    final_status = None
    for _ in range(params.timeout_per_agent // 5):
        await asyncio.sleep(5)
        status = await _client.get_session_status(session_id)
        if status is None:
            continue
        state = status.get("status") or status.get("state", "")
        if state in ("completed", "ended", "done"):
            final_status = status
            break
        # Check if all agents have responded
        turns = status.get("turns", [])
        responded = {t.get("agent_id") for t in turns if t.get("agent_id")}
        if responded >= set(params.agents):
            final_status = status
            break

    # Format results
    if final_status:
        turns = final_status.get("turns", [])
        lines = [f"## Discussion in #{params.channel} ({len(turns)} responses)"]
        for turn in turns:
            agent = turn.get("agent_id", "unknown")
            content = (turn.get("content") or turn.get("response") or "")[:2000]
            lines.append(f"\n### > {agent}:\n{content}")
        result_text = "\n".join(lines)
        if len(result_text) > CHARACTER_LIMIT:
            result_text = result_text[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
        return result_text

    return (
        f"Discussion started (session: {session_id}) but timed out waiting for responses. "
        f"Check #{params.channel} for agent replies."
    )


# Deprecated alias
cohort_roundtable = cohort_discussion


# =====================================================================
# Tool 19: cohort_adopt_persona
# =====================================================================

class AdoptPersonaInput(BaseModel):
    """Input for loading an agent persona."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ..., description="Agent ID to adopt (e.g. 'python_developer', 'marketing_agent').",
        min_length=1, max_length=100,
    )
    full: bool = Field(
        False, description="If true, return full agent prompt instead of lightweight persona.",
    )


@mcp.tool(
    name="cohort_adopt_persona",
    annotations={
        "title": "Adopt Agent Persona",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_adopt_persona(params: AdoptPersonaInput) -> str:
    """Load an agent persona for use in the current conversation.

    Returns the persona text that should be used as a system-level
    identity for the rest of the conversation. Light mode returns
    the compact persona (~500 tokens). Full mode returns the complete
    agent prompt.
    """
    from cohort.personas import load_persona

    if params.full:
        # Full mode: get the complete agent prompt from the server
        prompt = await _client.get_agent_persona(params.agent_id)
        if prompt:
            return (
                f"## Adopted: {params.agent_id} (full mode)\n\n"
                f"Use the following as your identity for this conversation:\n\n"
                f"---\n{prompt}\n---"
            )

    # Light mode: load from local persona files
    persona = load_persona(params.agent_id)
    if persona:
        return (
            f"## Adopted: {params.agent_id}\n\n"
            f"Use the following as your identity for this conversation:\n\n"
            f"---\n{persona}\n---"
        )

    # Fallback: try server for config-based identity
    agent_data = await _client.get_agent(params.agent_id)
    if agent_data and not agent_data.get("error"):
        name = agent_data.get("name", params.agent_id)
        role = agent_data.get("role", "Agent")
        personality = agent_data.get("personality", "")
        caps = agent_data.get("capabilities", [])
        return (
            f"## Adopted: {params.agent_id}\n\n"
            f"Use the following as your identity:\n\n"
            f"---\n"
            f"# {name}\n"
            f"**Role**: {role}\n"
            + (f"**Personality**: {personality}\n" if personality else "")
            + (f"**Capabilities**: {', '.join(caps)}\n" if caps else "")
            + "---"
        )

    return (
        f"Agent '{params.agent_id}' not found. "
        f"Use `cohort_list_agents` to see available agents."
    )


# =====================================================================
# Capability Routing Tools
# =====================================================================

class RouteTaskInput(BaseModel):
    """Input for dynamic task routing."""
    model_config = ConfigDict(extra="forbid")
    task_description: str = Field(
        description="Description of the task to route to the best agent."
    )
    prefer_type: Optional[str] = Field(
        default=None,
        description="Prefer agents of this type (e.g. 'specialist', 'orchestrator').",
    )


class FindAgentsInput(BaseModel):
    """Input for finding agents by topic."""
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(
        description="Topic or task description to match agents against."
    )
    max_results: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)


class PartnershipGraphInput(BaseModel):
    """Input for partnership graph queries."""
    model_config = ConfigDict(extra="forbid")
    agent_id: Optional[str] = Field(
        default=None,
        description="Get partnerships for a specific agent. Omit for full graph.",
    )


@mcp.tool()
async def cohort_route_task(params: RouteTaskInput) -> str:
    """Route a task to the best-qualified agent based on capabilities.

    Scores all active agents against the task description using their
    declared triggers, capabilities, domain expertise, and skill levels.
    Returns the best match with score breakdown.
    """
    from cohort.capability_router import find_agents_for_topic

    raw = await _client.list_agents()
    if not raw:
        return "No agents registered."
    agents_data = raw.get("agents", raw) if isinstance(raw, dict) else raw

    # We need AgentConfig objects -- fetch full configs from server
    from cohort.agent import AgentConfig
    configs: list[AgentConfig] = []
    for agent_info in agents_data:
        agent_id = agent_info.get("agent_id", agent_info.get("id", ""))
        if not agent_id:
            continue
        detail = await _client.get_agent(agent_id)
        if detail and not detail.get("error"):
            configs.append(AgentConfig.from_dict(detail))

    if not configs:
        return "No agent configs available for routing."

    results = find_agents_for_topic(
        configs,
        params.task_description,
        min_score=0.1,
        max_results=5,
        prefer_type=params.prefer_type,
    )

    if not results:
        return f"No agents match: '{params.task_description}'"

    lines = [f"## Task Routing: {params.task_description[:80]}", ""]
    for i, (agent, score) in enumerate(results, 1):
        marker = " [BEST]" if i == 1 else ""
        lines.append(
            f"{i}. **@{agent.agent_id}** ({agent.name}) -- "
            f"score: {score:.2f}{marker}"
        )
        lines.append(f"   Role: {agent.role}")
        if agent.partnerships:
            partner_ids = list(agent.partnerships.keys())
            lines.append(f"   Partnerships: {', '.join(partner_ids)}")

    # Check consultations for the top pick
    from cohort.capability_router import find_required_consultations
    top_agent = results[0][0]
    available = {c.agent_id: c for c in configs}
    from cohort.capability_router import _extract_keywords
    task_kw = _extract_keywords(params.task_description)
    consults = find_required_consultations(top_agent, task_kw, available)
    if consults:
        lines.append("")
        lines.append("### Required Consultations")
        for c in consults:
            lines.append(
                f"- **@{c['partner_id']}**: {c['reason']}"
            )
            lines.append(f"  Protocol: {c['protocol']}")

    return "\n".join(lines)


@mcp.tool()
async def cohort_find_agents(params: FindAgentsInput) -> str:
    """Find agents qualified for a topic, ranked by capability match.

    Unlike route_task (which picks the best one), this returns all
    agents above the score threshold for roundtable composition.
    """
    from cohort.capability_router import find_agents_for_topic
    from cohort.agent import AgentConfig

    raw = await _client.list_agents()
    if not raw:
        return "No agents registered."
    agents_data = raw.get("agents", raw) if isinstance(raw, dict) else raw

    configs: list[AgentConfig] = []
    for agent_info in agents_data:
        agent_id = agent_info.get("agent_id", agent_info.get("id", ""))
        if not agent_id:
            continue
        detail = await _client.get_agent(agent_id)
        if detail and not detail.get("error"):
            configs.append(AgentConfig.from_dict(detail))

    results = find_agents_for_topic(
        configs,
        params.topic,
        min_score=params.min_score,
        max_results=params.max_results,
    )

    if not results:
        return f"No agents match topic: '{params.topic}'"

    lines = [f"## Agents for: {params.topic[:80]}", ""]
    lines.append("| Agent | Score | Type | Group |")
    lines.append("|-------|-------|------|-------|")
    for agent, score in results:
        lines.append(
            f"| @{agent.agent_id} | {score:.2f} | {agent.agent_type} | {agent.group} |"
        )

    return "\n".join(lines)


@mcp.tool()
async def cohort_partnership_graph(params: PartnershipGraphInput) -> str:
    """View the partnership graph -- who consults whom and why.

    Shows consultation protocols between agents. Only includes
    partnerships where both agents exist in the current deployment.
    """
    from cohort.capability_router import build_partnership_graph, get_partnerships
    from cohort.agent import AgentConfig

    raw = await _client.list_agents()
    if not raw:
        return "No agents registered."
    agents_data = raw.get("agents", raw) if isinstance(raw, dict) else raw

    configs: list[AgentConfig] = []
    for agent_info in agents_data:
        agent_id = agent_info.get("agent_id", agent_info.get("id", ""))
        if not agent_id:
            continue
        detail = await _client.get_agent(agent_id)
        if detail and not detail.get("error"):
            configs.append(AgentConfig.from_dict(detail))

    if params.agent_id:
        # Single agent's partnerships
        target = next((c for c in configs if c.agent_id == params.agent_id), None)
        if not target:
            return f"Agent '{params.agent_id}' not found."
        partnerships = get_partnerships(target)
        if not partnerships:
            return f"@{params.agent_id} has no declared partnerships."

        available_ids = {c.agent_id for c in configs}
        lines = [f"## Partnerships: @{params.agent_id}", ""]
        for partner_id, details in partnerships.items():
            exists = "[active]" if partner_id in available_ids else "[not deployed]"
            lines.append(f"- **@{partner_id}** {exists}")
            lines.append(f"  Relationship: {details.get('relationship', 'N/A')}")
            lines.append(f"  Protocol: {details.get('protocol', 'N/A')}")
        return "\n".join(lines)

    # Full graph
    graph = build_partnership_graph(configs)
    if not graph:
        return "No partnerships declared in any agent configs."

    lines = ["## Partnership Graph", ""]
    for agent_id, edges in sorted(graph.items()):
        lines.append(f"### @{agent_id}")
        for edge in edges:
            lines.append(f"- -> @{edge['partner_id']}: {edge['relationship']}")
        lines.append("")

    return "\n".join(lines)


# =====================================================================
# Tool: cohort_compiled_discussion (was cohort_compiled_roundtable)
# =====================================================================

class CompiledDiscussionInput(BaseModel):
    """Input for compiled (single-call) multi-agent discussion."""
    model_config = ConfigDict(extra="forbid")

    agents: List[str] = Field(
        description="Agent IDs to participate (3-8 agents).",
        min_length=1,
    )
    topic: str = Field(
        description="Discussion topic or question.",
        min_length=1,
    )
    context: str = Field(
        default="",
        description="Optional background context or prior discussion summary.",
    )
    rounds: int = Field(
        default=2, ge=1, le=3,
        description="Number of discussion rounds (1-3). Default 2.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Ollama model to use. Auto-selects if omitted.",
    )
    temperature: float = Field(
        default=0.30, ge=0.0, le=1.0,
        description="Generation temperature.",
    )
    channel: Optional[str] = Field(
        default=None,
        description="Channel to post results to. If omitted, results are returned inline only.",
    )
    sender: str = Field(
        default="claude_code",
        description="Sender identity for channel messages.",
    )


# Deprecated alias
CompiledRoundtableInput = CompiledDiscussionInput


@mcp.tool(
    name="cohort_compiled_discussion",
    annotations={
        "title": "Compiled Discussion (Single-Call)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def cohort_compiled_discussion(params: CompiledDiscussionInput) -> str:
    """Run a compiled discussion: all agent personas in a single LLM call.

    ~90% token reduction vs separate per-agent calls. Best for 3-8 agent
    planning/review discussions. Uses local Ollama inference.

    If a channel is specified, individual agent responses are posted as
    separate messages to the channel.
    """
    from cohort.compiled_roundtable import run_compiled_roundtable

    result = run_compiled_roundtable(
        agents=params.agents,
        topic=params.topic,
        context=params.context,
        rounds=params.rounds,
        model=params.model,
        temperature=params.temperature,
    )

    if result.error:
        return f"Compiled discussion failed: {result.error}"

    # Format output
    lines = [
        f"## Compiled Discussion ({len(result.agent_responses)}/{len(params.agents)} agents)",
    ]

    if result.metadata:
        model_used = result.metadata.get("model", "unknown")
        latency = result.metadata.get("latency_ms", 0)
        lines.append(f"*Model: {model_used}, {latency}ms*\n")

    for agent_id, response in result.agent_responses.items():
        lines.append(f"### > {agent_id}:")
        lines.append(response)
        lines.append("")

    if result.synthesis:
        lines.append("### Synthesis:")
        lines.append(result.synthesis)
        lines.append("")

    # Post to channel if specified
    if params.channel and result.agent_responses:
        for agent_id, response in result.agent_responses.items():
            await _client.post_message(
                params.channel, agent_id, response,
            )
        if result.synthesis:
            await _client.post_message(
                params.channel, params.sender,
                f"**Discussion Synthesis:**\n{result.synthesis}",
            )
        lines.append(f"\n*Results posted to #{params.channel}*")

    # Warn about missing agents
    missing = set(params.agents) - set(result.agent_responses.keys())
    if missing:
        lines.append(f"\n*Missing responses from: {', '.join(sorted(missing))}*")

    output = "\n".join(lines)
    if len(output) > CHARACTER_LIMIT:
        output = output[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return output


# Deprecated alias
cohort_compiled_roundtable = cohort_compiled_discussion


# =====================================================================
# Meeting control tools
# =====================================================================

class MeetingStartInput(BaseModel):
    """Start a meeting session on a channel."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    channel: str = Field(..., description="Channel ID.", min_length=1, max_length=100)
    topic: str = Field(..., description="Discussion topic.", min_length=1)
    agents: Optional[List[str]] = Field(None, description="Agent IDs. Auto-selects if omitted.")
    max_turns: int = Field(20, ge=1, le=100, description="Max turns (default 20).")


@mcp.tool(
    name="cohort_meeting_start",
    annotations={"title": "Start Meeting Session", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def cohort_meeting_start(params: MeetingStartInput) -> str:
    """Start a meeting session with stakeholder gating on a channel."""
    result = await _client.start_session(
        channel=params.channel,
        agents=params.agents or [],
        prompt=params.topic,
    )
    if result is None:
        return _error_msg(service_down=True)
    if not result.get("success"):
        return f"Error: {result.get('error', 'Unknown')}"
    session = result.get("session", {})
    sid = session.get("session_id", "?")
    agents = session.get("initial_agents", [])
    return f"Session `{sid}` started in #{params.channel}. Participants: {', '.join(agents)}"


class MeetingStopInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID to end.", min_length=1)


@mcp.tool(
    name="cohort_meeting_stop",
    annotations={"title": "End Meeting Session", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_stop(params: MeetingStopInput) -> str:
    """End a meeting session and generate summary."""
    result = await _client.end_session(params.session_id)
    if result is None:
        return _error_msg(service_down=True)
    if not result.get("success"):
        return f"Error: {result.get('error', 'Unknown')}"
    summary = result.get("summary", {})
    return f"Session ended. {summary.get('total_turns', 0)} turns, {len(summary.get('participants', {}).get('contributed', []))} contributors."


class MeetingStatusInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)


@mcp.tool(
    name="cohort_meeting_status",
    annotations={"title": "Meeting Session Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_status(params: MeetingStatusInput) -> str:
    """Get the current status of a meeting session."""
    result = await _client.get_session_status(params.session_id)
    if result is None:
        return _error_msg(service_down=True)
    status = result.get("status", result)
    if not status.get("session_id"):
        return f"Error: {result.get('error', 'Session not found')}"
    p = status.get("participants", {})
    lines = [
        f"**{status.get('topic', '?')}** ({status.get('state', '?')})",
        f"Turn {status.get('current_turn', 0)}/{status.get('max_turns', 20)}",
        f"Active: {', '.join(p.get('active', []))}",
    ]
    if p.get("silent"):
        lines.append(f"Silent: {', '.join(p['silent'])}")
    if p.get("dormant"):
        lines.append(f"Dormant: {', '.join(p['dormant'])}")
    return "\n".join(lines)


class MeetingPauseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)


@mcp.tool(
    name="cohort_meeting_pause",
    annotations={"title": "Pause Meeting", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_pause(params: MeetingPauseInput) -> str:
    """Pause a meeting session."""
    result = await _client.pause_session(params.session_id)
    if result is None:
        return _error_msg(service_down=True)
    return "Session paused." if result.get("success") else f"Error: {result.get('error', 'Unknown')}"


class MeetingResumeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)


@mcp.tool(
    name="cohort_meeting_resume",
    annotations={"title": "Resume Meeting", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_resume(params: MeetingResumeInput) -> str:
    """Resume a paused meeting session."""
    result = await _client.resume_session(params.session_id)
    if result is None:
        return _error_msg(service_down=True)
    return "Session resumed." if result.get("success") else f"Error: {result.get('error', 'Unknown')}"


class MeetingPromoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)
    agent_id: str = Field(..., description="Agent to promote to ACTIVE.", min_length=1)


@mcp.tool(
    name="cohort_meeting_promote",
    annotations={"title": "Promote Agent", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_promote(params: MeetingPromoteInput) -> str:
    """Promote an agent to ACTIVE stakeholder status in a meeting."""
    result = await _client.update_participant_status(params.session_id, params.agent_id, "active")
    if result is None:
        return _error_msg(service_down=True)
    return f"{params.agent_id} -> ACTIVE" if result.get("success") else f"Error: {result.get('error', 'Unknown')}"


class MeetingDemoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)
    agent_id: str = Field(..., description="Agent to demote.", min_length=1)
    status: str = Field("silent", description="Target status: silent, observer, or dormant.")


@mcp.tool(
    name="cohort_meeting_demote",
    annotations={"title": "Demote Agent", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_demote(params: MeetingDemoteInput) -> str:
    """Demote an agent's stakeholder status in a meeting."""
    result = await _client.update_participant_status(params.session_id, params.agent_id, params.status)
    if result is None:
        return _error_msg(service_down=True)
    return f"{params.agent_id} -> {params.status.upper()}" if result.get("success") else f"Error: {result.get('error', 'Unknown')}"


class MeetingAddInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)
    agent_id: str = Field(..., description="Agent to add.", min_length=1)


@mcp.tool(
    name="cohort_meeting_add_participant",
    annotations={"title": "Add Meeting Participant", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_add_participant(params: MeetingAddInput) -> str:
    """Add a participant to an active meeting session."""
    result = await _client.add_participant(params.session_id, params.agent_id)
    if result is None:
        return _error_msg(service_down=True)
    return f"{params.agent_id} added." if result.get("success") else f"Error: {result.get('error', 'Unknown')}"


class MeetingRemoveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)
    agent_id: str = Field(..., description="Agent to remove.", min_length=1)


@mcp.tool(
    name="cohort_meeting_remove_participant",
    annotations={"title": "Remove Meeting Participant", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_remove_participant(params: MeetingRemoveInput) -> str:
    """Remove a participant from a meeting session."""
    result = await _client.remove_participant(params.session_id, params.agent_id)
    if result is None:
        return _error_msg(service_down=True)
    return f"{params.agent_id} removed." if result.get("success") else f"Error: {result.get('error', 'Unknown')}"


class MeetingNextSpeakerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)


@mcp.tool(
    name="cohort_meeting_next_speaker",
    annotations={"title": "Next Speaker Recommendation", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_next_speaker(params: MeetingNextSpeakerInput) -> str:
    """Get the recommended next speaker with relevance scores."""
    result = await _client.get_next_speaker(params.session_id)
    if result is None:
        return _error_msg(service_down=True)
    if not result.get("success"):
        return f"No speaker available: {result.get('error', 'threshold not met or session ended')}"
    rec = result.get("recommendation", {})
    lines = [
        f"**Recommended:** {rec.get('recommended_speaker', '?')} (score: {rec.get('relevance_score', 0):.3f})",
        f"Phase: {rec.get('phase', '?')}",
        f"Reason: {rec.get('reason', '?')}",
    ]
    alts = rec.get("alternatives", [])
    if alts:
        lines.append(f"Alternatives: {', '.join(alts)}")
    return "\n".join(lines)


class MeetingScoreInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)
    agent_id: str = Field(..., description="Agent to score.", min_length=1)


@mcp.tool(
    name="cohort_meeting_score",
    annotations={"title": "Agent Relevance Score", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_score(params: MeetingScoreInput) -> str:
    """Get the full 5-dimension composite relevance breakdown for an agent."""
    result = await _client.score_agent(params.session_id, params.agent_id)
    if result is None:
        return _error_msg(service_down=True)
    if not result.get("success"):
        return f"Error: {result.get('error', 'Unknown')}"
    score = result.get("score", {})
    dims = score.get("dimensions", {})
    lines = [
        f"**{score.get('agent_id', '?')}** -- {score.get('status', '?')} / Phase: {score.get('phase', '?')}",
        f"Composite: {score.get('composite_total', 0):.3f}",
    ]
    for dim, val in dims.items():
        lines.append(f"  {dim}: {float(val):.3f}")
    return "\n".join(lines)


class MeetingPhaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    channel: str = Field(..., description="Channel ID.", min_length=1, max_length=100)


@mcp.tool(
    name="cohort_meeting_phase",
    annotations={"title": "Detect Discussion Phase", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_phase(params: MeetingPhaseInput) -> str:
    """Detect the current discussion phase (DISCOVER/PLAN/EXECUTE/VALIDATE)."""
    result = await _client.detect_phase(params.channel)
    if result is None:
        return _error_msg(service_down=True)
    phase = result.get("phase", "?")
    evidence = result.get("evidence", [])
    lines = [f"Phase: **{phase}**"]
    if evidence:
        for e in evidence[:3]:
            kw = ", ".join(e.get("keywords", [])[:5])
            lines.append(f"  {e.get('sender', '?')}: {kw}")
    return "\n".join(lines)


class MeetingExtendInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    session_id: str = Field(..., description="Session ID.", min_length=1)
    turns: int = Field(10, ge=1, le=100, description="Turns to add (default 10).")


@mcp.tool(
    name="cohort_meeting_extend",
    annotations={"title": "Extend Meeting", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_extend(params: MeetingExtendInput) -> str:
    """Add more turns to a meeting session."""
    result = await _client.extend_session(params.session_id, turns=params.turns)
    if result is None:
        return _error_msg(service_down=True)
    return f"Extended by {params.turns} turns." if result.get("success") else f"Error: {result.get('error', 'Unknown')}"


class MeetingEnableInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    channel: str = Field(..., description="Channel ID.", min_length=1, max_length=100)
    agents: List[str] = Field(..., description="Agent IDs to gate.", min_length=1)
    topic: str = Field("", description="Optional topic for keyword scoring.")


@mcp.tool(
    name="cohort_meeting_enable",
    annotations={"title": "Enable Meeting Mode", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_enable(params: MeetingEnableInput) -> str:
    """Enable stakeholder gating on a channel without a full session."""
    result = await _client.enable_meeting_mode(params.channel, params.agents, topic=params.topic)
    if result is None:
        return _error_msg(service_down=True)
    if not result.get("success"):
        return f"Error: {result.get('error', 'Unknown')}"
    return f"Meeting mode enabled on #{params.channel}. Participants: {', '.join(params.agents)}"


class MeetingDisableInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    channel: str = Field(..., description="Channel ID.", min_length=1, max_length=100)


@mcp.tool(
    name="cohort_meeting_disable",
    annotations={"title": "Disable Meeting Mode", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def cohort_meeting_disable(params: MeetingDisableInput) -> str:
    """Disable stakeholder gating on a channel."""
    result = await _client.disable_meeting_mode(params.channel)
    if result is None:
        return _error_msg(service_down=True)
    was = result.get("was_active", False)
    return f"Meeting mode disabled on #{params.channel}." if was else f"Meeting mode was already off on #{params.channel}."


# =====================================================================
# Executive briefing
# =====================================================================


class GenerateBriefingInput(BaseModel):
    """Input for generating an executive briefing."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    hours: int = Field(
        24,
        description="Hours of activity to cover (default 24).",
        ge=1,
        le=168,
    )
    post_to_channel: bool = Field(
        True,
        description="Post briefing to a channel. Default True.",
    )
    channel: str = Field(
        "daily-digest",
        description="Channel to post to (default 'daily-digest').",
        min_length=1,
        max_length=100,
    )


@mcp.tool(
    name="cohort_generate_briefing",
    annotations={
        "title": "Generate Executive Briefing",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_generate_briefing(params: GenerateBriefingInput) -> str:
    """Generate an executive briefing for this Cohort deployment.

    Gathers stats from the work queue, chat channels, team status, and
    discussion sessions.  Returns a formatted summary and optionally
    posts it to a channel.  Each deployment produces a unique briefing
    from its own activity data.
    """
    result = await _client.generate_briefing(
        hours=params.hours,
        post_to_channel=params.post_to_channel,
        channel=params.channel,
    )
    if result is None:
        return _error_msg(service_down=True)

    if not result.get("success"):
        return f"Error generating briefing: {result.get('error', 'Unknown')}"

    report = result.get("report", {})
    sections = report.get("sections", [])

    lines = [
        f"## Executive Briefing ({report.get('generated_at', 'unknown')[:19]})",
        f"Period: {report.get('period_start', '?')[:10]} to "
        f"{report.get('period_end', '?')[:10]}",
        "",
    ]
    for section in sections:
        lines.append(f"### {section.get('title', 'Untitled')}")
        lines.append(section.get("content", "(no content)"))
        lines.append("")

    posted = (
        f"Posted to #{params.channel}."
        if params.post_to_channel
        else "Not posted."
    )
    lines.append(f"---\nBriefing ID: {report.get('id', '?')}. {posted}")

    text = "\n".join(lines)
    if len(text) > CHARACTER_LIMIT:
        text = text[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return text


# =====================================================================
# Tool: internal_web_search
# =====================================================================

class InternalWebSearchInput(BaseModel):
    """Input for searching the web via local DuckDuckGo scraping (no API key)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ..., description="Search query string",
        min_length=1, max_length=500,
    )
    max_results: int = Field(
        10, description="Maximum number of results to return (1-25)",
        ge=1, le=25,
    )
    region: str = Field(
        "us-en", description="Region code for search results (e.g. 'us-en', 'uk-en')",
        max_length=10,
    )
    time_range: Optional[str] = Field(
        None, description="Time filter: 'd' (day), 'w' (week), 'm' (month), 'y' (year)",
    )


@mcp.tool(
    name="internal_web_search",
    annotations={
        "title": "Internal Web Search",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def internal_web_search(params: InternalWebSearchInput) -> str:
    """Search the web locally using DuckDuckGo (no API cost).

    Returns search results with titles, URLs, and snippets.  Uses the ddgs
    library to scrape DuckDuckGo results directly -- no API key, no external
    service, completely free.  Pair with internal_web_fetch to read full pages.
    """
    import asyncio
    import logging

    logger = logging.getLogger(__name__)

    try:
        from ddgs import DDGS
    except ImportError:
        return (
            "Error: ddgs library not installed. "
            "Run: pip install ddgs"
        )

    # --- Run search in thread pool (ddgs is synchronous) ---
    def _do_search() -> list[dict]:
        kwargs: dict = {
            "query": params.query,
            "max_results": params.max_results,
            "region": params.region,
        }
        if params.time_range and params.time_range in ("d", "w", "m", "y"):
            kwargs["timelimit"] = params.time_range
        with DDGS() as ddgs:
            return list(ddgs.text(**kwargs))

    try:
        results = await asyncio.get_event_loop().run_in_executor(None, _do_search)
    except Exception as e:
        logger.exception("[X] InternalWebSearch failed for query: %s", params.query)
        return f"Search failed: {e}"

    if not results:
        return f"No results found for: {params.query}"

    # --- Format results ---
    lines = [f"Search results for: {params.query}", f"Results: {len(results)}", ""]
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("href", r.get("url", ""))
        snippet = r.get("body", r.get("content", ""))
        lines.append(f"{i}. {title}")
        lines.append(f"   URL: {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    output = "\n".join(lines)
    if len(output) > CHARACTER_LIMIT:
        output = output[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return output


# =====================================================================
# Tool: internal_web_fetch
# =====================================================================

class InternalWebFetchInput(BaseModel):
    """Input for fetching a web page via the local Playwright renderer."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(
        ..., description="HTTPS URL to fetch and render locally",
        min_length=8, max_length=2048,
    )
    extract_text: bool = Field(
        True, description="Extract readable text from the page (default True). "
        "If False, only returns metadata and screenshot paths.",
    )


@mcp.tool(
    name="internal_web_fetch",
    annotations={
        "title": "Internal Web Fetch",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def internal_web_fetch(params: InternalWebFetchInput) -> str:
    """Fetch and render a web page locally using Playwright (no API cost).

    Validates the URL (HTTPS-only, no private IPs), renders the page in a
    headless browser, dismisses cookie banners, triggers lazy loading, and
    returns extracted text content.  Screenshots are cached locally for
    later processing by the document pipeline.
    """
    import asyncio
    import hashlib
    import importlib
    import logging
    from pathlib import Path
    from urllib.parse import urlparse

    logger = logging.getLogger(__name__)

    # --- Lazy-import WebAdapter ---
    try:
        web_adapter = importlib.import_module("src.ingestion.web_adapter")
    except ImportError:
        web_adapter = None

    if web_adapter is None:
        return "Error: WebAdapter not available. Install the web_adapter package or configure PYTHONPATH."

    # --- Validate URL ---
    if not web_adapter.validate_url(params.url):
        return f"Error: URL validation failed for {params.url}. Must be HTTPS and not resolve to a private IP."

    # --- Generate stable source_id from URL ---
    url_hash = hashlib.sha256(params.url.encode()).hexdigest()[:12]
    hostname = urlparse(params.url).hostname or "unknown"
    source_id = f"web_{hostname}_{url_hash}"

    # --- Determine cache directory ---
    cohort_root = Path(__file__).resolve().parents[2]  # mcp/server.py -> cohort/ -> cohort project root
    cache_dir = cohort_root / "data" / "services" / "web_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # --- Render page ---
    try:
        paths = await web_adapter.process_url(
            params.url, source_id, output_dir=str(cache_dir)
        )
    except Exception as e:
        logger.exception("[X] WebAdapter render failed for %s", params.url)
        return f"Error rendering page: {e}"

    num_pages = len(paths)
    result_lines = [
        f"Fetched: {params.url}",
        f"Pages rendered: {num_pages}",
        f"Cache: {cache_dir / source_id}",
    ]

    # --- Extract text if requested ---
    if params.extract_text and paths:
        try:
            # Use Playwright to extract DOM text (faster than Surya for text-only)
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(params.url, timeout=30_000, wait_until="domcontentloaded")
                    await web_adapter.dismiss_cookie_consent(page)

                    # Extract readable text via DOM
                    text = await page.evaluate("""
                        () => {
                            // Remove scripts, styles, nav, footer
                            for (const el of document.querySelectorAll('script, style, nav, footer, header, [role="navigation"]')) {
                                el.remove();
                            }
                            return document.body.innerText;
                        }
                    """)

                    # Truncate to reasonable size for agent consumption
                    if text:
                        text = text.strip()
                        if len(text) > 15000:
                            text = text[:15000] + "\n\n[...truncated at 15000 chars]"
                        result_lines.append("")
                        result_lines.append("--- Page Content ---")
                        result_lines.append(text)
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning("[!] Text extraction failed for %s: %s", params.url, e)
            result_lines.append(f"\nText extraction failed: {e}")
            result_lines.append("Screenshots are still available in the cache directory.")

    output = "\n".join(result_lines)
    if len(output) > CHARACTER_LIMIT:
        output = output[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
    return output


# =====================================================================
# Tool: browser automation (dispatcher pattern)
# =====================================================================

# Lazy-loaded browser backend singleton
_browser_backend = None


def _get_browser_backend():
    """Lazy-init the browser backend."""
    global _browser_backend
    if _browser_backend is None:
        from cohort.mcp.browser_backend import get_browser_backend
        _browser_backend = get_browser_backend()
    return _browser_backend


# --- Action catalog for the dispatcher ---

_BROWSER_ACTION_CATALOG = """
BROWSE (read-only):
  navigate(url, wait_until="domcontentloaded") - Go to a URL
  navigate_back() - Go back to previous page
  snapshot() - Get accessibility tree of current page
  screenshot(full_page=false) - Take screenshot, returns file path
  get_text() - Extract visible text content from page
  console_messages() - Get browser console output
  network_requests() - List network requests since page load
  tabs_list() - List open tabs
  wait_for(text="", selector="", timeout_ms=5000) - Wait for content
  verify_text_visible(text) - Check if text is visible
  verify_element_visible(selector) - Check if element is visible
  close_page() - Close browser context for this agent

INTERACT (requires browser_interact permission):
  click(selector) - Click an element
  fill(selector, value) - Fill a form field
  type_text(selector, text) - Type text character by character
  press_key(key) - Press keyboard key (e.g. "Enter", "Tab")
  select_option(selector, value) - Select dropdown option
  hover(selector) - Hover over element
  drag(source, target) - Drag element to target
  file_upload(selector, paths) - Upload files
  handle_dialog(action, prompt_text="") - Handle alert/confirm/prompt
  mouse_click_xy(x, y, button="left") - Click at coordinates
  mouse_move_xy(x, y) - Move mouse to coordinates
  mouse_drag_xy(start_x, start_y, end_x, end_y) - Drag between points
  mouse_wheel(delta_x, delta_y) - Scroll
  resize(width, height) - Resize viewport
  tab_new(url="") - Open new tab
  tab_select(tab_id) - Switch to tab
  tab_close(tab_id) - Close tab

ADVANCED (requires browser_advanced permission):
  evaluate(expression) - Run JavaScript on page
  cookie_list() - List cookies
  cookie_set(name, value, domain="", path="/") - Set cookie
  cookie_delete(name) - Delete cookie
  cookie_clear() - Clear all cookies
  storage_get(key, storage_type="local") - Get localStorage/sessionStorage
  storage_set(key, value, storage_type="local") - Set storage item
  storage_delete(key, storage_type="local") - Delete storage item
  storage_clear(storage_type="local") - Clear storage
  storage_list(storage_type="local") - List storage items
  route_set(url_pattern, response_body, status=200) - Mock network
  route_list() - List active mocks
  route_remove(url_pattern) - Remove mock
  pdf_save(path) - Save page as PDF
"""


class BrowserActionInput(BaseModel):
    """Input for the browser action dispatcher."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = Field(
        ...,
        description=(
            "Browser action to perform. See tool description for full catalog. "
            "Examples: 'navigate', 'click', 'snapshot', 'fill', 'evaluate'."
        ),
        min_length=1,
        max_length=50,
    )
    agent_id: str = Field(
        default="default",
        description="Agent identity for browser context isolation. Each agent_id gets its own cookies, storage, and tabs.",
        max_length=100,
    )
    url: str = Field(
        default="",
        description="URL for navigate/tab_new actions.",
        max_length=4096,
    )
    selector: str = Field(
        default="",
        description="CSS selector or text selector for click/fill/hover/etc.",
        max_length=500,
    )
    value: str = Field(
        default="",
        description="Value for fill/select_option/cookie_set/storage_set.",
        max_length=50000,
    )
    text: str = Field(
        default="",
        description="Text for type_text/wait_for/verify_text_visible.",
        max_length=50000,
    )
    key: str = Field(
        default="",
        description="Key for press_key/storage_get/storage_set/storage_delete/cookie_delete.",
        max_length=200,
    )
    expression: str = Field(
        default="",
        description="JavaScript expression for evaluate action.",
        max_length=50000,
    )
    x: float = Field(default=0, description="X coordinate for mouse actions.")
    y: float = Field(default=0, description="Y coordinate for mouse actions.")
    end_x: float = Field(default=0, description="End X for mouse_drag_xy.")
    end_y: float = Field(default=0, description="End Y for mouse_drag_xy.")
    width: int = Field(default=0, description="Width for resize.", ge=0, le=7680)
    height: int = Field(default=0, description="Height for resize.", ge=0, le=4320)
    full_page: bool = Field(default=False, description="Full page screenshot.")
    button: str = Field(default="left", description="Mouse button: left/right/middle.")
    tab_id: str = Field(default="", description="Tab identifier for tab actions.", max_length=50)
    timeout_ms: int = Field(default=5000, description="Timeout in ms for wait_for.", ge=0, le=60000)
    path: str = Field(default="", description="File path for pdf_save/screenshot.", max_length=500)
    paths: List[str] = Field(default_factory=list, description="File paths for file_upload.")
    wait_until: str = Field(default="domcontentloaded", description="Wait strategy for navigate.", max_length=30)
    storage_type: str = Field(default="local", description="'local' or 'session' for storage actions.", max_length=10)
    source: str = Field(default="", description="Source selector for drag.", max_length=500)
    target: str = Field(default="", description="Target selector for drag.", max_length=500)
    dialog_action: str = Field(default="accept", description="'accept' or 'dismiss' for handle_dialog.", max_length=10)
    prompt_text: str = Field(default="", description="Text for dialog prompt input.", max_length=1000)
    url_pattern: str = Field(default="", description="URL pattern for route_set/route_remove.", max_length=500)
    response_body: str = Field(default="", description="Response body for route_set.", max_length=50000)
    status: int = Field(default=200, description="HTTP status for route_set.", ge=100, le=599)
    content_type: str = Field(default="text/plain", description="Content type for route_set.", max_length=100)
    name: str = Field(default="", description="Cookie name for cookie_set.", max_length=200)
    domain: str = Field(default="", description="Cookie domain.", max_length=200)


@mcp.tool(
    name="browser_action",
    annotations={
        "title": "Browser Automation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def browser_action(params: BrowserActionInput) -> str:
    """Automate a headless browser via Playwright. Each agent_id gets isolated browser state.

    Actions are grouped by permission tier:
    - BROWSE: navigate, snapshot, screenshot, get_text, console_messages, etc.
    - INTERACT: click, fill, type_text, press_key, hover, drag, mouse_*, resize, etc.
    - ADVANCED: evaluate (JS), cookies, storage, network mocking, pdf_save.

    Use action="help" to see the full action catalog.
    """
    from cohort.mcp.browser_backend import check_browser_permission

    action = params.action.lower().strip()

    # Help action -- return catalog
    if action == "help":
        return _BROWSER_ACTION_CATALOG

    backend = _get_browser_backend()

    # Check availability
    if not await backend.is_available():
        try:
            await backend.start()
        except Exception as exc:
            return f"Error: Browser backend unavailable: {exc}"

    # Dispatch to backend method
    aid = params.agent_id

    try:
        if action == "navigate":
            result = await backend.navigate(aid, params.url, wait_until=params.wait_until)
        elif action == "navigate_back":
            result = await backend.navigate_back(aid)
        elif action == "close_page":
            result = await backend.close_page(aid)
        elif action == "snapshot":
            result = await backend.snapshot(aid)
        elif action == "screenshot":
            result = await backend.screenshot(aid, full_page=params.full_page)
        elif action == "get_text":
            result = await backend.get_text(aid)
        elif action == "console_messages":
            result = await backend.console_messages(aid)
        elif action == "network_requests":
            result = await backend.network_requests(aid)
        elif action == "click":
            result = await backend.click(aid, params.selector)
        elif action == "fill":
            result = await backend.fill(aid, params.selector, params.value)
        elif action == "type_text":
            result = await backend.type_text(aid, params.selector, params.text)
        elif action == "press_key":
            result = await backend.press_key(aid, params.key)
        elif action == "select_option":
            result = await backend.select_option(aid, params.selector, params.value)
        elif action == "hover":
            result = await backend.hover(aid, params.selector)
        elif action == "drag":
            result = await backend.drag(aid, params.source, params.target)
        elif action == "file_upload":
            result = await backend.file_upload(aid, params.selector, params.paths)
        elif action == "handle_dialog":
            result = await backend.handle_dialog(aid, params.dialog_action, prompt_text=params.prompt_text)
        elif action == "mouse_click_xy":
            result = await backend.mouse_click_xy(aid, params.x, params.y, button=params.button)
        elif action == "mouse_move_xy":
            result = await backend.mouse_move_xy(aid, params.x, params.y)
        elif action == "mouse_drag_xy":
            result = await backend.mouse_drag_xy(aid, params.x, params.y, params.end_x, params.end_y)
        elif action == "mouse_wheel":
            result = await backend.mouse_wheel(aid, params.x, params.y)
        elif action == "resize":
            result = await backend.resize(aid, params.width, params.height)
        elif action == "evaluate":
            result = await backend.evaluate(aid, params.expression)
        elif action == "cookie_list":
            result = await backend.cookie_list(aid)
        elif action == "cookie_set":
            kwargs: dict = {}
            if params.domain:
                kwargs["domain"] = params.domain
            result = await backend.cookie_set(aid, params.name, params.value, **kwargs)
        elif action == "cookie_delete":
            result = await backend.cookie_delete(aid, params.name)
        elif action == "cookie_clear":
            result = await backend.cookie_clear(aid)
        elif action == "storage_get":
            result = await backend.storage_get(aid, params.key, storage_type=params.storage_type)
        elif action == "storage_set":
            result = await backend.storage_set(aid, params.key, params.value, storage_type=params.storage_type)
        elif action == "storage_delete":
            result = await backend.storage_delete(aid, params.key, storage_type=params.storage_type)
        elif action == "storage_clear":
            result = await backend.storage_clear(aid, storage_type=params.storage_type)
        elif action == "storage_list":
            result = await backend.storage_list(aid, storage_type=params.storage_type)
        elif action == "route_set":
            result = await backend.route_set(
                aid, params.url_pattern, params.response_body,
                status=params.status, content_type=params.content_type,
            )
        elif action == "route_list":
            result = await backend.route_list(aid)
        elif action == "route_remove":
            result = await backend.route_remove(aid, params.url_pattern)
        elif action == "tabs_list":
            result = await backend.tabs_list(aid)
        elif action == "tab_new":
            result = await backend.tab_new(aid, url=params.url)
        elif action == "tab_select":
            result = await backend.tab_select(aid, params.tab_id)
        elif action == "tab_close":
            result = await backend.tab_close(aid, params.tab_id)
        elif action == "wait_for":
            result = await backend.wait_for(aid, text=params.text, selector=params.selector, timeout_ms=params.timeout_ms)
        elif action == "pdf_save":
            result = await backend.pdf_save(aid, params.path)
        elif action == "verify_text_visible":
            result = await backend.verify_text_visible(aid, params.text)
        elif action == "verify_element_visible":
            result = await backend.verify_element_visible(aid, params.selector)
        else:
            return f"Error: Unknown browser action '{action}'. Use action='help' for catalog."

        output = result.to_str()
        if len(output) > CHARACTER_LIMIT:
            output = output[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
        return output

    except Exception as exc:
        return f"Error: Browser action '{action}' failed: {exc}"


class BrowserStatusInput(BaseModel):
    """Input for checking browser backend status."""
    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="browser_status",
    annotations={
        "title": "Browser Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def browser_status(params: BrowserStatusInput) -> str:
    """Check browser automation backend availability and active sessions."""
    lines = ["Browser Backend Status:"]

    # Check Playwright availability
    try:
        import playwright  # noqa: F401
        lines.append("  Playwright: installed")
    except ImportError:
        lines.append("  Playwright: NOT installed")
        return "\n".join(lines)

    backend = _get_browser_backend()
    available = await backend.is_available()
    lines.append(f"  Backend: {'running' if available else 'not started (starts on first use)'}")
    lines.append(f"  Type: PlaywrightDirectBackend")
    lines.append(f"  Max contexts: {backend._max_contexts}")
    lines.append(f"  Allow local network: {backend._allow_local}")

    if backend._agents:
        lines.append(f"  Active sessions: {len(backend._agents)}")
        for aid, state in backend._agents.items():
            tab_count = sum(1 for p in state.pages.values() if not p.is_closed())
            lines.append(f"    - {aid}: {tab_count} tab(s)")
    else:
        lines.append("  Active sessions: 0")

    return "\n".join(lines)


# =====================================================================
# Approval Pipeline Tools
# =====================================================================


class SubmitForReviewInput(BaseModel):
    """Input for submitting a task or work item for multi-stakeholder review."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    item_type: str = Field(
        ..., description="Type: 'task' or 'work_item'.",
    )
    item_id: str = Field(
        ..., description="The task_id or work queue item_id to submit for review.",
        min_length=1, max_length=100,
    )


@mcp.tool(
    name="cohort_submit_for_review",
    annotations={
        "title": "Submit For Review",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_submit_for_review(params: SubmitForReviewInput) -> str:
    """Submit a completed task or active work item for multi-stakeholder review."""
    result = await _client.submit_for_review(params.item_type, params.item_id)
    if result is None:
        return _error_msg(service_down=True)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Submitted {params.item_type} `{params.item_id}` for review."


class GetPendingReviewsInput(BaseModel):
    """Input for listing items awaiting review."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: Optional[str] = Field(
        "pending", description="Filter by approval status. Default: 'pending'.",
    )
    item_type: Optional[str] = Field(
        None, description="Filter by item type: 'task' or 'work_item'. Default: all.",
    )


@mcp.tool(
    name="cohort_get_pending_reviews",
    annotations={
        "title": "Get Pending Reviews",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_pending_reviews(params: GetPendingReviewsInput) -> str:
    """List approval requests awaiting review."""
    result = await _client.list_approvals(
        status=params.status, item_type=params.item_type,
    )
    if result is None:
        return _error_msg(service_down=True)

    approvals = result.get("approvals", [])
    if not approvals:
        return "No pending reviews."

    lines = [f"## Pending Reviews ({len(approvals)})"]
    for a in approvals:
        risk = a.get("risk_level", "?")
        desc = (a.get("description") or "")[:100]
        item_id = a.get("item_id", "?")
        lines.append(
            f"- **{a.get('id', '?')}** [{risk}] {desc} "
            f"(item: `{item_id}`, requester: {a.get('requester', '?')})"
        )
    return "\n".join(lines)


class SubmitReviewInput(BaseModel):
    """Input for submitting a review verdict from an agent."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    approval_id: str = Field(
        ..., description="The approval request ID to resolve.",
        min_length=1, max_length=100,
    )
    action: str = Field(
        ..., description="Verdict: 'approve' or 'deny'.",
    )
    resolved_by: str = Field(
        ..., description="Who is submitting this review (agent_id or 'human').",
        min_length=1, max_length=100,
    )
    notes: str = Field(
        "", description="Optional review notes.",
        max_length=2000,
    )


@mcp.tool(
    name="cohort_submit_review",
    annotations={
        "title": "Submit Review Verdict",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_submit_review(params: SubmitReviewInput) -> str:
    """Submit an approve/deny verdict for a pending approval request."""
    result = await _client.resolve_approval(
        params.approval_id, params.action,
        resolved_by=params.resolved_by, notes=params.notes,
    )
    if result is None:
        return _error_msg(service_down=True)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Approval `{params.approval_id}` {result.get('status', 'resolved')}."


class SetDeliverablesInput(BaseModel):
    """Input for setting acceptance criteria on a task or work item."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    item_type: str = Field(
        ..., description="Type: 'task' or 'work_item'.",
    )
    item_id: str = Field(
        ..., description="The task_id or work queue item_id.",
        min_length=1, max_length=100,
    )
    deliverables: List[dict[str, Any]] = Field(
        ..., description=(
            "List of deliverable dicts. Each must have 'id' (e.g. 'D1') and "
            "'description'. Optional: 'category' (functional/quality/security/testing)."
        ),
    )
    append: bool = Field(
        False, description="If true, append to existing deliverables instead of replacing.",
    )


@mcp.tool(
    name="cohort_set_deliverables",
    annotations={
        "title": "Set Deliverables",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_set_deliverables(params: SetDeliverablesInput) -> str:
    """Set or append acceptance criteria (deliverables) on a task or work item."""
    # This operates via the HTTP API which stores deliverables on the item
    if params.item_type == "task":
        url = f"/api/tasks/{params.item_id}"
        result = await _request(
            "PATCH",
            f"{_client.base_url}{url}",
            json_body={"status": "in_progress", "deliverables": params.deliverables},
        )
    else:
        url = f"/api/work-queue/{params.item_id}"
        result = await _request(
            "PATCH",
            f"{_client.base_url}{url}",
            json_body={"deliverables": params.deliverables},
        )

    if result is None:
        return _error_msg(service_down=True)
    if "error" in result:
        return f"Error: {result['error']}"

    count = len(params.deliverables)
    mode = "appended" if params.append else "set"
    return f"{mode.title()} {count} deliverable(s) on {params.item_type} `{params.item_id}`."


class RequeueItemInput(BaseModel):
    """Input for requeuing a rejected item with feedback."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    item_type: str = Field(
        ..., description="Type: 'task' or 'work_item'.",
    )
    item_id: str = Field(
        ..., description="The task_id or work queue item_id to requeue.",
        min_length=1, max_length=100,
    )
    feedback: str = Field(
        "", description="Feedback for the requeued item (why it was rejected, what to fix).",
        max_length=2000,
    )


@mcp.tool(
    name="cohort_requeue_item",
    annotations={
        "title": "Requeue Item",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_requeue_item(params: RequeueItemInput) -> str:
    """Requeue a rejected/failed task or work item with feedback."""
    result = await _client.requeue_item(
        params.item_type, params.item_id, feedback=params.feedback,
    )
    if result is None:
        return _error_msg(service_down=True)
    if "error" in result:
        return f"Error: {result['error']}"

    new_id = result.get("task", {}).get("task_id") or result.get("item", {}).get("id", "?")
    return f"Requeued {params.item_type} `{params.item_id}` -> new item `{new_id}`."


class GetApprovalStatusInput(BaseModel):
    """Input for checking approval status."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    item_type: Optional[str] = Field(
        None, description="Filter by item type: 'task' or 'work_item'.",
    )


@mcp.tool(
    name="cohort_get_approval_status",
    annotations={
        "title": "Get Approval Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cohort_get_approval_status(params: GetApprovalStatusInput) -> str:
    """Get an overview of approval pipeline status."""
    result = await _client.list_approvals(item_type=params.item_type)
    if result is None:
        return _error_msg(service_down=True)

    approvals = result.get("approvals", [])
    pending_count = result.get("pending_count", 0)

    if not approvals:
        return "No approval requests found."

    # Group by status
    by_status: dict[str, int] = {}
    for a in approvals:
        s = a.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    lines = [f"## Approval Pipeline Status"]
    lines.append(f"Pending: **{pending_count}** | Total: {len(approvals)}")
    for status, count in sorted(by_status.items()):
        lines.append(f"- {status}: {count}")

    return "\n".join(lines)


# =====================================================================
# Entry point
# =====================================================================

if __name__ == "__main__":
    mcp.run()
