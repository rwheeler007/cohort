"""Cohort HTTP server -- Starlette ASGI app wrapping ChatManager.

Provides the REST API that :class:`cohort.mcp.client.CohortClient` talks to,
plus Socket.IO real-time events for the Cohort dashboard UI.

Usage::

    python -m cohort serve                  # default 0.0.0.0:5100
    python -m cohort serve --port 8080      # custom port

Endpoints::

    GET  /                                   -> Cohort dashboard HTML
    GET  /health                              -> {"status": "ok"}
    GET  /api/channels                        -> [channel, ...]
    GET  /api/messages?channel=X&limit=50     -> {"messages": [...]}
    POST /api/send  {channel, sender, message} -> {"success": true, "message_id": "..."}
    POST /api/channels/{channel_id}/condense  -> {"success": true, "archived_count": N, ...}

Socket.IO events: see socketio_events.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from cohort.secret_store import decrypt_settings_secrets, encrypt_settings_secrets

from cohort.agent_store import AgentStore
from cohort.chat import ChatManager
from cohort.local.config import DEFAULT_MODEL
from cohort.registry import JsonFileStorage

logger = logging.getLogger(__name__)

# =====================================================================
# Shared state -- populated by create_app()
# =====================================================================

_chat: ChatManager | None = None
_data_layer: Any = None
_agent_store: AgentStore | None = None
_work_queue: Any = None  # WorkQueue instance, set in create_app()
_briefing: Any = None  # ExecutiveBriefing instance, set in create_app()

# Paths
_PACKAGE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


_settings_path: Path | None = None


def _get_chat() -> ChatManager:
    """Return the global ChatManager instance (set during app startup)."""
    assert _chat is not None, "ChatManager not initialised -- call create_app() first"
    return _chat


# =====================================================================
# Settings persistence
# =====================================================================

def _load_settings() -> dict[str, Any]:
    """Load settings from {data_dir}/settings.json.

    Secret fields (api_key, service key values) are transparently decrypted.
    Legacy plaintext values are accepted and will be re-encrypted on next save.
    """
    if _settings_path and _settings_path.exists():
        try:
            with open(_settings_path, encoding="utf-8") as f:
                settings = json.load(f)
            return decrypt_settings_secrets(settings)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load settings.json: %s", exc)
    return {}


def _save_settings(settings: dict[str, Any]) -> None:
    """Persist settings to {data_dir}/settings.json.

    Secret fields are obfuscated before writing so they are not stored as
    plaintext.  See :mod:`cohort.secret_store` for details.
    """
    if _settings_path is None:
        return
    try:
        _settings_path.parent.mkdir(parents=True, exist_ok=True)
        # Deep-copy to avoid mutating the caller's dict
        to_save = json.loads(json.dumps(settings))
        encrypt_settings_secrets(to_save)
        with open(_settings_path, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except OSError as exc:
        logger.warning("Failed to save settings.json: %s", exc)


def _load_tool_filter() -> tuple[list[str], dict[str, str]] | None:
    """Load curated tool ID list and display names from cohort_tools.json.

    Returns (tool_ids, display_names) if the file exists and is valid,
    or None if no filter should be applied (show all tools).
    """
    if _settings_path is None:
        return None

    filter_path = _settings_path.parent / "cohort_tools.json"
    if not filter_path.exists():
        return None

    try:
        with open(filter_path, encoding="utf-8") as f:
            data = json.load(f)

        tool_ids = data.get("tools", [])
        if isinstance(tool_ids, list) and all(isinstance(t, str) for t in tool_ids):
            display_names = data.get("display_names", {})
            return tool_ids, display_names

        logger.warning("cohort_tools.json has invalid format, showing all tools")
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load cohort_tools.json: %s", exc)
        return None


# =====================================================================
# Route handlers
# =====================================================================

async def index(request: Request) -> HTMLResponse:
    """GET / -- serve the Cohort dashboard."""
    html_path = _TEMPLATES_DIR / "cohort.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Cohort UI not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


async def health(request: Request) -> JSONResponse:
    """GET /health -- liveness probe."""
    return JSONResponse({"status": "ok"})


async def list_channels(request: Request) -> JSONResponse:
    """GET /api/channels -- return all non-archived channels."""
    try:
        chat = _get_chat()
        channels = chat.list_channels(include_archived=False)
        return JSONResponse([ch.to_dict() for ch in channels])
    except Exception as exc:
        logger.exception("Error listing channels")
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


async def create_channel_endpoint(request: Request) -> JSONResponse:
    """POST /api/channels -- create a new channel.

    Expects JSON body: ``{"name": "...", "description": "...", "members": [...], "is_private": false, "topic": ""}``.
    """
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    name = body.get("name")
    if not name:
        return JSONResponse({"error": "Missing required field: name"}, status_code=400)

    description = body.get("description", "")
    members = body.get("members", [])
    is_private = body.get("is_private", False)
    topic = body.get("topic", "")

    try:
        chat = _get_chat()

        # Check if channel already exists
        existing = chat.get_channel(name)
        if existing is not None:
            return JSONResponse(
                {"error": f"Channel '{name}' already exists"},
                status_code=409,
            )

        channel = chat.create_channel(
            name=name,
            description=description,
            members=members,
            is_private=is_private,
            topic=topic,
        )
        logger.info("Created channel: %s", name)
        return JSONResponse({
            "success": True,
            "channel": channel.to_dict(),
        })
    except Exception as exc:
        logger.exception("Error creating channel")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def get_messages(request: Request) -> JSONResponse:
    """GET /api/messages?channel=X&limit=50 -- fetch channel messages."""
    channel = request.query_params.get("channel")
    if not channel:
        return JSONResponse(
            {"error": "Missing required query parameter: channel"},
            status_code=400,
        )

    try:
        limit = int(request.query_params.get("limit", "50"))
    except (TypeError, ValueError):
        limit = 50

    try:
        chat = _get_chat()
        messages = chat.get_channel_messages(channel, limit=limit)
        return JSONResponse({"messages": [m.to_dict() for m in messages]})
    except Exception as exc:
        logger.exception("Error fetching messages for channel %s", channel)
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


async def send_message(request: Request) -> JSONResponse:
    """POST /api/send -- post a message to a channel.

    Expects JSON body: ``{"channel": "...", "sender": "...", "message": "...",
    "response_mode": "smart|smarter|smartest"}``.
    Auto-creates the channel if it does not exist yet.
    ``response_mode`` is optional (default: ``"smarter"``).
    """
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse(
            {"error": "Invalid JSON body"}, status_code=400,
        )

    channel = body.get("channel")
    sender = body.get("sender")
    message = body.get("message")

    if not channel or not sender or not message:
        missing = [
            name for name, val in [("channel", channel), ("sender", sender), ("message", message)]
            if not val
        ]
        return JSONResponse(
            {"error": f"Missing required fields: {', '.join(missing)}"},
            status_code=400,
        )

    try:
        chat = _get_chat()

        # Auto-create channel if it doesn't exist
        if chat.get_channel(channel) is None:
            chat.create_channel(
                name=channel,
                description=f"Auto-created channel: {channel}",
            )
            logger.info("Auto-created channel: %s", channel)

        msg = chat.post_message(
            channel_id=channel,
            sender=sender,
            content=message,
        )

        # Route @mentions to agent response pipeline
        response_mode = body.get("response_mode", "smarter")
        if response_mode not in ("smart", "smarter", "smartest"):
            response_mode = "smarter"

        mentions = msg.metadata.get("mentions", [])
        if mentions:
            from cohort.agent_router import route_mentions
            route_mentions(msg, mentions, response_mode=response_mode)

        return JSONResponse({"success": True, "message_id": msg.id})
    except Exception as exc:
        logger.exception("Error posting message to %s", channel)
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


async def list_agents(request: Request) -> JSONResponse:
    """GET /api/agents -- return all registered agents with status."""
    if _data_layer is None:
        return JSONResponse({"error": "Data layer not initialised"}, status_code=500)
    return JSONResponse(_data_layer.get_team_snapshot())


async def register_agent(request: Request) -> JSONResponse:
    """POST /api/agents -- register or update an agent.

    Expects JSON body: ``{"agent_id": "...", "name": "...", "triggers": [...], "capabilities": [...]}``.
    """
    if _data_layer is None:
        return JSONResponse({"error": "Data layer not initialised"}, status_code=500)

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    agent_id = body.get("agent_id")
    if not agent_id:
        return JSONResponse({"error": "Missing required field: agent_id"}, status_code=400)

    config = {k: v for k, v in body.items() if k != "agent_id"}
    _data_layer.register_agent(agent_id, config)

    # Persist to agents.json
    _save_agents_to_disk()

    logger.info("Registered agent: %s", agent_id)
    return JSONResponse({"success": True, "agent_id": agent_id})


async def get_task_queue(request: Request) -> JSONResponse:
    """GET /api/tasks -- return the task queue."""
    if _data_layer is None:
        return JSONResponse({"error": "Data layer not initialised"}, status_code=500)

    status_filter = request.query_params.get("status")
    tasks = _data_layer.get_task_queue(status_filter=status_filter)
    return JSONResponse({"tasks": tasks})


async def get_outputs(request: Request) -> JSONResponse:
    """GET /api/outputs -- return completed tasks awaiting review."""
    if _data_layer is None:
        return JSONResponse({"error": "Data layer not initialised"}, status_code=500)

    outputs = _data_layer.get_outputs_for_review()
    return JSONResponse({"outputs": outputs})


async def create_task(request: Request) -> JSONResponse:
    """POST /api/tasks -- create a task via HTTP (MCP-friendly).

    Mirrors the Socket.IO ``assign_task`` event but over HTTP so MCP
    tools can submit tasks without needing a WebSocket connection.
    """
    if _data_layer is None:
        return JSONResponse({"error": "Data layer not initialised"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    agent_id = body.get("agent_id")
    description = body.get("description")
    priority = body.get("priority", "medium")

    if not agent_id or not description:
        return JSONResponse(
            {"error": "Missing required fields: agent_id, description"},
            status_code=400,
        )

    task = _data_layer.assign_task(agent_id, description, priority)

    # Broadcast via Socket.IO if available
    try:
        from cohort.socketio_events import sio
        import asyncio
        asyncio.create_task(sio.emit("cohort:task_assigned", task))
    except Exception:
        pass  # Socket.IO not available -- task still created

    return JSONResponse({"success": True, "task": task})


async def update_task(request: Request) -> JSONResponse:
    """PATCH /api/tasks/{task_id} -- update task status/output (MCP-friendly).

    Supports advancing tasks through the lifecycle and attaching output
    for the review pipeline.
    """
    if _data_layer is None:
        return JSONResponse({"error": "Data layer not initialised"}, status_code=500)

    task_id = request.path_params.get("task_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    status = body.get("status")
    output = body.get("output")

    if status == "complete":
        task = _data_layer.complete_task(task_id, output=output)
    elif status:
        task = _data_layer.update_task_progress(task_id, status, progress=body.get("progress"))
    else:
        return JSONResponse({"error": "Missing 'status' field"}, status_code=400)

    if task is None:
        return JSONResponse({"error": f"Task '{task_id}' not found"}, status_code=404)

    # Broadcast via Socket.IO
    try:
        from cohort.socketio_events import sio
        import asyncio
        event = "cohort:task_complete" if status == "complete" else "cohort:task_progress"
        asyncio.create_task(sio.emit(event, task))
    except Exception:
        pass

    return JSONResponse({"success": True, "task": task})


# =====================================================================
# Work Queue endpoints (sequential execution queue)
# =====================================================================

async def get_work_queue(request: Request) -> JSONResponse:
    """GET /api/work-queue -- return sequential work queue items."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    status_filter = request.query_params.get("status")
    items = _work_queue.list_items(status=status_filter)
    return JSONResponse({"items": [i.to_dict() for i in items]})


async def enqueue_work_item(request: Request) -> JSONResponse:
    """POST /api/work-queue -- enqueue a new item."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    description = body.get("description")
    requester = body.get("requester", "anonymous")
    if not description:
        return JSONResponse(
            {"error": "Missing required field: description"}, status_code=400,
        )

    try:
        item = _work_queue.enqueue(
            description=description,
            requester=requester,
            priority=body.get("priority", "medium"),
            agent_id=body.get("agent_id"),
            depends_on=body.get("depends_on"),
            metadata=body.get("metadata"),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    _broadcast_work_queue()
    return JSONResponse({"success": True, "item": item.to_dict()})


async def claim_work_item(request: Request) -> JSONResponse:
    """POST /api/work-queue/claim -- claim the next queued item."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    result = _work_queue.claim_next()

    if "error" in result:
        return JSONResponse(result, status_code=409)

    _broadcast_work_queue()
    return JSONResponse({"success": True, **result})


async def get_work_item(request: Request) -> JSONResponse:
    """GET /api/work-queue/{item_id} -- return a single work queue item."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    item_id = request.path_params.get("item_id", "")
    item = _work_queue.get_item(item_id)
    if item is None:
        return JSONResponse({"error": f"Item '{item_id}' not found"}, status_code=404)
    return JSONResponse({"item": item.to_dict()})


async def update_work_item(request: Request) -> JSONResponse:
    """PATCH /api/work-queue/{item_id} -- update item status."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    item_id = request.path_params.get("item_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    status = body.get("status")
    result_text = body.get("result")

    if status == "completed":
        item = _work_queue.complete(item_id, result=result_text)
    elif status == "failed":
        item = _work_queue.fail(item_id, reason=result_text)
    elif status == "cancelled":
        item = _work_queue.cancel(item_id)
    else:
        return JSONResponse(
            {"error": "Invalid status. Use: completed, failed, or cancelled"},
            status_code=400,
        )

    if item is None:
        return JSONResponse(
            {"error": f"Item '{item_id}' not found or invalid transition"},
            status_code=404,
        )

    _broadcast_work_queue()
    return JSONResponse({"success": True, "item": item.to_dict()})


def _broadcast_work_queue() -> None:
    """Push updated work queue to all connected dashboard clients."""
    if _work_queue is None:
        return
    try:
        from cohort.socketio_events import sio
        items = _work_queue.list_items()
        asyncio.create_task(
            sio.emit("cohort:work_queue_update", {
                "items": [i.to_dict() for i in items],
            }),
        )
    except Exception:
        pass


# =====================================================================
# Executive briefing
# =====================================================================


async def generate_briefing(request: Request) -> JSONResponse:
    """POST /api/briefing/generate -- generate an executive briefing.

    Optional JSON body: {"hours": 24, "post_to_channel": true, "channel": "daily-digest"}
    """
    if _briefing is None:
        return JSONResponse(
            {"error": "Briefing system not initialised"}, status_code=500
        )

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    hours = body.get("hours", 24)
    post_to_channel = body.get("post_to_channel", True)
    channel = body.get("channel", "daily-digest")

    try:
        report = _briefing.generate(
            hours=hours,
            post_to_channel=post_to_channel,
            channel_id=channel,
        )
        return JSONResponse({"success": True, "report": report.to_dict()})
    except Exception as exc:
        logger.exception("Error generating briefing")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def get_latest_briefing(request: Request) -> JSONResponse:
    """GET /api/briefing/latest -- return the most recent briefing."""
    if _briefing is None:
        return JSONResponse(
            {"error": "Briefing system not initialised"}, status_code=500
        )

    report = _briefing.get_latest()
    if report is None:
        return JSONResponse(
            {"error": "No briefings generated yet"}, status_code=404
        )

    return JSONResponse({"success": True, "report": report.to_dict()})


async def get_agent_registry(request: Request) -> JSONResponse:
    """GET /api/agent-registry -- return all agent visual profiles (avatars, colors, nicknames)."""
    from cohort.agent_registry import get_all_agents
    profiles = get_all_agents()

    # Apply user identity from settings to the 'user' profile
    settings = _load_settings()
    user_name = settings.get("user_display_name", "")
    user_role = settings.get("user_display_role", "")
    user_avatar = settings.get("user_display_avatar", "")
    if "user" in profiles:
        if user_name:
            profiles["user"]["name"] = user_name
            profiles["user"]["nickname"] = user_name
        if user_avatar:
            profiles["user"]["avatar"] = user_avatar
        elif user_name:
            profiles["user"]["avatar"] = user_name[:2].upper()
        if user_role:
            profiles["user"]["role"] = user_role

    return JSONResponse(profiles)


async def get_agent_detail(request: Request) -> JSONResponse:
    """GET /api/agents/{agent_id} -- return full agent config."""
    if _agent_store is None:
        return JSONResponse({"error": "Agent store not initialised"}, status_code=500)

    agent_id = request.path_params["agent_id"]
    config = _agent_store.get(agent_id)
    if config is None:
        # Try alias resolution
        config = _agent_store.get_by_alias(agent_id)
    if config is None:
        return JSONResponse({"error": f"Agent not found: {agent_id}"}, status_code=404)

    return JSONResponse(config.to_dict())


async def get_agent_memory(request: Request) -> JSONResponse:
    """GET /api/agents/{agent_id}/memory -- return agent memory."""
    if _agent_store is None:
        return JSONResponse({"error": "Agent store not initialised"}, status_code=500)

    agent_id = request.path_params["agent_id"]
    memory = _agent_store.load_memory(agent_id)
    if memory is None:
        return JSONResponse({"error": f"No memory for agent: {agent_id}"}, status_code=404)

    return JSONResponse(memory.to_dict())


async def get_agent_prompt(request: Request) -> JSONResponse:
    """GET /api/agents/{agent_id}/prompt -- return agent prompt text."""
    if _agent_store is None:
        return JSONResponse({"error": "Agent store not initialised"}, status_code=500)

    agent_id = request.path_params["agent_id"]
    prompt = _agent_store.get_prompt(agent_id)
    if prompt is None:
        return JSONResponse({"error": f"No prompt for agent: {agent_id}"}, status_code=404)

    return JSONResponse({"agent_id": agent_id, "prompt": prompt})


async def create_agent(request: Request) -> JSONResponse:
    """POST /api/agents/create -- create a new agent from spec.

    Expects JSON body with at minimum: ``name``, ``role``, ``primary_task``.
    """
    if _agent_store is None:
        return JSONResponse({"error": "Agent store not initialised"}, status_code=500)

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    name = body.get("name")
    role = body.get("role")
    primary_task = body.get("primary_task", "")

    if not name or not role:
        return JSONResponse(
            {"error": "Missing required fields: name, role"},
            status_code=400,
        )

    try:
        from cohort.agent_creator import AgentCreator, AgentSpec, AgentType

        agent_type_str = body.get("agent_type", "specialist")
        try:
            agent_type = AgentType(agent_type_str)
        except ValueError:
            agent_type = AgentType.SPECIALIST

        spec = AgentSpec(
            name=name,
            role=role,
            primary_task=primary_task,
            agent_type=agent_type,
            personality=body.get("personality", ""),
            capabilities=body.get("capabilities", []),
            domain_expertise=body.get("domain_expertise", []),
            triggers=body.get("triggers", []),
            avatar=body.get("avatar", ""),
            aliases=body.get("aliases", []),
            nickname=body.get("nickname", ""),
            color=body.get("color", "#95A5A6"),
            group=body.get("group", "Agents"),
        )

        creator = AgentCreator(_agent_store)
        config = creator.create_agent(spec)

        logger.info("[OK] Created agent: %s", config.agent_id)
        return JSONResponse({
            "success": True,
            "agent_id": config.agent_id,
            "config": config.to_dict(),
        })
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except Exception as exc:
        logger.exception("Error creating agent")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def clean_agent_memory(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/memory/clean -- trim working memory."""
    if _agent_store is None:
        return JSONResponse({"error": "Agent store not initialised"}, status_code=500)

    agent_id = request.path_params["agent_id"]

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}

    keep_last = body.get("keep_last", 10)
    dry_run = body.get("dry_run", False)

    try:
        from cohort.memory_manager import MemoryManager

        mm = MemoryManager(_agent_store, keep_last=keep_last)
        result = mm.clean_agent(agent_id, keep_last=keep_last, dry_run=dry_run)
        return JSONResponse({
            "success": result.success,
            "agent_id": result.agent_id,
            "working_memory_removed": result.working_memory_removed,
            "working_memory_kept": result.working_memory_kept,
            "archive_path": str(result.archive_path) if result.archive_path else None,
            "error": result.error,
        })
    except Exception as exc:
        logger.exception("Error cleaning agent memory")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def add_agent_fact(request: Request) -> JSONResponse:
    """POST /api/agents/{agent_id}/memory/facts -- add a learned fact."""
    if _agent_store is None:
        return JSONResponse({"error": "Agent store not initialised"}, status_code=500)

    agent_id = request.path_params["agent_id"]

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    fact_text = body.get("fact")
    if not fact_text:
        return JSONResponse({"error": "Missing required field: fact"}, status_code=400)

    try:
        from cohort.agent import LearnedFact
        from cohort.memory_manager import MemoryManager

        fact = LearnedFact(
            fact=fact_text,
            learned_from=body.get("learned_from", "mcp"),
            timestamp=body.get("timestamp", datetime.now().isoformat()),
            confidence=body.get("confidence", "medium"),
            session_id=body.get("session_id", ""),
        )
        mm = MemoryManager(_agent_store)
        mm.add_learned_fact(agent_id, fact)
        return JSONResponse({"success": True, "agent_id": agent_id, "fact": fact.to_dict()})
    except Exception as exc:
        logger.exception("Error adding learned fact")
        return JSONResponse({"error": str(exc)}, status_code=500)


# =====================================================================
# Roundtable endpoints
# =====================================================================

_session_orch = None


def _get_session_orch():
    """Lazy-load session orchestrator singleton."""
    global _session_orch  # noqa: PLW0603
    if _session_orch is None:
        from cohort.orchestrator import Orchestrator
        from cohort.socketio_events import orchestrator_event_bridge
        agents_config = _data_layer._agents if _data_layer else {}
        _session_orch = Orchestrator(
            _get_chat(),
            agents=agents_config,
            on_event=orchestrator_event_bridge,
        )
        # Wire orchestrator into agent router for session gating
        from cohort.agent_router import set_orchestrator
        set_orchestrator(_session_orch)
    return _session_orch


# Deprecated alias
_get_roundtable_orch = _get_session_orch


async def start_session_endpoint(request: Request) -> JSONResponse:
    """POST /api/sessions/start -- start a new discussion session."""
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}

    channel_id = body.get("channel_id")
    topic = body.get("topic")

    if not channel_id:
        return JSONResponse({"success": False, "error": "channel_id is required"}, status_code=400)
    if not topic:
        return JSONResponse({"success": False, "error": "topic is required"}, status_code=400)

    chat = _get_chat()

    # Auto-create channel
    if chat.get_channel(channel_id) is None:
        chat.create_channel(name=channel_id, description=f"Discussion: {topic}")

    orch = _get_session_orch()

    # Check no existing active session
    existing = orch.get_session_for_channel(channel_id)
    if existing:
        return JSONResponse({
            "success": False,
            "error": f"Channel already has active session: {existing.session_id}",
        }, status_code=409)

    try:
        session = orch.start_session(
            channel_id=channel_id,
            topic=topic,
            initial_agents=body.get("initial_agents"),
            max_turns=body.get("max_turns", 20),
        )

        return JSONResponse({
            "success": True,
            "session": session.to_dict(),
        })
    except Exception as exc:
        logger.exception("Error starting session")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


async def get_session_status(request: Request) -> JSONResponse:
    """GET /api/sessions/{session_id}/status -- session status."""
    session_id = request.path_params["session_id"]
    orch = _get_session_orch()
    status = orch.get_status(session_id)
    if not status:
        return JSONResponse({"success": False, "error": "Session not found"}, status_code=404)
    return JSONResponse({"success": True, "status": status})


async def get_next_speaker(request: Request) -> JSONResponse:
    """GET /api/sessions/{session_id}/next-speaker -- recommended speaker."""
    session_id = request.path_params["session_id"]
    orch = _get_session_orch()
    recommendation = orch.get_next_speaker(session_id)
    if not recommendation:
        return JSONResponse({"success": False, "error": "No speaker available"}, status_code=400)
    return JSONResponse({"success": True, "recommendation": recommendation})


async def record_session_turn(request: Request) -> JSONResponse:
    """POST /api/sessions/{session_id}/record-turn -- record agent turn."""
    session_id = request.path_params["session_id"]
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}

    speaker = body.get("speaker")
    message_id = body.get("message_id")
    if not speaker or not message_id:
        return JSONResponse({"success": False, "error": "speaker and message_id required"}, status_code=400)

    orch = _get_session_orch()
    success = orch.record_turn(
        session_id=session_id,
        speaker=speaker,
        message_id=message_id,
        was_recommended=body.get("was_recommended", True),
    )
    if not success:
        return JSONResponse({"success": False, "error": "Failed to record turn"}, status_code=400)
    return JSONResponse({"success": True})


async def end_session_endpoint(request: Request) -> JSONResponse:
    """POST /api/sessions/{session_id}/end -- end session."""
    session_id = request.path_params["session_id"]
    orch = _get_session_orch()
    summary = orch.end_session(session_id)
    if not summary:
        return JSONResponse({"success": False, "error": "Session not found"}, status_code=404)
    return JSONResponse({"success": True, "summary": summary})


async def list_sessions_endpoint(request: Request) -> JSONResponse:
    """GET /api/sessions -- list all sessions."""
    orch = _get_session_orch()
    sessions = [s.to_dict() for s in orch.sessions.values()]
    return JSONResponse({"success": True, "sessions": sessions})


async def session_setup_parse(request: Request) -> JSONResponse:
    """POST /api/sessions/setup-parse -- parse natural language into config."""
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)

    context = body.get("context")
    orch = _get_session_orch()
    config = orch.suggest_session_config(message, context=context)
    return JSONResponse({"success": True, "config": config})


async def pause_session_endpoint(request: Request) -> JSONResponse:
    """POST /api/sessions/{session_id}/pause -- pause session."""
    session_id = request.path_params["session_id"]
    orch = _get_session_orch()
    ok = orch.pause_session(session_id)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found or not active"}, status_code=404)
    return JSONResponse({"success": True})


async def resume_session_endpoint(request: Request) -> JSONResponse:
    """POST /api/sessions/{session_id}/resume -- resume session."""
    session_id = request.path_params["session_id"]
    orch = _get_session_orch()
    ok = orch.resume_session(session_id)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found or not paused"}, status_code=404)
    return JSONResponse({"success": True})


async def get_channel_session(request: Request) -> JSONResponse:
    """GET /api/sessions/channel/{channel_id} -- get active session for channel."""
    channel_id = request.path_params["channel_id"]
    orch = _get_session_orch()
    session = orch.get_session_for_channel(channel_id)
    if not session:
        return JSONResponse({"success": True, "has_session": False, "session": None})
    return JSONResponse({"success": True, "has_session": True, "session": session.to_dict()})


async def condense_channel(request: Request) -> JSONResponse:
    """POST /api/channels/{channel_id}/condense -- trim old messages.

    Keeps the last *keep_last* messages and deletes the rest.
    """
    channel_id = request.path_params["channel_id"]

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}

    keep_last = body.get("keep_last", 5)
    if not isinstance(keep_last, int) or keep_last < 1:
        keep_last = 5

    try:
        chat = _get_chat()
        storage = chat._storage  # noqa: SLF001 -- internal access for condense

        if chat.get_channel(channel_id) is None:
            return JSONResponse(
                {"error": f"Channel not found: {channel_id}"},
                status_code=404,
            )

        # Fetch all messages for this channel (high limit to get everything)
        all_messages = chat.get_channel_messages(channel_id, limit=10000)
        total = len(all_messages)

        if total <= keep_last:
            return JSONResponse({
                "success": True,
                "archived_count": 0,
                "message": "Nothing to condense",
            })

        # Determine which messages to keep (the last N)
        keep_ids = {m.id for m in all_messages[-keep_last:]}
        archived_count = total - keep_last

        # Rewrite the messages file, removing the old messages for this channel
        raw_messages: list[dict] = storage._read_json(  # noqa: SLF001
            storage._messages_path, [],  # noqa: SLF001
        )
        new_messages = [
            m for m in raw_messages
            if m.get("channel_id") != channel_id or m.get("id") in keep_ids
        ]
        storage._write_json(storage._messages_path, new_messages)  # noqa: SLF001

        logger.info(
            "Condensed channel %s: archived %d messages, kept %d",
            channel_id, archived_count, keep_last,
        )
        return JSONResponse({
            "success": True,
            "archived_count": archived_count,
            "message": f"Kept last {keep_last} messages",
        })
    except Exception as exc:
        logger.exception("Error condensing channel %s", channel_id)
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


# =====================================================================
# Tools endpoint (reads from boss_config.yaml)
# =====================================================================

async def list_tools(request: Request) -> JSONResponse:
    """GET /api/tools -- return workflow tools from boss_config.yaml, filtered by cohort_tools.json."""
    settings = _load_settings()
    agents_root = settings.get("agents_root", "")
    if not agents_root:
        agents_root = os.environ.get("COHORT_AGENTS_ROOT", "")

    if not agents_root:
        return JSONResponse({"tools": []})

    tool_filter = _load_tool_filter()
    allowed_ids = tool_filter[0] if tool_filter else None
    display_names = tool_filter[1] if tool_filter else {}
    tools_cfg = _load_tools_config()
    native_descriptions = tools_cfg.get("descriptions", {})

    config_path = Path(agents_root) / "config" / "boss_config.yaml" if agents_root else None

    tools: list[dict[str, Any]] = []

    # Load from boss_config.yaml if available
    if config_path and config_path.exists():
        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            raw_tools = cfg.get("boss", {}).get("tools", {})
            for tool_id, info in raw_tools.items():
                if allowed_ids is not None and tool_id not in allowed_ids:
                    continue
                name = display_names.get(tool_id) or tool_id.replace("_", " ").title()
                tools.append({
                    "id": tool_id,
                    "name": name,
                    "description": info.get("description", ""),
                    "phases": info.get("phases", []),
                    "features": info.get("features", []),
                    "path": info.get("path", ""),
                    "implemented": bool(info.get("path")),
                })
        except Exception as exc:
            logger.warning("Failed to load tools from boss_config.yaml: %s", exc)

    # Fill in any tools from cohort_tools.json not already loaded
    if allowed_ids is not None:
        existing_ids = {t["id"] for t in tools}
        for tid in allowed_ids:
            if tid not in existing_ids:
                name = display_names.get(tid) or tid.replace("_", " ").title()
                tools.append({
                    "id": tid,
                    "name": name,
                    "description": native_descriptions.get(tid, ""),
                    "phases": [],
                    "features": [],
                    "path": "",
                    "implemented": True,
                })

    # Preserve curated ordering from cohort_tools.json
    if allowed_ids is not None:
        order = {tid: i for i, tid in enumerate(allowed_ids)}
        tools.sort(key=lambda t: order.get(t["id"], 999))

    return JSONResponse({"tools": tools})


# =====================================================================
# Settings endpoints
# =====================================================================

async def get_settings(request: Request) -> JSONResponse:
    """GET /api/settings -- return current settings (API key masked)."""
    settings = _load_settings()

    # Mask the API key for display
    api_key = settings.get("api_key", "")
    if api_key:
        masked = "sk-..." + api_key[-4:] if len(api_key) > 8 else "sk-...(set)"
    else:
        masked = ""

    # Check if Claude CLI path exists
    claude_cmd = settings.get("claude_cmd", "")
    claude_connected = bool(claude_cmd and Path(claude_cmd).exists())

    # Check if Claude CLI is available for Smartest mode
    smartest_available = False
    try:
        from cohort.agent_router import check_claude_cli_available
        smartest_available = check_claude_cli_available()
    except ImportError:
        pass

    return JSONResponse({
        "api_key_masked": masked,
        "claude_enabled": settings.get("claude_enabled", False),
        "claude_cmd": claude_cmd,
        "agents_root": settings.get("agents_root", ""),
        "response_timeout": settings.get("response_timeout", 300),
        "execution_backend": settings.get("execution_backend", "cli"),
        "claude_code_connected": claude_connected,
        "smartest_available": smartest_available,
        "admin_mode": settings.get("admin_mode", False),
        "force_to_claude_code": settings.get("force_to_claude_code", False),
        "user_display_name": settings.get("user_display_name", ""),
        "user_display_role": settings.get("user_display_role", ""),
        "user_display_avatar": settings.get("user_display_avatar", ""),
    })


async def post_settings(request: Request) -> JSONResponse:
    """POST /api/settings -- save settings."""
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    settings = _load_settings()

    # Update fields that were provided
    if "api_key" in body:
        settings["api_key"] = body["api_key"]
    if "claude_cmd" in body:
        settings["claude_cmd"] = body["claude_cmd"]
    if "agents_root" in body:
        settings["agents_root"] = body["agents_root"]
    if "response_timeout" in body:
        timeout = body["response_timeout"]
        if isinstance(timeout, int) and 30 <= timeout <= 600:
            settings["response_timeout"] = timeout
    if "execution_backend" in body:
        if body["execution_backend"] in ("cli", "api", "chat"):
            settings["execution_backend"] = body["execution_backend"]
    if "claude_enabled" in body:
        settings["claude_enabled"] = bool(body["claude_enabled"])
    if "admin_mode" in body:
        settings["admin_mode"] = bool(body["admin_mode"])
    if "force_to_claude_code" in body:
        settings["force_to_claude_code"] = bool(body["force_to_claude_code"])
    if "user_display_name" in body:
        name = str(body["user_display_name"]).strip()[:40]
        settings["user_display_name"] = name
    if "user_display_role" in body:
        role = str(body["user_display_role"]).strip()[:40]
        settings["user_display_role"] = role
    if "user_display_avatar" in body:
        avatar = str(body["user_display_avatar"]).strip().upper()[:3]
        settings["user_display_avatar"] = avatar

    _save_settings(settings)

    # Hot-reload settings into agent router
    try:
        from cohort.agent_router import apply_settings
        apply_settings(settings)
    except ImportError:
        pass

    # Hot-reload settings into task executor
    try:
        from cohort.socketio_events import _task_executor
        if _task_executor:
            _task_executor.apply_settings(settings)
    except ImportError:
        pass

    logger.info("[OK] Settings saved")
    return JSONResponse({"success": True})


async def test_connection(request: Request) -> JSONResponse:
    """POST /api/settings/test-connection -- verify Claude CLI is reachable."""
    import subprocess as _sp

    settings = _load_settings()
    claude_cmd = settings.get("claude_cmd", "")

    if not claude_cmd:
        return JSONResponse({"success": False, "error": "Claude CLI path not configured"})

    cmd_path = Path(claude_cmd)
    if not cmd_path.exists():
        return JSONResponse({"success": False, "error": f"CLI not found at: {claude_cmd}"})

    try:
        result = _sp.run(
            [claude_cmd, "--version"],
            capture_output=True, text=True, timeout=10,
            shell=True, encoding="utf-8", errors="replace",
        )
        version = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0:
            return JSONResponse({"success": True, "message": f"Connected: {version}"})
        else:
            return JSONResponse({"success": False, "error": f"CLI returned exit code {result.returncode}: {version[:100]}"})
    except _sp.TimeoutExpired:
        return JSONResponse({"success": False, "error": "CLI timed out (10s)"})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)})


# =====================================================================
# Permissions endpoints
# =====================================================================

async def get_permissions(request: Request) -> JSONResponse:
    """GET /api/permissions -- return service keys (masked) and per-agent permissions."""
    settings = _load_settings()
    raw_services = settings.get("service_keys", [])
    permissions = settings.get("agent_permissions", {})

    # Mask keys for display
    services = []
    for svc in raw_services:
        key = svc.get("key", "")
        services.append({
            "id": svc.get("id", ""),
            "type": svc.get("type", "custom"),
            "name": svc.get("name", ""),
            "has_key": bool(key),
            "key_masked": ("..." + key[-4:]) if len(key) > 8 else ("...(set)" if key else ""),
            "extra": svc.get("extra", ""),
        })

    return JSONResponse({
        "services": services,
        "permissions": permissions,
    })


async def post_permissions(request: Request) -> JSONResponse:
    """POST /api/permissions -- save service keys and per-agent permissions."""
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    settings = _load_settings()
    existing_keys = {svc["id"]: svc.get("key", "") for svc in settings.get("service_keys", [])}

    # Process incoming services -- preserve existing keys unless a new_key is provided
    incoming_services = body.get("services", [])
    updated_services = []
    for svc in incoming_services:
        svc_id = svc.get("id", "")
        new_key = svc.get("new_key", "")
        stored_key = new_key if new_key else existing_keys.get(svc_id, "")

        updated_services.append({
            "id": svc_id,
            "type": svc.get("type", "custom"),
            "name": svc.get("name", ""),
            "key": stored_key,
            "extra": svc.get("extra", ""),
        })

    settings["service_keys"] = updated_services
    settings["agent_permissions"] = body.get("permissions", {})
    _save_settings(settings)

    logger.info("[OK] Permissions saved (%d services)", len(updated_services))
    return JSONResponse({"success": True})


# =====================================================================
# Tool Permissions API (per-agent tool access)
# =====================================================================

# Canonical list of tools that can be toggled from the dashboard.
_ALL_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch"]


def _tool_permissions_path() -> Path:
    """Return the path to tool_permissions.json in the data directory."""
    return Path(_resolved_data_dir) / "tool_permissions.json"


def _load_tool_permissions() -> dict[str, Any]:
    """Read tool_permissions.json from disk (bypasses module cache)."""
    path = _tool_permissions_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[!] Failed to read tool_permissions.json: %s", exc)
    return {}


def _save_tool_permissions(data: dict[str, Any]) -> None:
    """Write tool_permissions.json and invalidate the module cache."""
    path = _tool_permissions_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("[!] Failed to write tool_permissions.json: %s", exc)
        return

    # Invalidate the cached permissions so the next resolve_permissions()
    # call picks up the new values immediately.
    from cohort.tool_permissions import reload_central_permissions
    reload_central_permissions(path.parent)


async def get_tool_permissions(request: Request) -> JSONResponse:
    """GET /api/tool-permissions -- profiles, defaults, and per-agent resolved view."""
    import re
    from cohort.tool_permissions import resolve_permissions

    central = _load_tool_permissions()
    profiles = central.get("tool_profiles", {})
    agent_defaults = central.get("agent_defaults", {})
    denied_tools = central.get("denied_tools", [])
    agent_overrides = central.get("agent_overrides", {})

    # Build per-agent resolved view
    agents_list: list[dict[str, Any]] = []
    if _agent_store is not None:
        for config in _agent_store.list_agents(include_hidden=False):
            # Determine profile source
            agent_perms = config.tool_permissions or {}
            raw_group = config.group or ""
            group_key = re.sub(r"[^a-z0-9]+", "_", raw_group.lower()).strip("_") if raw_group else ""
            profile_name = agent_perms.get("profile", "")
            if profile_name:
                profile_source = "agent_config"
            elif group_key and group_key in agent_defaults:
                profile_name = agent_defaults[group_key]
                profile_source = "group_default"
            elif config.agent_type in agent_defaults:
                profile_name = agent_defaults[config.agent_type]
                profile_source = "agent_type_default"
            else:
                profile_name = "minimal"
                profile_source = "fallback"

            has_override = config.agent_id in agent_overrides

            # Resolve effective tools
            resolved = resolve_permissions(config.agent_id, config, central)
            effective_tools = resolved.allowed_tools if resolved else []

            agents_list.append({
                "agent_id": config.agent_id,
                "name": config.name,
                "nickname": config.nickname or config.name,
                "group": config.group or "Agents",
                "agent_type": config.agent_type,
                "profile_name": profile_name,
                "profile_source": profile_source,
                "allowed_tools": effective_tools,
                "has_override": has_override,
            })

    return JSONResponse({
        "profiles": profiles,
        "agent_defaults": agent_defaults,
        "denied_tools": denied_tools,
        "all_tools": _ALL_TOOLS,
        "agent_overrides": agent_overrides,
        "agents": agents_list,
    })


async def put_tool_permissions(request: Request) -> JSONResponse:
    """PUT /api/tool-permissions -- save agent_defaults, denied_tools, and per-agent overrides."""
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    central = _load_tool_permissions()

    # Merge incoming fields (only update fields that are present)
    if "agent_defaults" in body:
        central["agent_defaults"] = body["agent_defaults"]

    if "denied_tools" in body:
        central["denied_tools"] = body["denied_tools"]

    if "agent_overrides" in body:
        overrides = body["agent_overrides"]
        # Clean up empty overrides (agent reverted to profile)
        central["agent_overrides"] = {
            aid: ov for aid, ov in overrides.items()
            if ov.get("allowed_tools_override") is not None
        }

    _save_tool_permissions(central)

    agent_count = len(central.get("agent_overrides", {}))
    logger.info("[OK] Tool permissions saved (%d agent overrides)", agent_count)
    return JSONResponse({"success": True})


# =====================================================================
# Setup Wizard API
# =====================================================================


async def setup_status(request: Request) -> JSONResponse:
    """GET /api/setup/status -- check if setup has been completed."""
    settings = _load_settings()
    setup_done = settings.get("setup_completed", False)

    return JSONResponse({
        "setup_completed": setup_done,
        "hardware_info": settings.get("hardware_info"),
        "model_name": settings.get("model_name"),
        "model_verified": settings.get("model_verified", False),
        "content_topic": settings.get("content_topic"),
    })


async def setup_detect_hardware(request: Request) -> JSONResponse:
    """POST /api/setup/detect -- detect GPU and VRAM."""
    from cohort.local.config import MODEL_DESCRIPTIONS, get_model_for_vram
    from cohort.local.detect import detect_hardware

    hw = detect_hardware()
    model = get_model_for_vram(hw.vram_mb)
    desc = MODEL_DESCRIPTIONS.get(model, {})

    # Build GPU list for multi-GPU display
    gpus_list = [
        {"index": g.index, "name": g.name, "vram_mb": g.vram_mb}
        for g in hw.gpus
    ]

    # Persist hardware info
    settings = _load_settings()
    settings["hardware_info"] = {
        "gpu_name": hw.gpu_name,
        "vram_mb": hw.vram_mb,
        "cpu_only": hw.cpu_only,
        "platform": hw.platform,
        "gpus": gpus_list,
        "total_vram_mb": hw.total_vram_mb,
    }
    settings["model_name"] = model
    _save_settings(settings)

    return JSONResponse({
        "gpu_name": hw.gpu_name,
        "vram_mb": hw.vram_mb,
        "cpu_only": hw.cpu_only,
        "platform": hw.platform,
        "recommended_model": model,
        "model_size": desc.get("size", "unknown"),
        "model_summary": desc.get("summary", ""),
        "gpus": gpus_list,
        "total_vram_mb": hw.total_vram_mb,
    })


async def setup_check_ollama(request: Request) -> JSONResponse:
    """POST /api/setup/check-ollama -- check if Ollama is running."""
    import shutil

    from cohort.local.ollama import OllamaClient

    client = OllamaClient(timeout=5)
    running = client.health_check()
    on_path = shutil.which("ollama") is not None
    models = client.list_models() if running else []

    settings = _load_settings()
    recommended = settings.get("model_name", "")
    model_installed = False
    if recommended and models:
        base = recommended.split(":")[0]
        model_installed = any(
            m == recommended or m.startswith(base + ":") for m in models
        )

    return JSONResponse({
        "running": running,
        "on_path": on_path,
        "models": models,
        "model_installed": model_installed,
        "platform": __import__("platform").system().lower(),
    })


async def setup_pull_model(request: Request) -> JSONResponse:
    """POST /api/setup/pull-model -- start model pull with Socket.IO progress."""
    settings = _load_settings()
    model = settings.get("model_name", "")
    if not model:
        return JSONResponse({"error": "No model selected"}, status_code=400)

    from cohort.socketio_events import sio

    asyncio.create_task(_stream_model_pull(model, sio))
    return JSONResponse({"status": "pulling", "model": model})


async def _stream_model_pull(model: str, sio_server: Any) -> None:
    """Background task: stream Ollama model pull via Socket.IO events."""
    import json as _json
    import queue
    import threading
    import urllib.request

    url = "http://127.0.0.1:11434/api/pull"
    body = _json.dumps({"model": model, "stream": True}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    progress_queue: queue.Queue[dict | None] = queue.Queue()

    def _pull_thread() -> None:
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        data = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    progress_queue.put(data)
        except Exception as exc:
            progress_queue.put({"error": str(exc)})
        finally:
            progress_queue.put(None)

    t = threading.Thread(target=_pull_thread, daemon=True)
    t.start()

    while True:
        try:
            data = await asyncio.to_thread(progress_queue.get, timeout=2.0)
        except Exception:
            await asyncio.sleep(0.5)
            continue

        if data is None:
            settings = _load_settings()
            settings["model_installed"] = True
            _save_settings(settings)
            await sio_server.emit("setup:complete", {"model": model, "success": True})
            break

        if "error" in data:
            await sio_server.emit(
                "setup:complete",
                {"model": model, "success": False, "error": data["error"]},
            )
            break

        await sio_server.emit("setup:progress", {
            "status": data.get("status", ""),
            "total": data.get("total", 0),
            "completed": data.get("completed", 0),
        })


async def setup_verify_model(request: Request) -> JSONResponse:
    """POST /api/setup/verify -- test inference with the selected model."""
    from cohort.local.ollama import OllamaClient

    settings = _load_settings()
    model = settings.get("model_name", "")
    if not model:
        return JSONResponse({"error": "No model selected"}, status_code=400)

    client = OllamaClient(timeout=120)
    result = await asyncio.to_thread(
        client.generate,
        model=model,
        prompt="What makes a good code review? Answer in two sentences.",
        temperature=0.3,
    )

    if result and result.text.strip():
        settings["model_verified"] = True
        _save_settings(settings)
        return JSONResponse({
            "success": True,
            "text": result.text.strip(),
            "elapsed_seconds": result.elapsed_seconds,
            "model": model,
        })

    return JSONResponse({
        "success": False,
        "error": "Model produced no output. First run can be slow -- try again.",
    })


async def setup_get_topics(request: Request) -> JSONResponse:
    """GET /api/setup/topics -- return available content pipeline topics."""
    from cohort.local.setup import TOPIC_FEEDS

    topics = {}
    for topic, feeds in TOPIC_FEEDS.items():
        topics[topic] = [{"name": f["name"], "url": f["url"]} for f in feeds]
    return JSONResponse({"topics": topics})


async def setup_save_config(request: Request) -> JSONResponse:
    """POST /api/setup/save-config -- save content config + mark setup complete."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    feeds = body.get("feeds", [])
    topic = body.get("topic", "")

    if feeds:
        config = {
            "feeds": feeds,
            "topic": topic,
            "check_interval_minutes": 60,
            "max_articles_per_feed": 10,
        }
        config_path = Path(_resolved_data_dir) / "content_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    settings = _load_settings()
    settings["setup_completed"] = True
    settings["content_topic"] = topic
    _save_settings(settings)

    return JSONResponse({"success": True})


async def setup_check_mcp(request: Request) -> JSONResponse:
    """POST /api/setup/check-mcp -- check MCP deps, Ollama, and model availability."""
    import shutil

    # 1. Check MCP package imports (fastmcp, mcp)
    deps: dict[str, bool] = {}
    for pkg in ("fastmcp", "mcp"):
        try:
            __import__(pkg)
            deps[pkg] = True
        except ImportError:
            deps[pkg] = False

    all_deps_ok = all(deps.values())
    missing = [pkg for pkg, ok in deps.items() if not ok]

    # 2. Check Ollama reachability
    ollama_ok = False
    try:
        from cohort.local.ollama import OllamaClient
        client = OllamaClient(timeout=5)
        ollama_ok = client.health_check()
    except Exception:
        pass

    # 3. Check model availability
    model_name = ""
    model_installed = False
    settings = _load_settings()
    model_name = settings.get("model_name", "")
    if ollama_ok and model_name:
        try:
            from cohort.local.ollama import OllamaClient
            client = OllamaClient(timeout=5)
            models = client.list_models()
            base = model_name.split(":")[0]
            model_installed = any(
                m == model_name or m.startswith(base + ":") for m in models
            )
        except Exception:
            pass

    # 4. Check if MCP config already written
    cohort_root = Path(__file__).resolve().parent.parent
    claude_settings_path = cohort_root / ".claude" / "settings.local.json"
    mcp_configured = False
    if claude_settings_path.exists():
        try:
            existing = json.loads(claude_settings_path.read_text(encoding="utf-8"))
            mcp_configured = "local_llm" in existing.get("mcpServers", {})
        except Exception:
            pass

    return JSONResponse({
        "deps": deps,
        "all_deps_ok": all_deps_ok,
        "missing": missing,
        "ollama_ok": ollama_ok,
        "model_name": model_name,
        "model_installed": model_installed,
        "mcp_configured": mcp_configured,
        "platform": __import__("platform").system().lower(),
    })


async def setup_write_mcp_config(request: Request) -> JSONResponse:
    """POST /api/setup/write-mcp-config -- write MCP server config to .claude/settings.local.json."""
    mcp_server_config = {
        "mcpServers": {
            "local_llm": {
                "command": "python",
                "args": ["-m", "cohort.mcp.local_llm_server"],
            }
        }
    }

    cohort_root = Path(__file__).resolve().parent.parent
    claude_dir = cohort_root / ".claude"
    settings_path = claude_dir / "settings.local.json"

    try:
        existing: dict = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}

        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["local_llm"] = mcp_server_config["mcpServers"]["local_llm"]

        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(existing, indent=2) + "\n",
            encoding="utf-8",
        )
        return JSONResponse({"success": True})
    except OSError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


async def setup_detect_claude(request: Request) -> JSONResponse:
    """POST /api/setup/detect-claude -- auto-detect Claude CLI and agents root."""
    import shutil
    import subprocess as _sp

    # 1. Find Claude CLI on PATH
    claude_path = shutil.which("claude")
    found = claude_path is not None

    # 2. If found, get version
    version = ""
    if found:
        try:
            result = _sp.run(
                [claude_path, "--version"],
                capture_output=True, text=True, timeout=10,
                shell=True, encoding="utf-8", errors="replace",
            )
            if result.returncode == 0:
                version = result.stdout.strip() or result.stderr.strip()
        except Exception:
            pass

    # 3. Auto-detect agents root (look for agents/ dir relative to Cohort)
    agents_root = ""
    cohort_root = Path(__file__).resolve().parent.parent
    if (cohort_root / "agents").is_dir():
        agents_root = str(cohort_root)

    # 4. Return existing saved values for pre-population
    settings = _load_settings()

    return JSONResponse({
        "found": found,
        "claude_path": claude_path or "",
        "version": version,
        "agents_root_detected": agents_root,
        "existing_claude_cmd": settings.get("claude_cmd", ""),
        "existing_agents_root": settings.get("agents_root", ""),
        "platform": __import__("platform").system().lower(),
    })


# =====================================================================
# Agent persistence helpers
# =====================================================================

_resolved_data_dir: str = "data"


def _load_agents_from_disk(data_dir: str) -> dict[str, dict[str, Any]]:
    """Load agent configs from {data_dir}/agents.json."""
    global _resolved_data_dir  # noqa: PLW0603
    _resolved_data_dir = data_dir
    agents_path = Path(data_dir) / "agents.json"
    if not agents_path.exists():
        return {}
    try:
        with open(agents_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load agents.json: %s", exc)
        return {}


def _save_agents_to_disk() -> None:
    """Persist current agent registry to agents.json."""
    if _data_layer is None:
        return
    agents_path = Path(_resolved_data_dir) / "agents.json"
    try:
        agents_path.parent.mkdir(parents=True, exist_ok=True)
        with open(agents_path, "w", encoding="utf-8") as f:
            json.dump(_data_layer._agents, f, indent=2)
    except OSError as exc:
        logger.warning("Failed to save agents.json: %s", exc)


# =====================================================================
# Tool dashboard endpoints
# =====================================================================

def _load_tools_config() -> dict[str, Any]:
    """Load the full cohort_tools.json config (descriptions, health endpoints, etc.)."""
    if _settings_path is None:
        return {}
    filter_path = _settings_path.parent / "cohort_tools.json"
    if not filter_path.exists():
        return {}
    try:
        with open(filter_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


async def get_service_status(request: Request) -> JSONResponse:
    """GET /api/service-status/{service_id} -- proxy health check to a service."""
    import urllib.request

    service_id = request.path_params["service_id"]
    tools_cfg = _load_tools_config()
    endpoints = tools_cfg.get("health_endpoints", {})
    url = endpoints.get(service_id)

    if not url:
        return JSONResponse({"status": "unknown", "detail": "No health endpoint configured"})

    # Only allow localhost URLs
    if "127.0.0.1" not in url and "localhost" not in url:
        return JSONResponse({"status": "unknown", "detail": "Non-localhost endpoint rejected"})

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = "up" if resp.status == 200 else "down"
            try:
                body = json.loads(resp.read().decode("utf-8"))
            except Exception:
                body = {}
            return JSONResponse({"status": status, "detail": body})
    except Exception:
        return JSONResponse({"status": "down", "detail": "Connection failed"})


async def get_tool_config(request: Request) -> JSONResponse:
    """GET /api/tool-config/{tool_id} -- return tool description and metadata."""
    tool_id = request.path_params["tool_id"]
    tools_cfg = _load_tools_config()
    descriptions = tools_cfg.get("descriptions", {})
    display_names = tools_cfg.get("display_names", {})
    has_health = tool_id in tools_cfg.get("health_endpoints", {})

    return JSONResponse({
        "id": tool_id,
        "name": display_names.get(tool_id, tool_id.replace("_", " ").title()),
        "description": descriptions.get(tool_id, ""),
        "has_health_endpoint": has_health,
    })


def _tool_config_values_path() -> str:
    return os.path.join(_resolved_data_dir, "tool_config_values.json")


def _load_tool_config_values() -> dict:
    """Load persisted tool config overrides."""
    p = _tool_config_values_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_tool_config_values(data: dict) -> None:
    with open(_tool_config_values_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


async def get_tool_config_values(request: Request) -> JSONResponse:
    """GET /api/tool-config/{tool_id}/values -- return saved config overrides."""
    tool_id = request.path_params["tool_id"]
    all_values = _load_tool_config_values()
    return JSONResponse(all_values.get(tool_id, {}))


async def put_tool_config_value(request: Request) -> JSONResponse:
    """PUT /api/tool-config/{tool_id}/values -- save a config value."""
    tool_id = request.path_params["tool_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    key = body.get("key", "").strip()
    value = body.get("value", "").strip()
    if not key:
        return JSONResponse({"error": "key is required"}, status_code=400)

    all_values = _load_tool_config_values()
    if tool_id not in all_values:
        all_values[tool_id] = {}
    all_values[tool_id][key] = value
    _save_tool_config_values(all_values)

    return JSONResponse({"ok": True, "tool_id": tool_id, "key": key, "value": value})


async def llm_list_models(request: Request) -> JSONResponse:
    """GET /api/llm/models -- list installed Ollama models with sizes."""
    import urllib.request

    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = []
            for m in data.get("models", []):
                size_bytes = m.get("size", 0)
                if size_bytes > 1_073_741_824:
                    size_str = f"{size_bytes / 1_073_741_824:.1f} GB"
                elif size_bytes > 1_048_576:
                    size_str = f"{size_bytes / 1_048_576:.0f} MB"
                else:
                    size_str = f"{size_bytes} B"
                models.append({
                    "name": m.get("name", ""),
                    "size": size_str,
                    "size_bytes": size_bytes,
                    "modified_at": m.get("modified_at", ""),
                    "family": m.get("details", {}).get("family", ""),
                    "parameter_size": m.get("details", {}).get("parameter_size", ""),
                    "quantization": m.get("details", {}).get("quantization_level", ""),
                })
            return JSONResponse({"status": "up", "models": models})
    except Exception:
        return JSONResponse({"status": "down", "models": []})


async def llm_pull_model(request: Request) -> JSONResponse:
    """POST /api/llm/pull -- pull an Ollama model (streams progress via Socket.IO)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    model = body.get("model", "").strip()
    if not model:
        return JSONResponse({"error": "model is required"}, status_code=400)

    # Reuse existing streaming pull mechanism
    asyncio.create_task(_stream_model_pull(model, sio))
    return JSONResponse({"status": "pulling", "model": model})


async def llm_delete_model(request: Request) -> JSONResponse:
    """DELETE /api/llm/models/{name} -- delete an Ollama model."""
    import urllib.request

    model_name = request.path_params["name"]
    if not model_name:
        return JSONResponse({"error": "model name is required"}, status_code=400)

    try:
        body = json.dumps({"model": model_name}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/delete",
            data=body,
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return JSONResponse({"status": "deleted", "model": model_name})
            return JSONResponse({"error": f"Ollama returned {resp.status}"}, status_code=500)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# =====================================================================
# Tool Dashboard Data Endpoints
# =====================================================================

def _boss_data_dir() -> Path:
    """Resolve BOSS data directory.  Tries (in order):
    1. BOSS_DATA_DIR env var
    2. Sibling G:/BOSS/data
    3. ../../BOSS/data relative to cohort root
    """
    env = os.environ.get("BOSS_DATA_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p

    for candidate in [Path("G:/BOSS/data"), Path(__file__).resolve().parent.parent.parent / "BOSS" / "data"]:
        if candidate.is_dir():
            return candidate

    return Path("G:/BOSS/data")  # fallback even if missing


def _read_json_safe(path: Path) -> dict | list | None:
    """Read a JSON file, returning None on any error."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


async def get_health_monitor_state(request: Request) -> JSONResponse:
    """GET /api/health-monitor/state -- return full health monitor state."""
    state_path = _boss_data_dir() / "health_monitor" / "state.json"
    data = _read_json_safe(state_path)
    if data is None:
        return JSONResponse({"error": "Health monitor state not available", "services": [], "alerts": {}})
    return JSONResponse(data)


async def get_health_monitor_alerts(request: Request) -> JSONResponse:
    """GET /api/health-monitor/alerts -- return recent alerts from today's log."""
    limit = int(request.query_params.get("limit", "20"))
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = _boss_data_dir() / "health_monitor" / "logs" / f"{today}_alerts.log"

    alerts = []
    try:
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Parse lines like: [2026-03-04 10:49:59] [!] ALERT: Service DOWN: SMACK Chat
                    alerts.append({"raw": line})
    except Exception:
        pass

    # Return last N alerts (newest last)
    return JSONResponse({"alerts": alerts[-limit:], "date": today})


async def get_scheduler_recent_runs(request: Request) -> JSONResponse:
    """GET /api/scheduler/recent-runs -- return recent scheduler runs."""
    task = request.query_params.get("task", "")
    source = request.query_params.get("source", "scheduler")  # scheduler or content_monitor
    limit = int(request.query_params.get("limit", "10"))

    now = datetime.now()
    month_str = now.strftime("%Y%m")

    if source == "content_monitor":
        runs_path = _boss_data_dir() / "content_monitor_logs" / f"runs_{month_str}.json"
    else:
        runs_path = _boss_data_dir() / "scheduler_logs" / f"runs_{month_str}.json"

    data = _read_json_safe(runs_path)
    if not isinstance(data, list):
        return JSONResponse({"runs": []})

    if task:
        data = [r for r in data if r.get("task") == task]

    return JSONResponse({"runs": data[-limit:]})


async def get_comms_recent_activity(request: Request) -> JSONResponse:
    """GET /api/comms/recent-activity -- return recent outbound communication logs."""
    limit = int(request.query_params.get("limit", "10"))
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = _boss_data_dir() / "comms_service" / "webhook_logs" / f"{today}.json"

    data = _read_json_safe(log_path)
    if not isinstance(data, list):
        return JSONResponse({"activity": [], "date": today})

    return JSONResponse({"activity": data[-limit:], "date": today})


async def get_intel_recent_articles(request: Request) -> JSONResponse:
    """GET /api/intel/recent-articles -- return recent tech intel articles."""
    limit = int(request.query_params.get("limit", "10"))
    db_path = _boss_data_dir() / "tech_intel" / "articles_db.json"

    data = _read_json_safe(db_path)
    if not isinstance(data, list):
        return JSONResponse({"articles": []})

    # Sort by date descending, return last N
    try:
        data.sort(key=lambda a: a.get("fetched_at", a.get("published", "")), reverse=True)
    except Exception:
        pass

    return JSONResponse({"articles": data[:limit]})


async def get_llm_running(request: Request) -> JSONResponse:
    """GET /api/llm/running -- return currently loaded Ollama models (via /api/ps)."""
    import urllib.request

    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            models = body.get("models", [])
            result = []
            for m in models:
                size_bytes = m.get("size", 0)
                vram_bytes = m.get("size_vram", 0)
                result.append({
                    "name": m.get("name", ""),
                    "size_bytes": size_bytes,
                    "vram_bytes": vram_bytes,
                    "expires_at": m.get("expires_at", ""),
                })
            return JSONResponse({"status": "up", "models": result})
    except Exception:
        return JSONResponse({"status": "down", "models": []})


async def get_llm_model_info(request: Request) -> JSONResponse:
    """GET /api/llm/model-info/{name} -- return Ollama model details."""
    import urllib.request

    model_name = request.path_params["name"]
    try:
        body = json.dumps({"model": model_name}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/show",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return JSONResponse({
                "name": model_name,
                "modelfile": data.get("modelfile", ""),
                "parameters": data.get("parameters", ""),
                "template": data.get("template", ""),
                "details": data.get("details", {}),
                "model_info": {k: v for k, v in data.get("model_info", {}).items()
                               if not k.startswith("tokenizer")},
            })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def get_content_monitor_pipeline(request: Request) -> JSONResponse:
    """GET /api/content-monitor/pipeline-status -- aggregated content pipeline stats."""
    now = datetime.now()
    month_str = now.strftime("%Y%m")
    runs_path = _boss_data_dir() / "content_monitor_logs" / f"runs_{month_str}.json"

    data = _read_json_safe(runs_path)
    if not isinstance(data, list):
        return JSONResponse({"stages": {}})

    # Group by task, find latest of each + today's totals
    today_str = now.strftime("%Y-%m-%d")
    stages = {}
    for run in data:
        task = run.get("task", "unknown")
        ts = run.get("timestamp", "")
        result = run.get("result", {})

        if task not in stages:
            stages[task] = {"last_run": ts, "today_count": 0, "last_result": result}
        else:
            if ts > stages[task]["last_run"]:
                stages[task]["last_run"] = ts
                stages[task]["last_result"] = result

        if ts.startswith(today_str):
            stages[task]["today_count"] += 1

    return JSONResponse({"stages": stages})


async def get_social_posts(request: Request) -> JSONResponse:
    """GET /api/content-monitor/posts -- list social media post drafts."""
    status_filter = request.query_params.get("status", "")  # pending, approved, posted, rejected
    limit = int(request.query_params.get("limit", "50"))

    posts_dir = _boss_data_dir() / "comms_service" / "social_posts"
    posts = []
    try:
        if posts_dir.is_dir():
            for f in sorted(posts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        post = json.load(fh)
                        if status_filter and post.get("status") != status_filter:
                            continue
                        posts.append(post)
                        if len(posts) >= limit:
                            break
                except Exception:
                    continue
    except Exception:
        pass

    return JSONResponse({"posts": posts, "total": len(posts)})


async def update_social_post(request: Request) -> JSONResponse:
    """PATCH /api/content-monitor/posts/{post_id} -- approve or reject a social post."""
    post_id = request.path_params["post_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    action = body.get("action", "")  # approve or reject
    if action not in ("approve", "reject"):
        return JSONResponse({"error": "action must be 'approve' or 'reject'"}, status_code=400)

    posts_dir = _boss_data_dir() / "comms_service" / "social_posts"
    post_path = posts_dir / f"{post_id}.json"
    if not post_path.exists():
        return JSONResponse({"error": "Post not found"}, status_code=404)

    try:
        with open(post_path, "r", encoding="utf-8") as f:
            post = json.load(f)

        now = datetime.now().isoformat()
        if action == "approve":
            post["status"] = "approved"
            post["approved_at"] = now
            post["approved_by"] = "cohort_user"
        else:
            post["status"] = "rejected"
            post["rejected_at"] = now
            post["reject_reason"] = body.get("reason", "Rejected via dashboard")

        with open(post_path, "w", encoding="utf-8") as f:
            json.dump(post, f, indent=2)

        return JSONResponse({"ok": True, "post_id": post_id, "status": post["status"]})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def get_content_articles(request: Request) -> JSONResponse:
    """GET /api/content-monitor/articles -- list analyzed content articles."""
    limit = int(request.query_params.get("limit", "20"))
    min_score = int(request.query_params.get("min_score", "0"))

    # Check content monitor articles DB (separate from tech_intel)
    # The content monitor stores articles in its own state
    articles = []

    # Try the content monitor's articles database
    for candidate in [
        _boss_data_dir() / "content_monitor_logs" / "articles_db.json",
        _boss_data_dir() / "content_monitor" / "articles_db.json",
    ]:
        data = _read_json_safe(candidate)
        if data and isinstance(data, dict) and "articles" in data:
            articles = data["articles"]
            break
        elif isinstance(data, list):
            articles = data
            break

    # Filter by min relevance score
    if min_score > 0:
        articles = [a for a in articles
                     if (a.get("analysis", {}).get("relevance_score", 0)
                         if isinstance(a.get("analysis"), dict)
                         else a.get("relevance_score", 0)) >= min_score]

    # Sort by fetch date descending
    try:
        articles.sort(key=lambda a: a.get("fetched_date", a.get("published_date", "")), reverse=True)
    except Exception:
        pass

    return JSONResponse({"articles": articles[:limit], "total_available": len(articles)})


async def get_content_config(request: Request) -> JSONResponse:
    """GET /api/content-monitor/config -- return content monitor configuration."""
    cfg_path = _boss_data_dir() / "content_monitor_config.json"
    data = _read_json_safe(cfg_path)
    if data is None:
        return JSONResponse({"error": "Config not found"})

    # Return safe subset (no internal paths)
    sources = data.get("sources", {})
    feed_names = []
    for section in sources.values():
        if isinstance(section, list):
            for s in section:
                if isinstance(s, dict):
                    feed_names.append(s.get("name", "unknown"))
                elif isinstance(s, str):
                    feed_names.append(s)

    schedules = data.get("schedules", {})
    schedule_summary = {}
    for task_name, task_cfg in schedules.items():
        if isinstance(task_cfg, dict):
            schedule_summary[task_name] = {
                "enabled": task_cfg.get("enabled", True),
                "description": task_cfg.get("description", ""),
                "interval_hours": task_cfg.get("interval_hours"),
                "time": task_cfg.get("time"),
                "day": task_cfg.get("day"),
                "days": task_cfg.get("days"),
            }

    safety = data.get("safety", {})

    return JSONResponse({
        "project": data.get("project", ""),
        "description": data.get("description", ""),
        "enabled": data.get("enabled", False),
        "feed_names": feed_names,
        "schedules": schedule_summary,
        "safety_limits": safety,
        "post_settings": data.get("post_settings", {}),
        "notifications": data.get("notifications", {}),
    })


async def web_search_test(request: Request) -> JSONResponse:
    """POST /api/web-search/test -- proxy a test search to the web search service."""
    import urllib.request

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    limit = body.get("limit", 3)

    try:
        payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8005/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": f"Web search service unavailable: {exc}"})


async def youtube_search_test(request: Request) -> JSONResponse:
    """POST /api/youtube/test -- proxy a test search to the YouTube service."""
    import urllib.request

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    limit = body.get("limit", 3)

    try:
        payload = json.dumps({"query": query, "max_results": limit}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8002/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": f"YouTube service unavailable: {exc}"})


def _claude_cli_request(prompt: str, timeout: int = 120) -> str:
    """Send a prompt to Claude Code CLI and return the text response.

    Uses ``claude --print`` with stdin delivery (proven pattern from
    agent_router.py).  Strips CLAUDECODE env vars to prevent nested
    conflicts and runs in a temp directory to avoid CLAUDE.md context.
    """
    import shutil
    import tempfile

    settings = _load_settings()
    claude_cmd = settings.get("claude_cmd", "") or shutil.which("claude") or "claude"

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}

    result = subprocess.run(
        [claude_cmd, "--print", "-"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=tempfile.gettempdir(),
        env=env,
        shell=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:300]
        raise RuntimeError(f"Claude CLI failed (rc={result.returncode}): {stderr}")
    return (result.stdout or "").strip()


def _claude_cli_vision_request(
    prompt: str, image_bytes: bytes, filename: str, timeout: int = 120
) -> str:
    """Send an image + prompt to Claude Code CLI for vision analysis.

    Writes the image to a temp file, then passes the prompt via stdin
    referencing the temp file path so Claude can read it.
    """
    import shutil
    import tempfile

    settings = _load_settings()
    claude_cmd = settings.get("claude_cmd", "") or shutil.which("claude") or "claude"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}

    # Write image to temp file so Claude CLI can access it
    ext = Path(filename).suffix.lower() or ".png"
    tmp_dir = tempfile.gettempdir()
    tmp_path = Path(tmp_dir) / f"cohort_vision_{os.getpid()}{ext}"
    try:
        tmp_path.write_bytes(image_bytes)
        full_prompt = (
            f"I have an image file at: {tmp_path}\n\n"
            f"Please analyze this image. {prompt}"
        )
        result = subprocess.run(
            [claude_cmd, "--print", "-"],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tmp_dir,
            env=env,
            shell=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:300]
            raise RuntimeError(f"Claude CLI vision failed (rc={result.returncode}): {stderr}")
        return (result.stdout or "").strip()
    finally:
        tmp_path.unlink(missing_ok=True)


async def doc_summarize(request: Request) -> JSONResponse:
    """POST /api/doc-processor/summarize -- summarize text via local Ollama or Claude CLI."""
    from cohort.local.ollama import OllamaClient

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    if len(text) > 50000:
        return JSONResponse({"error": "Text too long (max 50000 chars)"}, status_code=400)

    mode = body.get("mode", "summary")  # summary, outline, extract_key_points
    model_override = body.get("model", "")

    # Use explicit model from request, else configured model from settings
    model = model_override.strip() if model_override and model_override.strip() else _get_configured_model()

    prompts = {
        "summary": (
            "Summarize the following text in 3-5 concise bullet points. "
            "Focus on key facts, decisions, and actionable items.\n\n"
        ),
        "outline": (
            "Create a structured outline of the following text with headings "
            "and sub-points. Use markdown formatting.\n\n"
        ),
        "extract_key_points": (
            "Extract all key facts, numbers, names, dates, and actionable items "
            "from the following text. Present as a bullet list.\n\n"
        ),
    }
    prompt = prompts.get(mode, prompts["summary"]) + f"TEXT:\n{text[:30000]}"

    input_words = len(text.split())
    engine = body.get("engine", "ollama")
    import time
    t0 = time.time()

    if engine == "smart":
        # Route through Claude Code CLI
        try:
            response_text = await asyncio.to_thread(_claude_cli_request, prompt)
            elapsed = round(time.time() - t0, 1)
            if response_text:
                output_words = len(response_text.split())
                compression = round((1 - output_words / max(input_words, 1)) * 100, 1) if input_words > 0 else 0
                return JSONResponse({
                    "ok": True,
                    "summary": response_text,
                    "model": "claude",
                    "mode": mode,
                    "stats": {
                        "input_words": input_words,
                        "input_chars": len(text),
                        "output_words": output_words,
                        "compression_pct": compression,
                        "elapsed_seconds": elapsed,
                    },
                })
            return JSONResponse({"ok": False, "error": "Claude returned no output."})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Claude CLI error: {exc}"})

    # Default: Ollama
    client = OllamaClient(timeout=180)
    try:
        result = await asyncio.to_thread(
            client.generate,
            model=model,
            prompt=prompt,
            temperature=0.3,
        )
        elapsed = round(time.time() - t0, 1)
        if result and result.text:
            output_words = len(result.text.split())
            compression = round((1 - output_words / max(input_words, 1)) * 100, 1) if input_words > 0 else 0
            return JSONResponse({
                "ok": True,
                "summary": result.text,
                "model": model,
                "mode": mode,
                "stats": {
                    "input_words": input_words,
                    "input_chars": len(text),
                    "output_words": output_words,
                    "compression_pct": compression,
                    "tokens_in": getattr(result, "tokens_in", 0),
                    "tokens_out": getattr(result, "tokens_out", 0),
                    "elapsed_seconds": elapsed,
                },
            })
        return JSONResponse({"ok": False, "error": "Model returned no output. Is Ollama running?"})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


def _get_configured_model() -> str:
    """Read model name from Cohort settings."""
    settings_path = _PACKAGE_DIR.parent / "data" / "settings.json"
    try:
        if settings_path.exists():
            s = json.loads(settings_path.read_text(encoding="utf-8"))
            return s.get("model_name", DEFAULT_MODEL)
    except Exception:
        pass
    return DEFAULT_MODEL


def _extract_text_from_file(file_bytes: bytes, filename: str, content_type: str) -> dict:
    """Extract text from various file formats.  Returns dict with keys:
    text, format, pages (optional), error (optional).
    """
    ext = Path(filename).suffix.lower() if filename else ""
    result: dict = {"text": "", "format": ext or content_type, "pages": None, "error": None}

    try:
        # ── PDF ──
        if ext == ".pdf" or content_type == "application/pdf":
            import pdfplumber
            import io
            pages_text = []
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                result["pages"] = len(pdf.pages)
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    if t.strip():
                        pages_text.append(t)
            result["text"] = "\n\n".join(pages_text)
            result["format"] = "pdf"

        # ── Word (.docx) ──
        elif ext == ".docx" or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            import docx
            import io
            doc = docx.Document(io.BytesIO(file_bytes))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            result["text"] = "\n\n".join(paragraphs)
            result["format"] = "docx"

        # ── Excel (.xlsx) ──
        elif ext in (".xlsx", ".xls") or "spreadsheet" in content_type:
            import openpyxl
            import io
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            sheets_text = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    sheets_text.append(f"Sheet: {ws.title}\n" + "\n".join(rows[:200]))
            wb.close()
            result["text"] = "\n\n".join(sheets_text)
            result["format"] = "xlsx"

        # ── HTML ──
        elif ext in (".html", ".htm") or "html" in content_type:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(file_bytes, "lxml")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            result["text"] = soup.get_text(separator="\n", strip=True)
            result["format"] = "html"

        # ── CSV ──
        elif ext == ".csv" or "csv" in content_type:
            import csv
            import io
            text = file_bytes.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text))
            rows = []
            for i, row in enumerate(reader):
                if i > 200:
                    rows.append("... (truncated)")
                    break
                rows.append(" | ".join(row))
            result["text"] = "\n".join(rows)
            result["format"] = "csv"

        # ── JSON ──
        elif ext == ".json" or "json" in content_type:
            parsed = json.loads(file_bytes.decode("utf-8", errors="replace"))
            result["text"] = json.dumps(parsed, indent=2)[:30000]
            result["format"] = "json"

        # ── Markdown / plain text / code ──
        elif ext in (".md", ".txt", ".log", ".py", ".js", ".ts", ".css", ".yaml", ".yml",
                      ".toml", ".ini", ".cfg", ".xml", ".sql", ".sh", ".bat", ".ps1",
                      ".rs", ".go", ".java", ".c", ".cpp", ".h", ".rb", ".php") \
                or "text/" in content_type:
            result["text"] = file_bytes.decode("utf-8", errors="replace")
            result["format"] = ext.lstrip(".") or "text"

        else:
            # Try as plain text as fallback
            try:
                result["text"] = file_bytes.decode("utf-8", errors="replace")
                result["format"] = "text"
            except Exception:
                result["error"] = f"Unsupported file type: {ext or content_type}"

    except Exception as exc:
        result["error"] = f"Extraction failed: {exc}"

    return result


def _is_image_type(filename: str, content_type: str) -> bool:
    ext = Path(filename).suffix.lower() if filename else ""
    if ext == ".svg" or content_type == "image/svg+xml":
        return False  # SVG is XML text — route to text extraction, not vision
    return ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif") \
        or content_type.startswith("image/")


def _is_video_type(filename: str, content_type: str) -> bool:
    ext = Path(filename).suffix.lower() if filename else ""
    return ext in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v") \
        or content_type.startswith("video/")


def _extract_video_frames(file_bytes: bytes, max_frames: int = 4) -> list[bytes]:
    """Extract evenly-spaced keyframes from video as JPEG bytes."""
    import cv2
    import tempfile
    import numpy as np

    frames = []
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(file_bytes)
        tmp.close()

        cap = cv2.VideoCapture(tmp.name)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return frames

        # Pick evenly spaced frame indices
        indices = [int(i * total / (max_frames + 1)) for i in range(1, max_frames + 1)]
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                # Resize to max 512px on longest edge to keep payload reasonable
                h, w = frame.shape[:2]
                if max(h, w) > 512:
                    scale = 512 / max(h, w)
                    frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                frames.append(buf.tobytes())
        cap.release()
    except Exception:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    return frames


def _ollama_vision_request(model: str, prompt: str, image_b64_list: list[str],
                           timeout: int = 180) -> dict | None:
    """Send a vision request to Ollama /api/chat with images."""
    import urllib.request

    payload = json.dumps({
        "model": model,
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": image_b64_list,
        }],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1024},
        "keep_alive": "2m",
        "think": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        error_detail = str(exc)
        # Try to read error body from Ollama
        if hasattr(exc, 'read'):
            try:
                error_detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
        print(f"[doc-processor] Vision request failed for model={model}: {error_detail}")
        return {"_error": error_detail}


async def doc_process_file(request: Request) -> JSONResponse:
    """POST /api/doc-processor/process -- universal file processor.

    Accepts multipart file upload.  Extracts text from documents, analyzes
    images via vision model, extracts video keyframes and describes them.
    Then summarizes the content.
    """
    import base64
    import time

    form = await request.form()
    upload = form.get("file")
    if not upload:
        return JSONResponse({"error": "No file uploaded"}, status_code=400)

    file_bytes = await upload.read()
    filename = upload.filename or "unknown"
    content_type = upload.content_type or "application/octet-stream"
    mode = form.get("mode", "summary")
    model_override = form.get("model", "")
    engine = form.get("engine", "ollama")

    if len(file_bytes) > 50 * 1024 * 1024:  # 50MB limit
        return JSONResponse({"error": "File too large (max 50MB)"}, status_code=400)

    model = model_override.strip() if model_override and model_override.strip() else _get_configured_model()
    t0 = time.time()

    # ── Image handling ──
    if _is_image_type(filename, content_type):
        prompts = {
            "summary": "Describe this image in detail. What does it contain? List all notable elements, text, diagrams, charts, or information visible.",
            "outline": "Create a structured breakdown of everything visible in this image. Use headings and bullet points.",
            "extract_key_points": "Extract all text, numbers, labels, data points, and key information visible in this image. Present as a bullet list.",
            "image": "Describe what this image depicts. Include the subject, style, colors, composition, mood, and any text or symbols visible. Be precise in your identifications — note specific features that distinguish items from similar alternatives. If you can identify makes, models, brands, species, varieties, or specific types, explain what visual evidence supports your identification rather than guessing. When uncertain, describe what you observe and state what it could be.",
        }
        prompt = prompts.get(mode, prompts["summary"])

        if engine == "smart":
            try:
                response_text = await asyncio.to_thread(
                    _claude_cli_vision_request, prompt, file_bytes, filename
                )
                elapsed = round(time.time() - t0, 1)
                if response_text:
                    return JSONResponse({
                        "ok": True,
                        "summary": response_text,
                        "model": "claude",
                        "mode": mode,
                        "file_type": "image",
                        "filename": filename,
                        "stats": {
                            "file_size": len(file_bytes),
                            "output_words": len(response_text.split()),
                            "elapsed_seconds": elapsed,
                        },
                    })
                return JSONResponse({"ok": False, "error": "Claude returned no output for image."})
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Claude CLI vision error: {exc}"})

        # Ollama vision
        img_b64 = base64.b64encode(file_bytes).decode()
        vision_result = await asyncio.to_thread(
            _ollama_vision_request, model, prompt, [img_b64]
        )

        elapsed = round(time.time() - t0, 1)
        if vision_result and "_error" not in vision_result:
            msg = vision_result.get("message", {})
            text = msg.get("content", "")
            if text:
                return JSONResponse({
                    "ok": True,
                    "summary": text,
                    "model": model,
                    "mode": mode,
                    "file_type": "image",
                    "filename": filename,
                    "stats": {
                        "file_size": len(file_bytes),
                        "output_words": len(text.split()),
                        "elapsed_seconds": elapsed,
                    },
                })
        err_detail = (vision_result or {}).get("_error", "") if isinstance(vision_result, dict) else ""
        err_msg = f"Vision model '{model}' failed to process image."
        if err_detail:
            err_msg += f" Error: {err_detail[:300]}"
        return JSONResponse({"ok": False, "error": err_msg})

    # ── Video handling: extract keyframes ──
    if _is_video_type(filename, content_type):
        frames = await asyncio.to_thread(_extract_video_frames, file_bytes, 6)
        if not frames:
            return JSONResponse({"ok": False, "error": "Could not extract frames from video. Is the format supported?"})

        prompts = {
            "summary": f"These are frames from a video called '{filename}'. Describe what happens in the video. Provide a coherent narrative summary.",
            "outline": f"These are frames from a video. Create a structured timeline of what happens at each stage.",
            "extract_key_points": f"These are frames from a video. Extract all key information: actions, text visible, objects, people, settings, and any data shown.",
            "image": f"These are frames from a video. Describe what is visually depicted: subjects, actions, colors, style, setting, and composition.",
        }
        prompt = prompts.get(mode, prompts["summary"])

        if engine == "smart":
            # Send first keyframe to Claude CLI for analysis
            try:
                response_text = await asyncio.to_thread(
                    _claude_cli_vision_request, prompt, frames[0], "frame.png"
                )
                elapsed = round(time.time() - t0, 1)
                if response_text:
                    return JSONResponse({
                        "ok": True,
                        "summary": response_text,
                        "model": "claude",
                        "mode": mode,
                        "file_type": "video",
                        "filename": filename,
                        "stats": {
                            "file_size": len(file_bytes),
                            "frames_extracted": len(frames),
                            "output_words": len(response_text.split()),
                            "elapsed_seconds": elapsed,
                        },
                    })
                return JSONResponse({"ok": False, "error": "Claude returned no output for video."})
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"Claude CLI vision error: {exc}"})

        # Ollama vision with all frames
        frames_b64 = [base64.b64encode(f).decode() for f in frames]
        vision_result = await asyncio.to_thread(
            _ollama_vision_request, model, prompt, frames_b64, 300
        )

        elapsed = round(time.time() - t0, 1)
        if vision_result and "_error" not in vision_result:
            msg = vision_result.get("message", {})
            text = msg.get("content", "")
            if text:
                return JSONResponse({
                    "ok": True,
                    "summary": text,
                    "model": model,
                    "mode": mode,
                    "file_type": "video",
                    "filename": filename,
                    "stats": {
                        "file_size": len(file_bytes),
                        "frames_extracted": len(frames),
                        "output_words": len(text.split()),
                        "elapsed_seconds": elapsed,
                    },
                })
        err_detail = (vision_result or {}).get("_error", "") if isinstance(vision_result, dict) else ""
        err_msg = f"Vision model '{model}' failed to process video frames."
        if err_detail:
            err_msg += f" Error: {err_detail[:300]}"
        return JSONResponse({"ok": False, "error": err_msg})

    # ── Document/text handling: extract text then summarize ──
    extraction = await asyncio.to_thread(_extract_text_from_file, file_bytes, filename, content_type)
    if extraction.get("error"):
        return JSONResponse({"ok": False, "error": extraction["error"]})

    text = extraction.get("text", "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": f"No text could be extracted from {filename}"})

    # Now summarize the extracted text
    from cohort.local.ollama import OllamaClient

    # SVG files: describe the visual rather than summarizing XML code
    ext = Path(filename).suffix.lower() if filename else ""
    if ext == ".svg":
        svg_prompts = {
            "summary": (
                "This is SVG (vector graphics) source code. Describe what this image "
                "depicts based on the shapes, paths, colors, and structure in the code. "
                "Focus on the visual content, not the code syntax.\n\n"
            ),
            "outline": (
                "This is SVG source code. Create a structured breakdown of the visual "
                "elements: shapes, colors, layers, and what they form together. "
                "Describe what the image looks like, not the code.\n\n"
            ),
            "extract_key_points": (
                "This is SVG source code. Extract key visual details: colors used, "
                "shapes present, what the image depicts, dimensions, and any text "
                "or labels embedded in the graphic. Present as a bullet list.\n\n"
            ),
            "image": (
                "This is SVG source code for a vector graphic. Describe what this image "
                "depicts based on the shapes, paths, colors, and structure. What is the "
                "subject? What does it look like? Describe it as a visual, not as code.\n\n"
            ),
        }
        prompts = svg_prompts
    else:
        prompts = {
            "summary": (
                "Summarize the following text in 3-5 concise bullet points. "
                "Focus on key facts, decisions, and actionable items.\n\n"
            ),
            "outline": (
                "Create a structured outline of the following text with headings "
                "and sub-points. Use markdown formatting.\n\n"
            ),
            "extract_key_points": (
                "Extract all key facts, numbers, names, dates, and actionable items "
                "from the following text. Present as a bullet list.\n\n"
            ),
            "image": (
                "Describe any visual elements, diagrams, layouts, or imagery described "
                "or implied in this text. If it contains code for graphics (HTML/CSS, "
                "charts, UI), describe what it would look like when rendered.\n\n"
            ),
        }
    prompt = prompts.get(mode, prompts["summary"]) + f"TEXT:\n{text[:30000]}"
    input_words = len(text.split())

    if engine == "smart":
        try:
            response_text = await asyncio.to_thread(_claude_cli_request, prompt)
            elapsed = round(time.time() - t0, 1)
            if response_text:
                output_words = len(response_text.split())
                compression = round((1 - output_words / max(input_words, 1)) * 100, 1)
                return JSONResponse({
                    "ok": True,
                    "summary": response_text,
                    "extracted_text_preview": text[:500],
                    "model": "claude",
                    "mode": mode,
                    "file_type": extraction["format"],
                    "filename": filename,
                    "stats": {
                        "file_size": len(file_bytes),
                        "pages": extraction.get("pages"),
                        "input_words": input_words,
                        "input_chars": len(text),
                        "output_words": output_words,
                        "compression_pct": compression,
                        "elapsed_seconds": elapsed,
                    },
                })
            return JSONResponse({"ok": False, "error": "Claude returned no output."})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Claude CLI error: {exc}"})

    # Default: Ollama
    client = OllamaClient(timeout=180)
    try:
        result = await asyncio.to_thread(
            client.generate, model=model, prompt=prompt, temperature=0.3,
        )
        elapsed = round(time.time() - t0, 1)
        if result and result.text:
            output_words = len(result.text.split())
            compression = round((1 - output_words / max(input_words, 1)) * 100, 1)
            return JSONResponse({
                "ok": True,
                "summary": result.text,
                "extracted_text_preview": text[:500],
                "model": model,
                "mode": mode,
                "file_type": extraction["format"],
                "filename": filename,
                "stats": {
                    "file_size": len(file_bytes),
                    "pages": extraction.get("pages"),
                    "input_words": input_words,
                    "input_chars": len(text),
                    "output_words": output_words,
                    "compression_pct": compression,
                    "tokens_in": getattr(result, "tokens_in", 0),
                    "tokens_out": getattr(result, "tokens_out", 0),
                    "elapsed_seconds": elapsed,
                },
            })
        return JSONResponse({"ok": False, "error": "Model returned no output. Is Ollama running?"})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


async def doc_fetch_url(request: Request) -> JSONResponse:
    """POST /api/doc-processor/fetch-url -- fetch a web page and summarize it."""
    import time
    import urllib.request

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "url is required"}, status_code=400)
    if len(url) > 2000:
        return JSONResponse({"error": "URL too long (max 2000 chars)"}, status_code=400)
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "URL must start with http:// or https://"}, status_code=400)

    mode = body.get("mode", "summary")
    model_override = body.get("model", "")
    model = model_override.strip() if model_override and model_override.strip() else _get_configured_model()

    t0 = time.time()

    # Fetch the URL
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; CohortBot/1.0)",
            "Accept": "text/html,application/xhtml+xml,*/*",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            # Limit to 10MB
            data = resp.read(10 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        return JSONResponse({"ok": False, "error": f"HTTP {exc.code}: {exc.reason}"})
    except urllib.error.URLError as exc:
        return JSONResponse({"ok": False, "error": f"Could not reach URL: {exc.reason}"})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Fetch failed: {exc}"})

    if not data:
        return JSONResponse({"ok": False, "error": "URL returned empty response"})

    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.netloc or "webpage"
    path_ext = Path(parsed.path).suffix.lower() if parsed.path else ""
    pseudo_filename = f"page{path_ext}" if path_ext else "page.html"

    # ── Image URL: route to vision model ──
    if _is_image_type(pseudo_filename, content_type):
        import base64
        b64 = base64.b64encode(data).decode("ascii")
        vision_prompts = {
            "summary": "Describe this image in detail. What does it contain? List all notable elements, text, diagrams, charts, or information visible.",
            "outline": "Create a structured breakdown of everything visible in this image. Use headings and bullet points.",
            "extract_key_points": "Extract all text, numbers, labels, data points, and key information visible in this image. Present as a bullet list.",
            "image": "Describe what this image depicts. Include the subject, style, colors, composition, mood, and any text or symbols visible. Be precise in your identifications.",
        }
        vprompt = vision_prompts.get(mode, vision_prompts["summary"])
        vision_result = await asyncio.to_thread(
            _ollama_vision_request, model, vprompt, [b64], 180
        )
        elapsed = round(time.time() - t0, 1)
        if vision_result and "_error" not in vision_result:
            msg = vision_result.get("message", {})
            response_text = msg.get("content", "") if isinstance(msg, dict) else ""
            if response_text:
                return JSONResponse({
                    "ok": True,
                    "summary": response_text,
                    "model": model,
                    "mode": mode,
                    "file_type": "image",
                    "filename": domain,
                    "stats": {
                        "url": url,
                        "file_size": len(data),
                        "output_words": len(response_text.split()),
                        "elapsed_seconds": elapsed,
                    },
                })
        error_detail = ""
        if vision_result and "_error" in vision_result:
            error_detail = f": {vision_result['_error']}"
        return JSONResponse({"ok": False, "error": f"Vision model failed for image URL{error_detail}"})

    # ── Text/document URL: extract text and summarize ──
    extraction = await asyncio.to_thread(
        _extract_text_from_file, data, pseudo_filename, content_type
    )
    if extraction.get("error"):
        return JSONResponse({"ok": False, "error": extraction["error"]})

    text = extraction.get("text", "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": f"No text could be extracted from {url}"})

    # Summarize extracted text
    engine = body.get("engine", "ollama")
    prompts = {
        "summary": (
            "Summarize the following web page content in 3-5 concise bullet points. "
            "Focus on key facts, decisions, and actionable items.\n\n"
        ),
        "outline": (
            "Create a structured outline of the following web page with headings "
            "and sub-points. Use markdown formatting.\n\n"
        ),
        "extract_key_points": (
            "Extract all key facts, numbers, names, dates, and actionable items "
            "from the following web page. Present as a bullet list.\n\n"
        ),
    }
    prompt = prompts.get(mode, prompts["summary"]) + f"TEXT:\n{text[:30000]}"
    input_words = len(text.split())

    if engine == "smart":
        try:
            response_text = await asyncio.to_thread(_claude_cli_request, prompt)
            elapsed = round(time.time() - t0, 1)
            if response_text:
                output_words = len(response_text.split())
                compression = round((1 - output_words / max(input_words, 1)) * 100, 1)
                return JSONResponse({
                    "ok": True,
                    "summary": response_text,
                    "extracted_text_preview": text[:500],
                    "model": "claude",
                    "mode": mode,
                    "file_type": "url",
                    "filename": domain,
                    "stats": {
                        "url": url,
                        "content_size": len(data),
                        "input_words": input_words,
                        "input_chars": len(text),
                        "output_words": output_words,
                        "compression_pct": compression,
                        "elapsed_seconds": elapsed,
                    },
                })
            return JSONResponse({"ok": False, "error": "Claude returned no output."})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Claude CLI error: {exc}"})

    # Default: Ollama
    from cohort.local.ollama import OllamaClient
    client = OllamaClient(timeout=180)
    try:
        result = await asyncio.to_thread(
            client.generate, model=model, prompt=prompt, temperature=0.3,
        )
        elapsed = round(time.time() - t0, 1)
        if result and result.text:
            output_words = len(result.text.split())
            compression = round((1 - output_words / max(input_words, 1)) * 100, 1)
            return JSONResponse({
                "ok": True,
                "summary": result.text,
                "extracted_text_preview": text[:500],
                "model": model,
                "mode": mode,
                "file_type": "url",
                "filename": domain,
                "stats": {
                    "url": url,
                    "content_size": len(data),
                    "input_words": input_words,
                    "input_chars": len(text),
                    "output_words": output_words,
                    "compression_pct": compression,
                    "tokens_in": getattr(result, "tokens_in", 0),
                    "tokens_out": getattr(result, "tokens_out", 0),
                    "elapsed_seconds": elapsed,
                },
            })
        return JSONResponse({"ok": False, "error": "Model returned no output. Is Ollama running?"})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


_DOC_HISTORY_MAX = 50


def _doc_history_path() -> Path:
    return Path(_resolved_data_dir) / "doc_history.json"


def _load_doc_history() -> list:
    p = _doc_history_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_doc_history(history: list) -> None:
    p = _doc_history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


async def doc_history_get(request: Request) -> JSONResponse:
    """GET /api/doc-processor/history -- return saved processing history."""
    return JSONResponse({"ok": True, "history": _load_doc_history()})


async def doc_history_post(request: Request) -> JSONResponse:
    """POST /api/doc-processor/history -- append a processing result to history."""
    body = await request.json()
    entry = body.get("entry")
    if not entry:
        return JSONResponse({"ok": False, "error": "Missing entry"}, status_code=400)
    history = _load_doc_history()
    history.insert(0, entry)
    if len(history) > _DOC_HISTORY_MAX:
        history = history[:_DOC_HISTORY_MAX]
    _save_doc_history(history)
    return JSONResponse({"ok": True, "count": len(history)})


async def doc_history_delete(request: Request) -> JSONResponse:
    """DELETE /api/doc-processor/history -- clear all processing history."""
    _save_doc_history([])
    return JSONResponse({"ok": True})


async def get_comms_pending_approvals(request: Request) -> JSONResponse:
    """GET /api/comms/pending-approvals -- count and list pending social post drafts."""
    boss_data = _boss_data_dir()
    posts_dir = boss_data / "comms_service" / "social_posts"
    pending = []
    if posts_dir.is_dir():
        for fp in posts_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if data.get("status") == "pending":
                    pending.append({
                        "post_id": data.get("post_id"),
                        "platform": data.get("platform"),
                        "text": (data.get("text") or "")[:120],
                        "created_at": data.get("created_at"),
                    })
            except Exception:
                continue
    pending.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return JSONResponse({"count": len(pending), "pending": pending[:10]})


# =====================================================================
# App factory
# =====================================================================

def create_app(data_dir: str = "data") -> Starlette:
    """Create and return the ASGI application (Starlette + Socket.IO).

    Parameters
    ----------
    data_dir:
        Directory for JSON file storage.  Defaults to the
        ``COHORT_DATA_DIR`` environment variable, or ``./data``.
    """
    global _chat, _data_layer, _agent_store, _work_queue  # noqa: PLW0603

    global _settings_path  # noqa: PLW0603

    resolved_dir = os.environ.get("COHORT_DATA_DIR", data_dir)
    storage = JsonFileStorage(resolved_dir)
    _chat = ChatManager(storage)

    _settings_path = Path(resolved_dir) / "settings.json"
    saved_settings = _load_settings()

    logger.info("[OK] ChatManager initialised (data_dir=%s)", resolved_dir)

    # -- Agent store (file-backed agent configs + memory) ---------------
    from cohort.agent_registry import _LEGACY_REGISTRY, set_store
    from cohort.agent_store import set_global_store

    agents_dir_env = os.environ.get("COHORT_AGENTS_DIR")
    if agents_dir_env:
        agents_dir = Path(agents_dir_env)
    else:
        # Prefer agents_root from settings (e.g. "G:/cohort" -> "G:/cohort/agents")
        # over data_dir (e.g. "G:/cohort/data" -> "G:/cohort/data/agents")
        _agents_root = saved_settings.get("agents_root") or os.environ.get(
            "COHORT_AGENTS_ROOT", "",
        )
        if _agents_root:
            agents_dir = Path(_agents_root) / "agents"
        else:
            agents_dir = Path(resolved_dir) / "agents"
    # Ensure agents_dir exists so AgentStore can cache Gateway agent prompts
    if not agents_dir.is_dir():
        agents_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[OK] Created agents_dir: %s", agents_dir)
    remote_url = (
        os.environ.get("COHORT_AGENTS_API_URL")
        or saved_settings.get("agents_api_url", "")
    )
    api_key = (
        os.environ.get("COHORT_AGENTS_API_KEY")
        or saved_settings.get("agents_api_key", "")
    )
    _agent_store = AgentStore(
        agents_dir=agents_dir if agents_dir.is_dir() else None,
        fallback_registry=_LEGACY_REGISTRY,
        remote_url=remote_url,
        api_key=api_key,
    )
    set_store(_agent_store)
    set_global_store(_agent_store)

    # -- Load tool permissions (central defaults) ----------------------
    from cohort.tool_permissions import load_central_permissions
    tp = load_central_permissions(Path(resolved_dir))
    if tp:
        logger.info("[OK] Tool permissions loaded (%d profiles)",
                    len(tp.get("tool_profiles", {})))

    # -- Load agents (file-backed + legacy agents.json) -----------------
    agents = _load_agents_from_disk(resolved_dir)
    # Merge file-backed agents into the dict
    agents.update(_agent_store.as_config_dict())
    logger.info("[OK] Loaded %d agents (file-backed + legacy)", len(agents))

    # -- Socket.IO setup ------------------------------------------------
    from cohort.data_layer import CohortDataLayer
    from cohort.socketio_events import setup_socketio, sio

    data_layer = CohortDataLayer(chat=_chat, agents=agents)
    _data_layer = data_layer
    setup_socketio(data_layer, chat=_chat, agent_store=_agent_store)

    # -- Agent router (@mention -> agent response pipeline) -------------
    from cohort.agent_router import setup_agent_router, apply_settings as _apply_router_settings

    agents_root = saved_settings.get("agents_root") or os.environ.get(
        "COHORT_AGENTS_ROOT", "",
    )
    setup_agent_router(chat=_chat, sio=sio, agents_root=agents_root, store=_agent_store)
    _apply_router_settings(saved_settings)

    # -- Task executor (briefing + execution layer) ---------------------
    from cohort.task_executor import TaskExecutor
    from cohort.socketio_events import setup_task_executor

    executor_settings = {**saved_settings, "agents_root": str(agents_root)}
    executor = TaskExecutor(data_layer, _chat, executor_settings)
    executor.set_sio(sio)
    setup_task_executor(executor)

    # -- Work Queue (sequential execution queue) ------------------------
    from cohort.work_queue import WorkQueue
    from cohort.socketio_events import setup_work_queue
    _work_queue = WorkQueue(Path(resolved_dir))
    setup_work_queue(_work_queue)
    logger.info("[OK] Work queue initialised")

    # -- Executive Briefing ---------------------------------------------
    from cohort.executive_briefing import ExecutiveBriefing
    global _briefing  # noqa: PLW0603
    _briefing = ExecutiveBriefing(
        data_dir=Path(resolved_dir),
        chat=_chat,
        work_queue=_work_queue,
        data_layer=_data_layer,
        orchestrator_getter=_get_session_orch,
    )
    logger.info("[OK] Executive briefing initialised")

    # -- Routes ---------------------------------------------------------
    routes = [
        Route("/", index, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/api/channels", list_channels, methods=["GET"]),
        Route("/api/channels", create_channel_endpoint, methods=["POST"]),
        Route("/api/messages", get_messages, methods=["GET"]),
        Route("/api/send", send_message, methods=["POST"]),
        Route("/api/channels/{channel_id}/condense", condense_channel, methods=["POST"]),
        Route("/api/agents", list_agents, methods=["GET"]),
        Route("/api/agents", register_agent, methods=["POST"]),
        Route("/api/agents/create", create_agent, methods=["POST"]),
        Route("/api/agents/{agent_id}", get_agent_detail, methods=["GET"]),
        Route("/api/agents/{agent_id}/memory", get_agent_memory, methods=["GET"]),
        Route("/api/agents/{agent_id}/prompt", get_agent_prompt, methods=["GET"]),
        Route("/api/agents/{agent_id}/memory/clean", clean_agent_memory, methods=["POST"]),
        Route("/api/agents/{agent_id}/memory/facts", add_agent_fact, methods=["POST"]),
        Route("/api/agent-registry", get_agent_registry, methods=["GET"]),
        Route("/api/settings", get_settings, methods=["GET"]),
        Route("/api/settings", post_settings, methods=["POST"]),
        Route("/api/settings/test-connection", test_connection, methods=["POST"]),
        Route("/api/permissions", get_permissions, methods=["GET"]),
        Route("/api/permissions", post_permissions, methods=["POST"]),
        Route("/api/tool-permissions", get_tool_permissions, methods=["GET"]),
        Route("/api/tool-permissions", put_tool_permissions, methods=["PUT"]),
        Route("/api/tools", list_tools, methods=["GET"]),
        Route("/api/tasks", get_task_queue, methods=["GET"]),
        Route("/api/tasks", create_task, methods=["POST"]),
        Route("/api/tasks/{task_id}", update_task, methods=["PATCH"]),
        Route("/api/outputs", get_outputs, methods=["GET"]),
        # Work queue (sequential execution)
        Route("/api/work-queue", get_work_queue, methods=["GET"]),
        Route("/api/work-queue", enqueue_work_item, methods=["POST"]),
        Route("/api/work-queue/claim", claim_work_item, methods=["POST"]),
        Route("/api/work-queue/{item_id}", get_work_item, methods=["GET"]),
        Route("/api/work-queue/{item_id}", update_work_item, methods=["PATCH"]),
        # Executive briefing
        Route("/api/briefing/generate", generate_briefing, methods=["POST"]),
        Route("/api/briefing/latest", get_latest_briefing, methods=["GET"]),
        # Setup wizard endpoints
        Route("/api/setup/status", setup_status, methods=["GET"]),
        Route("/api/setup/detect", setup_detect_hardware, methods=["POST"]),
        Route("/api/setup/check-ollama", setup_check_ollama, methods=["POST"]),
        Route("/api/setup/pull-model", setup_pull_model, methods=["POST"]),
        Route("/api/setup/verify", setup_verify_model, methods=["POST"]),
        Route("/api/setup/topics", setup_get_topics, methods=["GET"]),
        Route("/api/setup/save-config", setup_save_config, methods=["POST"]),
        Route("/api/setup/detect-claude", setup_detect_claude, methods=["POST"]),
        Route("/api/setup/check-mcp", setup_check_mcp, methods=["POST"]),
        Route("/api/setup/write-mcp-config", setup_write_mcp_config, methods=["POST"]),
        # Session endpoints (canonical)
        Route("/api/sessions", list_sessions_endpoint, methods=["GET"]),
        Route("/api/sessions/setup-parse", session_setup_parse, methods=["POST"]),
        Route("/api/sessions/start", start_session_endpoint, methods=["POST"]),
        Route("/api/sessions/{session_id}/status", get_session_status, methods=["GET"]),
        Route("/api/sessions/{session_id}/next-speaker", get_next_speaker, methods=["GET"]),
        Route("/api/sessions/{session_id}/record-turn", record_session_turn, methods=["POST"]),
        Route("/api/sessions/{session_id}/pause", pause_session_endpoint, methods=["POST"]),
        Route("/api/sessions/{session_id}/resume", resume_session_endpoint, methods=["POST"]),
        Route("/api/sessions/{session_id}/end", end_session_endpoint, methods=["POST"]),
        Route("/api/sessions/channel/{channel_id}", get_channel_session, methods=["GET"]),
        # Deprecated aliases (roundtable -> sessions)
        Route("/api/roundtable/sessions", list_sessions_endpoint, methods=["GET"]),
        Route("/api/roundtable/setup-parse", session_setup_parse, methods=["POST"]),
        Route("/api/roundtable/start", start_session_endpoint, methods=["POST"]),
        Route("/api/roundtable/{session_id}/status", get_session_status, methods=["GET"]),
        Route("/api/roundtable/{session_id}/next-speaker", get_next_speaker, methods=["GET"]),
        Route("/api/roundtable/{session_id}/record-turn", record_session_turn, methods=["POST"]),
        Route("/api/roundtable/{session_id}/pause", pause_session_endpoint, methods=["POST"]),
        Route("/api/roundtable/{session_id}/resume", resume_session_endpoint, methods=["POST"]),
        Route("/api/roundtable/{session_id}/end", end_session_endpoint, methods=["POST"]),
        Route("/api/roundtable/channel/{channel_id}", get_channel_session, methods=["GET"]),
        # Tool dashboard endpoints
        Route("/api/service-status/{service_id}", get_service_status, methods=["GET"]),
        Route("/api/tool-config/{tool_id}/values", get_tool_config_values, methods=["GET"]),
        Route("/api/tool-config/{tool_id}/values", put_tool_config_value, methods=["PUT"]),
        Route("/api/tool-config/{tool_id}", get_tool_config, methods=["GET"]),
        Route("/api/llm/models", llm_list_models, methods=["GET"]),
        Route("/api/llm/pull", llm_pull_model, methods=["POST"]),
        Route("/api/llm/models/{name:path}", llm_delete_model, methods=["DELETE"]),
        Route("/api/llm/running", get_llm_running, methods=["GET"]),
        Route("/api/llm/model-info/{name:path}", get_llm_model_info, methods=["GET"]),
        # Tool data endpoints (read from BOSS data directory)
        Route("/api/health-monitor/state", get_health_monitor_state, methods=["GET"]),
        Route("/api/health-monitor/alerts", get_health_monitor_alerts, methods=["GET"]),
        Route("/api/scheduler/recent-runs", get_scheduler_recent_runs, methods=["GET"]),
        Route("/api/comms/recent-activity", get_comms_recent_activity, methods=["GET"]),
        Route("/api/intel/recent-articles", get_intel_recent_articles, methods=["GET"]),
        Route("/api/content-monitor/pipeline-status", get_content_monitor_pipeline, methods=["GET"]),
        Route("/api/content-monitor/posts", get_social_posts, methods=["GET"]),
        Route("/api/content-monitor/posts/{post_id}", update_social_post, methods=["PATCH"]),
        Route("/api/content-monitor/articles", get_content_articles, methods=["GET"]),
        Route("/api/content-monitor/config", get_content_config, methods=["GET"]),
        Route("/api/web-search/test", web_search_test, methods=["POST"]),
        Route("/api/youtube/test", youtube_search_test, methods=["POST"]),
        Route("/api/doc-processor/summarize", doc_summarize, methods=["POST"]),
        Route("/api/doc-processor/process", doc_process_file, methods=["POST"]),
        Route("/api/doc-processor/fetch-url", doc_fetch_url, methods=["POST"]),
        Route("/api/doc-processor/history", doc_history_get, methods=["GET"]),
        Route("/api/doc-processor/history", doc_history_post, methods=["POST"]),
        Route("/api/doc-processor/history", doc_history_delete, methods=["DELETE"]),
        Route("/api/comms/pending-approvals", get_comms_pending_approvals, methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"),
    ]

    # Restrict CORS to localhost origins.  Override with COHORT_CORS_ORIGINS
    # env var (comma-separated) if the server needs to be accessed from other
    # hosts, e.g. COHORT_CORS_ORIGINS=http://192.168.1.10:5100
    _cors_env = os.environ.get("COHORT_CORS_ORIGINS", "")
    _cors_origins = (
        [o.strip() for o in _cors_env.split(",") if o.strip()]
        if _cors_env
        else [
            "http://localhost:5100",
            "http://127.0.0.1:5100",
            "http://localhost:5200",
            "http://127.0.0.1:5200",
        ]
    )

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
        ),
    ]

    async def on_startup() -> None:
        """Capture the running event loop for the agent router's sync->async bridge."""
        loop = asyncio.get_running_loop()
        from cohort.agent_router import set_event_loop
        set_event_loop(loop)
        executor.set_event_loop(loop)
        logger.info("[OK] Event loop captured for agent router + task executor")

    starlette_app = Starlette(
        routes=routes,
        middleware=middleware,
        on_startup=[on_startup],
    )

    # Wrap Starlette with Socket.IO ASGI app
    import socketio as sio_module
    app = sio_module.ASGIApp(sio, other_asgi_app=starlette_app)

    return app


# =====================================================================
# Convenience runner
# =====================================================================

def serve(host: str = "0.0.0.0", port: int = 5100, data_dir: str = "data") -> None:
    """Start the cohort HTTP server with uvicorn.

    Parameters
    ----------
    host:
        Bind address (default ``0.0.0.0``).
    port:
        Port number (default ``5100``).
    data_dir:
        Directory for JSON file storage.
    """
    import uvicorn

    app = create_app(data_dir=data_dir)
    logger.info("[*] Starting cohort server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
