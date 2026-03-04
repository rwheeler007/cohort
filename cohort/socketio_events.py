"""Socket.IO event layer for Cohort UI.

Provides real-time WebSocket communication between the Cohort backend
and the browser-based dashboard.  Uses python-socketio's AsyncServer
with ASGI mode to integrate alongside Starlette routes.

Events (server -> client) -- Dashboard:
    cohort:team_update      Agent cards (status, skills, session info)
    cohort:task_assigned     New task routed to an agent
    cohort:task_progress     In-flight task progress update
    cohort:task_complete     Task finished
    cohort:output_ready      Code diff / test results available
    cohort:review_submitted  Human review verdict
    cohort:status_change     Agent or session status change
    cohort:error             Error notification

Events (server -> client) -- Chat:
    channels_list            List of channels
    channel_messages         Messages for a channel
    new_message              A new message was posted

Events (client -> server) -- Dashboard:
    join                     Client connects and requests initial state
    request_team_update      Client requests fresh team snapshot
    submit_review            Human submits review verdict
    assign_task              Human assigns a task to an agent

Events (client -> server) -- Chat:
    get_channels             Client requests channel list
    join_channel             Client joins a channel room
    send_message             Client sends a message
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import socketio

logger = logging.getLogger(__name__)

# =====================================================================
# Socket.IO server instance
# =====================================================================

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

# Reference to the data layer -- set during setup
_data_layer: Any = None
_chat: Any = None
_task_executor: Any = None


def setup_socketio(data_layer: Any, chat: Any = None) -> None:
    """Wire the data layer into the Socket.IO event handlers."""
    global _data_layer, _chat  # noqa: PLW0603
    _data_layer = data_layer
    _chat = chat or (data_layer.chat if data_layer else None)
    logger.info("[OK] Socket.IO event layer initialised")


def setup_task_executor(executor: Any) -> None:
    """Wire the task executor for briefing and execution."""
    global _task_executor  # noqa: PLW0603
    _task_executor = executor
    logger.info("[OK] Task executor wired into Socket.IO events")


# =====================================================================
# Client -> Server events: Dashboard
# =====================================================================

@sio.event
async def connect(sid: str, environ: dict) -> None:
    logger.info("[>>] Client connected: %s", sid)


@sio.event
async def disconnect(sid: str) -> None:
    logger.info("[<<] Client disconnected: %s", sid)


@sio.event
async def join(sid: str, data: dict | None = None) -> dict:
    """Client joins and receives initial team snapshot."""
    logger.info("[*] Client %s joined", sid)
    if _data_layer is None:
        return {"error": "Data layer not initialised"}
    snapshot = _data_layer.get_team_snapshot()
    await sio.emit("cohort:team_update", snapshot, to=sid)
    return {"status": "ok"}


@sio.event
async def request_team_update(sid: str, data: dict | None = None) -> None:
    """Client requests a fresh team snapshot."""
    if _data_layer is None:
        return
    snapshot = _data_layer.get_team_snapshot()
    await sio.emit("cohort:team_update", snapshot, to=sid)


@sio.event
async def submit_review(sid: str, data: dict) -> dict:
    """Human submits a review verdict for an output."""
    if _data_layer is None:
        return {"error": "Data layer not initialised"}

    task_id = data.get("task_id")
    verdict = data.get("verdict")
    notes = data.get("notes", "")

    if not task_id or not verdict:
        return {"error": "Missing task_id or verdict"}

    review = _data_layer.record_review(task_id, verdict, notes)
    await sio.emit("cohort:review_submitted", review)
    return {"status": "ok"}


@sio.event
async def assign_task(sid: str, data: dict) -> dict:
    """Human assigns a task to an agent."""
    if _data_layer is None:
        return {"error": "Data layer not initialised"}

    agent_id = data.get("agent_id")
    description = data.get("description")
    priority = data.get("priority", "medium")

    if not agent_id or not description:
        return {"error": "Missing agent_id or description"}

    task = _data_layer.assign_task(agent_id, description, priority)
    await sio.emit("cohort:task_assigned", task)

    # Start conversational briefing
    if _task_executor:
        asyncio.create_task(_task_executor.start_briefing(task))

    return {"status": "ok", "task_id": task["task_id"]}


@sio.event
async def confirm_task(sid: str, data: dict) -> dict:
    """User confirms the briefing -- transition to execution."""
    if _data_layer is None:
        return {"error": "Data layer not initialised"}

    task_id = data.get("task_id")
    confirmed_brief = data.get("brief", {})

    if not task_id:
        return {"error": "Missing task_id"}

    task = _data_layer.confirm_task(task_id, confirmed_brief)
    if not task:
        return {"error": "Task not found"}

    # Broadcast the status update (now "assigned", ready for execution)
    await sio.emit("cohort:task_assigned", task)

    # Kick off execution
    if _task_executor:
        asyncio.create_task(_task_executor.execute_task(task, confirmed_brief))

    return {"status": "ok"}


# =====================================================================
# Client -> Server events: Chat
# =====================================================================

@sio.event
async def get_channels(sid: str, data: dict | None = None) -> None:
    """Client requests channel list."""
    if _chat is None:
        return
    channels = _chat.list_channels(include_archived=False)
    await sio.emit("channels_list", {
        "channels": [ch.to_dict() for ch in channels],
    }, to=sid)


@sio.event
async def join_channel(sid: str, data: dict | None = None) -> None:
    """Client joins a channel and receives its messages."""
    if _chat is None or data is None:
        return

    channel_id = data.get("channel_id")
    if not channel_id:
        return

    # Auto-create channel if it doesn't exist (e.g. dm-<agent> from Chat button)
    if _chat.get_channel(channel_id) is None:
        _chat.create_channel(
            name=channel_id,
            description=f"Auto-created channel: {channel_id}",
        )

    # Join the Socket.IO room for this channel
    sio.enter_room(sid, channel_id)

    # Send messages for this channel
    messages = _chat.get_channel_messages(channel_id, limit=100)
    await sio.emit("channel_messages", {
        "channel_id": channel_id,
        "messages": [m.to_dict() for m in messages],
    }, to=sid)


@sio.event
async def send_message(sid: str, data: dict) -> dict:
    """Client sends a message to a channel.

    Expected data: {"channel_id": str, "sender": str, "content": str}
    """
    if _chat is None:
        return {"error": "Chat not initialised"}

    channel_id = data.get("channel_id")
    sender = data.get("sender", "user")
    content = data.get("content")

    if not channel_id or not content:
        return {"error": "Missing channel_id or content"}

    # Auto-create channel if needed
    if _chat.get_channel(channel_id) is None:
        _chat.create_channel(
            name=channel_id,
            description=f"Auto-created channel: {channel_id}",
        )

    msg = _chat.post_message(
        channel_id=channel_id,
        sender=sender,
        content=content,
    )

    # Broadcast to all clients
    msg_data = msg.to_dict()
    await sio.emit("new_message", msg_data)

    # Route @mentions to agent response pipeline
    mentions = msg.metadata.get("mentions", [])
    if mentions:
        from cohort.agent_router import route_mentions
        route_mentions(msg, mentions)

    return {"status": "ok", "message_id": msg.id}


# =====================================================================
# Server -> Client broadcast helpers
# =====================================================================

async def emit_team_update(data: dict) -> None:
    await sio.emit("cohort:team_update", data)


async def emit_task_assigned(data: dict) -> None:
    await sio.emit("cohort:task_assigned", data)


async def emit_task_progress(data: dict) -> None:
    await sio.emit("cohort:task_progress", data)


async def emit_task_complete(data: dict) -> None:
    await sio.emit("cohort:task_complete", data)


async def emit_output_ready(data: dict) -> None:
    await sio.emit("cohort:output_ready", data)


async def emit_review_submitted(data: dict) -> None:
    await sio.emit("cohort:review_submitted", data)


async def emit_status_change(data: dict) -> None:
    await sio.emit("cohort:status_change", data)


async def emit_error(data: dict) -> None:
    await sio.emit("cohort:error", data)


async def emit_new_message(data: dict) -> None:
    """Broadcast a new message to all clients."""
    await sio.emit("new_message", data)


# =====================================================================
# Orchestrator bridge
# =====================================================================

# Map orchestrator events to Socket.IO emitters
_EVENT_MAP: dict[str, Any] = {
    "session_started": emit_status_change,
    "session_paused": emit_status_change,
    "session_resumed": emit_status_change,
    "session_ended": emit_status_change,
    "turn_recorded": emit_task_progress,
}


def orchestrator_event_bridge(event: str, data: dict) -> None:
    """Sync callback for Orchestrator.on_event -- bridges to async Socket.IO.

    Usage::

        orchestrator = Orchestrator(chat, on_event=orchestrator_event_bridge)
    """
    emitter = _EVENT_MAP.get(event)
    if emitter is None:
        return

    payload = {"event_type": event, **data}

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(emitter(payload))
    except RuntimeError:
        # No running event loop -- skip (happens during tests or sync contexts)
        logger.debug("No event loop for Socket.IO bridge event: %s", event)
