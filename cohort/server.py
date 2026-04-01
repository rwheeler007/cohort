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
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from cohort.api import DEFAULT_MODEL, AgentStore, ChatManager, create_storage
from cohort.secret_store import decrypt_settings_secrets, encrypt_settings_secrets

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

# Keys that belong to the machine, not the workspace.  These are written
# to ``~/.cohort/defaults.json`` after the first successful wizard run
# and seeded into new workspaces so users don't have to repeat setup.
_GLOBAL_DEFAULT_KEYS: set[str] = {
    "claude_cmd",
    "hardware_info",
    "service_keys",
    "user_display_name",
    "user_display_role",
    "user_display_avatar",
    "model_name",
    "model_verified",
}


def _global_defaults_path() -> Path:
    """Return ``~/.cohort/defaults.json``."""
    return Path(os.path.expanduser("~")) / ".cohort" / "defaults.json"


def _load_global_defaults() -> dict[str, Any]:
    """Load machine-level defaults from ``~/.cohort/defaults.json``."""
    p = _global_defaults_path()
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load global defaults: %s", exc)
    return {}


def _save_global_defaults(settings: dict[str, Any]) -> None:
    """Write machine-level keys from *settings* to ``~/.cohort/defaults.json``."""
    defaults = {k: settings[k] for k in _GLOBAL_DEFAULT_KEYS if k in settings}
    if not defaults:
        return
    p = _global_defaults_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Encrypt secrets before writing
        to_save = json.loads(json.dumps(defaults))
        encrypt_settings_secrets(to_save)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
        logger.info("[OK] Global defaults saved to %s", p)
    except OSError as exc:
        logger.warning("Failed to save global defaults: %s", exc)


def _seed_from_global_defaults() -> dict[str, Any]:
    """Seed a fresh workspace from global defaults.

    Returns the seeded settings dict (already saved to disk).  If the
    global defaults include ``claude_cmd``, ``channel_mode`` is
    automatically enabled so agents work out of the box.
    """
    defaults = _load_global_defaults()
    if not defaults:
        return {}

    defaults = decrypt_settings_secrets(defaults)

    # If Claude is available, default channel_mode to True
    if defaults.get("claude_cmd"):
        defaults.setdefault("channel_mode", True)
        defaults.setdefault("force_to_claude_code", True)
        defaults.setdefault("execution_backend", "channel")
        defaults.setdefault("claude_enabled", True)

    # Mark setup as complete since machine is already configured
    defaults["setup_completed"] = True

    _save_settings(defaults)
    logger.info("[OK] Seeded workspace settings from global defaults (%d keys)",
                len(defaults))
    return defaults


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


def _load_tier_settings() -> dict[str, Any]:
    """Load model tier settings from cohort/data/tier_settings.json."""
    try:
        from cohort.local.config import get_tier_settings
        return get_tier_settings()
    except Exception:
        return {
            "smart": {"primary": "local", "fallback": None},
            "smarter": {"primary": "local", "fallback": "smart"},
            "smartest": {"primary": "qwen3.5:35b-a3b", "fallback": "cloud_api"},
        }


def _save_tier_settings(tier_settings: dict[str, Any]) -> bool:
    """Save model tier settings to cohort/data/tier_settings.json."""
    try:
        from cohort.local.config import save_tier_settings
        return save_tier_settings(tier_settings)
    except Exception as exc:
        logger.warning("Failed to save tier settings: %s", exc)
        return False


def _get_token_usage_summary() -> dict[str, Any]:
    """Get token usage summary for the settings UI."""
    try:
        from cohort.local.config import get_budget_limits
        limits = get_budget_limits()
        storage = getattr(_chat, "_storage", None) if _chat else None
        if storage is not None and hasattr(storage, "get_token_usage"):
            today = storage.get_token_usage("today")
            month = storage.get_token_usage("month")
            return {
                "today": today,
                "month": month,
                "limits": limits,
            }
    except Exception:
        pass
    return {"today": {}, "month": {}, "limits": {}}


def _apply_global_agent_links(enable: bool) -> None:
    """Create or remove ~/.claude/agents and ~/.claude/skills junctions/symlinks.

    Called when the user toggles the global agents setting.  Non-blocking.
    """
    import os
    import platform
    import subprocess as _sp

    cohort_root = Path(__file__).resolve().parent.parent
    global_claude = Path.home() / ".claude"
    link_names = ("agents", "skills")

    if enable:
        global_claude.mkdir(exist_ok=True)
        plat = platform.system()
        for name in link_names:
            source = cohort_root / ".claude" / name
            link = global_claude / name
            if not source.exists() or link.exists():
                continue
            try:
                if plat == "Windows":
                    _sp.run(
                        ["cmd", "/c", "mklink", "/J", str(link), str(source)],
                        check=True, capture_output=True, text=True,
                    )
                else:
                    link.symlink_to(source)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not create global agent link %s: %s", name, exc)
    else:
        for name in link_names:
            link = global_claude / name
            try:
                if os.path.islink(str(link)):
                    link.unlink()
                # Never remove real directories -- only junctions/symlinks
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not remove global agent link %s: %s", name, exc)


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
    """GET / -- serve the Cohort dashboard (if installed)."""
    html_path = _TEMPLATES_DIR / "cohort.html"
    if not html_path.exists():
        return HTMLResponse(
            "<h1>Cohort API Server</h1>"
            "<p>The API is running. Use the "
            "<a href='https://marketplace.visualstudio.com/items?itemName=cohort-ai.cohort-vscode'>"
            "VS Code extension</a> to connect.</p>",
        )
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
    workspace_path = body.get("workspace_path")

    metadata: dict[str, Any] = {}
    if workspace_path:
        metadata["workspace_path"] = workspace_path

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
            metadata=metadata,
        )
        logger.info("Created channel: %s", name)
        return JSONResponse({
            "success": True,
            "channel": channel.to_dict(),
        })
    except Exception as exc:
        logger.exception("Error creating channel")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def delete_channel_endpoint(request: Request) -> JSONResponse:
    """DELETE /api/channels/{channel_id} -- delete a channel."""
    channel_id = request.path_params["channel_id"]
    try:
        chat = _get_chat()
        ok = chat.delete_channel(channel_id)
        if ok:
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def rename_channel_endpoint(request: Request) -> JSONResponse:
    """PATCH /api/channels/{channel_id} -- rename a channel."""
    channel_id = request.path_params["channel_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    new_name = body.get("name", "")
    if not new_name:
        return JSONResponse({"error": "Missing name"}, status_code=400)
    try:
        chat = _get_chat()
        ch = chat.rename_channel(channel_id, new_name)
        if ch:
            return JSONResponse({"ok": True, "channel": ch.to_dict()})
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def archive_channel_endpoint(request: Request) -> JSONResponse:
    """POST /api/channels/{channel_id}/archive -- archive a channel."""
    channel_id = request.path_params["channel_id"]
    try:
        chat = _get_chat()
        ch = chat.archive_channel(channel_id, archived_by="user")
        if ch:
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    except Exception as exc:
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
            ws_path = body.get("workspace_path")
            chat.create_channel(
                name=channel,
                description=f"Auto-created channel: {channel}",
                metadata={"workspace_path": ws_path} if ws_path else None,
            )
            logger.info("Auto-created channel: %s", channel)

        msg = chat.post_message(
            channel_id=channel,
            sender=sender,
            content=message,
            metadata=body.get("metadata"),
        )

        # Route @mentions to agent response pipeline
        # Skip routing for agent-sent messages (roundtable posts, etc.)
        # to prevent re-triggering loops.  Only human messages trigger agents.
        response_mode = body.get("response_mode", "channel")
        if response_mode not in ("smart", "smarter", "smartest", "channel"):
            response_mode = "channel"

        # Pass project path from extension for per-project memory injection
        project_path = body.get("project_path")

        from cohort.agent_router import resolve_agent_id
        is_agent_sender = resolve_agent_id(sender) is not None
        mentions = msg.metadata.get("mentions", [])
        if mentions and not is_agent_sender:
            from cohort.agent_router import route_mentions
            route_mentions(msg, mentions, response_mode=response_mode, project_path=project_path)

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
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

    status_filter = request.query_params.get("status")
    tasks = _task_store.list_tasks(status_filter=status_filter)
    return JSONResponse({"tasks": tasks})


async def get_outputs(request: Request) -> JSONResponse:
    """GET /api/outputs -- return completed tasks awaiting review."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

    outputs = _task_store.get_outputs_for_review()
    return JSONResponse({"outputs": outputs})


async def create_task(request: Request) -> JSONResponse:
    """POST /api/tasks -- create a task via HTTP (MCP-friendly).

    Mirrors the Socket.IO ``assign_task`` event but over HTTP so MCP
    tools can submit tasks without needing a WebSocket connection.
    """
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

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

    # Build triad fields from request body
    trigger = {
        "type": body.get("trigger_type", "manual"),
        "source": body.get("trigger_source", "user"),
    }
    action = None
    if body.get("tool"):
        action = {"tool": body["tool"], "tool_ref": body.get("tool_ref"), "parameters": {}}
    outcome = None
    if body.get("success_criteria"):
        outcome = {
            "type": body.get("outcome_type", "artifact"),
            "success_criteria": body["success_criteria"],
        }

    task = _task_store.create_task(
        agent_id, description, priority,
        trigger=trigger, action=action, outcome=outcome,
    )

    # Broadcast via Socket.IO if available
    try:
        from cohort.socketio_events import sio
        asyncio.create_task(sio.emit("cohort:task_assigned", task))
    except Exception:
        pass  # Socket.IO not available -- task still created

    return JSONResponse({"success": True, "task": task})


async def update_task(request: Request) -> JSONResponse:
    """PATCH /api/tasks/{task_id} -- update task status/output (MCP-friendly).

    Supports advancing tasks through the lifecycle and attaching output
    for the review pipeline.
    """
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

    task_id = request.path_params.get("task_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    status = body.get("status")
    output = body.get("output")

    if status == "complete":
        task = _task_store.complete_task(task_id, output=output)
    elif status == "failed":
        task = _task_store.fail_task(task_id, reason=body.get("reason", ""))
    elif status:
        updates = {"status": status}
        if body.get("progress"):
            updates["progress"] = body["progress"]
        task = _task_store.update_task(task_id, **updates)
    else:
        return JSONResponse({"error": "Missing 'status' field"}, status_code=400)

    if task is None:
        return JSONResponse({"error": f"Task '{task_id}' not found"}, status_code=404)

    # Broadcast via Socket.IO
    try:
        from cohort.socketio_events import sio
        event = "cohort:task_complete" if status == "complete" else "cohort:task_progress"
        asyncio.create_task(sio.emit(event, task))
    except Exception:
        pass

    # Auto-trigger review pipeline on task completion (BOSS pattern)
    if status == "complete":
        _maybe_trigger_auto_review(task)

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
# Approval Pipeline endpoints
# =====================================================================

_approval_store: Optional["ApprovalStore"] = None  # noqa: F821
_deliverable_tracker: Optional["DeliverableTracker"] = None  # noqa: F821
_review_pipeline: Optional["ReviewPipeline"] = None  # noqa: F821


# =====================================================================
# Auto-Review Pipeline (runs on task completion, like BOSS)
# =====================================================================

REVIEW_CHANNEL_ID = "review-pipeline"
REVIEW_SESSION_WAIT_TIMEOUT = 30  # seconds to wait for channel session


def _make_ollama_reviewer_fn() -> Callable:
    """Create a reviewer_fn that uses Ollama local LLM."""

    def reviewer_fn(stage, system_prompt, user_prompt):
        try:
            from cohort.local.ollama import OllamaClient
            client = OllamaClient()
            if not client.health_check():
                logger.warning("[!] Ollama not available for review stage %s", stage.role)
                return None

            models = client.list_models()
            review_model = None
            for preferred in ["qwen3.5:35b-a3b", "qwen3.5:9b", "qwen2.5-coder:14b", "qwen2.5-coder:7b"]:
                if any(preferred in m for m in models):
                    review_model = preferred
                    break
            if not review_model and models:
                review_model = models[0]
            if not review_model:
                logger.warning("[!] No Ollama models available for review")
                return None

            result = client.generate(
                model=review_model,
                prompt=user_prompt,
                system=system_prompt,
                temperature=0.15,
            )
            return result.text if result else None

        except Exception as exc:
            logger.warning("[!] Ollama reviewer failed for %s: %s", stage.role, exc)
            return None

    return reviewer_fn


def _make_channel_reviewer_fn(task_id: str) -> Callable:
    """Create a reviewer_fn that uses Claude Code Channels.

    Each review stage is sent as a separate request to the ``review-pipeline``
    channel.  Claude Code processes the prompt and responds via the channel
    plugin.  Blocks until response or timeout (300s).
    """

    def reviewer_fn(stage, system_prompt, user_prompt):
        try:
            from cohort.channel_bridge import (
                await_channel_response,
                enqueue_channel_request,
            )

            # Combine system + user prompt for the channel session
            combined_prompt = (
                f"{system_prompt}\n\n"
                f"---\n\n"
                f"{user_prompt}\n\n"
                f"IMPORTANT: Respond with valid JSON only. No markdown fences, "
                f"no explanation outside the JSON object."
            )

            request_id = enqueue_channel_request(
                prompt=combined_prompt,
                agent_id=stage.agent_id,
                channel_id=REVIEW_CHANNEL_ID,
                response_mode="channel",
                metadata={
                    "review_stage": stage.role,
                    "task_id": task_id,
                    "type": "auto_review",
                },
            )
            logger.info(
                "[>>] Channel review request %s for stage %s (task %s)",
                request_id, stage.role, task_id,
            )

            # Block until Claude responds
            response_content, response_meta = await_channel_response(
                request_id, timeout=300.0,
            )

            if response_content:
                logger.info(
                    "[OK] Channel review response for %s/%s (%d chars)",
                    stage.role, task_id, len(response_content),
                )
                return response_content
            else:
                error = response_meta.get("error", "no response")
                logger.warning(
                    "[!] Channel review failed for %s/%s: %s",
                    stage.role, task_id, error,
                )
                return None

        except Exception as exc:
            logger.warning("[!] Channel reviewer failed for %s: %s", stage.role, exc)
            return None

    return reviewer_fn


def _channel_session_available(channel_id: str = REVIEW_CHANNEL_ID) -> bool:
    """Check if a channel session is alive and healthy."""
    try:
        from cohort.channel_bridge import channel_mode_active
        return channel_mode_active(channel_id)
    except Exception:
        return False


def _ensure_review_channel_session() -> bool:
    """Ensure the review-pipeline channel session exists.

    Requests a session launch and waits up to REVIEW_SESSION_WAIT_TIMEOUT
    seconds for it to become healthy.  Returns True if session is ready.
    """
    import time as _time

    from cohort.channel_bridge import channel_mode_active, request_session

    if channel_mode_active(REVIEW_CHANNEL_ID):
        return True

    # Request VS Code to launch a terminal for this channel
    request_session(REVIEW_CHANNEL_ID)
    logger.info("[>>] Requested review channel session, waiting for launch...")

    deadline = _time.time() + REVIEW_SESSION_WAIT_TIMEOUT
    while _time.time() < deadline:
        if channel_mode_active(REVIEW_CHANNEL_ID):
            logger.info("[OK] Review channel session is ready")
            return True
        _time.sleep(2.0)

    logger.warning("[!] Review channel session did not start within %ds", REVIEW_SESSION_WAIT_TIMEOUT)
    return False


def _get_review_backend() -> str:
    """Read the review_backend setting. Defaults to 'local'."""
    settings = _load_settings()
    return settings.get("review_backend", "local")


def _run_auto_review(task: Dict[str, Any], review_backend: str = "local") -> None:
    """Background thread: run the review pipeline on a completed task.

    Mirrors BOSS's automatic flow:
    1. Move task to 'reviewing'
    2. Run each pipeline stage via selected backend (Ollama or Claude Code Channel)
    3. Attach review results
    4. If majority rejects -> auto-requeue with combined feedback
    5. If approved -> emit for human review

    Parameters
    ----------
    review_backend:
        ``"local"`` (Ollama), ``"channel"`` (Claude Code), or ``"auto"``
        (try channel first, fall back to local).
    """
    task_id = task.get("task_id", "?")
    logger.info("[>>] Auto-review starting for %s (backend=%s)", task_id, review_backend)

    try:
        if _task_store is None or _review_pipeline is None:
            logger.warning("[!] Auto-review skipped: stores not initialised")
            return

        # Skip tasks without output
        output = task.get("output")
        if not output:
            logger.info("[*] Auto-review skipped for %s: no output", task_id)
            return

        # Step 1: Move to reviewing
        reviewed = _task_store.submit_for_review(task_id)
        if reviewed is None:
            logger.warning("[!] Auto-review: could not move %s to reviewing", task_id)
            return

        _emit_async("cohort:task_progress", reviewed)

        # Step 2: Build task context for the pipeline
        output_text = output.get("content") or output.get("diff") or output.get("summary") or ""
        deliverables = task.get("deliverables") or []
        deliverables_text = "\n".join(
            f"- [{d.get('id', '?')}] {d.get('description', '')}"
            for d in deliverables
        ) if deliverables else "(no deliverables defined)"

        task_context = {
            "description": task.get("description", "(no description)"),
            "deliverables": deliverables_text,
            "code": output_text[:15000],
            "self_review": "(automated)",
        }

        # Step 3: Select reviewer backend
        if review_backend == "channel":
            if _ensure_review_channel_session():
                reviewer_fn = _make_channel_reviewer_fn(task_id)
                logger.info("[*] Using Claude Code Channel for review of %s", task_id)
            else:
                logger.warning("[!] Channel session unavailable, falling back to local for %s", task_id)
                reviewer_fn = _make_ollama_reviewer_fn()
        elif review_backend == "auto":
            if _channel_session_available():
                reviewer_fn = _make_channel_reviewer_fn(task_id)
                logger.info("[*] Auto-selected channel backend for review of %s", task_id)
            else:
                reviewer_fn = _make_ollama_reviewer_fn()
                logger.info("[*] Auto-selected local backend for review of %s", task_id)
        else:
            reviewer_fn = _make_ollama_reviewer_fn()

        reviews = _review_pipeline.run_reviews(task_context, reviewer_fn)

        # Step 4: Attach reviews to task
        if reviews:
            review_dicts = [r.to_dict() for r in reviews]
            _task_store.attach_reviews(task_id, review_dicts)

        # Step 5: Evaluate verdict
        verdict = _review_pipeline.evaluate_verdict(reviews)

        from cohort.review_pipeline import PipelineVerdict

        if verdict == PipelineVerdict.REJECTED:
            feedback = _review_pipeline.collect_rejection_feedback(reviews)
            new_task = _task_store.requeue_task(task_id, feedback=feedback)
            if new_task:
                logger.info(
                    "[X] Auto-review REJECTED %s -> requeued as %s",
                    task_id, new_task.get("task_id"),
                )
                _emit_async("cohort:task_requeued", {
                    "old_task_id": task_id,
                    "new_task": new_task,
                })
            else:
                logger.warning("[!] Auto-review rejected %s but requeue failed (max requeues?)", task_id)
                _task_store.record_review(task_id, "rejected", feedback)
                updated = _task_store.get_task(task_id)
                if updated:
                    _emit_async("cohort:review_submitted", updated.get("review", {}))
        else:
            logger.info("[OK] Auto-review %s for %s — awaiting human review", verdict.value, task_id)
            updated = _task_store.get_task(task_id)
            if updated:
                _emit_async("cohort:task_progress", updated)

    except Exception as exc:
        logger.error("[X] Auto-review failed for %s: %s", task_id, exc, exc_info=True)


def _emit_async(event: str, data: Any) -> None:
    """Emit a Socket.IO event from a background thread."""
    try:
        from cohort.socketio_events import sio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(sio.emit(event, data), loop)
        else:
            asyncio.run(sio.emit(event, data))
    except Exception:
        try:
            from cohort.socketio_events import sio
            asyncio.create_task(sio.emit(event, data))
        except Exception:
            pass


def _maybe_trigger_auto_review(task: Dict[str, Any]) -> None:
    """Start auto-review in a background thread if pipeline is configured."""
    if _review_pipeline is None or not _review_pipeline.stages:
        return
    if task.get("status") != "complete":
        return
    review_backend = _get_review_backend()
    thread = threading.Thread(
        target=_run_auto_review,
        args=(task, review_backend),
        daemon=True,
        name=f"auto-review-{task.get('task_id', '?')}",
    )
    thread.start()


# =====================================================================
# Auto-Review Pipeline for Work Queue items
# =====================================================================

def _run_auto_review_work_item(item_dict: Dict[str, Any], review_backend: str = "local") -> None:
    """Background thread: run the review pipeline on a work item in reviewing state.

    Mirrors ``_run_auto_review()`` but operates on WorkQueue items instead of Tasks.

    Verdict outcomes:
    - APPROVED: leave in reviewing state (human makes final call), emit update.
    - REJECTED / NEEDS_WORK: reject the item, requeue with combined feedback, emit update.
    - INCOMPLETE: log warning, emit current state, no transition.
    """
    item_id = item_dict.get("id", "?")
    logger.info("[>>] Auto-review (work-queue) starting for %s (backend=%s)", item_id, review_backend)

    try:
        if _work_queue is None or _review_pipeline is None:
            logger.warning("[!] Auto-review (wq) skipped: stores not initialised")
            return

        # Build task context for the review pipeline
        description = item_dict.get("description", "(no description)")
        result_text = item_dict.get("result", "") or ""
        deliverables = item_dict.get("deliverables") or []
        deliverables_text = "\n".join(
            f"- [{d.get('id', '?')}] {d.get('description', '')}"
            for d in deliverables
        ) if deliverables else "(no deliverables defined)"

        task_context = {
            "description": description,
            "deliverables": deliverables_text,
            "code": result_text[:15000],
            "self_review": "(automated)",
        }

        # Select reviewer backend
        if review_backend == "channel":
            if _ensure_review_channel_session():
                reviewer_fn = _make_channel_reviewer_fn(item_id)
            else:
                reviewer_fn = _make_ollama_reviewer_fn()
        elif review_backend == "auto":
            if _channel_session_available():
                reviewer_fn = _make_channel_reviewer_fn(item_id)
            else:
                reviewer_fn = _make_ollama_reviewer_fn()
        else:
            reviewer_fn = _make_ollama_reviewer_fn()

        reviews = _review_pipeline.run_reviews(task_context, reviewer_fn)

        # Attach reviews
        if reviews:
            review_dicts = [r.to_dict() for r in reviews]
            _work_queue.attach_reviews(item_id, review_dicts)

        # Evaluate verdict
        verdict = _review_pipeline.evaluate_verdict(reviews)

        from cohort.review_pipeline import PipelineVerdict

        if verdict == PipelineVerdict.APPROVED:
            # Leave in reviewing -- human makes final call
            logger.info("[OK] Auto-review (wq) APPROVED %s -- awaiting human review", item_id)
            _broadcast_work_queue()

        elif verdict in (PipelineVerdict.REJECTED, PipelineVerdict.NEEDS_WORK):
            feedback = _review_pipeline.collect_rejection_feedback(reviews)
            # Must reject before requeue (requeue requires rejected/stale_bounced/failed status)
            _work_queue.reject(item_id, rejected_by="auto-review", reason=feedback)
            _broadcast_work_queue()
            new_item = _work_queue.requeue(item_id, feedback=feedback)
            if new_item:
                logger.info(
                    "[X] Auto-review (wq) %s %s -> requeued as %s",
                    verdict.value, item_id, new_item.id,
                )
            else:
                logger.warning("[!] Auto-review (wq) rejected %s but requeue failed", item_id)
            _broadcast_work_queue()

        elif verdict == PipelineVerdict.INCOMPLETE:
            logger.warning("[!] Auto-review (wq) INCOMPLETE for %s -- no transition", item_id)
            _broadcast_work_queue()

    except Exception as exc:
        logger.error("[X] Auto-review (wq) failed for %s: %s", item_id, exc, exc_info=True)


def _work_item_review_trigger(item: Any) -> None:
    """Callback: auto-trigger review when work item enters reviewing state."""
    if _review_pipeline is None or not _review_pipeline.stages:
        return
    review_backend = _get_review_backend()
    thread = threading.Thread(
        target=_run_auto_review_work_item,
        args=(item.to_dict() if hasattr(item, "to_dict") else item, review_backend),
        daemon=True,
        name=f"auto-review-wq-{getattr(item, 'id', '?')}",
    )
    thread.start()


def _broadcast_approvals() -> None:
    """Push approval updates to all connected dashboard clients."""
    if _approval_store is None:
        return
    try:
        from cohort.socketio_events import sio
        pending = _approval_store.get_pending()
        asyncio.create_task(
            sio.emit("cohort:approvals_update", {
                "pending": [a.to_dict() for a in pending],
                "pending_count": len(pending),
            }),
        )
    except Exception:
        pass


async def list_approvals(request: Request) -> JSONResponse:
    """GET /api/approvals -- list approval requests."""
    if _approval_store is None:
        return JSONResponse({"error": "Approval store not initialised"}, status_code=500)

    status_filter = request.query_params.get("status")
    item_type = request.query_params.get("item_type")
    limit = int(request.query_params.get("limit", "50"))

    items = _approval_store.list_all(status=status_filter, item_type=item_type, limit=limit)
    return JSONResponse({
        "approvals": [a.to_dict() for a in items],
        "pending_count": _approval_store.get_pending_count(),
    })


async def create_approval(request: Request) -> JSONResponse:
    """POST /api/approvals -- create a new approval request."""
    if _approval_store is None:
        return JSONResponse({"error": "Approval store not initialised"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    required = ("item_id", "item_type", "requester", "action_type", "risk_level", "description")
    missing = [f for f in required if not body.get(f)]
    if missing:
        return JSONResponse({"error": f"Missing fields: {', '.join(missing)}"}, status_code=400)

    try:
        approval = _approval_store.create(
            item_id=body["item_id"],
            item_type=body["item_type"],
            requester=body["requester"],
            action_type=body["action_type"],
            risk_level=body["risk_level"],
            description=body["description"],
            details=body.get("details"),
            timeout=body.get("timeout"),
            reviewer_role=body.get("reviewer_role"),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    _broadcast_approvals()
    return JSONResponse({"success": True, "approval": approval.to_dict()})


async def resolve_approval(request: Request) -> JSONResponse:
    """PATCH /api/approvals/{approval_id} -- approve/deny/cancel."""
    if _approval_store is None:
        return JSONResponse({"error": "Approval store not initialised"}, status_code=500)

    approval_id = request.path_params.get("approval_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    action = body.get("action")  # approve | deny | cancel
    resolved_by = body.get("resolved_by", "human")

    if action == "cancel":
        result = _approval_store.cancel(approval_id, cancelled_by=resolved_by)
    elif action in ("approve", "deny"):
        result = _approval_store.resolve(
            approval_id, action, resolved_by,
            audit_notes=body.get("notes", ""),
        )
    else:
        return JSONResponse({"error": "action must be: approve, deny, or cancel"}, status_code=400)

    if "error" in result:
        return JSONResponse(result, status_code=400)

    _broadcast_approvals()
    return JSONResponse({"success": True, **result})


async def submit_task_for_review(request: Request) -> JSONResponse:
    """POST /api/tasks/{task_id}/submit-review -- move task to reviewing."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

    task_id = request.path_params.get("task_id", "")
    task = _task_store.submit_for_review(task_id)
    if task is None:
        return JSONResponse(
            {"error": f"Task '{task_id}' not found or not in 'complete' status"},
            status_code=400,
        )

    # Create an approval record so the review pipeline can track it
    if _approval_store is not None:
        try:
            approval = _approval_store.create(
                item_id=task_id,
                item_type="task",
                requester=task.get("agent_id", "system"),
                action_type="custom",
                risk_level="medium",
                description=task.get("description", f"Review task {task_id}"),
            )
            task["approval_id"] = approval.id
            _task_store._save_tasks()  # persist the approval_id link
        except Exception as exc:
            logger.warning("Could not create approval for task %s: %s", task_id, exc)

    # Broadcast task update
    try:
        from cohort.socketio_events import sio
        asyncio.create_task(sio.emit("cohort:task_progress", task))
    except Exception:
        pass

    return JSONResponse({"success": True, "task": task})


async def attach_task_reviews(request: Request) -> JSONResponse:
    """POST /api/tasks/{task_id}/reviews -- attach review pipeline results."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

    task_id = request.path_params.get("task_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    reviews = body.get("reviews", [])
    task = _task_store.attach_reviews(task_id, reviews)
    if task is None:
        return JSONResponse({"error": f"Task '{task_id}' not found"}, status_code=404)

    return JSONResponse({"success": True, "task": task})


async def requeue_task(request: Request) -> JSONResponse:
    """POST /api/tasks/{task_id}/requeue -- requeue with feedback."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

    task_id = request.path_params.get("task_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    feedback = body.get("feedback", "")
    new_task = _task_store.requeue_task(task_id, feedback=feedback)
    if new_task is None:
        return JSONResponse(
            {"error": f"Task '{task_id}' cannot be requeued (not rejected/needs_work/failed, or max requeues reached)"},
            status_code=400,
        )

    # Broadcast
    try:
        from cohort.socketio_events import sio
        asyncio.create_task(sio.emit("cohort:task_requeued", {
            "old_task_id": task_id,
            "new_task": new_task,
        }))
    except Exception:
        pass

    return JSONResponse({"success": True, "task": new_task})


async def submit_work_item_for_review(request: Request) -> JSONResponse:
    """POST /api/work-queue/{item_id}/submit-review -- move to reviewing."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    item_id = request.path_params.get("item_id", "")
    item = _work_queue.submit_for_review(item_id)
    if item is None:
        return JSONResponse(
            {"error": f"Item '{item_id}' not found or not in 'active' status"},
            status_code=400,
        )

    # Create an approval record so the review pipeline can track it
    if _approval_store is not None:
        try:
            approval = _approval_store.create(
                item_id=item_id,
                item_type="work_item",
                requester=getattr(item, "requester", "system"),
                action_type="custom",
                risk_level="medium",
                description=getattr(item, "description", f"Review work item {item_id}"),
            )
            item.approval_id = approval.id
            _work_queue._save_to_disk()  # persist the approval_id link
        except Exception as exc:
            logger.warning("Could not create approval for work item %s: %s", item_id, exc)

    _broadcast_work_queue()
    return JSONResponse({"success": True, "item": item.to_dict()})


async def attach_work_item_reviews(request: Request) -> JSONResponse:
    """POST /api/work-queue/{item_id}/reviews -- attach review results."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    item_id = request.path_params.get("item_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    reviews = body.get("reviews", [])
    item = _work_queue.attach_reviews(item_id, reviews)
    if item is None:
        return JSONResponse({"error": f"Item '{item_id}' not found"}, status_code=404)

    return JSONResponse({"success": True, "item": item.to_dict()})


async def requeue_work_item(request: Request) -> JSONResponse:
    """POST /api/work-queue/{item_id}/requeue -- requeue with feedback."""
    if _work_queue is None:
        return JSONResponse({"error": "Work queue not initialised"}, status_code=500)

    item_id = request.path_params.get("item_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    feedback = body.get("feedback", "")
    new_item = _work_queue.requeue(item_id, feedback=feedback)
    if new_item is None:
        return JSONResponse(
            {"error": f"Item '{item_id}' cannot be requeued"},
            status_code=400,
        )

    _broadcast_work_queue()
    return JSONResponse({"success": True, "item": new_item.to_dict()})


async def get_review_pipeline_config(request: Request) -> JSONResponse:
    """GET /api/review-pipeline/config -- get pipeline stage configuration."""
    if _review_pipeline is None:
        return JSONResponse({"error": "Review pipeline not initialised"}, status_code=500)
    return JSONResponse(_review_pipeline.to_dict())


async def put_review_pipeline_config(request: Request) -> JSONResponse:
    """PUT /api/review-pipeline/config -- update pipeline stages."""
    global _review_pipeline  # noqa: PLW0603
    if _review_pipeline is None:
        return JSONResponse({"error": "Review pipeline not initialised"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    from cohort.review_pipeline import ReviewPipeline
    _review_pipeline = ReviewPipeline.from_dict(body)
    _review_pipeline.save_config(Path(_resolved_data_dir))
    return JSONResponse({"success": True, **_review_pipeline.to_dict()})


# =====================================================================
# Channel endpoints -- Claude Code Channels integration
#
# These endpoints let a Claude Code Channel plugin drive agent responses
# externally.  The plugin polls for pending requests, claims them (gets
# the full prompt), then delivers a response back.  All prompt construction,
# context enrichment, and response posting stay in agent_router.py.
# =====================================================================


async def channel_poll(request: Request) -> JSONResponse:
    """GET /api/channel/poll -- return next pending agent request.

    Accepts optional ``channel_id`` query param to scope to one channel.
    """
    from cohort.channel_bridge import poll_next_request

    channel_id = request.query_params.get("channel_id")
    pending = poll_next_request(channel_id=channel_id)
    if pending is None:
        return JSONResponse({"request": None, "reason": "queue_empty"})
    return JSONResponse({"request": pending})


async def channel_claim(request: Request) -> JSONResponse:
    """POST /api/channel/{request_id}/claim -- claim request, get prompt."""
    from cohort.channel_bridge import claim_request

    request_id = request.path_params.get("request_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}

    session_id = body.get("session_id", "unknown")
    result = claim_request(request_id, session_id=session_id)
    if result is None:
        return JSONResponse(
            {"error": f"Request '{request_id}' not found or already claimed"},
            status_code=404,
        )

    return JSONResponse(result)


async def channel_respond(request: Request) -> JSONResponse:
    """POST /api/channel/{request_id}/respond -- deliver agent response.

    Accepts ``{content: str}``.  Posts the response to the Cohort channel,
    emits Socket.IO events, records working memory, and triggers learning --
    the same post-response pipeline as the synchronous agent path.
    """
    from cohort.channel_bridge import deliver_response

    request_id = request.path_params.get("request_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "Empty response content"}, status_code=400)

    metadata = body.get("metadata")  # Optional: plugin can send timing/token info
    ok = deliver_response(request_id, content, metadata=metadata)
    if not ok:
        return JSONResponse(
            {"error": f"Request '{request_id}' not found or not claimed"},
            status_code=404,
        )

    return JSONResponse({"ok": True})


async def channel_error(request: Request) -> JSONResponse:
    """POST /api/channel/{request_id}/error -- report request failure."""
    from cohort.channel_bridge import deliver_error

    request_id = request.path_params.get("request_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}

    error_msg = body.get("error", "Unknown channel error")
    ok = deliver_error(request_id, error_msg)
    if not ok:
        return JSONResponse(
            {"error": f"Request '{request_id}' not found or not claimed"},
            status_code=404,
        )

    return JSONResponse({"ok": True})


async def channel_heartbeat(request: Request) -> JSONResponse:
    """POST /api/channel/heartbeat -- register/update session heartbeat."""
    from cohort.channel_bridge import update_heartbeat

    try:
        body = await request.json()
    except Exception:
        body = {}

    update_heartbeat(
        session_id=body.get("session_id", "unknown"),
        pid=body.get("pid"),
        channel_id=body.get("channel_id"),
    )
    return JSONResponse({"ok": True})


async def channel_status(request: Request) -> JSONResponse:
    """GET /api/channel/status -- return channel session health."""
    from cohort.channel_bridge import get_session_status

    channel_id = request.query_params.get("channel_id")
    return JSONResponse(get_session_status(channel_id=channel_id))


async def channel_launch_queue(request: Request) -> JSONResponse:
    """GET /api/channel/launch-queue -- next channel needing a session."""
    from cohort.channel_bridge import pop_launch_queue

    pending = pop_launch_queue()
    return JSONResponse({"pending": pending})


async def channel_launch_ack(request: Request) -> JSONResponse:
    """POST /api/channel/launch-queue/<channel_id>/ack -- extension launched it."""
    from cohort.channel_bridge import ack_launch

    channel_id = request.path_params["channel_id"]
    ok = ack_launch(channel_id)
    return JSONResponse({"ok": ok})


async def channel_capabilities(request: Request) -> JSONResponse:
    """GET /api/channel/capabilities -- report server session management features."""
    from cohort.channel_bridge import _session_limit
    return JSONResponse({
        "server_managed_sessions": True,
        "session_limit": _session_limit,
        "wq_dispatch": "internal",
        "version": "0.4.33",
    })


async def channel_sessions(request: Request) -> JSONResponse:
    """GET /api/channel/sessions -- detailed session status for VS Code panel."""
    from cohort.channel_bridge import get_all_sessions_status

    return JSONResponse(get_all_sessions_status())


async def channel_ensure_session(request: Request) -> JSONResponse:
    """POST /api/channel/ensure-session -- add a channel to the VS Code launch queue.

    Non-blocking.  The VS Code ChannelSessionLauncher polls the launch queue
    and spawns a terminal.  Callers should poll /api/channel/status until
    healthy before sending the first prompt.

    Expects JSON: {channel_id}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    channel_id = body.get("channel_id", "")
    if not channel_id:
        return JSONResponse({"error": "Missing channel_id"}, status_code=400)

    workspace_path = body.get("workspace_path")

    from cohort.channel_bridge import _add_to_launch_queue, channel_mode_active
    if channel_mode_active(channel_id):
        return JSONResponse({"ok": True, "already_alive": True, "channel_id": channel_id})

    _add_to_launch_queue(channel_id, workspace_path=workspace_path)
    return JSONResponse({"ok": True, "already_alive": False, "channel_id": channel_id})


async def channel_invoke(request: Request) -> JSONResponse:
    """POST /api/channel/invoke -- build the full agent prompt and enqueue it.

    Call this AFTER /api/channel/ensure-session and confirming the session is
    healthy via /api/channel/status.  Bypasses the global channel_mode_enabled
    flag -- the extension decides channel mode per-channel.

    Expects JSON: {agent_id, channel_id, message, thread_id?, workspace_path?, project_path?}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    raw_agent_id = body.get("agent_id", "")
    channel_id = body.get("channel_id", "")
    message = body.get("message", "")
    thread_id = body.get("thread_id")
    project_path = body.get("workspace_path") or body.get("project_path")
    reply_channel = body.get("reply_channel")  # Where to post the response (if different from channel_id)

    if not raw_agent_id or not channel_id or not message:
        missing = [n for n, v in [("agent_id", raw_agent_id), ("channel_id", channel_id), ("message", message)] if not v]
        return JSONResponse({"error": f"Missing required fields: {', '.join(missing)}"}, status_code=400)

    # Resolve agent_id from mention text (e.g., "Cohort" -> "cohort_orchestrator")
    from cohort.agent_router import resolve_agent_id
    agent_id = resolve_agent_id(raw_agent_id) or raw_agent_id

    try:
        import threading

        from cohort.agent_router import enqueue_agent_channel_request
        threading.Thread(
            target=enqueue_agent_channel_request,
            kwargs=dict(
                agent_id=agent_id,
                channel_id=channel_id,
                message=message,
                thread_id=thread_id,
                project_path=project_path,
                reply_channel=reply_channel,
            ),
            daemon=True,
        ).start()
        return JSONResponse({"ok": True, "agent_id": agent_id, "channel_id": channel_id})
    except Exception as exc:
        logger.exception("channel_invoke failed for %s in #%s", agent_id, channel_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


async def channel_register(request: Request) -> JSONResponse:
    """POST /api/channel/register -- register a new channel session."""
    from cohort.channel_bridge import register_channel_session

    try:
        body = await request.json()
    except Exception:
        body = {}

    result = register_channel_session(
        channel_id=body.get("channel_id", ""),
        session_id=body.get("session_id", "unknown"),
        pid=body.get("pid"),
    )
    status_code = 200 if result.get("ok") else 429
    return JSONResponse(result, status_code=status_code)


# =====================================================================
# Schedule endpoints
# =====================================================================

_task_store: Any = None  # TaskStore instance, set in create_app()
_scheduler: Any = None  # TaskScheduler instance, set in create_app()


async def get_schedules(request: Request) -> JSONResponse:
    """GET /api/schedules -- list all task schedules."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)
    enabled_only = request.query_params.get("enabled") == "true"
    schedules = _task_store.list_schedules(enabled_only=enabled_only)
    scheduler_status = _scheduler.status if _scheduler else {"running": False}
    return JSONResponse({
        "schedules": [s.to_dict() for s in schedules],
        "scheduler": scheduler_status,
    })


async def create_schedule_endpoint(request: Request) -> JSONResponse:
    """POST /api/schedules -- create a new task schedule."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    agent_id = body.get("agent_id")
    description = body.get("description")
    schedule_type = body.get("schedule_type")
    schedule_expr = body.get("schedule_expr")
    priority = body.get("priority", "medium")
    preset = body.get("preset")

    if not agent_id or not description:
        return JSONResponse({"error": "Missing agent_id or description"}, status_code=400)

    # Resolve preset
    if preset:
        try:
            from cohort.api import resolve_preset
            schedule_type, schedule_expr = resolve_preset(preset)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    if not schedule_type or not schedule_expr:
        return JSONResponse(
            {"error": "Missing schedule_type/schedule_expr or preset"},
            status_code=400,
        )

    try:
        from datetime import timezone

        from cohort.api import compute_next_run
        next_run = compute_next_run(
            schedule_type, schedule_expr, datetime.now(timezone.utc),
        )
        schedule = _task_store.create_schedule(
            agent_id=agent_id,
            description=description,
            schedule_type=schedule_type,
            schedule_expr=schedule_expr,
            priority=priority,
            next_run_at=next_run,
            created_by="user",
            metadata=body.get("metadata", {}),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse({"success": True, "schedule": schedule.to_dict()})


async def get_schedule_detail(request: Request) -> JSONResponse:
    """GET /api/schedules/{schedule_id} -- get a single schedule."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)
    schedule_id = request.path_params.get("schedule_id", "")
    schedule = _task_store.get_schedule(schedule_id)
    if schedule is None:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    # Include recent runs
    runs = _task_store.list_tasks(schedule_id=schedule_id, limit=10)
    return JSONResponse({
        "schedule": schedule.to_dict(),
        "recent_runs": runs,
    })


async def update_schedule_endpoint(request: Request) -> JSONResponse:
    """PATCH /api/schedules/{schedule_id} -- update a schedule."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)
    schedule_id = request.path_params.get("schedule_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    updates = {k: v for k, v in body.items() if k != "schedule_id"}
    schedule = _task_store.update_schedule(schedule_id, **updates)
    if schedule is None:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return JSONResponse({"success": True, "schedule": schedule.to_dict()})


async def delete_schedule_endpoint(request: Request) -> JSONResponse:
    """DELETE /api/schedules/{schedule_id} -- delete a schedule."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)
    schedule_id = request.path_params.get("schedule_id", "")
    success = _task_store.delete_schedule(schedule_id)
    if not success:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return JSONResponse({"success": True})


async def toggle_schedule_endpoint(request: Request) -> JSONResponse:
    """POST /api/schedules/{schedule_id}/toggle -- toggle enabled/disabled."""
    if _task_store is None:
        return JSONResponse({"error": "Task store not initialised"}, status_code=500)
    schedule_id = request.path_params.get("schedule_id", "")
    schedule = _task_store.toggle_schedule(schedule_id)
    if schedule is None:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return JSONResponse({"success": True, "enabled": schedule.enabled})


async def force_run_schedule_endpoint(request: Request) -> JSONResponse:
    """POST /api/schedules/{schedule_id}/run -- manually trigger a schedule."""
    if _scheduler is None:
        return JSONResponse({"error": "Scheduler not initialised"}, status_code=500)
    schedule_id = request.path_params.get("schedule_id", "")
    result = await _scheduler.force_run(schedule_id)
    if result is None:
        return JSONResponse({"error": "Schedule not found"}, status_code=404)
    return JSONResponse(result)


async def get_scheduler_status(request: Request) -> JSONResponse:
    """GET /api/scheduler/status -- get scheduler heartbeat info."""
    if _scheduler is None:
        return JSONResponse({"running": False})
    return JSONResponse(_scheduler.status)


async def get_schedule_presets(request: Request) -> JSONResponse:
    """GET /api/schedules/presets -- list available schedule presets."""
    from cohort.api import PRESETS, preset_label
    presets = [
        {"id": name, "label": preset_label(name), "type": ptype, "expr": pexpr}
        for name, (ptype, pexpr) in PRESETS.items()
    ]
    return JSONResponse({"presets": presets})


# =====================================================================
# Executive briefing
# =====================================================================


async def generate_briefing(request: Request) -> JSONResponse:
    """POST /api/briefing/generate -- generate an executive briefing.

    Optional JSON body:
        {"hours": 24, "post_to_channel": true, "channel": "daily-digest", "format": "json"}

    If ``format`` is ``"html"``, also generates the HTML report.
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
    fmt = body.get("format", "json")

    try:
        # Run blocking LLM-heavy generation in a thread to avoid
        # starving the async event loop (kills WebSocket pings).
        loop = asyncio.get_event_loop()
        if fmt == "html":
            html_path = await loop.run_in_executor(
                None,
                lambda: _briefing.generate_html(
                    hours=hours,
                    post_to_channel=post_to_channel,
                    channel_id=channel,
                ),
            )
            report = _briefing.get_latest()
            result: dict[str, Any] = {
                "success": True,
                "report": report.to_dict() if report else {},
            }
            if html_path:
                result["html_path"] = str(html_path)
            return JSONResponse(result)
        else:
            report = await loop.run_in_executor(
                None,
                lambda: _briefing.generate(
                    hours=hours,
                    post_to_channel=post_to_channel,
                    channel_id=channel,
                ),
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


async def get_latest_briefing_html(request: Request) -> HTMLResponse:
    """GET /api/briefing/latest/html -- serve the latest HTML briefing report."""
    if _briefing is None:
        return HTMLResponse(
            "<h1>Briefing system not initialised</h1>", status_code=500
        )

    html_path = _briefing.get_latest_html()
    if html_path is None or not html_path.exists():
        return HTMLResponse(
            "<h1>No HTML briefing available</h1>"
            "<p>Generate one via POST /api/briefing/generate with "
            '{"format": "html"}</p>',
            status_code=404,
        )

    try:
        content = html_path.read_text(encoding="utf-8")
        return HTMLResponse(content)
    except OSError as exc:
        return HTMLResponse(f"<h1>Error reading briefing</h1><p>{exc}</p>", status_code=500)


async def list_briefing_reports(request: Request) -> JSONResponse:
    """GET /api/briefing/list -- list recent HTML briefing reports."""
    if _briefing is None:
        return JSONResponse({"reports": []})

    reports_dir = _briefing._reports_dir
    if not reports_dir.exists():
        return JSONResponse({"reports": []})

    import re as _re
    files = sorted(reports_dir.glob("executive_briefing_*.html"), reverse=True)[:10]
    reports = []
    for f in files:
        m = _re.search(r"executive_briefing_(\d{4}-\d{2}-\d{2})\.html", f.name)
        if m:
            reports.append({"date": m.group(1), "filename": f.name})
    return JSONResponse({"reports": reports})


async def get_briefing_by_date(request: Request) -> HTMLResponse:
    """GET /api/briefing/{date}/html -- serve a specific date's HTML briefing."""
    if _briefing is None:
        return HTMLResponse("<h1>Briefing system not initialised</h1>", status_code=500)

    date_str = request.path_params["date"]
    import re as _re
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return HTMLResponse("<h1>Invalid date format</h1>", status_code=400)

    path = _briefing._reports_dir / f"executive_briefing_{date_str}.html"
    if not path.exists():
        return HTMLResponse(f"<h1>No briefing for {date_str}</h1>", status_code=404)

    try:
        content = path.read_text(encoding="utf-8")
        return HTMLResponse(content)
    except OSError as exc:
        return HTMLResponse(f"<h1>Error reading briefing</h1><p>{exc}</p>", status_code=500)


async def fetch_intel(request: Request) -> JSONResponse:
    """POST /api/intel/fetch -- trigger an RSS feed fetch.

    Optional JSON body: {"keywords": ["python", "ai"]}
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    keywords = body.get("keywords")

    try:
        from cohort.api import IntelFetcher

        data_dir = Path(_resolved_data_dir)
        fetcher = IntelFetcher(data_dir)
        new_count = fetcher.fetch(keywords=keywords)
        return JSONResponse({"success": True, "new_articles": new_count})
    except Exception as exc:
        logger.exception("Error fetching intel feeds")
        return JSONResponse({"error": str(exc)}, status_code=500)


# =====================================================================
# Benchmark A/B endpoints (dev tool -- gated by BENCHMARK_ENABLED)
# =====================================================================

def _benchmark_enabled() -> bool:
    from cohort.api import BENCHMARK_ENABLED
    return BENCHMARK_ENABLED


async def benchmark_status(request: Request) -> JSONResponse:
    """GET /api/benchmark/status -- check if benchmark is enabled."""
    return JSONResponse({"enabled": _benchmark_enabled()})


async def benchmark_scenarios(request: Request) -> JSONResponse:
    """GET /api/benchmark/scenarios -- list available benchmark scenarios."""
    if not _benchmark_enabled():
        return JSONResponse({"error": "Benchmark disabled", "enabled": False}, status_code=403)
    from cohort.api import get_benchmark_runner
    runner = get_benchmark_runner()
    return JSONResponse({"scenarios": runner.list_scenarios()})


async def benchmark_runs(request: Request) -> JSONResponse:
    """GET /api/benchmark/runs -- list recent benchmark runs."""
    from cohort.api import get_benchmark_runner
    runner = get_benchmark_runner()
    return JSONResponse({"runs": runner.list_runs()})


async def benchmark_run_detail(request: Request) -> JSONResponse:
    """GET /api/benchmark/runs/{run_id} -- get a specific run."""
    from cohort.api import get_benchmark_runner
    runner = get_benchmark_runner()
    run_id = request.path_params["run_id"]
    run = runner.get_run(run_id)
    if run is None:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return JSONResponse(run)


async def benchmark_start(request: Request) -> JSONResponse:
    """POST /api/benchmark/start -- start a new A/B benchmark run."""
    if not _benchmark_enabled():
        return JSONResponse({"error": "Benchmark disabled"}, status_code=403)
    from cohort.api import get_benchmark_runner
    runner = get_benchmark_runner()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    scenario_id = body.get("scenario_id")
    if not scenario_id:
        return JSONResponse({"error": "Missing scenario_id"}, status_code=400)

    if runner.is_running:
        return JSONResponse({"error": "A benchmark is already running"}, status_code=409)

    result = runner.start_run(scenario_id)
    if result is None:
        return JSONResponse({"error": "Unknown scenario"}, status_code=404)

    return JSONResponse({"status": "started", "run": result})


async def benchmark_score(request: Request) -> JSONResponse:
    """POST /api/benchmark/runs/{run_id}/score -- score one arm of a run."""
    from cohort.api import get_benchmark_runner
    runner = get_benchmark_runner()

    run_id = request.path_params["run_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    arm = body.get("arm")
    scores = body.get("scores", {})
    if arm not in ("a", "b"):
        return JSONResponse({"error": "arm must be 'a' or 'b'"}, status_code=400)

    result = runner.score_run(run_id, arm, scores)
    if result is None:
        return JSONResponse({"error": "Run not found"}, status_code=404)

    return JSONResponse({"status": "scored", "run": result})


async def benchmark_auto_score(request: Request) -> JSONResponse:
    """POST /api/benchmark/runs/{run_id}/auto-score -- trigger auto-scoring on a completed run."""
    from cohort.api import get_benchmark_runner
    runner = get_benchmark_runner()

    run_id = request.path_params["run_id"]
    result = runner.trigger_auto_score(run_id)
    if result is None:
        return JSONResponse({"error": "Run not found or not in scoreable state"}, status_code=404)

    return JSONResponse({"status": "scoring", "run_id": run_id})


async def get_agent_registry(request: Request) -> JSONResponse:
    """GET /api/agent-registry -- return all agent visual profiles (avatars, colors, nicknames)."""
    from cohort.api import get_all_agents
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
        from cohort.api import AgentCreator, AgentSpec, AgentType

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
        from cohort.api import MemoryManager

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
        from cohort.api import LearnedFact, MemoryManager

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
        from cohort.api import Orchestrator
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


# =========================================================================
# MEETING MODE / PARTICIPANT MANAGEMENT ENDPOINTS
# =========================================================================

async def session_add_participant(request: Request) -> JSONResponse:
    """POST /api/sessions/{session_id}/participants -- add participant."""
    session_id = request.path_params["session_id"]
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    agent_id = body.get("agent_id")
    if not agent_id:
        return JSONResponse({"success": False, "error": "agent_id is required"}, status_code=400)
    orch = _get_session_orch()
    ok = orch.add_participant(session_id, agent_id)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found, not active, or agent already present"}, status_code=400)
    return JSONResponse({"success": True})


async def session_remove_participant(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{session_id}/participants/{agent_id} -- remove participant."""
    session_id = request.path_params["session_id"]
    agent_id = request.path_params["agent_id"]
    orch = _get_session_orch()
    ok = orch.remove_participant(session_id, agent_id)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found or agent not in session"}, status_code=400)
    return JSONResponse({"success": True})


async def session_update_participant_status(request: Request) -> JSONResponse:
    """PUT /api/sessions/{session_id}/participants/{agent_id}/status -- promote/demote."""
    session_id = request.path_params["session_id"]
    agent_id = request.path_params["agent_id"]
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    status_str = body.get("status", "").lower()
    status_map = {
        "active": "active_stakeholder",
        "active_stakeholder": "active_stakeholder",
        "silent": "approved_silent",
        "approved_silent": "approved_silent",
        "observer": "observer",
        "dormant": "dormant",
    }
    mapped = status_map.get(status_str)
    if not mapped:
        return JSONResponse({
            "success": False,
            "error": f"Invalid status '{status_str}'. Use: active, silent, observer, dormant",
        }, status_code=400)

    from cohort.meeting import StakeholderStatus
    status_enum = StakeholderStatus(mapped)
    orch = _get_session_orch()
    ok = orch.update_participant_status(session_id, agent_id, status_enum)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found or agent not in session"}, status_code=400)
    return JSONResponse({"success": True})


async def session_score_agent(request: Request) -> JSONResponse:
    """GET /api/sessions/{session_id}/score/{agent_id} -- agent relevance breakdown."""
    session_id = request.path_params["session_id"]
    agent_id = request.path_params["agent_id"]
    orch = _get_session_orch()
    result = orch.score_agent(session_id, agent_id)
    if not result:
        return JSONResponse({"success": False, "error": "Session or agent not found"}, status_code=404)
    return JSONResponse({"success": True, "score": result})


async def session_extend_turns(request: Request) -> JSONResponse:
    """POST /api/sessions/{session_id}/extend -- add more turns."""
    session_id = request.path_params["session_id"]
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    additional = body.get("turns", 10)
    orch = _get_session_orch()
    ok = orch.extend_turns(session_id, additional_turns=additional)
    if not ok:
        return JSONResponse({"success": False, "error": "Session not found"}, status_code=404)
    return JSONResponse({"success": True})


async def channel_meeting_enable(request: Request) -> JSONResponse:
    """POST /api/channels/{channel_id}/meeting-mode -- enable meeting mode."""
    channel_id = request.path_params["channel_id"]
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    agents = body.get("agents", [])
    topic = body.get("topic", "")
    if not agents:
        return JSONResponse({"success": False, "error": "agents list is required"}, status_code=400)

    chat = _get_chat()
    channel = chat.get_channel(channel_id)
    if not channel:
        return JSONResponse({"success": False, "error": f"Channel '{channel_id}' not found"}, status_code=404)

    from cohort.meeting import enable_meeting_mode
    context = enable_meeting_mode(channel, agents, chat, topic=topic)
    return JSONResponse({"success": True, "meeting_context": context})


async def channel_meeting_disable(request: Request) -> JSONResponse:
    """DELETE /api/channels/{channel_id}/meeting-mode -- disable meeting mode."""
    channel_id = request.path_params["channel_id"]
    chat = _get_chat()
    channel = chat.get_channel(channel_id)
    if not channel:
        return JSONResponse({"success": False, "error": f"Channel '{channel_id}' not found"}, status_code=404)

    from cohort.meeting import disable_meeting_mode
    was_active = disable_meeting_mode(channel, chat)
    return JSONResponse({"success": True, "was_active": was_active})


async def channel_meeting_context(request: Request) -> JSONResponse:
    """GET /api/channels/{channel_id}/meeting-context -- introspect meeting state."""
    channel_id = request.path_params["channel_id"]
    orch = _get_session_orch()
    ctx = orch.get_meeting_context(channel_id)
    return JSONResponse({"success": True, "meeting_context": ctx})


async def channel_detect_phase(request: Request) -> JSONResponse:
    """GET /api/channels/{channel_id}/phase -- detect discussion phase."""
    channel_id = request.path_params["channel_id"]
    chat = _get_chat()
    recent = chat.get_channel_messages(channel_id, limit=10)
    if not recent:
        return JSONResponse({"success": True, "phase": "DISCOVER", "evidence": []})

    from cohort.meeting import detect_current_phase, extract_keywords
    phase = detect_current_phase(recent)
    # Gather evidence keywords from recent messages
    evidence = []
    for msg in recent[-5:]:
        kw = extract_keywords(msg.content)
        if kw:
            evidence.append({"sender": msg.sender, "keywords": kw[:5]})
    return JSONResponse({"success": True, "phase": phase, "evidence": evidence})


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

        # Delete old messages via storage API (works with both JSON and SQLite)
        for msg in all_messages:
            if msg.id not in keep_ids:
                storage.delete_message(msg.id, channel_id=channel_id)

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
# Tools endpoint (reads from cohort_tools.json)
# =====================================================================

async def list_tools(request: Request) -> JSONResponse:
    """GET /api/tools -- return workflow tools defined in cohort_tools.json."""
    tool_filter = _load_tool_filter()
    allowed_ids = tool_filter[0] if tool_filter else None
    display_names = tool_filter[1] if tool_filter else {}
    tools_cfg = _load_tools_config()
    native_descriptions = tools_cfg.get("descriptions", {})

    tools: list[dict[str, Any]] = []

    if allowed_ids is not None:
        for tid in allowed_ids:
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

    # Check if Smartest mode is available (cloud API, dev-mode CLI, or CLI handoff)
    smartest_available = False
    try:
        from cohort.agent_router import _channel_mode_enabled, _dev_mode, check_claude_cli_available
        from cohort.channel_bridge import channel_mode_active
        from cohort.local.cloud import check_cloud_available
        smartest_available = (
            check_cloud_available(settings)
            or channel_mode_active()
            or (_dev_mode and check_claude_cli_available())
            or check_claude_cli_available()  # Handoff mode (no harvest)
        )
    except ImportError:
        pass

    # Channel mode available if global setting is on OR any channel session is healthy
    channel_available = False
    try:
        from cohort.agent_router import _channel_mode_enabled
        from cohort.channel_bridge import channel_mode_active
        channel_available = bool(_channel_mode_enabled) or channel_mode_active()
    except ImportError:
        pass

    # Mask cloud API key
    cloud_api_key = settings.get("cloud_api_key", "")
    if cloud_api_key:
        cloud_key_masked = "sk-..." + cloud_api_key[-4:] if len(cloud_api_key) > 8 else "sk-...(set)"
    else:
        cloud_key_masked = ""

    return JSONResponse({
        "api_key_masked": masked,
        "claude_enabled": settings.get("claude_enabled", False),
        "claude_cmd": claude_cmd,
        "agents_root": settings.get("agents_root", ""),
        "response_timeout": settings.get("response_timeout", 300),
        "execution_backend": settings.get("execution_backend", "cli"),
        "claude_code_connected": claude_connected,
        "smartest_available": smartest_available,
        "channel_available": channel_available,
        "admin_mode": settings.get("admin_mode", False),
        "dev_mode": settings.get("dev_mode", False),
        "force_to_claude_code": settings.get("force_to_claude_code", False),
        "channel_mode": settings.get("channel_mode", False),
        "cloud_provider": settings.get("cloud_provider", ""),
        "cloud_api_key_masked": cloud_key_masked,
        "cloud_model": settings.get("cloud_model", ""),
        "cloud_base_url": settings.get("cloud_base_url", ""),
        "global_agents_linked": settings.get("global_agents_linked", False),
        "user_display_name": settings.get("user_display_name", ""),
        "user_display_role": settings.get("user_display_role", ""),
        "user_display_avatar": settings.get("user_display_avatar", ""),
        "permission_tier": settings.get("permission_tier", os.environ.get("COHORT_TIER", "unrestricted")),
        "tier_settings": _load_tier_settings(),
        "token_usage": _get_token_usage_summary(),
        "default_permissions": settings.get("default_permissions", {
            "profile": "developer",
            "deny_paths": [],
            "max_turns": 15,
        }),
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
        if body["execution_backend"] in ("cli", "api", "chat", "channel"):
            settings["execution_backend"] = body["execution_backend"]
    if "claude_enabled" in body:
        settings["claude_enabled"] = bool(body["claude_enabled"])
    if "admin_mode" in body:
        settings["admin_mode"] = bool(body["admin_mode"])
    if "force_to_claude_code" in body:
        settings["force_to_claude_code"] = bool(body["force_to_claude_code"])
    if "dev_mode" in body:
        settings["dev_mode"] = bool(body["dev_mode"])
    if "channel_mode" in body:
        settings["channel_mode"] = bool(body["channel_mode"])
    if "permission_tier" in body:
        tier_val = str(body["permission_tier"]).strip().lower()
        if tier_val in ("sandbox", "local", "unrestricted"):
            settings["permission_tier"] = tier_val
            os.environ["COHORT_TIER"] = tier_val
            try:
                from cohort.permissions import reset_tier_cache
                reset_tier_cache()
            except ImportError:
                pass
    if "cloud_provider" in body:
        if body["cloud_provider"] in ("", "anthropic", "openai"):
            settings["cloud_provider"] = body["cloud_provider"]
    if "cloud_api_key" in body:
        settings["cloud_api_key"] = body["cloud_api_key"]
    if "cloud_model" in body:
        settings["cloud_model"] = str(body["cloud_model"]).strip()[:80]
    if "cloud_base_url" in body:
        settings["cloud_base_url"] = str(body["cloud_base_url"]).strip()[:200]
    if "global_agents_linked" in body:
        want = bool(body["global_agents_linked"])
        settings["global_agents_linked"] = want
        _apply_global_agent_links(want)
    if "user_display_name" in body:
        name = str(body["user_display_name"]).strip()[:40]
        settings["user_display_name"] = name
    if "user_display_role" in body:
        role = str(body["user_display_role"]).strip()[:40]
        settings["user_display_role"] = role
    if "user_display_avatar" in body:
        avatar = str(body["user_display_avatar"]).strip().upper()[:3]
        settings["user_display_avatar"] = avatar
    if "default_permissions" in body:
        dp = body["default_permissions"]
        if isinstance(dp, dict):
            valid_profiles = {"readonly", "developer", "researcher", "research_local", "research_hybrid", "minimal"}
            profile = dp.get("profile", "developer")
            if profile not in valid_profiles:
                profile = "developer"
            deny_paths = [str(p) for p in dp.get("deny_paths", []) if str(p).strip()]
            settings["default_permissions"] = {
                "profile": profile,
                "allow_paths": [],
                "deny_paths": deny_paths,
                "allowed_tools": {
                    "developer":  ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                    "readonly":   ["Read", "Glob", "Grep"],
                    "researcher": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
                    "research_local": ["Read", "Glob", "Grep"],
                    "research_hybrid": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
                    "minimal": [],
                }.get(profile, ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]),
                "max_turns": max(1, min(50, int(dp.get("max_turns", 15)))),
            }

    # Save tier settings separately (own file, not in settings.json)
    if "tier_settings" in body and isinstance(body["tier_settings"], dict):
        tier_data = body["tier_settings"]
        # Budget limits are nested under tier_settings.budget
        if "budget" in tier_data:
            # Merge budget into the existing tier settings file
            pass  # budget is saved as part of tier_settings below
        _save_tier_settings(tier_data)

    # Channel session settings
    if "channel_session_limit" in body:
        val = body["channel_session_limit"]
        if isinstance(val, int) and 1 <= val <= 20:
            settings["channel_session_limit"] = val
    if "channel_session_warn" in body:
        val = body["channel_session_warn"]
        if isinstance(val, int) and 1 <= val <= 20:
            settings["channel_session_warn"] = val
    if "channel_session_default" in body:
        val = body["channel_session_default"]
        if isinstance(val, int) and 0 <= val <= 10:
            settings["channel_session_default"] = val
    if "channel_idle_timeout" in body:
        val = body["channel_idle_timeout"]
        if isinstance(val, int) and 60 <= val <= 3600:
            settings["channel_idle_timeout"] = val
    if "channel_auto_launch" in body:
        settings["channel_auto_launch"] = bool(body["channel_auto_launch"])

    _save_settings(settings)

    # Update global defaults if any machine-level keys changed
    if _GLOBAL_DEFAULT_KEYS & set(body):
        _save_global_defaults(settings)

    # Hot-reload channel session settings
    try:
        from cohort.channel_bridge import apply_channel_settings
        apply_channel_settings(
            limit=settings.get("channel_session_limit", 5),
            warn=settings.get("channel_session_warn", 3),
            default=settings.get("channel_session_default", 1),
            idle_timeout=settings.get("channel_idle_timeout", 600),
            auto_launch=settings.get("channel_auto_launch", False),
        )
    except ImportError:
        pass

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
# User Profile endpoints
# =====================================================================

async def get_profile(request: Request) -> JSONResponse:
    """GET /api/settings/profile -- return user profile or defaults."""
    from cohort.learning import load_profile

    profile = load_profile()
    if profile is None:
        # Return empty defaults so UI can show the form
        settings = _load_settings()
        profile = {
            "version": "1.0",
            "core_paragraph": "",
            "adaptation_rules": {
                "response_length": "medium",
                "summarize_back": True,
                "confirm_decisions": True,
                "praise_before_feedback": True,
                "options_per_question": 3,
                "custom_rules": [],
            },
            "display_name": settings.get("user_display_name", ""),
            "display_role": settings.get("user_display_role", ""),
            "exists": False,
        }
    else:
        profile["exists"] = True

    return JSONResponse(profile)


async def put_profile(request: Request) -> JSONResponse:
    """PUT /api/settings/profile -- create or update user profile."""
    from cohort.learning import bootstrap_profile, load_profile

    body = await request.json()
    core_paragraph = body.get("core_paragraph", "").strip()
    adaptation_rules = body.get("adaptation_rules")

    settings = _load_settings()
    display_name = body.get("display_name", settings.get("user_display_name", "User"))
    display_role = body.get("display_role", settings.get("user_display_role", ""))

    # Create or update
    existing = load_profile()
    if existing is None:
        profile = bootstrap_profile(display_name, display_role, core_paragraph)
    else:
        import json as _json
        from datetime import datetime as _dt
        from pathlib import Path as _P

        if core_paragraph:
            existing["core_paragraph"] = core_paragraph
        if adaptation_rules and isinstance(adaptation_rules, dict):
            if "adaptation_rules" not in existing:
                existing["adaptation_rules"] = {}
            existing["adaptation_rules"].update(adaptation_rules)
        existing["last_updated"] = _dt.now().isoformat()
        profile_path = _P.home() / ".cohort" / "profile.json"
        profile_path.write_text(_json.dumps(existing, indent=2), encoding="utf-8")
        profile = existing

        # Reset cached profile path
        try:
            import cohort.agent_context as ac
            ac._DEFAULT_PROFILE_PATH = None  # noqa: SLF001
        except Exception:
            pass

    return JSONResponse({"success": True, "profile": profile})


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
# Service Key Testing API
# =====================================================================

async def test_service_key(request: Request) -> JSONResponse:
    """POST /api/service-keys/test -- verify a service key connects successfully.

    Body: { "service_id": "anthropic_default" }
    Returns: { "success": true/false, "message": "...", "latency_ms": 123 }
    """
    import time

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse({"success": False, "message": "Invalid JSON body"}, status_code=400)

    service_id = body.get("service_id", "")
    if not service_id:
        return JSONResponse({"success": False, "message": "Missing service_id"}, status_code=400)

    settings = _load_settings()
    raw_services = settings.get("service_keys", [])
    svc = next((s for s in raw_services if s.get("id") == service_id), None)
    if not svc:
        return JSONResponse({"success": False, "message": "Service not found"})

    svc_type = svc.get("type", "custom")
    key = svc.get("key", "")
    extra = svc.get("extra", "")

    if not key and svc_type not in ("internal_web", "internal_web_search", "rss"):
        return JSONResponse({"success": False, "message": "No API key configured"})

    # Parse extra fields (JSON string with additional credentials)
    extra_data = {}
    if extra:
        try:
            extra_data = json.loads(extra)
        except (json.JSONDecodeError, TypeError):
            pass

    t0 = time.monotonic()

    try:
        result = await _test_service_connection(svc_type, key, extra_data)
        latency = int((time.monotonic() - t0) * 1000)
        result["latency_ms"] = latency
        return JSONResponse(result)
    except Exception as exc:
        latency = int((time.monotonic() - t0) * 1000)
        return JSONResponse({
            "success": False,
            "message": f"Connection failed: {exc}",
            "latency_ms": latency,
        })


async def _test_service_connection(svc_type: str, key: str, extra: dict) -> dict:
    """Run a lightweight connectivity test for a given service type.

    Returns {"success": bool, "message": str}.
    """
    import urllib.error
    import urllib.request

    def _http_test(url: str, headers: dict | None = None, method: str = "GET") -> dict:
        """Synchronous HTTP request helper for API tests."""
        req = urllib.request.Request(url, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                if 200 <= status < 300:
                    return {"success": True, "message": f"Connected (HTTP {status})"}
                return {"success": False, "message": f"Unexpected status: HTTP {status}"}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"success": False, "message": "Authentication failed (401) -- check your API key"}
            if e.code == 403:
                return {"success": False, "message": "Access denied (403) -- key may lack required permissions"}
            return {"success": False, "message": f"HTTP error: {e.code} {e.reason}"}
        except urllib.error.URLError as e:
            return {"success": False, "message": f"Connection error: {e.reason}"}

    # --- Anthropic ---
    if svc_type == "anthropic":
        # Minimal messages API call with max_tokens=1
        import urllib.request
        url = "https://api.anthropic.com/v1/messages"
        data = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: F841
                return {"success": True, "message": "API key valid -- connected to Anthropic"}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"success": False, "message": "Invalid API key (401)"}
            if e.code == 403:
                return {"success": False, "message": "API key lacks permissions (403)"}
            if e.code == 429:
                # Rate limited means the key IS valid
                return {"success": True, "message": "API key valid (rate limited, try again later)"}
            if e.code == 400:
                body_text = e.read().decode("utf-8", errors="replace")
                # Key is valid if Anthropic recognized it but rejected for billing
                if "credit balance" in body_text or "billing" in body_text.lower():
                    return {"success": True, "message": "API key valid (account has no credits -- check Plans & Billing)"}
                return {"success": False, "message": f"Bad request (400): {body_text[:200]}"}
            return {"success": False, "message": f"HTTP {e.code}: {e.reason}"}
        except urllib.error.URLError as e:
            return {"success": False, "message": f"Connection failed: {e.reason}"}

    # --- OpenAI ---
    if svc_type == "openai":
        return _http_test("https://api.openai.com/v1/models", headers={
            "Authorization": f"Bearer {key}",
        })

    # --- GitHub ---
    if svc_type == "github":
        return _http_test("https://api.github.com/user", headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Cohort-ServiceTest/1.0",
        })

    # --- YouTube ---
    if svc_type == "youtube":
        # YouTube Data API key test -- list a public video category
        url = f"https://www.googleapis.com/youtube/v3/videoCategories?part=snippet&regionCode=US&key={key}"
        result = _http_test(url)
        if not result["success"] and "403" in result["message"]:
            result["message"] = "API key valid but YouTube Data API v3 is not enabled -- enable it in Google Cloud Console"
            result["success"] = True  # key itself is valid
        return result

    # --- Google Cloud ---
    if svc_type == "google":
        # OAuth token test
        return _http_test("https://www.googleapis.com/oauth2/v1/tokeninfo", headers={
            "Authorization": f"Bearer {key}",
        })

    # --- Cloudflare ---
    if svc_type == "cloudflare":
        return _http_test("https://api.cloudflare.com/client/v4/user/tokens/verify", headers={
            "Authorization": f"Bearer {key}",
        })

    # --- Resend ---
    if svc_type == "resend":
        result = _http_test("https://api.resend.com/api-keys", headers={
            "Authorization": f"Bearer {key}",
        })
        if not result["success"] and "403" in result["message"]:
            # 403 means the key authenticated but has limited scope (e.g. sending-only)
            result["success"] = True
            result["message"] = "API key valid (sending-only scope -- cannot list api-keys)"
        return result

    # --- Twitter/X ---
    if svc_type == "twitter":
        # Use bearer token if available in extra, otherwise use main key
        bearer = extra.get("bearer_token") or key
        return _http_test("https://api.twitter.com/2/users/me", headers={
            "Authorization": f"Bearer {bearer}",
        })

    # --- Reddit ---
    if svc_type == "reddit":
        return _http_test("https://www.reddit.com/api/v1/me", headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": "Cohort-ServiceTest/1.0",
        })

    # --- LinkedIn ---
    if svc_type == "linkedin":
        return _http_test("https://api.linkedin.com/v2/userinfo", headers={
            "Authorization": f"Bearer {key}",
        })

    # --- Slack (webhook) ---
    if svc_type == "slack":
        # Slack webhooks don't have a "test" endpoint. We can only verify the URL format.
        if key.startswith("https://hooks.slack.com/"):
            return {"success": True, "message": "Webhook URL format valid (cannot verify without sending)"}
        return {"success": False, "message": "Invalid Slack webhook URL -- should start with https://hooks.slack.com/"}

    # --- Discord (webhook) ---
    if svc_type == "discord":
        if key.startswith("https://discord.com/api/webhooks/") or key.startswith("https://discordapp.com/api/webhooks/"):
            # Discord webhooks support GET to verify
            return _http_test(key)
        return {"success": False, "message": "Invalid Discord webhook URL"}

    # --- AWS ---
    if svc_type == "aws":
        # AWS needs access_key + secret_key -- can test with STS GetCallerIdentity
        secret = extra.get("secret_access_key", "")
        region = extra.get("region", "us-east-1")
        if not secret:
            return {"success": False, "message": "Missing Secret Access Key in extra fields"}
        # Lightweight check: just verify credentials are non-empty and formatted correctly
        if len(key) == 20 and key.startswith("AKIA"):
            return {"success": True, "message": f"Credentials format valid (region: {region}) -- full STS test not implemented"}
        return {"success": True, "message": f"Credentials set (region: {region}) -- format unrecognized but may still work"}

    # --- Email SMTP ---
    if svc_type == "email_smtp":
        import smtplib
        host = extra.get("host", "")
        port = int(extra.get("port", 587))
        username = extra.get("username", "")
        if not host:
            return {"success": False, "message": "Missing SMTP host in extra fields"}
        try:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.ehlo()
                if port in (587, 465):
                    smtp.starttls()
                if username and key:
                    smtp.login(username, key)
                return {"success": True, "message": f"SMTP connected to {host}:{port}"}
        except smtplib.SMTPAuthenticationError:
            return {"success": False, "message": "SMTP authentication failed -- check username/password"}
        except Exception as e:
            return {"success": False, "message": f"SMTP connection failed: {e}"}

    # --- Email IMAP ---
    if svc_type == "email_imap":
        import imaplib
        host = extra.get("host", "")
        port = int(extra.get("port", 993))
        username = extra.get("username", "")
        if not host:
            return {"success": False, "message": "Missing IMAP host in extra fields"}
        try:
            imap = imaplib.IMAP4_SSL(host, port) if port == 993 else imaplib.IMAP4(host, port)
            if username and key:
                imap.login(username, key)
            imap.logout()
            return {"success": True, "message": f"IMAP connected to {host}:{port}"}
        except imaplib.IMAP4.error as e:
            return {"success": False, "message": f"IMAP auth failed: {e}"}
        except Exception as e:
            return {"success": False, "message": f"IMAP connection failed: {e}"}

    # --- Internal Web (local) ---
    if svc_type in ("internal_web", "internal_web_search"):
        parts = []
        try:
            import playwright  # noqa: F401
            parts.append("Playwright: OK")
        except ImportError:
            parts.append("Playwright: not installed")
        try:
            import ddgs  # noqa: F401
            parts.append("DuckDuckGo Search: OK")
        except ImportError:
            parts.append("DuckDuckGo Search: not installed")
        ok = any("OK" in p for p in parts)
        return {"success": ok, "message": " | ".join(parts)}

    # --- RSS ---
    if svc_type == "rss":
        return {"success": True, "message": "RSS feeds are fetched on-demand -- no key needed"}

    # --- Fallback for custom/unknown ---
    return {"success": False, "message": f"No test available for service type '{svc_type}'"}


# =====================================================================
# Tool Permissions API (per-agent tool access)
# =====================================================================

# Canonical list of tools that can be toggled from the dashboard.
_ALL_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "InternalWebFetch", "InternalWebSearch", "BrowserBrowse", "BrowserInteract", "BrowserAdvanced"]


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
    from cohort.api import reload_central_permissions
    reload_central_permissions(path.parent)


async def get_tool_permissions(request: Request) -> JSONResponse:
    """GET /api/tool-permissions -- profiles, defaults, and per-agent resolved view."""
    import re

    from cohort.api import resolve_permissions

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
                "file_permissions": resolved.file_permissions if resolved else [],
            })

    file_permissions = central.get("file_permissions", {"defaults": {}, "agent_overrides": {}})

    return JSONResponse({
        "profiles": profiles,
        "agent_defaults": agent_defaults,
        "denied_tools": denied_tools,
        "all_tools": _ALL_TOOLS,
        "agent_overrides": agent_overrides,
        "agents": agents_list,
        "file_permissions": file_permissions,
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
            or ov.get("file_permissions_override") is not None
        }

    if "file_permissions" in body:
        central["file_permissions"] = body["file_permissions"]

    _save_tool_permissions(central)

    agent_count = len(central.get("agent_overrides", {}))
    logger.info("[OK] Tool permissions saved (%d agent overrides)", agent_count)
    return JSONResponse({"success": True})


async def get_internal_web_status(request: Request) -> JSONResponse:
    """GET /api/internal-web/status -- check if Internal Web Accessor is available."""
    status: dict[str, Any] = {
        "available": False,
        "web_adapter": False,
        "playwright": False,
        "ddgs": False,
        "cache_dir": str(_cohort_data_dir() / "web_cache"),
    }

    # Check Playwright (for internal_web_fetch)
    try:
        import playwright  # noqa: F401
        status["playwright"] = True
    except ImportError:
        pass

    # Check ddgs (for internal_web_search)
    try:
        import ddgs as _ddgs_mod  # noqa: F401
        status["ddgs"] = True
    except ImportError:
        pass

    # Browser backend status
    status["browser_backend"] = False
    if status["playwright"]:
        status["browser_backend"] = True
        status["browser_backend_type"] = "PlaywrightDirect"

    # Available if either capability works
    status["available"] = status["playwright"] or status["ddgs"]

    return JSONResponse(status)


async def get_internal_web_search_status(request: Request) -> JSONResponse:
    """GET /api/internal-web-search/status -- check if Internal Web Search is available."""
    status: dict[str, Any] = {
        "available": False,
        "ddgs": False,
    }

    try:
        import ddgs as _ddgs_mod  # noqa: F401
        status["ddgs"] = True
    except ImportError:
        pass

    status["available"] = status["ddgs"]
    return JSONResponse(status)


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
    from cohort.api import MODEL_DESCRIPTIONS, detect_hardware, get_model_for_vram

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

    from cohort.api import OllamaClient

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
    """POST /api/setup/verify -- test inference with the selected model.

    Runs two tests:
    1. Basic generation (no thinking) -- confirms model works
    2. Thinking mode test -- confirms /think works for Smarter mode

    Returns both results so the UI can show thinking mode status.
    """
    from cohort.api import OllamaClient

    settings = _load_settings()
    model = settings.get("model_name", "")
    if not model:
        return JSONResponse({"error": "No model selected"}, status_code=400)

    client = OllamaClient(timeout=120)

    # Test 1: Basic generation (Smart mode equivalent)
    result = await asyncio.to_thread(
        client.generate,
        model=model,
        prompt="What makes a good code review? Answer in two sentences.",
        temperature=0.3,
    )

    if not result or not result.text.strip():
        return JSONResponse({
            "success": False,
            "error": "Model produced no output. First run can be slow -- try again.",
        })

    basic_text = result.text.strip()
    basic_elapsed = result.elapsed_seconds

    # Test 2: Thinking mode (Smarter mode equivalent)
    thinking_ok = False
    thinking_error = ""
    try:
        think_result = await asyncio.to_thread(
            client.generate,
            model=model,
            prompt="Is 17 a prime number? Think step by step, then answer yes or no.",
            temperature=0.3,
            think=True,
        )
        if think_result and think_result.text.strip():
            thinking_ok = True
    except Exception as exc:
        thinking_error = str(exc)

    settings["model_verified"] = True
    _save_settings(settings)

    return JSONResponse({
        "success": True,
        "text": basic_text,
        "elapsed_seconds": basic_elapsed,
        "model": model,
        "thinking_ok": thinking_ok,
        "thinking_error": thinking_error,
    })


async def setup_get_topics(request: Request) -> JSONResponse:
    """GET /api/setup/topics -- return available content pipeline topics."""
    from cohort.api import TOPIC_CATEGORIES, TOPIC_FEEDS, TOPIC_KEYWORDS

    topics = {}
    for topic, feeds in TOPIC_FEEDS.items():
        topics[topic] = [{"name": f["name"], "url": f["url"]} for f in feeds]
    return JSONResponse({
        "topics": topics,
        "categories": TOPIC_CATEGORIES,
        "topic_keywords": TOPIC_KEYWORDS,
    })


async def setup_save_config(request: Request) -> JSONResponse:
    """POST /api/setup/save-config -- save content config + mark setup complete."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    feeds = body.get("feeds", [])
    topic = body.get("topic", "")
    keywords = body.get("interest_keywords", [])

    if feeds or keywords:
        config: dict[str, Any] = {
            "feeds": feeds,
            "topic": topic,
            "check_interval_minutes": 60,
            "max_articles_per_feed": 10,
        }
        if keywords:
            config["interest_keywords"] = keywords
        config_path = Path(_resolved_data_dir) / "content_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    settings = _load_settings()
    settings["setup_completed"] = True
    settings["content_topic"] = topic
    _save_settings(settings)

    # Persist machine-level settings so future workspaces inherit them
    _save_global_defaults(settings)

    return JSONResponse({"success": True})


async def setup_check_mcp(request: Request) -> JSONResponse:
    """POST /api/setup/check-mcp -- check MCP deps, Ollama, and model availability."""

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
        from cohort.api import OllamaClient
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
            from cohort.api import OllamaClient
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


async def setup_global_agents(request: Request) -> JSONResponse:
    """POST /api/setup/global-agents -- junction ~/.claude/agents and skills to Cohort.

    Makes Cohort agents available in any Claude Code project, not just the
    Cohort folder.  Non-blocking -- returns success:false with a message on
    failure rather than raising.
    """
    import os
    import platform
    import subprocess as _sp

    cohort_root = Path(__file__).resolve().parent.parent
    global_claude = Path.home() / ".claude"
    global_claude.mkdir(exist_ok=True)

    targets = {
        "agents": cohort_root / ".claude" / "agents",
        "skills": cohort_root / ".claude" / "skills",
    }

    plat = platform.system()
    warnings: list[str] = []

    for name, source in targets.items():
        link = global_claude / name

        if not source.exists():
            warnings.append(f"Source not found: {source}")
            continue

        if link.exists():
            if not os.path.islink(str(link)):
                warnings.append(
                    f"{link} exists as a real directory -- remove it manually to enable global agents"
                )
            # Already linked or real dir handled above -- skip
            continue

        try:
            if plat == "Windows":
                _sp.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(source)],
                    check=True, capture_output=True, text=True,
                )
            else:
                link.symlink_to(source)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not link {name}: {exc}")

    if warnings:
        return JSONResponse({"success": False, "warnings": warnings})
    return JSONResponse({"success": True})


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


def _check_tool_readiness(service_id: str) -> dict[str, Any]:
    """Check readiness of a sidebar tool using internal logic (no external HTTP proxying).

    Returns dict with 'status' ('up', 'down', 'not_configured') and 'detail'.
    """
    import urllib.request

    # Built-in tools: always up if the server is running
    BUILTIN_TOOLS = {
        "document_processor", "website_creator", "project_manager",
        "hardware_monitor",
    }
    if service_id in BUILTIN_TOOLS:
        return {"status": "up", "detail": "Built-in feature"}

    if service_id in ("intel_scheduler", "content_monitor_scheduler"):
        return {"status": "up", "detail": "Built-in scheduler"}

    if service_id == "health_monitor":
        from cohort.api import list_services
        svcs = list_services()
        return {"status": "up" if svcs else "unknown", "detail": {"services": len(svcs)}}

    # LLM Manager: check Ollama health
    if service_id == "llm_manager":
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    try:
                        body = json.loads(resp.read().decode("utf-8"))
                        model_count = len(body.get("models", []))
                        return {"status": "up", "detail": f"Ollama responding ({model_count} models)"}
                    except Exception:
                        return {"status": "up", "detail": "Ollama responding"}
        except Exception:
            return {"status": "down", "detail": "Ollama not responding"}

    # Web Search: check if ddgs is importable or search API key configured
    if service_id == "web_search":
        ddgs_available = False
        try:
            import ddgs as _ddgs_mod  # noqa: F401
            ddgs_available = True
        except ImportError:
            pass
        if ddgs_available:
            return {"status": "up", "detail": "DuckDuckGo available"}
        # Check for SerpAPI/Serper keys
        settings = _load_settings()
        for svc in settings.get("service_keys", []):
            if svc.get("type") in ("serpapi", "serper") and svc.get("key"):
                return {"status": "up", "detail": f"{svc['type'].title()} API key configured"}
        return {"status": "not_configured", "detail": "Install ddgs or add a search API key"}

    # YouTube: check for YouTube API key
    if service_id == "youtube_service":
        settings = _load_settings()
        for svc in settings.get("service_keys", []):
            if svc.get("type") == "youtube" and svc.get("key"):
                return {"status": "up", "detail": "YouTube API key configured"}
        return {"status": "not_configured", "detail": "No YouTube API key configured"}

    # Comms Service: check if the Cohort-owned comms service is running
    if service_id == "comms_service":
        try:
            req = urllib.request.Request("http://127.0.0.1:8001/health", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                svc_status = "up" if resp.status == 200 else "down"
        except Exception:
            svc_status = "down"
        # Check if Resend key is configured
        settings = _load_settings()
        has_resend = any(
            svc.get("type") == "resend" and svc.get("key")
            for svc in settings.get("service_keys", [])
        )
        result: dict[str, Any] = {"status": svc_status, "detail": "Service running" if svc_status == "up" else "Service offline"}
        result["providers"] = {
            "email": {"name": "Resend", "status": "configured" if has_resend else "not_configured"},
            "calendar": {"name": "Google Calendar", "status": svc_status},
            "social": {
                "twitter": {"status": "not_configured"},
                "linkedin": {"status": "not_configured"},
                "facebook": {"status": "not_configured"},
                "threads": {"status": "not_configured"},
            },
        }
        return result

    # If the tool exists in the tools config but has no specific check,
    # treat it as a built-in feature that's always available.
    tools_cfg = _load_tools_config()
    if service_id in tools_cfg.get("tools", []):
        return {"status": "up", "detail": "Built-in feature"}

    return {"status": "unknown", "detail": "Unknown tool"}


async def get_service_status(request: Request) -> JSONResponse:
    """GET /api/service-status/{service_id} -- check tool readiness via internal logic."""
    service_id = request.path_params["service_id"]
    return JSONResponse(_check_tool_readiness(service_id))


async def get_tool_readiness_all(request: Request) -> JSONResponse:
    """GET /api/tool-readiness/all -- return readiness status for all sidebar tools."""
    tools_cfg = _load_tools_config()
    tool_ids = tools_cfg.get("tools", [])
    display_names = tools_cfg.get("display_names", {})

    tools = []
    for tid in tool_ids:
        result = _check_tool_readiness(tid)
        tools.append({
            "id": tid,
            "name": display_names.get(tid, tid.replace("_", " ").title()),
            "status": result.get("status", "unknown"),
            "detail": result.get("detail", ""),
        })
    return JSONResponse({"tools": tools})


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


async def get_tool_context(request: Request) -> JSONResponse:
    """GET /api/tool-context/{tool_id} -- rich context for the help agent.

    Returns tool knowledge (what_it_does, settings schema, FAQ) merged with
    live state (current config values, service health status).
    """
    tool_id = request.path_params["tool_id"]
    tools_cfg = _load_tools_config()
    display_names = tools_cfg.get("display_names", {})
    descriptions = tools_cfg.get("descriptions", {})
    tool_ctx = tools_cfg.get("tool_context", {}).get(tool_id, {})

    # Live config values
    all_values = _load_tool_config_values()
    live_values = all_values.get(tool_id, {})

    # Live service status via internal readiness check
    readiness = _check_tool_readiness(tool_id)
    service_status = readiness.get("status", "unknown")

    return JSONResponse({
        "id": tool_id,
        "name": display_names.get(tool_id, tool_id.replace("_", " ").title()),
        "description": descriptions.get(tool_id, ""),
        "what_it_does": tool_ctx.get("what_it_does", ""),
        "settings": tool_ctx.get("settings", {}),
        "faq": tool_ctx.get("faq", []),
        "current_values": live_values,
        "service_status": service_status,
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
    from cohort.socketio_events import sio

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

def _cohort_data_dir() -> Path:
    """Resolve Cohort's own service data directory.

    Returns ``<data_dir>/services/`` where ``<data_dir>`` is the directory
    used by the data layer (typically ``G:/cohort/data``).

    Override with the ``COHORT_SERVICE_DATA`` environment variable.
    """
    if _settings_path is not None:
        candidate = _settings_path.parent / "services"
        if candidate.is_dir():
            return candidate

    env = os.environ.get("COHORT_SERVICE_DATA")
    if env:
        p = Path(env)
        if p.is_dir():
            return p

    # Fallback: relative to this file  (cohort/cohort/server.py -> cohort/data/services)
    fallback = Path(__file__).resolve().parent.parent / "data" / "services"
    return fallback


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
    from cohort.api import get_state
    data = get_state()
    if not data:
        return JSONResponse({"error": "Health monitor state not available", "target_status": {}, "last_alerts": {}})
    return JSONResponse(data)


async def get_health_monitor_alerts(request: Request) -> JSONResponse:
    """GET /api/health-monitor/alerts -- return recent alerts from today's log."""
    limit = int(request.query_params.get("limit", "20"))
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = _cohort_data_dir() / "health_monitor" / "logs" / f"{today}_alerts.log"

    alerts = []
    try:
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    alerts.append({"raw": line})
    except Exception:
        pass

    return JSONResponse({"alerts": alerts[-limit:], "date": today})


async def health_monitor_run_checks(request: Request) -> JSONResponse:
    """POST /api/health-monitor/run -- run health checks on all services now."""
    import asyncio

    from cohort.api import run_service_checks
    # Run blocking checks in a thread to not block the event loop
    loop = asyncio.get_event_loop()
    state = await loop.run_in_executor(None, run_service_checks)
    return JSONResponse(state)


async def health_monitor_services(request: Request) -> JSONResponse:
    """GET /api/health-monitor/services -- list all registered services."""
    from cohort.api import list_services
    return JSONResponse({"services": list_services()})


async def health_monitor_stop(request: Request) -> JSONResponse:
    """POST /api/health-monitor/stop/{service_key} -- stop a service."""
    import asyncio

    from cohort.api import stop_service
    service_key = request.path_params["service_key"]
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, stop_service, service_key)
    status_code = 200 if result.get("success") else 400
    return JSONResponse(result, status_code=status_code)


async def health_monitor_start(request: Request) -> JSONResponse:
    """POST /api/health-monitor/start/{service_key} -- start a service."""
    import asyncio

    from cohort.api import start_service
    service_key = request.path_params["service_key"]
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, start_service, service_key)
        status_code = 200 if result.get("success") else 400
        return JSONResponse(result, status_code=status_code)
    except Exception as e:
        logger.warning("[!] start_service(%s) failed: %s", service_key, e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def health_monitor_restart(request: Request) -> JSONResponse:
    """POST /api/health-monitor/restart/{service_key} -- restart a service."""
    import asyncio

    from cohort.api import restart_service
    service_key = request.path_params["service_key"]
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, restart_service, service_key)
        status_code = 200 if result.get("success") else 400
        return JSONResponse(result, status_code=status_code)
    except Exception as e:
        logger.warning("[!] restart_service(%s) failed: %s", service_key, e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def get_scheduler_recent_runs(request: Request) -> JSONResponse:
    """GET /api/scheduler/recent-runs -- return recent scheduler runs."""
    task = request.query_params.get("task", "")
    source = request.query_params.get("source", "scheduler")  # scheduler or content_monitor
    limit = int(request.query_params.get("limit", "10"))

    now = datetime.now()
    month_str = now.strftime("%Y%m")

    if source == "content_monitor":
        runs_path = _cohort_data_dir() / "content_monitor_logs" / f"runs_{month_str}.json"
    else:
        runs_path = _cohort_data_dir() / "scheduler_logs" / f"runs_{month_str}.json"

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
    log_path = _cohort_data_dir() / "comms_service" / "webhook_logs" / f"{today}.json"

    data = _read_json_safe(log_path)
    if not isinstance(data, list):
        return JSONResponse({"activity": [], "date": today})

    return JSONResponse({"activity": data[-limit:], "date": today})


async def get_intel_recent_articles(request: Request) -> JSONResponse:
    """GET /api/intel/recent-articles -- return recent tech intel articles."""
    limit = int(request.query_params.get("limit", "10"))
    db_path = _cohort_data_dir() / "tech_intel" / "articles_db.json"

    data = _read_json_safe(db_path)
    if not isinstance(data, list):
        return JSONResponse({"articles": []})

    # Sort by date descending, return last N
    try:
        data.sort(key=lambda a: a.get("fetched_at", a.get("published", "")), reverse=True)
    except Exception:
        pass

    return JSONResponse({"articles": data[:limit]})


async def get_intel_feeds(request: Request) -> JSONResponse:
    """GET /api/intel/feeds -- return configured RSS feeds and available topics."""
    from cohort.api import TOPIC_CATEGORIES, TOPIC_FEEDS, TOPIC_KEYWORDS

    config_path = Path(_resolved_data_dir) / "content_config.json"
    config = _read_json_safe(config_path) or {}
    feeds = config.get("feeds", [])
    keywords = config.get("interest_keywords", [])
    relevance_mode = config.get("relevance_mode", "hybrid")

    # Build curated topics list
    topics = {}
    for topic, topic_feeds in TOPIC_FEEDS.items():
        topics[topic] = [{"name": f["name"], "url": f["url"]} for f in topic_feeds]

    return JSONResponse({
        "feeds": feeds,
        "keywords": keywords,
        "relevance_mode": relevance_mode,
        "topics": topics,
        "categories": TOPIC_CATEGORIES,
        "topic_keywords": TOPIC_KEYWORDS,
    })


async def add_intel_feed(request: Request) -> JSONResponse:
    """POST /api/intel/feeds -- add one or more RSS feeds."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    new_feeds = body.get("feeds", [])
    if not new_feeds:
        # Single feed shorthand
        name = body.get("name", "").strip()
        url = body.get("url", "").strip()
        if not url:
            return JSONResponse({"error": "Feed URL is required"}, status_code=400)
        if not name:
            name = url.split("//")[-1].split("/")[0]
        new_feeds = [{"name": name, "url": url}]

    config_path = Path(_resolved_data_dir) / "content_config.json"
    config = _read_json_safe(config_path) or {}
    if not isinstance(config, dict):
        config = {}
    existing = config.get("feeds", [])

    # Deduplicate by URL
    existing_urls = {f.get("url") for f in existing}
    added = []
    for f in new_feeds:
        if f.get("url") and f["url"] not in existing_urls:
            existing.append({"name": f.get("name", f["url"]), "url": f["url"]})
            existing_urls.add(f["url"])
            added.append(f["url"])

    config["feeds"] = existing
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    return JSONResponse({"success": True, "added": len(added), "total": len(existing)})


async def delete_intel_feed(request: Request) -> JSONResponse:
    """DELETE /api/intel/feeds -- remove a feed by URL."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    url = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "Feed URL is required"}, status_code=400)

    config_path = Path(_resolved_data_dir) / "content_config.json"
    config = _read_json_safe(config_path) or {}
    if not isinstance(config, dict):
        config = {}
    existing = config.get("feeds", [])

    before = len(existing)
    config["feeds"] = [f for f in existing if f.get("url") != url]
    after = len(config["feeds"])

    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    return JSONResponse({"success": True, "removed": before - after, "total": after})


async def update_intel_keywords(request: Request) -> JSONResponse:
    """PUT /api/intel/keywords -- update interest keywords list."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    keywords = body.get("keywords")
    if not isinstance(keywords, list):
        return JSONResponse({"error": "keywords must be a list of strings"}, status_code=400)

    # Sanitize: lowercase, strip, deduplicate, remove empties
    clean = list(dict.fromkeys(kw.strip().lower() for kw in keywords if isinstance(kw, str) and kw.strip()))

    config_path = Path(_resolved_data_dir) / "content_config.json"
    config = _read_json_safe(config_path) or {}
    if not isinstance(config, dict):
        config = {}
    config["interest_keywords"] = clean
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    return JSONResponse({"success": True, "keywords": clean})


_VALID_RELEVANCE_MODES = {"off", "keywords", "llm", "hybrid"}


async def update_intel_relevance_mode(request: Request) -> JSONResponse:
    """PUT /api/intel/relevance-mode -- update relevance scoring mode."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    mode = body.get("mode", "").strip().lower()
    if mode not in _VALID_RELEVANCE_MODES:
        return JSONResponse(
            {"error": f"Invalid mode. Must be one of: {', '.join(sorted(_VALID_RELEVANCE_MODES))}"},
            status_code=400,
        )

    config_path = Path(_resolved_data_dir) / "content_config.json"
    config = _read_json_safe(config_path) or {}
    if not isinstance(config, dict):
        config = {}
    config["relevance_mode"] = mode
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    return JSONResponse({"success": True, "relevance_mode": mode})


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
    runs_path = _cohort_data_dir() / "content_monitor_logs" / f"runs_{month_str}.json"

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

    posts_dir = _cohort_data_dir() / "comms_service" / "social_posts"
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

    posts_dir = _cohort_data_dir() / "comms_service" / "social_posts"
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
        _cohort_data_dir() / "content_monitor_logs" / "articles_db.json",
        _cohort_data_dir() / "content_monitor" / "articles_db.json",
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
    cfg_path = _cohort_data_dir() / "content_monitor_config.json"
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


# ── Content Projects (multi-project content strategy) ──────────────────


def _projects_path() -> Path:
    """Path to the content projects JSON store."""
    return Path(_resolved_data_dir) / "content_projects.json"


def _load_projects() -> dict:
    """Load all projects from content_projects.json."""
    data = _read_json_safe(_projects_path())
    if not isinstance(data, dict) or "projects" not in data:
        return {"projects": {}, "version": 1}
    return data


def _save_projects(data: dict) -> None:
    """Write projects back to content_projects.json."""
    path = _projects_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def list_content_projects(request: Request) -> JSONResponse:
    """GET /api/content-projects -- list all projects."""
    data = _load_projects()
    projects = list(data.get("projects", {}).values())
    # Sort by created_at descending
    projects.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    return JSONResponse({"projects": projects})


async def get_content_project(request: Request) -> JSONResponse:
    """GET /api/content-projects/{project_id} -- get a single project."""
    project_id = request.path_params["project_id"]
    data = _load_projects()
    project = data.get("projects", {}).get(project_id)
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    return JSONResponse({"project": project})


async def create_content_project(request: Request) -> JSONResponse:
    """POST /api/content-projects -- create a new project."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "Project name is required"}, status_code=400)

    import uuid
    from datetime import datetime

    project_id = "proj_" + uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()

    project = {
        "id": project_id,
        "name": name,
        "description": body.get("description", "").strip(),
        "created_at": now,
        "updated_at": now,
        "active": True,
        "brand_voice": body.get("brand_voice", []),
        "audiences": body.get("audiences", []),
        "content_pillars": body.get("content_pillars", []),
        "keywords": {
            "critical": body.get("keywords", {}).get("critical", []),
            "standard": body.get("keywords", {}).get("standard", []),
            "negative": body.get("keywords", {}).get("negative", []),
        },
        "platform_config": body.get("platform_config", {
            "twitter": {"tone": "punchy", "max_length": 280},
            "linkedin": {"tone": "professional", "max_length": 1500},
            "reddit": {"tone": "helpful", "max_length": 2000},
        }),
        "feed_ids": body.get("feed_ids", []),
        "min_relevance": body.get("min_relevance", 5),
    }

    data = _load_projects()
    data["projects"][project_id] = project
    _save_projects(data)

    return JSONResponse({"project": project}, status_code=201)


async def update_content_project(request: Request) -> JSONResponse:
    """PUT /api/content-projects/{project_id} -- update a project."""
    project_id = request.path_params["project_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    data = _load_projects()
    project = data.get("projects", {}).get(project_id)
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    from datetime import datetime

    # Update allowed fields
    updatable = [
        "name", "description", "active", "brand_voice", "audiences",
        "content_pillars", "keywords", "platform_config", "feed_ids",
        "min_relevance",
    ]
    for field in updatable:
        if field in body:
            project[field] = body[field]

    project["updated_at"] = datetime.now().isoformat()
    data["projects"][project_id] = project
    _save_projects(data)

    return JSONResponse({"project": project})


async def delete_content_project(request: Request) -> JSONResponse:
    """DELETE /api/content-projects/{project_id} -- delete a project."""
    project_id = request.path_params["project_id"]
    data = _load_projects()
    projects = data.get("projects", {})

    if project_id not in projects:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    del projects[project_id]
    _save_projects(data)

    return JSONResponse({"success": True})


async def score_article_endpoint(request: Request) -> JSONResponse:
    """POST /api/content-projects/{project_id}/score -- score an article against a project.

    Body: {"title": "...", "summary": "...", ...}
    Returns: {"score": 0-10, "matched_keywords": [...], ...}
    """
    project_id = request.path_params["project_id"]
    data = _load_projects()
    project = data.get("projects", {}).get(project_id)
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    try:
        article = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    from cohort.api import score_article
    result = score_article(article, project)
    return JSONResponse(result)


async def score_all_articles_for_projects(request: Request) -> JSONResponse:
    """POST /api/content-projects/score-all -- score existing articles against all active projects."""
    from cohort.api import IntelFetcher

    data = _load_projects()
    projects = [p for p in data.get("projects", {}).values() if p.get("active", True)]
    if not projects:
        return JSONResponse({"updated": 0, "message": "No active projects"})

    fetcher = IntelFetcher(data_dir=Path(_resolved_data_dir))
    updated = fetcher.score_for_projects(projects)
    return JSONResponse({"updated": updated, "project_count": len(projects)})


async def get_project_articles(request: Request) -> JSONResponse:
    """GET /api/content-projects/{project_id}/articles -- get top articles for a project."""
    from cohort.api import IntelFetcher

    project_id = request.path_params["project_id"]
    data = _load_projects()
    project = data.get("projects", {}).get(project_id)
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    limit = int(request.query_params.get("limit", "20"))
    min_score = int(request.query_params.get("min_score", str(project.get("min_relevance", 5))))

    fetcher = IntelFetcher(data_dir=Path(_resolved_data_dir))
    articles = fetcher.get_top_for_project(project_id, limit=limit, min_score=min_score)
    return JSONResponse({"articles": articles, "total": len(articles)})


async def web_search_test(request: Request) -> JSONResponse:
    """POST /api/web-search/test -- search using inlined web search module."""
    import asyncio

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    limit = min(body.get("limit", 5), 25)

    settings = _load_settings()
    service_keys = settings.get("service_keys", [])

    def _do_search():
        from cohort.web_search import search
        return search(query, num_results=limit, service_keys=service_keys)

    result = await asyncio.get_event_loop().run_in_executor(None, _do_search)
    return JSONResponse(result)


async def web_search_local_test(request: Request) -> JSONResponse:
    """POST /api/web-search/test-local -- test search via local ddgs (DuckDuckGo)."""
    import asyncio

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    limit = min(body.get("limit", 5), 25)

    try:
        from ddgs import DDGS
    except ImportError:
        return JSONResponse({"error": "ddgs not installed. Run: pip install ddgs"})

    def _search():
        with DDGS() as ddgs:
            return list(ddgs.text(query=query, max_results=limit))

    try:
        raw = await asyncio.get_event_loop().run_in_executor(None, _search)
    except Exception as exc:
        return JSONResponse({"error": f"Local search failed: {exc}"})

    results = []
    for r in raw:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("href", r.get("url", "")),
            "snippet": r.get("body", r.get("content", "")),
        })
    return JSONResponse({"results": results, "provider": "ddgs_local"})


async def youtube_search_test(request: Request) -> JSONResponse:
    """POST /api/youtube/test -- search YouTube using inlined module."""
    import asyncio

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    limit = body.get("limit", 3)

    settings = _load_settings()
    api_key = ""
    for svc in settings.get("service_keys", []):
        if svc.get("type") == "youtube" and svc.get("key"):
            api_key = svc["key"]
            break

    if not api_key:
        return JSONResponse({"error": "YouTube API key not configured. Add it in Settings > Permissions."})

    def _do_search():
        from cohort.youtube import search_videos
        return search_videos(query, api_key, max_results=limit)

    result = await asyncio.get_event_loop().run_in_executor(None, _do_search)
    if "error" in result:
        return JSONResponse(result, status_code=500 if "API error" in result.get("error", "") else 400)
    return JSONResponse(result)


async def web_search_query(request: Request) -> JSONResponse:
    """GET /api/web-search/search -- search the web (inlined, no external service)."""
    import asyncio

    query = request.query_params.get("q", "").strip()
    if not query:
        return JSONResponse({"error": "q parameter is required"}, status_code=400)
    num = min(int(request.query_params.get("num", "5")), 25)

    settings = _load_settings()
    service_keys = settings.get("service_keys", [])

    def _do():
        from cohort.web_search import search
        return search(query, num_results=num, service_keys=service_keys)

    result = await asyncio.get_event_loop().run_in_executor(None, _do)
    return JSONResponse(result)


async def youtube_video_detail(request: Request) -> JSONResponse:
    """GET /api/youtube/video/{video_id} -- get video metadata (inlined)."""
    import asyncio

    video_id = request.path_params["video_id"]
    settings = _load_settings()
    api_key = ""
    for svc in settings.get("service_keys", []):
        if svc.get("type") == "youtube" and svc.get("key"):
            api_key = svc["key"]
            break
    if not api_key:
        return JSONResponse({"error": "YouTube API key not configured"}, status_code=400)

    def _do():
        from cohort.youtube import get_video
        return get_video(video_id, api_key)

    result = await asyncio.get_event_loop().run_in_executor(None, _do)
    if "error" in result:
        return JSONResponse(result, status_code=404 if "not found" in result.get("error", "").lower() else 500)
    return JSONResponse(result)


async def youtube_transcript(request: Request) -> JSONResponse:
    """GET /api/youtube/transcript/{video_id} -- get video transcript (inlined)."""
    import asyncio

    video_id = request.path_params["video_id"]
    language = request.query_params.get("language", "en")

    def _do():
        from cohort.youtube import get_transcript
        return get_transcript(video_id, language=language)

    result = await asyncio.get_event_loop().run_in_executor(None, _do)
    if "error" in result:
        return JSONResponse(result, status_code=404 if "not found" in result.get("error", "").lower() else 500)
    return JSONResponse(result)


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
    from cohort.api import OllamaClient

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
            import io

            import pdfplumber
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
            import io

            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            result["text"] = "\n\n".join(paragraphs)
            result["format"] = "docx"

        # ── Excel (.xlsx) ──
        elif ext in (".xlsx", ".xls") or "spreadsheet" in content_type:
            import io

            import openpyxl
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
    import tempfile

    import cv2

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
            "outline": "These are frames from a video. Create a structured timeline of what happens at each stage.",
            "extract_key_points": "These are frames from a video. Extract all key information: actions, text visible, objects, people, settings, and any data shown.",
            "image": "These are frames from a video. Describe what is visually depicted: subjects, actions, colors, style, setting, and composition.",
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
    from cohort.api import OllamaClient

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
    from cohort.api import OllamaClient
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
    svc_data = _cohort_data_dir()
    posts_dir = svc_data / "comms_service" / "social_posts"
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


async def get_comms_safety_status(request: Request) -> JSONResponse:
    """GET /api/comms/safety-status -- aggregate safety/audit data for the comms panel."""
    svc_data = _cohort_data_dir()
    posts_dir = svc_data / "comms_service" / "social_posts"

    approved_count = 0
    denied_count = 0
    pending_count = 0
    total_drafted = 0
    integrity_violations = 0
    last_action_at = None

    if posts_dir.is_dir():
        for fp in posts_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            total_drafted += 1
            status = data.get("status", "")
            if status == "pending":
                pending_count += 1
            elif status == "approved":
                approved_count += 1
            elif status in ("rejected", "denied"):
                denied_count += 1

            # Integrity check: posted_at set but approved_at not set
            if data.get("posted_at") and not data.get("approved_at"):
                integrity_violations += 1

            # Track most recent action timestamp
            for ts_key in ("approved_at", "rejected_at", "posted_at", "created_at"):
                ts_val = data.get(ts_key)
                if ts_val and (last_action_at is None or ts_val > last_action_at):
                    last_action_at = ts_val

    # Read today's webhook log for recent activity
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = svc_data / "comms_service" / "webhook_logs" / f"{today}.json"
    log_data = _read_json_safe(log_path)
    recent_activity = []
    if isinstance(log_data, list):
        for entry in log_data[-8:]:
            recent_activity.append({
                "timestamp": entry.get("timestamp"),
                "agent_id": entry.get("agent_id"),
                "title": entry.get("title"),
                "message": (entry.get("message") or "")[:200],
                "priority": entry.get("priority"),
            })

    return JSONResponse({
        "gate_active": True,
        "integrity_violations": integrity_violations,
        "approved_count": approved_count,
        "denied_count": denied_count,
        "pending_count": pending_count,
        "total_drafted": total_drafted,
        "last_action_at": last_action_at,
        "recent_activity": recent_activity,
    })


# =====================================================================
# Website Creator endpoints
# =====================================================================

async def website_create(request: Request) -> JSONResponse:
    """POST /api/website/create -- Generate a website from a site brief or URL.

    Body (JSON):
        - brief_yaml: str (raw YAML content) -- generate from inline brief
        - brief_path: str (file path) -- generate from YAML file
        - url: str + competitor_urls: list[str] + answers: dict -- full pipeline
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    import tempfile

    try:
        from cohort.website_creator.pipeline import WebsiteCreator
    except ImportError:
        return JSONResponse({"error": "Website Creator is not available in this build"}, status_code=501)

    output_base = Path(__file__).parent / "website_creator" / "output"
    creator = WebsiteCreator(output_base=output_base)

    try:
        if "brief_yaml" in body:
            # Inline YAML string
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                              delete=False, encoding="utf-8")
            tmp.write(body["brief_yaml"])
            tmp.close()
            result = await creator.create_from_yaml(tmp.name)
            Path(tmp.name).unlink(missing_ok=True)
        elif "brief_path" in body:
            result = await creator.create_from_yaml(body["brief_path"])
        elif "url" in body:
            answers = {int(k): v for k, v in body.get("answers", {}).items()}
            result = await creator.create_from_url(
                body["url"],
                body.get("competitor_urls", []),
                answers,
            )
        else:
            return JSONResponse(
                {"error": "Provide brief_yaml, brief_path, or url + answers"},
                status_code=400,
            )

        # List generated files
        files = []
        for f in sorted(result.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size})

        project_name = result.name
        return JSONResponse({
            "status": "ok",
            "project": project_name,
            "output_dir": str(result),
            "files": files,
            "preview_url": f"/api/website/projects/{project_name}/index.html",
        })
    except Exception as e:
        logger.exception("Website creation failed")
        return JSONResponse({"error": str(e)}, status_code=500)


async def website_worksheet(request: Request) -> JSONResponse:
    """GET /api/website/worksheet -- Return the 20 intake questions."""
    try:
        from cohort.website_creator.intake import get_worksheet_questions
    except ImportError:
        return JSONResponse({"error": "Website Creator is not available in this build"}, status_code=501)
    return JSONResponse({"questions": get_worksheet_questions()})


_GRADUATED_DIR = Path(__file__).parent / "website"
_CREATOR_OUTPUT_DIR = Path(__file__).parent / "website_creator" / "output"


def _resolve_project_path(project: str, filename: str) -> tuple[Path | None, bool]:
    """Resolve a project file, checking graduated location first.

    Returns (file_path, is_graduated). None if not found.
    """
    for base, graduated in ((_GRADUATED_DIR, True), (_CREATOR_OUTPUT_DIR, False)):
        if not base.exists():
            continue
        candidate = base / project / filename
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(base.resolve())
        except ValueError:
            continue
        return candidate, graduated
    return None, False


async def website_list_projects(request: Request) -> JSONResponse:
    """GET /api/website/projects -- List generated website projects."""
    projects = []
    seen = set()

    # Graduated sites first (take priority)
    for base, graduated in ((_GRADUATED_DIR, True), (_CREATOR_OUTPUT_DIR, False)):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.name in seen:
                continue
            if d.is_dir() and (d / "index.html").exists():
                seen.add(d.name)
                files = [f.name for f in d.iterdir() if f.is_file()]
                projects.append({
                    "name": d.name,
                    "files": files,
                    "graduated": graduated,
                    "preview_url": f"/api/website/projects/{d.name}/index.html",
                })
    return JSONResponse({"projects": projects})


def _serve_static_site_file(file_path: Path, is_graduated: bool) -> "Response":  # noqa: F821
    """Serve a static site file with appropriate headers."""
    from starlette.responses import HTMLResponse
    from starlette.responses import Response as StarletteResponse

    content = file_path.read_text(encoding="utf-8")

    ext = file_path.suffix.lower()
    content_types = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".xml": "application/xml",
        ".txt": "text/plain",
        ".json": "application/json",
        ".yaml": "text/yaml",
    }
    ct = content_types.get(ext, "text/plain")

    # Cache headers: graduated sites can cache, drafts/previews should not
    cache = "max-age=3600" if is_graduated else "no-cache"
    headers = {
        "Cache-Control": cache,
        "X-Content-Type-Options": "nosniff",
    }

    if ext == ".html":
        return HTMLResponse(content, headers=headers)
    return StarletteResponse(content, media_type=ct, headers=headers)


async def website_serve_page(request: Request) -> Response:  # noqa: F821
    """GET /api/website/projects/{project_name}/{path} -- Serve generated pages."""
    project_name = request.path_params["project_name"]

    # project_name may include the filename (e.g. "cohort/index.html")
    parts = project_name.split("/", 1)
    if len(parts) == 2:
        project = parts[0]
        filename = parts[1]
    else:
        project = parts[0]
        filename = "index.html"

    file_path, is_graduated = _resolve_project_path(project, filename)

    if file_path is None:
        return JSONResponse({"error": f"Not found: {project}/{filename}"}, status_code=404)

    return _serve_static_site_file(file_path, is_graduated)


async def website_serve_preview(request: Request) -> Response:  # noqa: F821
    """GET /api/website/preview/{project_name}/{path} -- Serve preview pages."""
    project_name = request.path_params["project_name"]

    parts = project_name.split("/", 1)
    if len(parts) == 2:
        project = parts[0]
        filename = parts[1]
    else:
        project = parts[0]
        filename = "index.html"

    preview_dir = _CREATOR_OUTPUT_DIR / f"{project}-preview"
    if not preview_dir.exists():
        return JSONResponse({"error": f"No preview for: {project}"}, status_code=404)

    file_path = preview_dir / filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": f"Not found: {project}/{filename}"}, status_code=404)

    # Path traversal guard
    try:
        file_path.resolve().relative_to(preview_dir.resolve())
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=403)

    from starlette.responses import HTMLResponse
    from starlette.responses import Response as StarletteResponse

    content = file_path.read_text(encoding="utf-8")
    ext = file_path.suffix.lower()
    content_types = {
        ".html": "text/html", ".css": "text/css",
        ".js": "application/javascript", ".xml": "application/xml",
        ".txt": "text/plain", ".json": "application/json",
    }
    ct = content_types.get(ext, "text/plain")
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

    if ext == ".html":
        return HTMLResponse(content, headers=headers)
    return StarletteResponse(content, media_type=ct, headers=headers)


# =====================================================================
# Ecosystem Inventory endpoints
# =====================================================================

# Module-level cache for the merged inventory (loaded on startup or first request)
_inventory_cache: list[dict[str, Any]] | None = None


def _load_inventory_cache() -> list[dict[str, Any]]:
    """Load or refresh the merged inventory from all sources."""
    global _inventory_cache  # noqa: PLW0603
    from cohort.inventory_loader import load_merged_inventory
    entries = load_merged_inventory()
    _inventory_cache = [e.to_dict() for e in entries]
    logger.info("[OK] Inventory loaded: %d entries", len(_inventory_cache))
    return _inventory_cache


async def get_inventory(request: Request) -> JSONResponse:
    """GET /api/inventory -- return the merged ecosystem inventory."""
    global _inventory_cache  # noqa: PLW0603
    if _inventory_cache is None:
        _load_inventory_cache()
    return JSONResponse({"entries": _inventory_cache, "count": len(_inventory_cache or [])})


async def refresh_inventory(request: Request) -> JSONResponse:
    """POST /api/inventory/refresh -- force reload from all sources."""
    entries = _load_inventory_cache()
    return JSONResponse({"entries": entries, "count": len(entries), "refreshed": True})


# =====================================================================
# Project Manager endpoints
# =====================================================================

def _projects_registry_path() -> Path:
    """Path to the projects registry JSON file."""
    return Path(_resolved_data_dir) / "projects_registry.json"


def _load_projects_registry() -> dict[str, Any]:
    """Load projects registry.  Returns {"projects": [...]} ."""
    path = _projects_registry_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"projects": []}


def _save_projects_registry(data: dict[str, Any]) -> None:
    path = _projects_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _scan_project(entry: dict[str, Any]) -> dict[str, Any]:
    """Enrich a registry entry with live filesystem status."""
    project_dir = Path(entry["path"])
    entry["exists"] = project_dir.exists()
    entry["has_manifest"] = (project_dir / ".cohort").exists() if entry["exists"] else False
    entry["has_git"] = (project_dir / ".git").exists() if entry["exists"] else False

    # Read manifest permissions if present
    if entry["has_manifest"]:
        try:
            from cohort.project_manifest import CohortManifest
            manifest = CohortManifest.load(project_dir)
            entry["permissions"] = manifest.permissions.to_dict()
        except Exception:
            entry["permissions"] = None
    else:
        entry["permissions"] = None

    return entry


async def list_projects(request: Request) -> JSONResponse:
    """GET /api/projects -- list all registered projects with live status."""
    registry = _load_projects_registry()
    projects = [_scan_project(dict(p)) for p in registry.get("projects", [])]
    return JSONResponse({"projects": projects})


async def create_project(request: Request) -> JSONResponse:
    """POST /api/projects -- create a new project (like 'cohort new')."""
    import subprocess as _sp

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    name = str(body.get("name", "")).strip()
    parent = str(body.get("parent_dir", "")).strip()
    profile = str(body.get("profile", "developer")).strip()
    deny_paths = [str(p).strip() for p in body.get("deny_paths", []) if str(p).strip()]
    max_turns = max(1, min(50, int(body.get("max_turns", 15))))
    init_git = bool(body.get("init_git", True))

    if not name:
        return JSONResponse({"error": "Project name is required"}, status_code=400)
    if not parent:
        return JSONResponse({"error": "Parent directory is required"}, status_code=400)

    parent_path = Path(parent)
    if not parent_path.exists():
        return JSONResponse({"error": f"Parent directory not found: {parent}"}, status_code=400)

    project_dir = parent_path / name
    if project_dir.exists():
        return JSONResponse({"error": f"Directory already exists: {project_dir}"}, status_code=400)

    cohort_root = Path(__file__).resolve().parent.parent

    # Profile -> tools mapping
    profile_tools = {
        "developer":  ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "readonly":   ["Read", "Glob", "Grep"],
        "researcher": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
        "minimal":    [],
    }

    try:
        # Create directory
        project_dir.mkdir(parents=True)

        # Git init
        if init_git:
            try:
                _sp.run(["git", "init", str(project_dir)], check=True, capture_output=True, text=True)
            except Exception:
                pass

        # Housekeeping: .gitignore
        gitignore = project_dir / ".gitignore"
        gitignore.write_text(
            "# Cohort working memory\n.cohort-memory/\n\n# Secrets\n.env\n*.pem\n*.key\n\n"
            "# Python\n__pycache__/\n*.py[cod]\n.venv/\nvenv/\n\n"
            "# Node\nnode_modules/\n.next/\ndist/\n\n"
            "# OS / IDE\n.DS_Store\nThumbs.db\n.vscode/\n.idea/\n",
            encoding="utf-8",
        )

        # .env.example + .env
        (project_dir / ".env.example").write_text(
            "# Copy to .env and fill in values\n# .env is git-ignored\n", encoding="utf-8"
        )
        (project_dir / ".env").write_text("# Project secrets -- do not commit\n", encoding="utf-8")

        # Build manifest
        from cohort.project_manifest import CohortManifest, ProjectPermissions
        permissions = ProjectPermissions(
            profile=profile,
            allow_paths=[str(project_dir)],
            deny_paths=deny_paths,
            allowed_tools=profile_tools.get(profile, profile_tools["developer"]),
            max_turns=max_turns,
        )
        manifest = CohortManifest.create(
            project_dir=project_dir,
            cohort_root=cohort_root,
            project_name=name,
            permissions=permissions,
        )
        manifest.write(project_dir)
        manifest.ensure_working_memory_dir(project_dir)

        # Register in projects list
        registry = _load_projects_registry()
        registry["projects"].append({
            "name": name,
            "path": str(project_dir),
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "type": "new",
        })
        _save_projects_registry(registry)

        return JSONResponse({
            "success": True,
            "project": _scan_project({
                "name": name,
                "path": str(project_dir),
                "type": "new",
            }),
        })

    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def link_project(request: Request) -> JSONResponse:
    """POST /api/projects/link -- link an existing directory (like 'cohort link')."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    dir_path = str(body.get("dir", "")).strip()
    name_override = str(body.get("name", "")).strip() or None
    profile = str(body.get("profile", "developer")).strip()
    deny_paths = [str(p).strip() for p in body.get("deny_paths", []) if str(p).strip()]
    max_turns = max(1, min(50, int(body.get("max_turns", 15))))

    if not dir_path:
        return JSONResponse({"error": "Directory path is required"}, status_code=400)

    project_dir = Path(dir_path).resolve()
    if not project_dir.exists():
        return JSONResponse({"error": f"Directory not found: {project_dir}"}, status_code=400)

    if (project_dir / ".cohort").exists():
        return JSONResponse({"error": "Project already linked (has .cohort file)"}, status_code=400)

    cohort_root = Path(__file__).resolve().parent.parent
    project_name = name_override or project_dir.name

    profile_tools = {
        "developer":  ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "readonly":   ["Read", "Glob", "Grep"],
        "researcher": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
        "minimal":    [],
    }

    try:
        # Housekeeping: merge .gitignore
        gitignore = project_dir / ".gitignore"
        cohort_lines = [".cohort-memory/", ".env"]
        if gitignore.exists():
            existing = gitignore.read_text(encoding="utf-8")
            additions = [line for line in cohort_lines if line not in existing]
            if additions:
                gitignore.write_text(
                    existing.rstrip() + "\n" + "\n".join(additions) + "\n",
                    encoding="utf-8",
                )
        else:
            gitignore.write_text("\n".join(cohort_lines) + "\n", encoding="utf-8")

        # .env.example + .env
        if not (project_dir / ".env.example").exists():
            (project_dir / ".env.example").write_text(
                "# Copy to .env and fill in values\n# .env is git-ignored\n", encoding="utf-8"
            )
        if not (project_dir / ".env").exists():
            (project_dir / ".env").write_text("# Project secrets -- do not commit\n", encoding="utf-8")

        # Build manifest
        from cohort.project_manifest import CohortManifest, ProjectPermissions
        permissions = ProjectPermissions(
            profile=profile,
            allow_paths=[str(project_dir)],
            deny_paths=deny_paths,
            allowed_tools=profile_tools.get(profile, profile_tools["developer"]),
            max_turns=max_turns,
        )
        manifest = CohortManifest.create(
            project_dir=project_dir,
            cohort_root=cohort_root,
            project_name=project_name,
            permissions=permissions,
        )
        manifest.write(project_dir)
        manifest.ensure_working_memory_dir(project_dir)

        # Register
        registry = _load_projects_registry()
        registry["projects"].append({
            "name": project_name,
            "path": str(project_dir),
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "type": "linked",
        })
        _save_projects_registry(registry)

        return JSONResponse({
            "success": True,
            "project": _scan_project({
                "name": project_name,
                "path": str(project_dir),
                "type": "linked",
            }),
        })

    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


def _decode_project_path(encoded: str) -> str:
    """Decode a URL-safe base64-encoded project path (padding-tolerant)."""
    import base64
    # Re-add padding stripped by JS
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8")


async def update_project_permissions(request: Request) -> JSONResponse:
    """PUT /api/projects/{project_path}/permissions -- update a project's .cohort permissions."""
    encoded_path = request.path_params["project_path"]
    try:
        project_path = _decode_project_path(encoded_path)
    except Exception:
        return JSONResponse({"error": "Invalid project path encoding"}, status_code=400)

    project_dir = Path(project_path)
    if not (project_dir / ".cohort").exists():
        return JSONResponse({"error": "No .cohort manifest found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    profile_tools = {
        "developer":  ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "readonly":   ["Read", "Glob", "Grep"],
        "researcher": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
        "minimal":    [],
    }

    try:
        from cohort.project_manifest import CohortManifest, ProjectPermissions
        manifest = CohortManifest.load(project_dir)

        profile = str(body.get("profile", manifest.permissions.profile)).strip()
        deny_paths = body.get("deny_paths", manifest.permissions.deny_paths)
        max_turns = max(1, min(50, int(body.get("max_turns", manifest.permissions.max_turns))))

        from dataclasses import replace as _replace
        manifest = _replace(manifest, permissions=ProjectPermissions(
            profile=profile,
            allow_paths=manifest.permissions.allow_paths,
            deny_paths=[str(p).strip() for p in deny_paths if str(p).strip()],
            allowed_tools=profile_tools.get(profile, manifest.permissions.allowed_tools),
            max_turns=max_turns,
        ))
        manifest.write(project_dir)

        return JSONResponse({"success": True, "permissions": manifest.permissions.to_dict()})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def delete_project(request: Request) -> JSONResponse:
    """DELETE /api/projects/{project_path} -- unregister a project (does NOT delete files)."""
    encoded_path = request.path_params["project_path"]
    try:
        project_path = _decode_project_path(encoded_path)
    except Exception:
        return JSONResponse({"error": "Invalid project path encoding"}, status_code=400)

    registry = _load_projects_registry()
    before = len(registry["projects"])
    registry["projects"] = [p for p in registry["projects"] if p.get("path") != project_path]
    after = len(registry["projects"])

    if before == after:
        return JSONResponse({"error": "Project not found in registry"}, status_code=404)

    _save_projects_registry(registry)
    return JSONResponse({"success": True})


async def open_project_vscode(request: Request) -> JSONResponse:
    """POST /api/projects/open-vscode -- open a project directory in VS Code."""
    import shutil
    import subprocess as _sp

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    dir_path = str(body.get("dir", "")).strip()
    if not dir_path:
        return JSONResponse({"error": "dir is required"}, status_code=400)

    project_dir = Path(dir_path)
    if not project_dir.exists():
        return JSONResponse({"error": f"Directory not found: {dir_path}"}, status_code=404)

    code_cmd = shutil.which("code") or shutil.which("code.cmd")
    if not code_cmd:
        return JSONResponse({"error": "VS Code CLI not found on PATH"}, status_code=404)

    try:
        _sp.Popen([code_cmd, str(project_dir)], close_fds=True)
        return JSONResponse({"success": True})
    except OSError as exc:
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
    global _chat, _data_layer, _agent_store, _work_queue, _task_store, _scheduler  # noqa: PLW0603

    global _settings_path  # noqa: PLW0603

    resolved_dir = os.environ.get("COHORT_DATA_DIR", data_dir)
    storage = create_storage(resolved_dir)
    _chat = ChatManager(storage)

    _settings_path = Path(resolved_dir) / "settings.json"
    saved_settings = _load_settings()

    # -- Seed from global defaults if this workspace hasn't been set up ---
    if not saved_settings.get("setup_completed"):
        seeded = _seed_from_global_defaults()
        if seeded:
            saved_settings.update(seeded)

    logger.info("[OK] ChatManager initialised (data_dir=%s)", resolved_dir)

    # -- Agent store (file-backed agent configs + memory) ---------------
    from cohort.api import _LEGACY_REGISTRY, set_global_store
    from cohort.api import set_registry_store as set_store

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
    # [DEPRECATED] Remote Gateway sync disabled — using local disk agents only.
    # Reactivate by uncommenting remote_url/api_key when the Gateway API is live.
    _agent_store = AgentStore(
        agents_dir=agents_dir if agents_dir.is_dir() else None,
        fallback_registry=_LEGACY_REGISTRY,
        # remote_url=remote_url,
        # api_key=api_key,
    )
    set_store(_agent_store)
    set_global_store(_agent_store)

    # -- Load tool permissions (central defaults) ----------------------
    from cohort.api import load_central_permissions
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
    from cohort.agent_router import apply_settings as _apply_router_settings
    from cohort.agent_router import setup_agent_router

    agents_root = saved_settings.get("agents_root") or os.environ.get(
        "COHORT_AGENTS_ROOT", "",
    )
    setup_agent_router(chat=_chat, sio=sio, agents_root=agents_root, store=_agent_store, settings_path=_settings_path)
    _apply_router_settings(saved_settings)

    # -- Task executor (briefing + execution layer) ---------------------
    from cohort.api import TaskExecutor
    from cohort.socketio_events import setup_task_executor

    executor_settings = {**saved_settings, "agents_root": str(agents_root)}
    executor = TaskExecutor(data_layer, _chat, executor_settings)
    executor.set_sio(sio)
    setup_task_executor(executor)

    # -- Work Queue (sequential execution queue) ------------------------
    from cohort.api import WorkQueue
    from cohort.socketio_events import setup_work_queue
    _work_queue = WorkQueue(Path(resolved_dir))
    setup_work_queue(_work_queue)
    logger.info("[OK] Work queue initialised")

    # -- Approval Pipeline (multi-stakeholder review) -------------------
    # (hooks wired after _deliverable_tracker and _review_pipeline init below)
    global _approval_store, _deliverable_tracker, _review_pipeline  # noqa: PLW0603
    from cohort.approval_store import ApprovalStore
    from cohort.deliverables import DeliverableTracker
    from cohort.review_pipeline import ReviewPipeline
    _approval_store = ApprovalStore(data_dir=Path(resolved_dir))
    _deliverable_tracker = DeliverableTracker(data_dir=Path(resolved_dir))
    _review_pipeline = ReviewPipeline.load_config(Path(resolved_dir))
    logger.info("[OK] Approval pipeline initialised (%d review stages)", len(_review_pipeline.stages))

    # -- Wire work queue pipeline hooks ---------------------------------
    if _deliverable_tracker is not None:
        _work_queue.set_deliverable_tracker(_deliverable_tracker)
    _work_queue.set_on_complete_callback(_work_item_review_trigger)

    # -- Task Store (file-backed tasks + schedules) -----------------------
    from cohort.api import TaskStore
    from cohort.socketio_events import setup_scheduler, setup_task_store
    global _task_store, _scheduler  # noqa: PLW0603
    _task_store = TaskStore(Path(resolved_dir))
    setup_task_store(_task_store)
    executor.set_task_store(_task_store)
    data_layer.set_task_store(_task_store)
    logger.info("[OK] Task store initialised")

    # -- Task Scheduler (asyncio background tick loop) --------------------
    from cohort.api import TaskScheduler
    _scheduler = TaskScheduler(
        task_store=_task_store,
        task_executor=executor,
        sio=sio,
    )
    setup_scheduler(_scheduler)
    logger.info("[OK] Task scheduler initialised (starts on event loop capture)")

    # -- Executive Briefing ---------------------------------------------
    from cohort.api import ExecutiveBriefing
    global _briefing  # noqa: PLW0603
    _briefing = ExecutiveBriefing(
        data_dir=Path(resolved_dir),
        chat=_chat,
        work_queue=_work_queue,
        data_layer=_data_layer,
        orchestrator_getter=_get_session_orch,
    )
    logger.info("[OK] Executive briefing initialised")

    # -- Benchmark Runner -----------------------------------------------
    from cohort.api import get_benchmark_runner
    _bench = get_benchmark_runner(data_dir=_resolved_data_dir)
    _bench.set_chat(_chat)
    _bench.set_agent_store(_agent_store)
    logger.info("[OK] Benchmark runner initialised")

    # -- Routes ---------------------------------------------------------
    routes = [
        Route("/", index, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/api/channels", list_channels, methods=["GET"]),
        Route("/api/channels", create_channel_endpoint, methods=["POST"]),
        Route("/api/channels/{channel_id}", delete_channel_endpoint, methods=["DELETE"]),
        Route("/api/channels/{channel_id}", rename_channel_endpoint, methods=["PATCH"]),
        Route("/api/channels/{channel_id}/archive", archive_channel_endpoint, methods=["POST"]),
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
        Route("/api/settings/profile", get_profile, methods=["GET"]),
        Route("/api/settings/profile", put_profile, methods=["PUT"]),
        Route("/api/permissions", get_permissions, methods=["GET"]),
        Route("/api/permissions", post_permissions, methods=["POST"]),
        Route("/api/service-keys/test", test_service_key, methods=["POST"]),
        Route("/api/tool-permissions", get_tool_permissions, methods=["GET"]),
        Route("/api/tool-permissions", put_tool_permissions, methods=["PUT"]),
        Route("/api/internal-web/status", get_internal_web_status, methods=["GET"]),
        Route("/api/internal-web-search/status", get_internal_web_search_status, methods=["GET"]),
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
        Route("/api/work-queue/{item_id}/submit-review", submit_work_item_for_review, methods=["POST"]),
        Route("/api/work-queue/{item_id}/reviews", attach_work_item_reviews, methods=["POST"]),
        Route("/api/work-queue/{item_id}/requeue", requeue_work_item, methods=["POST"]),
        # Approval pipeline
        Route("/api/approvals", list_approvals, methods=["GET"]),
        Route("/api/approvals", create_approval, methods=["POST"]),
        Route("/api/approvals/{approval_id}", resolve_approval, methods=["PATCH"]),
        Route("/api/tasks/{task_id}/submit-review", submit_task_for_review, methods=["POST"]),
        Route("/api/tasks/{task_id}/reviews", attach_task_reviews, methods=["POST"]),
        Route("/api/tasks/{task_id}/requeue", requeue_task, methods=["POST"]),
        Route("/api/review-pipeline/config", get_review_pipeline_config, methods=["GET"]),
        Route("/api/review-pipeline/config", put_review_pipeline_config, methods=["PUT"]),
        # Channel (Claude Code Channels integration)
        Route("/api/channel/poll", channel_poll, methods=["GET"]),
        Route("/api/channel/heartbeat", channel_heartbeat, methods=["POST"]),
        Route("/api/channel/capabilities", channel_capabilities, methods=["GET"]),
        Route("/api/channel/status", channel_status, methods=["GET"]),
        Route("/api/channel/sessions", channel_sessions, methods=["GET"]),
        Route("/api/channel/ensure-session", channel_ensure_session, methods=["POST"]),
        Route("/api/channel/invoke", channel_invoke, methods=["POST"]),
        Route("/api/channel/register", channel_register, methods=["POST"]),
        Route("/api/channel/launch-queue", channel_launch_queue, methods=["GET"]),
        Route("/api/channel/launch-queue/{channel_id}/ack", channel_launch_ack, methods=["POST"]),
        Route("/api/channel/{request_id}/claim", channel_claim, methods=["POST"]),
        Route("/api/channel/{request_id}/respond", channel_respond, methods=["POST"]),
        Route("/api/channel/{request_id}/error", channel_error, methods=["POST"]),
        # Schedules
        Route("/api/schedules", get_schedules, methods=["GET"]),
        Route("/api/schedules", create_schedule_endpoint, methods=["POST"]),
        Route("/api/schedules/presets", get_schedule_presets, methods=["GET"]),
        Route("/api/scheduler/status", get_scheduler_status, methods=["GET"]),
        Route("/api/schedules/{schedule_id}", get_schedule_detail, methods=["GET"]),
        Route("/api/schedules/{schedule_id}", update_schedule_endpoint, methods=["PATCH"]),
        Route("/api/schedules/{schedule_id}", delete_schedule_endpoint, methods=["DELETE"]),
        Route("/api/schedules/{schedule_id}/toggle", toggle_schedule_endpoint, methods=["POST"]),
        Route("/api/schedules/{schedule_id}/run", force_run_schedule_endpoint, methods=["POST"]),
        # Executive briefing
        Route("/api/briefing/generate", generate_briefing, methods=["POST"]),
        Route("/api/briefing/latest", get_latest_briefing, methods=["GET"]),
        Route("/api/briefing/latest/html", get_latest_briefing_html, methods=["GET"]),
        Route("/api/briefing/list", list_briefing_reports, methods=["GET"]),
        Route("/api/briefing/{date}/html", get_briefing_by_date, methods=["GET"]),
        # Intel feed
        Route("/api/intel/fetch", fetch_intel, methods=["POST"]),
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
        Route("/api/setup/global-agents", setup_global_agents, methods=["POST"]),
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
        Route("/api/sessions/{session_id}/extend", session_extend_turns, methods=["POST"]),
        Route("/api/sessions/{session_id}/participants", session_add_participant, methods=["POST"]),
        Route("/api/sessions/{session_id}/participants/{agent_id}", session_remove_participant, methods=["DELETE"]),
        Route("/api/sessions/{session_id}/participants/{agent_id}/status", session_update_participant_status, methods=["PUT"]),
        Route("/api/sessions/{session_id}/score/{agent_id}", session_score_agent, methods=["GET"]),
        Route("/api/sessions/channel/{channel_id}", get_channel_session, methods=["GET"]),
        Route("/api/channels/{channel_id}/meeting-mode", channel_meeting_enable, methods=["POST"]),
        Route("/api/channels/{channel_id}/meeting-mode", channel_meeting_disable, methods=["DELETE"]),
        Route("/api/channels/{channel_id}/meeting-context", channel_meeting_context, methods=["GET"]),
        Route("/api/channels/{channel_id}/phase", channel_detect_phase, methods=["GET"]),
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
        Route("/api/tool-readiness/all", get_tool_readiness_all, methods=["GET"]),
        Route("/api/tool-context/{tool_id}", get_tool_context, methods=["GET"]),
        Route("/api/tool-config/{tool_id}/values", get_tool_config_values, methods=["GET"]),
        Route("/api/tool-config/{tool_id}/values", put_tool_config_value, methods=["PUT"]),
        Route("/api/tool-config/{tool_id}", get_tool_config, methods=["GET"]),
        Route("/api/llm/models", llm_list_models, methods=["GET"]),
        Route("/api/llm/pull", llm_pull_model, methods=["POST"]),
        Route("/api/llm/models/{name:path}", llm_delete_model, methods=["DELETE"]),
        Route("/api/llm/running", get_llm_running, methods=["GET"]),
        Route("/api/llm/model-info/{name:path}", get_llm_model_info, methods=["GET"]),
        # Tool data endpoints (read from Cohort service data directory)
        Route("/api/health-monitor/state", get_health_monitor_state, methods=["GET"]),
        Route("/api/health-monitor/alerts", get_health_monitor_alerts, methods=["GET"]),
        Route("/api/health-monitor/run", health_monitor_run_checks, methods=["POST"]),
        Route("/api/health-monitor/services", health_monitor_services, methods=["GET"]),
        Route("/api/health-monitor/stop/{service_key}", health_monitor_stop, methods=["POST"]),
        Route("/api/health-monitor/start/{service_key}", health_monitor_start, methods=["POST"]),
        Route("/api/health-monitor/restart/{service_key}", health_monitor_restart, methods=["POST"]),
        Route("/api/scheduler/recent-runs", get_scheduler_recent_runs, methods=["GET"]),
        Route("/api/comms/recent-activity", get_comms_recent_activity, methods=["GET"]),
        Route("/api/intel/recent-articles", get_intel_recent_articles, methods=["GET"]),
        Route("/api/intel/feeds", get_intel_feeds, methods=["GET"]),
        Route("/api/intel/feeds", add_intel_feed, methods=["POST"]),
        Route("/api/intel/feeds", delete_intel_feed, methods=["DELETE"]),
        Route("/api/intel/keywords", update_intel_keywords, methods=["PUT"]),
        Route("/api/intel/relevance-mode", update_intel_relevance_mode, methods=["PUT"]),
        Route("/api/content-monitor/pipeline-status", get_content_monitor_pipeline, methods=["GET"]),
        Route("/api/content-monitor/posts", get_social_posts, methods=["GET"]),
        Route("/api/content-monitor/posts/{post_id}", update_social_post, methods=["PATCH"]),
        Route("/api/content-monitor/articles", get_content_articles, methods=["GET"]),
        Route("/api/content-monitor/config", get_content_config, methods=["GET"]),
        # Content projects (multi-project content strategy)
        Route("/api/content-projects", list_content_projects, methods=["GET"]),
        Route("/api/content-projects", create_content_project, methods=["POST"]),
        Route("/api/content-projects/score-all", score_all_articles_for_projects, methods=["POST"]),
        Route("/api/content-projects/{project_id}", get_content_project, methods=["GET"]),
        Route("/api/content-projects/{project_id}", update_content_project, methods=["PUT"]),
        Route("/api/content-projects/{project_id}", delete_content_project, methods=["DELETE"]),
        Route("/api/content-projects/{project_id}/score", score_article_endpoint, methods=["POST"]),
        Route("/api/content-projects/{project_id}/articles", get_project_articles, methods=["GET"]),
        Route("/api/web-search/test", web_search_test, methods=["POST"]),
        Route("/api/web-search/test-local", web_search_local_test, methods=["POST"]),
        Route("/api/web-search/search", web_search_query, methods=["GET"]),
        Route("/api/youtube/test", youtube_search_test, methods=["POST"]),
        Route("/api/youtube/video/{video_id}", youtube_video_detail, methods=["GET"]),
        Route("/api/youtube/transcript/{video_id}", youtube_transcript, methods=["GET"]),
        Route("/api/doc-processor/summarize", doc_summarize, methods=["POST"]),
        Route("/api/doc-processor/process", doc_process_file, methods=["POST"]),
        Route("/api/doc-processor/fetch-url", doc_fetch_url, methods=["POST"]),
        Route("/api/doc-processor/history", doc_history_get, methods=["GET"]),
        Route("/api/doc-processor/history", doc_history_post, methods=["POST"]),
        Route("/api/doc-processor/history", doc_history_delete, methods=["DELETE"]),
        Route("/api/comms/pending-approvals", get_comms_pending_approvals, methods=["GET"]),
        Route("/api/comms/safety-status", get_comms_safety_status, methods=["GET"]),
        # Website Creator
        # Benchmark A/B (dev tool)
        Route("/api/benchmark/status", benchmark_status, methods=["GET"]),
        Route("/api/benchmark/scenarios", benchmark_scenarios, methods=["GET"]),
        Route("/api/benchmark/runs", benchmark_runs, methods=["GET"]),
        Route("/api/benchmark/runs/{run_id}", benchmark_run_detail, methods=["GET"]),
        Route("/api/benchmark/start", benchmark_start, methods=["POST"]),
        Route("/api/benchmark/runs/{run_id}/score", benchmark_score, methods=["POST"]),
        Route("/api/benchmark/runs/{run_id}/auto-score", benchmark_auto_score, methods=["POST"]),
        # Ecosystem Inventory
        Route("/api/inventory", get_inventory, methods=["GET"]),
        Route("/api/inventory/refresh", refresh_inventory, methods=["POST"]),
        # Project Manager
        Route("/api/projects", list_projects, methods=["GET"]),
        Route("/api/projects", create_project, methods=["POST"]),
        Route("/api/projects/link", link_project, methods=["POST"]),
        Route("/api/projects/open-vscode", open_project_vscode, methods=["POST"]),
        Route("/api/projects/{project_path}", delete_project, methods=["DELETE"]),
        Route("/api/projects/{project_path}/permissions", update_project_permissions, methods=["PUT"]),
        # Website Creator
        Route("/api/website/create", website_create, methods=["POST"]),
        Route("/api/website/worksheet", website_worksheet, methods=["GET"]),
        Route("/api/website/projects", website_list_projects, methods=["GET"]),
        Route("/api/website/projects/{project_name:path}", website_serve_page, methods=["GET"]),
        Route("/api/website/preview/{project_name:path}", website_serve_preview, methods=["GET"]),
    ]

    # Desktop automation endpoints (lazy-init, no impact if desktop extras not installed)
    try:
        from cohort.desktop.http_endpoints import desktop_routes
        routes += desktop_routes()
        logger.info("[OK] Desktop automation endpoints registered (/api/desktop/*)")
    except ImportError:
        logger.debug("Desktop automation endpoints not available (missing extras)")
    except Exception as exc:
        logger.warning("[!] Desktop endpoints failed to register: %s", exc)

    # Only mount static files if the directory exists (dashboard is optional)
    if _STATIC_DIR.is_dir():
        routes.append(Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"))

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
        """Capture the running event loop and start background services."""
        loop = asyncio.get_running_loop()
        from cohort.agent_router import set_event_loop
        set_event_loop(loop)
        executor.set_event_loop(loop)
        logger.info("[OK] Event loop captured for agent router + task executor")

        # Wire benchmark runner to Socket.IO
        _bench.set_emit(sio.emit, loop)
        logger.info("[OK] Benchmark runner wired to Socket.IO")

        # Initialize channel bridge state persistence
        try:
            from cohort.channel_bridge import load_session_state, set_data_dir
            set_data_dir(_resolved_data_dir)
            recovered = load_session_state()
            if recovered:
                logger.info("[OK] Re-adopted %d channel session(s) from prior run", recovered)
        except Exception as exc:
            logger.warning("[!] Channel session recovery failed: %s", exc)

        # Load ecosystem inventory cache (best-effort, non-blocking)
        try:
            _load_inventory_cache()
        except Exception as exc:
            logger.warning("[!] Inventory pre-load failed (will retry on first request): %s", exc)

        # Start the task scheduler background loop
        if _scheduler is not None:
            _scheduler.start()
            logger.info("[OK] Task scheduler started")

        # Run initial health checks so the Health Monitor has data immediately
        try:
            from cohort.api import run_service_checks
            await asyncio.get_event_loop().run_in_executor(None, run_service_checks)
            logger.info("[OK] Initial health checks completed")
        except Exception as exc:
            logger.warning("[!] Initial health checks failed (will retry on first request): %s", exc)

    starlette_app = Starlette(
        routes=routes,
        middleware=middleware,
        on_startup=[on_startup],
    )

    # Wrap Starlette with Socket.IO ASGI app
    import socketio as sio_module
    app = sio_module.ASGIApp(sio, other_asgi_app=starlette_app)

    # Start channel session idle reaper (background daemon)
    def _channel_idle_reaper() -> None:
        import time as _time
        while True:
            _time.sleep(60)
            try:
                from cohort.channel_bridge import reap_idle_sessions, recover_crashed_sessions
                reaped = reap_idle_sessions()
                if reaped:
                    logger.info("[*] Reaped %d idle channel session(s)", reaped)
                recovered = recover_crashed_sessions()
                if recovered:
                    logger.info("[OK] Auto-respawned %d crashed session(s)", recovered)
            except Exception:
                logger.exception("[!] Channel reaper error")

    # Start work-queue timeout reaper (background daemon)
    def _wq_timeout_reaper() -> None:
        import time as _time
        while True:
            _time.sleep(30)
            try:
                timed_out = _work_queue.expire_timed_out()
                if timed_out:
                    logger.info(
                        "[*] Work queue: timed out %d item(s): %s",
                        len(timed_out), timed_out,
                    )
                    _broadcast_work_queue()
            except Exception:
                logger.exception("[!] Work queue timeout reaper error")

    # Work-queue dispatch thread -- claims items and routes to channel sessions.
    # Replaces the standalone wq_worker.py service.
    _wq_dispatch_interval = int(os.environ.get("COHORT_WQ_POLL_INTERVAL", "3"))

    def _wq_dispatch_loop() -> None:
        import time as _time
        logger.info("[OK] WQ dispatch thread started (poll every %ds)", _wq_dispatch_interval)
        while True:
            _time.sleep(_wq_dispatch_interval)
            try:
                if _work_queue is None:
                    continue
                result = _work_queue.claim_next()
                if not result or "error" in result:
                    continue
                item_data = result.get("item")
                if item_data is None:
                    continue

                item_id = item_data.get("id", "unknown")
                meta = item_data.get("metadata") or {}
                agent_id = item_data.get("agent_id") or meta.get("agent_id")
                description = item_data.get("description") or ""

                if not agent_id:
                    # No agent -- post as plain message to the source channel
                    channel = meta.get("target_channel") or meta.get("channel", "general")
                    chat = _get_chat()
                    if chat:
                        chat.post_message(
                            channel_id=channel,
                            sender="wq-dispatcher",
                            content=f"[Work Queue] {item_id}\n\n{description}",
                            metadata={"wq_item_id": item_id, "source": "wq_dispatcher"},
                        )
                    logger.info("[OK] WQ %s posted to #%s (no agent)", item_id, channel)
                    continue

                # Route to agent via channel invoke (same path as /api/channel/invoke)
                source_channel = meta.get("channel", "general")
                thread_id = meta.get("thread_id")

                from cohort.agent_router import enqueue_agent_channel_request, resolve_agent_id
                resolved = resolve_agent_id(agent_id) or agent_id

                import threading as _thr
                _thr.Thread(
                    target=enqueue_agent_channel_request,
                    kwargs=dict(
                        agent_id=resolved,
                        channel_id=source_channel,
                        message=description,
                        thread_id=thread_id,
                        reply_channel=source_channel,
                    ),
                    daemon=True,
                ).start()
                logger.info("[OK] WQ %s dispatched to %s in #%s", item_id, resolved, source_channel)

            except Exception:
                logger.exception("[!] WQ dispatch error")

    import threading
    threading.Thread(target=_channel_idle_reaper, daemon=True, name="channel-reaper").start()
    threading.Thread(target=_wq_timeout_reaper, daemon=True, name="wq-timeout-reaper").start()
    threading.Thread(target=_wq_dispatch_loop, daemon=True, name="wq-dispatcher").start()

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
