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
# Entry point
# =====================================================================

if __name__ == "__main__":
    mcp.run()
