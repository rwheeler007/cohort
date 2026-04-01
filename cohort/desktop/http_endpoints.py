"""Desktop automation HTTP endpoints for the Cohort server.

Exposes the DesktopBackend over REST so pytest (and other HTTP clients)
can drive desktop actions without an MCP connection.

Endpoints:
    POST /api/desktop/action   -- dispatch a desktop action
    GET  /api/desktop/status   -- read-only status check

Usage in server.py:
    from cohort.desktop.http_endpoints import desktop_routes
    routes += desktop_routes()

The backend is lazily initialised on the first request.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from cohort.desktop.backend import DesktopBackend
from cohort.desktop.config import DesktopConfig, load_config
from cohort.desktop.safety import check_desktop_permission

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton backend (same pattern as mcp_server.py)
# ---------------------------------------------------------------------------

_backend: Optional[DesktopBackend] = None
_config: Optional[DesktopConfig] = None


def _get_config() -> DesktopConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


async def _get_backend() -> DesktopBackend:
    global _backend
    if _backend is None:
        cfg = _get_config()
        _backend = DesktopBackend(cfg)
    if not _backend._started:
        await _backend.start()
    return _backend


# ---------------------------------------------------------------------------
# Dispatcher (reuses the same routing logic as mcp_server._dispatch)
# ---------------------------------------------------------------------------

async def _dispatch(backend: DesktopBackend, action: str, params: dict):
    """Route action string to backend method. Returns DesktopResult."""
    from cohort.desktop.desktop_types import DesktopResult

    sid = params.get("session_id", "default")

    # Session lifecycle
    if action == "start_session":
        return await backend.start_session(sid)
    if action == "stop_session":
        return await backend.stop_session(sid)

    # Observe
    if action == "screenshot":
        return await backend.screenshot(sid)
    if action == "screenshot_window":
        return await backend.screenshot_window(sid, params.get("window_title", ""))
    if action == "screenshot_region":
        return await backend.screenshot_region(
            sid, params.get("x", 0), params.get("y", 0),
            params.get("width", 0), params.get("height", 0),
        )
    if action == "list_windows":
        return await backend.list_windows(sid)
    if action == "get_active_window":
        return await backend.get_active_window(sid)
    if action == "get_mouse_position":
        return await backend.get_mouse_position(sid)
    if action == "get_clipboard":
        return await backend.get_clipboard(sid)

    # Interact
    if action == "click":
        return await backend.click(
            sid, params.get("x", 0), params.get("y", 0),
            params.get("button", "left"), params.get("clicks", 1),
        )
    if action == "double_click":
        return await backend.click(sid, params.get("x", 0), params.get("y", 0), "left", 2)
    if action == "right_click":
        return await backend.click(sid, params.get("x", 0), params.get("y", 0), "right", 1)
    if action == "type_text":
        return await backend.type_text(sid, params.get("text", ""), params.get("interval", 0.02))
    if action == "press_key":
        return await backend.press_key(sid, params.get("key_combo", ""))
    if action == "mouse_move":
        return await backend.mouse_move(sid, params.get("x", 0), params.get("y", 0))
    if action == "mouse_drag":
        return await backend.mouse_drag(
            sid, params.get("x", 0), params.get("y", 0),
            params.get("end_x", 0), params.get("end_y", 0),
        )
    if action == "scroll":
        return await backend.scroll(
            sid, params.get("x", 0), params.get("y", 0),
            params.get("scroll_clicks", 0),
        )
    if action == "focus_window":
        return await backend.focus_window(sid, params.get("window_title", ""))
    if action == "set_clipboard":
        return await backend.set_clipboard(sid, params.get("text", ""))

    # Advanced
    if action == "launch_app":
        return await backend.launch_app(
            sid, params.get("app_path", ""), params.get("app_args", []),
        )
    if action == "close_window":
        return await backend.close_window(sid, params.get("window_title", ""))
    if action == "resize_window":
        return await backend.resize_window(
            sid, params.get("window_title", ""),
            params.get("x", 0), params.get("y", 0),
            params.get("width", 0), params.get("height", 0),
        )
    if action == "minimize_window":
        return await backend.minimize_window(sid, params.get("window_title", ""))
    if action == "maximize_window":
        return await backend.maximize_window(sid, params.get("window_title", ""))
    if action == "run_command":
        return await backend.run_command(sid, params.get("command", ""))

    # Observer mode
    if action == "start_observer":
        from cohort.desktop.observer import start_observer
        result_dict = start_observer(
            user_goal=params.get("text", ""),
            desktop_session_id=sid,
        )
        return DesktopResult(
            success="error" not in result_dict,
            data=json.dumps(result_dict),
            action=action,
        )
    if action == "stop_observer":
        from cohort.desktop.observer import stop_observer
        return DesktopResult(success=True, data=json.dumps(stop_observer()), action=action)
    if action == "pause_observer":
        from cohort.desktop.observer import pause_observer
        return DesktopResult(success=True, data=json.dumps(pause_observer()), action=action)
    if action == "resume_observer":
        from cohort.desktop.observer import resume_observer
        return DesktopResult(success=True, data=json.dumps(resume_observer()), action=action)
    if action == "set_observer_goal":
        from cohort.desktop.observer import set_goal
        return DesktopResult(success=True, data=json.dumps(set_goal(params.get("text", ""))), action=action)
    if action == "get_observer_status":
        from cohort.desktop.observer import get_status
        return DesktopResult(success=True, data=json.dumps(get_status()), action=action)
    if action == "get_observer_guidance":
        from cohort.desktop.observer import get_history
        return DesktopResult(success=True, data=json.dumps(get_history()), action=action)

    return DesktopResult(
        success=False,
        error=f"Unknown action: '{action}'. Valid actions: screenshot, list_windows, click, press_key, ...",
        action=action,
    )


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

async def desktop_action_endpoint(request: Request) -> JSONResponse:
    """POST /api/desktop/action -- dispatch a desktop action.

    Body: {action: str, session_id?: str, ...params}
    Returns: {ok: bool, data?: str, error?: str, action: str}
    """
    config = _get_config()

    if not config.enabled:
        return JSONResponse(
            {"ok": False, "error": "Desktop automation is disabled (enabled: false in config)"},
            status_code=503,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    action = body.get("action", "").lower().strip()
    if not action:
        return JSONResponse({"ok": False, "error": "Missing 'action' field"}, status_code=400)

    # Permission check
    ok, reason = check_desktop_permission(action, config.permission_tier)
    if not ok:
        return JSONResponse({"ok": False, "error": reason}, status_code=403)

    try:
        backend = await _get_backend()
        result = await _dispatch(backend, action, body)
        return JSONResponse({
            "ok": result.success,
            "data": result.data,
            "error": result.error,
            "action": result.action,
            "window_title": result.window_title,
        })
    except Exception as exc:
        logger.exception("desktop_action failed: %s", action)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


async def desktop_status_endpoint(request: Request) -> JSONResponse:
    """GET /api/desktop/status -- read-only desktop automation status."""
    config = _get_config()

    status = {
        "enabled": config.enabled,
        "virtual_display": config.virtual_display.enabled,
        "resolution": f"{config.virtual_display.width}x{config.virtual_display.height}",
        "permission_tier": config.permission_tier,
        "sessions": {},
    }

    if config.enabled and _backend is not None:
        for sid, session in _backend._sessions.items():
            bounds = session.display_bounds
            status["sessions"][sid] = {
                "device": session.device_name,
                "bounds": f"{bounds.x},{bounds.y} {bounds.width}x{bounds.height}" if bounds else None,
                "has_virtual_display": session.virtual_display is not None,
            }

    # Screenshot count
    if config.screenshot_dir.exists():
        count = len(list(config.screenshot_dir.glob("*.jpg")))
        status["screenshot_count"] = count

    return JSONResponse(status)


# ---------------------------------------------------------------------------
# Observer Mode REST endpoints
# ---------------------------------------------------------------------------

async def observer_start_endpoint(request: Request) -> JSONResponse:
    """POST /api/desktop/observer/start — start observer mode."""
    from cohort.desktop.observer import start_observer
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = start_observer(
        user_goal=body.get("user_goal", ""),
        desktop_session_id=body.get("desktop_session_id", "default"),
    )
    status = 200 if "error" not in result else 409
    return JSONResponse(result, status_code=status)


async def observer_stop_endpoint(request: Request) -> JSONResponse:
    """POST /api/desktop/observer/stop — stop observer mode."""
    from cohort.desktop.observer import stop_observer
    return JSONResponse(stop_observer())


async def observer_pause_endpoint(request: Request) -> JSONResponse:
    """POST /api/desktop/observer/pause — pause observer mode."""
    from cohort.desktop.observer import pause_observer
    return JSONResponse(pause_observer())


async def observer_resume_endpoint(request: Request) -> JSONResponse:
    """POST /api/desktop/observer/resume — resume observer mode."""
    from cohort.desktop.observer import resume_observer
    return JSONResponse(resume_observer())


async def observer_goal_endpoint(request: Request) -> JSONResponse:
    """POST /api/desktop/observer/goal — update observer goal."""
    from cohort.desktop.observer import set_goal
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = set_goal(body.get("user_goal", ""))
    status = 200 if "error" not in result else 400
    return JSONResponse(result, status_code=status)


async def observer_status_endpoint(request: Request) -> JSONResponse:
    """GET /api/desktop/observer/status — observer session state."""
    from cohort.desktop.observer import get_status
    return JSONResponse(get_status())


async def observer_history_endpoint(request: Request) -> JSONResponse:
    """GET /api/desktop/observer/history — recent guidance items."""
    from cohort.desktop.observer import get_history
    limit = int(request.query_params.get("limit", "20"))
    return JSONResponse({"guidance": get_history(limit)})


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------

def desktop_routes():
    """Return list of Starlette Routes for desktop automation."""
    return [
        Route("/api/desktop/action", desktop_action_endpoint, methods=["POST"]),
        Route("/api/desktop/status", desktop_status_endpoint, methods=["GET"]),
        # Observer mode
        Route("/api/desktop/observer/start", observer_start_endpoint, methods=["POST"]),
        Route("/api/desktop/observer/stop", observer_stop_endpoint, methods=["POST"]),
        Route("/api/desktop/observer/pause", observer_pause_endpoint, methods=["POST"]),
        Route("/api/desktop/observer/resume", observer_resume_endpoint, methods=["POST"]),
        Route("/api/desktop/observer/goal", observer_goal_endpoint, methods=["POST"]),
        Route("/api/desktop/observer/status", observer_status_endpoint, methods=["GET"]),
        Route("/api/desktop/observer/history", observer_history_endpoint, methods=["GET"]),
    ]
