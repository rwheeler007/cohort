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
    cohort:work_queue_update Work queue snapshot (sequential execution queue)
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
    request_work_queue       Client requests current work queue state

Events (client -> server) -- Chat:
    get_channels             Client requests channel list
    join_channel             Client joins a channel room
    send_message             Client sends a message
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
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
_work_queue: Any = None
_agent_store: Any = None
_task_store: Any = None
_scheduler: Any = None


def setup_socketio(data_layer: Any, chat: Any = None, agent_store: Any = None) -> None:
    """Wire the data layer into the Socket.IO event handlers."""
    global _data_layer, _chat, _agent_store  # noqa: PLW0603
    _data_layer = data_layer
    _chat = chat or (data_layer.chat if data_layer else None)
    _agent_store = agent_store
    logger.info("[OK] Socket.IO event layer initialised")


def setup_task_executor(executor: Any) -> None:
    """Wire the task executor for briefing and execution."""
    global _task_executor  # noqa: PLW0603
    _task_executor = executor
    logger.info("[OK] Task executor wired into Socket.IO events")


def setup_work_queue(wq: Any) -> None:
    """Wire the work queue for real-time updates."""
    global _work_queue  # noqa: PLW0603
    _work_queue = wq
    logger.info("[OK] Work queue wired into Socket.IO events")


def setup_task_store(store: Any) -> None:
    """Wire the task store for schedule events."""
    global _task_store  # noqa: PLW0603
    _task_store = store
    logger.info("[OK] Task store wired into Socket.IO events")


def setup_scheduler(scheduler: Any) -> None:
    """Wire the scheduler for heartbeat and control events."""
    global _scheduler  # noqa: PLW0603
    _scheduler = scheduler
    logger.info("[OK] Scheduler wired into Socket.IO events")


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
    """Client joins and receives initial team snapshot + work queue."""
    logger.info("[*] Client %s joined", sid)
    if _data_layer is None:
        return {"error": "Data layer not initialised"}
    snapshot = _data_layer.get_team_snapshot()
    await sio.emit("cohort:team_update", snapshot, to=sid)
    # Send work queue state
    if _work_queue is not None:
        items = _work_queue.list_items()
        await sio.emit(
            "cohort:work_queue_update",
            {"items": [i.to_dict() for i in items]},
            to=sid,
        )
    # Send task list (for Review panel -- briefing + complete tasks)
    if _task_store is not None:
        tasks = _task_store.list_tasks(limit=200)
        await sio.emit("cohort:tasks_sync", {"tasks": tasks}, to=sid)
    # Send schedule state
    if _task_store is not None:
        schedules = _task_store.list_schedules()
        scheduler_status = _scheduler.status if _scheduler else {"running": False}
        await sio.emit("cohort:schedules_update", {
            "schedules": [_enrich_schedule(s) for s in schedules],
            "scheduler": scheduler_status,
        }, to=sid)
    return {"status": "ok"}


@sio.event
async def request_team_update(sid: str, data: dict | None = None) -> None:
    """Client requests a fresh team snapshot."""
    if _data_layer is None:
        return
    snapshot = _data_layer.get_team_snapshot()
    await sio.emit("cohort:team_update", snapshot, to=sid)


@sio.event
async def request_work_queue(sid: str, data: dict | None = None) -> None:
    """Client requests current work queue state."""
    if _work_queue is None:
        return
    items = _work_queue.list_items()
    await sio.emit(
        "cohort:work_queue_update",
        {"items": [i.to_dict() for i in items]},
        to=sid,
    )


@sio.event
async def submit_review(sid: str, data: dict) -> dict:
    """Human submits a review verdict for an output."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    task_id = data.get("task_id")
    verdict = data.get("verdict")
    notes = data.get("notes", "")

    if not task_id or not verdict:
        return {"error": "Missing task_id or verdict"}

    review = _task_store.record_review(task_id, verdict, notes)
    if review is None:
        return {"error": "Task not found"}
    await sio.emit("cohort:review_submitted", review)
    return {"status": "ok"}


@sio.event
async def assign_task(sid: str, data: dict) -> dict:
    """Human assigns a task to an agent."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    agent_id = data.get("agent_id")
    description = data.get("description")
    priority = data.get("priority", "medium")

    if not agent_id or not description:
        return {"error": "Missing agent_id or description"}

    # Build trigger from the submission source
    trigger = {
        "type": data.get("trigger_type", "manual"),
        "source": data.get("trigger_source", "user"),
    }

    # Build action/outcome from optional triad fields
    action = None
    tool_name = data.get("tool")
    if tool_name:
        action = {"tool": tool_name}

    outcome = None
    success_criteria = data.get("success_criteria")
    if success_criteria:
        outcome = {"success_criteria": success_criteria}

    task = _task_store.create_task(
        agent_id, description, priority,
        trigger=trigger, action=action, outcome=outcome,
    )
    await sio.emit("cohort:task_assigned", task)

    # Start conversational briefing
    if _task_executor:
        asyncio.create_task(_task_executor.start_briefing(task))

    return {"status": "ok", "task_id": task["task_id"]}


@sio.event
async def confirm_task(sid: str, data: dict) -> dict:
    """User confirms the briefing -- transition to execution.

    Extracts action and outcome from the confirmed brief (Tool/Outcome
    fields) and persists them on the task before execution.
    """
    if _task_store is None:
        return {"error": "Task store not initialised"}

    task_id = data.get("task_id")
    confirmed_brief = data.get("brief", {})

    if not task_id:
        return {"error": "Missing task_id"}

    # Extract triad fields from the confirmed brief
    from cohort.briefing import extract_triad_from_brief
    action, outcome = extract_triad_from_brief(confirmed_brief)

    updates: dict = {"status": "assigned", "brief": confirmed_brief}
    if action:
        updates["action"] = action
    if outcome:
        updates["outcome"] = outcome

    task = _task_store.update_task(task_id, **updates)
    if not task:
        return {"error": "Task not found"}

    # Broadcast the status update (now "assigned", ready for execution)
    await sio.emit("cohort:task_assigned", task)

    # Kick off execution
    if _task_executor:
        asyncio.create_task(_task_executor.execute_task(task, confirmed_brief))

    return {"status": "ok"}


@sio.event
async def cancel_task(sid: str, data: dict) -> dict:
    """User declines a briefing -- mark task as failed."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    task_id = data.get("task_id")
    if not task_id:
        return {"error": "Missing task_id"}

    task = _task_store.fail_task(task_id, reason="Declined by user from review panel")
    if not task:
        return {"error": "Task not found"}

    await sio.emit("cohort:task_updated", task)
    return {"status": "ok"}


@sio.event
async def delete_task(sid: str, data: dict) -> dict:
    """Permanently delete a task."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    task_id = data.get("task_id")
    if not task_id:
        return {"error": "Missing task_id"}

    success = _task_store.delete_task(task_id)
    if not success:
        return {"error": "Task not found"}

    # Broadcast updated task list to all clients
    tasks = _task_store.list_tasks(limit=200)
    await sio.emit("cohort:tasks_sync", {"tasks": tasks})
    return {"status": "ok"}


@sio.event
async def archive_task(sid: str, data: dict) -> dict:
    """Move a completed/failed task to archived status."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    task_id = data.get("task_id")
    if not task_id:
        return {"error": "Missing task_id"}

    task = _task_store.archive_task(task_id)
    if not task:
        return {"error": "Task not found or not in a finished state"}

    # Broadcast updated task list to all clients
    tasks = _task_store.list_tasks(limit=200)
    await sio.emit("cohort:tasks_sync", {"tasks": tasks})
    return {"status": "ok"}


# =====================================================================
# Client -> Server events: Schedules
# =====================================================================

@sio.event
async def create_schedule(sid: str, data: dict) -> dict:
    """Create a new task schedule."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    agent_id = data.get("agent_id")
    description = data.get("description")
    schedule_type = data.get("schedule_type")
    schedule_expr = data.get("schedule_expr")
    priority = data.get("priority", "medium")
    preset = data.get("preset")

    if not agent_id or not description:
        return {"error": "Missing agent_id or description"}

    # Resolve preset if provided
    if preset:
        try:
            from cohort.cron import compute_next_run, resolve_preset
            schedule_type, schedule_expr = resolve_preset(preset)
        except ValueError as exc:
            return {"error": str(exc)}

    if not schedule_type or not schedule_expr:
        return {"error": "Missing schedule_type/schedule_expr or preset"}

    try:
        from datetime import datetime, timezone

        from cohort.cron import compute_next_run
        next_run = compute_next_run(schedule_type, schedule_expr, datetime.now(timezone.utc))

        # Build triad templates from optional fields
        action_template = {}
        action_tool = data.get("action_tool")
        if action_tool:
            action_template = {"tool": action_tool}

        outcome_template = {}
        outcome_criteria = data.get("outcome_criteria")
        if outcome_criteria:
            outcome_template = {"success_criteria": outcome_criteria}

        schedule = _task_store.create_schedule(
            agent_id=agent_id,
            description=description,
            schedule_type=schedule_type,
            schedule_expr=schedule_expr,
            priority=priority,
            next_run_at=next_run,
            created_by="user",
            metadata=data.get("metadata", {}),
            action_template=action_template,
            outcome_template=outcome_template,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    # Broadcast updated schedule list
    await _broadcast_schedules()
    return {"status": "ok", "schedule_id": schedule.id}


@sio.event
async def update_schedule(sid: str, data: dict) -> dict:
    """Update a schedule definition."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    schedule_id = data.get("schedule_id")
    if not schedule_id:
        return {"error": "Missing schedule_id"}

    updates = {k: v for k, v in data.items() if k != "schedule_id"}

    # Translate frontend triad field names to schedule template fields
    action_tool = updates.pop("action_tool", None)
    outcome_criteria = updates.pop("outcome_criteria", None)
    if action_tool is not None:
        updates["action_template"] = {"tool": action_tool} if action_tool else {}
    if outcome_criteria is not None:
        updates["outcome_template"] = {"success_criteria": outcome_criteria} if outcome_criteria else {}

    schedule = _task_store.update_schedule(schedule_id, **updates)
    if schedule is None:
        return {"error": "Schedule not found"}

    await _broadcast_schedules()
    return {"status": "ok"}


@sio.event
async def delete_schedule(sid: str, data: dict) -> dict:
    """Delete a schedule."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    schedule_id = data.get("schedule_id")
    if not schedule_id:
        return {"error": "Missing schedule_id"}

    success = _task_store.delete_schedule(schedule_id)
    if not success:
        return {"error": "Schedule not found"}

    await _broadcast_schedules()
    return {"status": "ok"}


@sio.event
async def toggle_schedule(sid: str, data: dict) -> dict:
    """Toggle a schedule's enabled/disabled state."""
    if _task_store is None:
        return {"error": "Task store not initialised"}

    schedule_id = data.get("schedule_id")
    if not schedule_id:
        return {"error": "Missing schedule_id"}

    schedule = _task_store.toggle_schedule(schedule_id)
    if schedule is None:
        return {"error": "Schedule not found"}

    await _broadcast_schedules()
    return {"status": "ok", "enabled": schedule.enabled}


@sio.event
async def force_run_schedule(sid: str, data: dict) -> dict:
    """Manually trigger a schedule to run now."""
    if _scheduler is None:
        return {"error": "Scheduler not initialised"}

    schedule_id = data.get("schedule_id")
    if not schedule_id:
        return {"error": "Missing schedule_id"}

    result = await _scheduler.force_run(schedule_id)
    if result is None:
        return {"error": "Schedule not found"}

    return result


@sio.event
async def request_schedules(sid: str, data: dict | None = None) -> None:
    """Client requests current schedule list."""
    if _task_store is None:
        return
    schedules = _task_store.list_schedules()
    scheduler_status = _scheduler.status if _scheduler else {"running": False}
    await sio.emit("cohort:schedules_update", {
        "schedules": [_enrich_schedule(s) for s in schedules],
        "scheduler": scheduler_status,
    }, to=sid)


@sio.event
async def request_scheduler_status(sid: str, data: dict | None = None) -> None:
    """Client requests scheduler heartbeat status."""
    if _scheduler is None:
        await sio.emit("cohort:scheduler_heartbeat", {"running": False}, to=sid)
        return
    await sio.emit("cohort:scheduler_heartbeat", _scheduler.status, to=sid)


def _enrich_schedule(schedule) -> dict:
    """Add recent_runs to a schedule dict for the UI."""
    d = schedule.to_dict()
    if _task_store is not None:
        runs = _task_store.list_tasks(schedule_id=schedule.id, limit=3)
        d["recent_runs"] = [
            {"status": r.get("status", ""), "completed_at": r.get("completed_at"), "created_at": r.get("created_at")}
            for r in runs
        ]
    return d


async def _broadcast_schedules() -> None:
    """Broadcast updated schedule list to all clients."""
    if _task_store is None:
        return
    schedules = _task_store.list_schedules()
    scheduler_status = _scheduler.status if _scheduler else {"running": False}
    await sio.emit("cohort:schedules_update", {
        "schedules": [_enrich_schedule(s) for s in schedules],
        "scheduler": scheduler_status,
    })


# =====================================================================
# Helpers
# =====================================================================

def _post_agent_greeting(agent_id: str, channel_id: str) -> None:
    """Post a short greeting from the agent when a DM channel is first created."""
    if _chat is None:
        return

    name = agent_id.replace("_", " ").title()
    role = ""

    if _agent_store is not None:
        config = _agent_store.get(agent_id)
        if config is not None:
            name = getattr(config, "name", name) or name
            role = getattr(config, "role", "") or ""

    if role:
        greeting = f"Hey! I'm **{name}** ({role}). How can I help?"
    else:
        greeting = f"Hey! I'm **{name}**. How can I help?"

    _chat.post_message(
        channel_id=channel_id,
        sender=agent_id,
        content=greeting,
    )


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
    is_new = _chat.get_channel(channel_id) is None
    if is_new:
        _chat.create_channel(
            name=channel_id,
            description=f"Auto-created channel: {channel_id}",
        )

        # Post a greeting from the agent in DM channels
        # Handles dm-{agent_id} and dm-{agent_id}-{n} patterns
        if channel_id.startswith("dm-"):
            stripped = channel_id[3:]  # remove "dm-" prefix
            agent_id = re.sub(r"-\d+$", "", stripped)  # remove trailing -N
            _post_agent_greeting(agent_id, channel_id)

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

    thread_id = data.get("thread_id")
    response_mode = data.get("response_mode", "smarter")
    if response_mode not in ("smart", "smarter", "smartest", "channel"):
        response_mode = "smarter"

    msg = _chat.post_message(
        channel_id=channel_id,
        sender=sender,
        content=content,
        thread_id=thread_id,
    )

    # Broadcast to all clients
    msg_data = msg.to_dict()
    await sio.emit("new_message", msg_data)

    # Route @mentions to agent response pipeline
    # Skip routing for system messages -- these are context injections
    # (e.g. tool help context) that should not trigger agent responses.
    mentions = msg.metadata.get("mentions", []) if sender != "system" else []

    # Auto-route in DM channels: if this is a dm-<agent> channel and the
    # sender is a human (not the agent itself), inject the agent as a mention
    # so the routing pipeline fires without requiring an explicit @mention.
    # Handles dm-{agent_id} and dm-{agent_id}-{n} patterns.
    if channel_id.startswith("dm-") and sender != "system":
        stripped = channel_id[3:]  # strip "dm-" prefix
        dm_agent = re.sub(r"-\d+$", "", stripped)  # remove trailing -N
        if dm_agent and dm_agent != sender and dm_agent not in mentions:
            mentions = list(mentions) + [dm_agent]

    # Auto-route in task channels: if this is a task-<task_id> channel,
    # look up the assigned agent and inject as a mention so the user
    # can have a natural conversation without explicit @mentions.
    if channel_id.startswith("task-") and sender != "system":
        task_id = channel_id[5:]  # strip "task-" prefix
        if _task_store:
            task = _task_store.get_task(task_id)
            if task:
                task_agent = task.get("agent_id")
                if task_agent and task_agent != sender and task_agent not in mentions:
                    mentions = list(mentions) + [task_agent]

    if mentions:
        from cohort.agent_router import route_mentions
        route_mentions(msg, mentions, response_mode=response_mode)

    return {"status": "ok", "message_id": msg.id}


@sio.event
async def delete_message(sid: str, data: dict) -> dict:
    """Client requests deletion of a message."""
    if _chat is None:
        return {"error": "Chat not initialised"}

    message_id = data.get("message_id")
    channel_id = data.get("channel_id")
    if not message_id:
        return {"error": "Missing message_id"}

    success = _chat.delete_message(message_id, channel_id=channel_id)
    if success:
        await sio.emit("message_deleted", {
            "message_id": message_id,
            "channel_id": channel_id,
        })
        return {"success": True}
    return {"error": "Message not found"}


@sio.event
async def rename_channel(sid: str, data: dict) -> dict:
    """Rename a channel's display name."""
    if _chat is None:
        return {"error": "Chat not initialised"}
    channel_id = data.get("channel_id")
    new_name = data.get("name", "").strip()
    if not channel_id or not new_name:
        return {"error": "Missing channel_id or name"}
    ch = _chat.rename_channel(channel_id, new_name)
    if ch is None:
        return {"error": "Channel not found"}
    await _broadcast_channel_lists()
    return {"success": True, "channel": ch.to_dict()}


@sio.event
async def archive_channel(sid: str, data: dict) -> dict:
    """Client archives a DM channel."""
    if _chat is None:
        return {"error": "Chat not initialised"}
    channel_id = data.get("channel_id")
    if not channel_id:
        return {"error": "Missing channel_id"}
    ch = _chat.archive_channel(channel_id, archived_by="user")
    if ch is None:
        return {"error": "Channel not found"}
    await _broadcast_channel_lists()
    return {"success": True}


@sio.event
async def create_channel_from_chat(sid: str, data: dict) -> dict:
    """Create a named channel by copying messages from a DM, then archive the DM.

    Expected data: {"source_channel_id": str, "channel_name": str, "description": str?}
    """
    if _chat is None:
        return {"error": "Chat not initialised"}

    source_id = data.get("source_channel_id")
    channel_name = data.get("channel_name", "").strip()
    if not source_id or not channel_name:
        return {"error": "Missing source_channel_id or channel_name"}

    # Slugify the channel name for the ID
    channel_id = re.sub(r"[^a-z0-9]+", "-", channel_name.lower()).strip("-")
    if not channel_id:
        return {"error": "Invalid channel name"}

    # Avoid collision
    if _chat.get_channel(channel_id) is not None:
        channel_id = f"{channel_id}-{int(time.time())}"

    description = data.get("description", f"Created from chat: {source_id}")

    # Create the new channel (id = slug, then rename to display name)
    _chat.create_channel(name=channel_id, description=description)
    _chat.rename_channel(channel_id, channel_name)

    # Copy messages from the source DM (skip system "channel created" messages)
    source_msgs = _chat.get_channel_messages(source_id, limit=500)
    for msg in source_msgs:
        if msg.sender == "system" and "created:" in msg.content:
            continue
        _chat.post_message(
            channel_id=channel_id,
            sender=msg.sender,
            content=msg.content,
            message_type=msg.message_type,
            metadata=msg.metadata,
        )

    # Archive the source DM
    _chat.archive_channel(source_id, archived_by="user")

    await _broadcast_channel_lists()

    # Send the new channel's messages to the requesting client
    new_msgs = _chat.get_channel_messages(channel_id, limit=500)
    await sio.emit("channel_messages", {
        "channel_id": channel_id,
        "messages": [m.to_dict() for m in new_msgs],
    }, to=sid)

    return {"success": True, "channel_id": channel_id}


@sio.event
async def delete_channel(sid: str, data: dict) -> dict:
    """Soft-delete a channel (recoverable for 30 days)."""
    if _chat is None:
        return {"error": "Chat not initialised"}
    channel_id = data.get("channel_id")
    if not channel_id:
        return {"error": "Missing channel_id"}
    success = _chat.delete_channel(channel_id)
    if not success:
        return {"error": "Channel not found"}
    await _broadcast_channel_lists()
    return {"success": True}


@sio.event
async def list_deleted_channels(sid: str, data: dict | None = None) -> dict:
    """Return soft-deleted channels available for recovery."""
    if _chat is None:
        return {"error": "Chat not initialised"}
    return {"channels": _chat.list_deleted_channels()}


@sio.event
async def restore_channel(sid: str, data: dict) -> dict:
    """Restore a soft-deleted channel."""
    if _chat is None:
        return {"error": "Chat not initialised"}
    channel_id = data.get("channel_id")
    if not channel_id:
        return {"error": "Missing channel_id"}
    success = _chat.restore_channel(channel_id)
    if not success:
        return {"error": "Channel not found in trash"}
    await _broadcast_channel_lists()
    return {"success": True}


@sio.event
async def permanently_delete_channel(sid: str, data: dict) -> dict:
    """Permanently remove a channel from trash."""
    if _chat is None:
        return {"error": "Chat not initialised"}
    channel_id = data.get("channel_id")
    if not channel_id:
        return {"error": "Missing channel_id"}
    success = _chat.permanently_delete_channel(channel_id)
    if not success:
        return {"error": "Channel not found in trash"}
    return {"success": True}


@sio.event
async def unarchive_channel(sid: str, data: dict) -> dict:
    """Client unarchives a DM channel."""
    if _chat is None:
        return {"error": "Chat not initialised"}
    channel_id = data.get("channel_id")
    if not channel_id:
        return {"error": "Missing channel_id"}
    ch = _chat.unarchive_channel(channel_id)
    if ch is None:
        return {"error": "Channel not found"}
    await _broadcast_channel_lists()
    return {"success": True}


@sio.event
async def get_archived_channels(sid: str, data: dict | None = None) -> None:
    """Client requests archived channel list."""
    if _chat is None:
        return
    all_channels = _chat.list_channels(include_archived=True)
    archived = [ch.to_dict() for ch in all_channels if ch.is_archived]
    await sio.emit("archived_channels_list", {"channels": archived}, to=sid)


async def _broadcast_channel_lists() -> None:
    """Broadcast updated active + archived channel lists to all clients."""
    if _chat is None:
        return
    active = _chat.list_channels(include_archived=False)
    await sio.emit("channels_list", {
        "channels": [ch.to_dict() for ch in active],
    })
    all_ch = _chat.list_channels(include_archived=True)
    archived = [ch.to_dict() for ch in all_ch if ch.is_archived]
    await sio.emit("archived_channels_list", {"channels": archived})


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
