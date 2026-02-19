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
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from cohort.chat import ChatManager
from cohort.registry import JsonFileStorage

logger = logging.getLogger(__name__)

# =====================================================================
# Shared state -- populated by create_app()
# =====================================================================

_chat: ChatManager | None = None
_data_layer: Any = None

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


# =====================================================================
# Roundtable endpoints (mirroring SMACK's smack_routes.py)
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
        "boss_root": settings.get("boss_root", ""),
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
    if "boss_root" in body:
        settings["boss_root"] = body["boss_root"]
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
    global _chat, _data_layer  # noqa: PLW0603

    global _settings_path  # noqa: PLW0603

    resolved_dir = os.environ.get("COHORT_DATA_DIR", data_dir)
    storage = JsonFileStorage(resolved_dir)
    _chat = ChatManager(storage)

    _settings_path = Path(resolved_dir) / "settings.json"

    logger.info("[OK] ChatManager initialised (data_dir=%s)", resolved_dir)

    # -- Load agents from agents.json -----------------------------------
    agents = _load_agents_from_disk(resolved_dir)
    logger.info("[OK] Loaded %d agents from agents.json", len(agents))

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
    boss_root = saved_settings.get("boss_root") or os.environ.get(
        "BOSS_ROOT",
        r"d:\Projects\PersonalAssistant\PersonalAssistant\BOSS",
    )
    setup_agent_router(chat=_chat, sio=sio, boss_root=boss_root)
    _apply_router_settings(saved_settings)

    # -- Task executor (briefing + execution layer) ---------------------
    from cohort.task_executor import TaskExecutor
    from cohort.socketio_events import setup_task_executor

    executor_settings = {**saved_settings, "boss_root": str(boss_root)}
    executor = TaskExecutor(data_layer, _chat, executor_settings)
    executor.set_sio(sio)
    setup_task_executor(executor)

    # -- Routes ---------------------------------------------------------
    routes = [
        Route("/", index, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/api/channels", list_channels, methods=["GET"]),
        Route("/api/messages", get_messages, methods=["GET"]),
        Route("/api/send", send_message, methods=["POST"]),
        Route("/api/channels/{channel_id}/condense", condense_channel, methods=["POST"]),
        Route("/api/agents", list_agents, methods=["GET"]),
        Route("/api/agents", register_agent, methods=["POST"]),
        Route("/api/agent-registry", get_agent_registry, methods=["GET"]),
        Route("/api/settings", get_settings, methods=["GET"]),
        Route("/api/settings", post_settings, methods=["POST"]),
        Route("/api/settings/test-connection", test_connection, methods=["POST"]),
        Route("/api/permissions", get_permissions, methods=["GET"]),
        Route("/api/permissions", post_permissions, methods=["POST"]),
        Route("/api/tasks", get_task_queue, methods=["GET"]),
        Route("/api/outputs", get_outputs, methods=["GET"]),
        # Roundtable endpoints
        Route("/api/roundtable/start", start_roundtable, methods=["POST"]),
        Route("/api/roundtable/{session_id}/status", get_roundtable_status, methods=["GET"]),
        Route("/api/roundtable/{session_id}/next-speaker", get_next_speaker, methods=["GET"]),
        Route("/api/roundtable/{session_id}/record-turn", record_roundtable_turn, methods=["POST"]),
        Route("/api/roundtable/{session_id}/end", end_roundtable, methods=["POST"]),
        Route("/api/roundtable/channel/{channel_id}", get_channel_roundtable, methods=["GET"]),
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
        Port number (default ``5000``).
    data_dir:
        Directory for JSON file storage.
    """
    import uvicorn

    app = create_app(data_dir=data_dir)
    logger.info("[*] Starting cohort server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
