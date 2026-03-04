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

from cohort.agent_store import AgentStore
from cohort.chat import ChatManager
from cohort.registry import JsonFileStorage

logger = logging.getLogger(__name__)

# =====================================================================
# Shared state -- populated by create_app()
# =====================================================================

_chat: ChatManager | None = None
_data_layer: Any = None
_agent_store: AgentStore | None = None

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
    """Load settings from {data_dir}/settings.json."""
    if _settings_path and _settings_path.exists():
        try:
            with open(_settings_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load settings.json: %s", exc)
    return {}


def _save_settings(settings: dict[str, Any]) -> None:
    """Persist settings to {data_dir}/settings.json."""
    if _settings_path is None:
        return
    try:
        _settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
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

    Expects JSON body: ``{"channel": "...", "sender": "...", "message": "..."}``.
    Auto-creates the channel if it does not exist yet.
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
        mentions = msg.metadata.get("mentions", [])
        if mentions:
            from cohort.agent_router import route_mentions
            route_mentions(msg, mentions)

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


async def get_agent_registry(request: Request) -> JSONResponse:
    """GET /api/agent-registry -- return all agent visual profiles (avatars, colors, nicknames)."""
    from cohort.agent_registry import get_all_agents
    return JSONResponse(get_all_agents())


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

_roundtable_orch = None


def _get_roundtable_orch():
    """Lazy-load roundtable orchestrator singleton."""
    global _roundtable_orch  # noqa: PLW0603
    if _roundtable_orch is None:
        from cohort.orchestrator import Orchestrator
        from cohort.socketio_events import orchestrator_event_bridge
        agents_config = _data_layer._agents if _data_layer else {}
        _roundtable_orch = Orchestrator(
            _get_chat(),
            agents=agents_config,
            on_event=orchestrator_event_bridge,
        )
        # Wire orchestrator into agent router for roundtable gating
        from cohort.agent_router import set_orchestrator
        set_orchestrator(_roundtable_orch)
    return _roundtable_orch


async def start_roundtable(request: Request) -> JSONResponse:
    """POST /api/roundtable/start -- start a new roundtable session."""
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
        chat.create_channel(name=channel_id, description=f"Roundtable: {topic}")

    orch = _get_roundtable_orch()

    # Check no existing active session
    existing = orch.get_session_for_channel(channel_id)
    if existing:
        return JSONResponse({
            "success": False,
            "error": f"Channel already has active roundtable: {existing.session_id}",
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
        logger.exception("Error starting roundtable")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


async def get_roundtable_status(request: Request) -> JSONResponse:
    """GET /api/roundtable/{session_id}/status -- session status."""
    session_id = request.path_params["session_id"]
    orch = _get_roundtable_orch()
    status = orch.get_status(session_id)
    if not status:
        return JSONResponse({"success": False, "error": "Session not found"}, status_code=404)
    return JSONResponse({"success": True, "status": status})


async def get_next_speaker(request: Request) -> JSONResponse:
    """GET /api/roundtable/{session_id}/next-speaker -- recommended speaker."""
    session_id = request.path_params["session_id"]
    orch = _get_roundtable_orch()
    recommendation = orch.get_next_speaker(session_id)
    if not recommendation:
        return JSONResponse({"success": False, "error": "No speaker available"}, status_code=400)
    return JSONResponse({"success": True, "recommendation": recommendation})


async def record_roundtable_turn(request: Request) -> JSONResponse:
    """POST /api/roundtable/{session_id}/record-turn -- record agent turn."""
    session_id = request.path_params["session_id"]
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}

    speaker = body.get("speaker")
    message_id = body.get("message_id")
    if not speaker or not message_id:
        return JSONResponse({"success": False, "error": "speaker and message_id required"}, status_code=400)

    orch = _get_roundtable_orch()
    success = orch.record_turn(
        session_id=session_id,
        speaker=speaker,
        message_id=message_id,
        was_recommended=body.get("was_recommended", True),
    )
    if not success:
        return JSONResponse({"success": False, "error": "Failed to record turn"}, status_code=400)
    return JSONResponse({"success": True})


async def end_roundtable(request: Request) -> JSONResponse:
    """POST /api/roundtable/{session_id}/end -- end session."""
    session_id = request.path_params["session_id"]
    orch = _get_roundtable_orch()
    summary = orch.end_session(session_id)
    if not summary:
        return JSONResponse({"success": False, "error": "Session not found"}, status_code=404)
    return JSONResponse({"success": True, "summary": summary})


async def list_roundtable_sessions(request: Request) -> JSONResponse:
    """GET /api/roundtable/sessions -- list all sessions."""
    orch = _get_roundtable_orch()
    sessions = [s.to_dict() for s in orch.sessions.values()]
    return JSONResponse({"success": True, "sessions": sessions})


async def roundtable_setup_parse(request: Request) -> JSONResponse:
    """POST /api/roundtable/setup-parse -- parse natural language into config."""
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)

    context = body.get("context")
    orch = _get_roundtable_orch()
    config = orch.suggest_roundtable_config(message, context=context)
    return JSONResponse({"success": True, "config": config})


async def pause_roundtable(request: Request) -> JSONResponse:
    """POST /api/roundtable/{session_id}/pause -- pause session."""
    session_id = request.path_params["session_id"]
    orch = _get_roundtable_orch()
    ok = orch.pause_session(session_id)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found or not active"}, status_code=404)
    return JSONResponse({"success": True})


async def resume_roundtable(request: Request) -> JSONResponse:
    """POST /api/roundtable/{session_id}/resume -- resume session."""
    session_id = request.path_params["session_id"]
    orch = _get_roundtable_orch()
    ok = orch.resume_session(session_id)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found or not paused"}, status_code=404)
    return JSONResponse({"success": True})


async def get_channel_roundtable(request: Request) -> JSONResponse:
    """GET /api/roundtable/channel/{channel_id} -- get active session for channel."""
    channel_id = request.path_params["channel_id"]
    orch = _get_roundtable_orch()
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

    config_path = Path(agents_root) / "config" / "boss_config.yaml"
    if not config_path.exists():
        return JSONResponse({"tools": []})

    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        raw_tools = cfg.get("boss", {}).get("tools", {})
        tool_filter = _load_tool_filter()
        allowed_ids = tool_filter[0] if tool_filter else None
        display_names = tool_filter[1] if tool_filter else {}

        tools = []
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

        # Inject Cohort-native tools not in boss_config (e.g. llm_manager)
        if allowed_ids is not None:
            tools_cfg = _load_tools_config()
            native_descriptions = tools_cfg.get("descriptions", {})
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
    except Exception as exc:
        logger.warning("Failed to load tools from boss_config.yaml: %s", exc)
        return JSONResponse({"tools": []})


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

    return JSONResponse({
        "api_key_masked": masked,
        "claude_cmd": claude_cmd,
        "agents_root": settings.get("agents_root", ""),
        "response_timeout": settings.get("response_timeout", 300),
        "execution_backend": settings.get("execution_backend", "cli"),
        "claude_code_connected": claude_connected,
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

    # Persist hardware info
    settings = _load_settings()
    settings["hardware_info"] = {
        "gpu_name": hw.gpu_name,
        "vram_mb": hw.vram_mb,
        "cpu_only": hw.cpu_only,
        "platform": hw.platform,
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
    global _chat, _data_layer, _agent_store  # noqa: PLW0603

    global _settings_path  # noqa: PLW0603

    resolved_dir = os.environ.get("COHORT_DATA_DIR", data_dir)
    storage = JsonFileStorage(resolved_dir)
    _chat = ChatManager(storage)

    _settings_path = Path(resolved_dir) / "settings.json"

    logger.info("[OK] ChatManager initialised (data_dir=%s)", resolved_dir)

    # -- Agent store (file-backed agent configs + memory) ---------------
    from cohort.agent_registry import _LEGACY_REGISTRY, set_store

    agents_dir_env = os.environ.get("COHORT_AGENTS_DIR")
    agents_dir = Path(agents_dir_env) if agents_dir_env else Path(resolved_dir) / "agents"
    remote_url = os.environ.get("COHORT_AGENTS_API_URL", "")
    api_key = os.environ.get("COHORT_AGENTS_API_KEY", "")
    _agent_store = AgentStore(
        agents_dir=agents_dir if agents_dir.is_dir() else None,
        fallback_registry=_LEGACY_REGISTRY,
        remote_url=remote_url,
        api_key=api_key,
    )
    set_store(_agent_store)

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
    setup_socketio(data_layer, chat=_chat)

    # -- Agent router (@mention -> agent response pipeline) -------------
    from cohort.agent_router import setup_agent_router, apply_settings as _apply_router_settings

    # Load saved settings, fall back to defaults
    saved_settings = _load_settings()
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
        Route("/api/tools", list_tools, methods=["GET"]),
        Route("/api/tasks", get_task_queue, methods=["GET"]),
        Route("/api/outputs", get_outputs, methods=["GET"]),
        # Setup wizard endpoints
        Route("/api/setup/status", setup_status, methods=["GET"]),
        Route("/api/setup/detect", setup_detect_hardware, methods=["POST"]),
        Route("/api/setup/check-ollama", setup_check_ollama, methods=["POST"]),
        Route("/api/setup/pull-model", setup_pull_model, methods=["POST"]),
        Route("/api/setup/verify", setup_verify_model, methods=["POST"]),
        Route("/api/setup/topics", setup_get_topics, methods=["GET"]),
        Route("/api/setup/save-config", setup_save_config, methods=["POST"]),
        # Roundtable endpoints
        Route("/api/roundtable/sessions", list_roundtable_sessions, methods=["GET"]),
        Route("/api/roundtable/setup-parse", roundtable_setup_parse, methods=["POST"]),
        Route("/api/roundtable/start", start_roundtable, methods=["POST"]),
        Route("/api/roundtable/{session_id}/status", get_roundtable_status, methods=["GET"]),
        Route("/api/roundtable/{session_id}/next-speaker", get_next_speaker, methods=["GET"]),
        Route("/api/roundtable/{session_id}/record-turn", record_roundtable_turn, methods=["POST"]),
        Route("/api/roundtable/{session_id}/pause", pause_roundtable, methods=["POST"]),
        Route("/api/roundtable/{session_id}/resume", resume_roundtable, methods=["POST"]),
        Route("/api/roundtable/{session_id}/end", end_roundtable, methods=["POST"]),
        Route("/api/roundtable/channel/{channel_id}", get_channel_roundtable, methods=["GET"]),
        # Tool dashboard endpoints
        Route("/api/service-status/{service_id}", get_service_status, methods=["GET"]),
        Route("/api/tool-config/{tool_id}", get_tool_config, methods=["GET"]),
        Route("/api/llm/models", llm_list_models, methods=["GET"]),
        Route("/api/llm/pull", llm_pull_model, methods=["POST"]),
        Route("/api/llm/models/{name:path}", llm_delete_model, methods=["DELETE"]),
        Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
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
