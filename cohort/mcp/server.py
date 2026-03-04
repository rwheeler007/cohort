"""Cohort MCP Server -- Claude Code tool integration.

Provides native MCP tool access to cohort channels, messages, and
checklists.  Returns compact, pre-filtered responses to minimise
token usage in iterative conversations.

Usage::

    python -m cohort.mcp.server          # stdio transport (default)
    fastmcp dev cohort/mcp/server.py     # MCP inspector UI
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from cohort.mcp.client import CohortClient

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

# Default client instance
_client = CohortClient()


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
    result = await _client.post_message(params.channel, params.sender, params.message)
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
    agents = await _client.list_agents()
    if agents is None:
        return _error_msg(service_down=True)
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
# Tool 16: cohort_get_work_queue
# =====================================================================

class GetWorkQueueInput(BaseModel):
    """Input for reading the work queue."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: Optional[str] = Field(
        None,
        description=(
            "Filter by task status: 'briefing', 'assigned', 'in_progress', "
            "'complete', or None for all."
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
    """Read the work queue with optional status filtering.

    Returns task cards showing agent, priority, status, and description.
    Status lifecycle: briefing -> assigned -> in_progress -> complete.
    """
    tasks = await _client.get_task_queue(status=params.status)
    if tasks is None:
        return _error_msg(service_down=True)
    if not tasks:
        scope = f"(status={params.status})" if params.status else ""
        return f"Work queue is empty {scope}".strip() + "."

    lines = [f"## Work Queue ({len(tasks)} tasks)"]
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
    """
    result = await _client.create_task(
        agent_id=params.agent_id,
        description=params.description,
        priority=params.priority,
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
# Tool 18: cohort_roundtable
# =====================================================================

class RoundtableInput(BaseModel):
    """Input for running a roundtable discussion."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel: str = Field(
        ..., description="Channel ID to run the roundtable in.",
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


@mcp.tool(
    name="cohort_roundtable",
    annotations={
        "title": "Roundtable Discussion",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def cohort_roundtable(params: RoundtableInput) -> str:
    """Run a roundtable discussion across multiple agents.

    Posts the prompt with @mentions to the channel, starts a roundtable
    session via the Cohort server, and returns the session details.
    Agents respond asynchronously through the server's orchestrator.
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

    # Start the roundtable session
    result = await _client.start_roundtable(
        channel=params.channel,
        agents=params.agents,
        prompt=params.prompt,
        sender=params.sender,
    )
    if result is None:
        return _error_msg(service_down=True)
    if not result.get("success") and not result.get("session_id"):
        return f"Error starting roundtable: {result.get('error', 'Unknown')}"

    session_id = result.get("session_id", "?")

    # Poll for completion (simplified -- check a few times)
    final_status = None
    for _ in range(params.timeout_per_agent // 5):
        await asyncio.sleep(5)
        status = await _client.get_roundtable_status(session_id)
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
        lines = [f"## Roundtable in #{params.channel} ({len(turns)} responses)"]
        for turn in turns:
            agent = turn.get("agent_id", "unknown")
            content = (turn.get("content") or turn.get("response") or "")[:2000]
            lines.append(f"\n### > {agent}:\n{content}")
        result_text = "\n".join(lines)
        if len(result_text) > CHARACTER_LIMIT:
            result_text = result_text[:CHARACTER_LIMIT] + "\n\n*[truncated]*"
        return result_text

    return (
        f"Roundtable started (session: {session_id}) but timed out waiting for responses. "
        f"Check #{params.channel} for agent replies."
    )


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

    agents_data = await _client.list_agents()
    if not agents_data:
        return "No agents registered."

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

    agents_data = await _client.list_agents()
    if not agents_data:
        return "No agents registered."

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

    agents_data = await _client.list_agents()
    if not agents_data:
        return "No agents registered."

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
# Entry point
# =====================================================================

if __name__ == "__main__":
    mcp.run()
