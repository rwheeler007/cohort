"""Desktop Computer Use MCP Server.

Provides Claude Code sessions with Windows desktop automation via a single
dispatcher tool (desktop_action). Uses Parsec VDD virtual displays for
session isolation -- each session gets its own monitor.

Safety: Global kill switch (default OFF), permission tiers, coordinate
bounds checking, key combo blocklist, mandatory audit trail.

Usage:
    python tools/desktop_computer_use/service.py          # stdio transport
    fastmcp dev tools/desktop_computer_use/service.py     # MCP inspector
"""

import atexit
import logging
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Resolve paths
MCP_DIR = Path(__file__).resolve().parent
TOOLS_DIR = MCP_DIR.parent
BOSS_ROOT = TOOLS_DIR.parent

boss_root_str = str(BOSS_ROOT)
if boss_root_str not in sys.path:
    sys.path.insert(0, boss_root_str)

from cohort.desktop.backend import DesktopBackend
from cohort.desktop.config import DesktopConfig, load_config
from cohort.desktop.safety import (
    check_desktop_permission,
)
from cohort.desktop.types import DesktopResult

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("desktop_computer_use")

CHARACTER_LIMIT = 25000

# Singleton backend
_backend: Optional[DesktopBackend] = None
_config: Optional[DesktopConfig] = None


def _get_config() -> DesktopConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_backend() -> DesktopBackend:
    global _backend
    if _backend is None:
        cfg = _get_config()
        _backend = DesktopBackend(cfg)
    return _backend


def _cleanup():
    """Tear down all sessions on exit."""
    if _backend:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_backend.stop())
            else:
                loop.run_until_complete(_backend.stop())
        except Exception:
            pass


atexit.register(_cleanup)


# ---------------------------------------------------------------------------
# Action catalog (embedded in tool description for the LLM)
# ---------------------------------------------------------------------------

_ACTION_CATALOG = """
OBSERVE (read-only, no side effects):
  screenshot              -- Capture entire virtual display. Returns file path.
  screenshot_window       -- Capture specific window. Params: window_title
  screenshot_region       -- Capture region. Params: x, y, width, height
  list_windows            -- List visible windows on virtual display
  get_active_window       -- Get foreground window info
  get_mouse_position      -- Get cursor position (display-relative)
  get_clipboard           -- Read clipboard text

INTERACT (mouse/keyboard on virtual display):
  click                   -- Click at position. Params: x, y, button, clicks
  double_click            -- Double-click. Params: x, y
  right_click             -- Right-click. Params: x, y
  type_text               -- Type text. Params: text, interval
  press_key               -- Key combo. Params: key_combo (e.g., 'ctrl+c')
  mouse_move              -- Move cursor. Params: x, y
  mouse_drag              -- Drag. Params: x, y (start), end_x, end_y
  scroll                  -- Scroll wheel. Params: x, y, scroll_clicks
  focus_window            -- Focus window. Params: window_title
  set_clipboard           -- Set clipboard. Params: text

ADVANCED (session management, apps):
  start_session           -- Create virtual display session
  stop_session            -- Tear down virtual display
  launch_app              -- Launch app. Params: app_path, app_args
  close_window            -- Close window. Params: window_title
  resize_window           -- Move/resize. Params: window_title, x, y, width, height
  minimize_window         -- Minimize. Params: window_title
  maximize_window         -- Maximize. Params: window_title
  run_command             -- Shell command (disabled by default). Params: command

All coordinates are relative to the virtual display (0,0 = top-left).
Sessions auto-create on first action if not explicitly started.
""".strip()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="desktop_action",
    annotations={
        "title": "Desktop Computer Use",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def desktop_action(
    action: str,
    session_id: str = "default",
    x: int = 0,
    y: int = 0,
    end_x: int = 0,
    end_y: int = 0,
    width: int = 0,
    height: int = 0,
    text: str = "",
    key_combo: str = "",
    button: str = "left",
    clicks: int = 1,
    scroll_clicks: int = 0,
    window_title: str = "",
    app_path: str = "",
    app_args: list = None,
    command: str = "",
    interval: float = 0.02,
) -> str:
    """Control the Windows desktop via an isolated virtual display.

    Creates a Parsec VDD virtual monitor (1024x768) for each session.
    All coordinates are relative to the virtual display (0,0 = top-left).
    Sessions auto-create on first action.

    Actions grouped by permission tier:

    OBSERVE: screenshot, screenshot_window, screenshot_region, list_windows,
    get_active_window, get_mouse_position, get_clipboard

    INTERACT: click, double_click, right_click, type_text, press_key,
    mouse_move, mouse_drag, scroll, focus_window, set_clipboard

    ADVANCED: start_session, stop_session, launch_app, close_window,
    resize_window, minimize_window, maximize_window, run_command
    """
    config = _get_config()

    # Kill switch
    if not config.enabled:
        return "Error: Desktop computer use is disabled. Set enabled: true in config/desktop_computer_use.yaml"

    action = action.lower().strip()

    # Help action
    if action == "help":
        return _ACTION_CATALOG

    # Permission check
    ok, reason = check_desktop_permission(action, config.permission_tier)
    if not ok:
        return f"Error: {reason}"

    backend = _get_backend()
    if not backend._started:
        await backend.start()

    # Dispatch
    if app_args is None:
        app_args = []

    result = await _dispatch(
        backend, action, session_id,
        x=x, y=y, end_x=end_x, end_y=end_y,
        width=width, height=height,
        text=text, key_combo=key_combo,
        button=button, clicks=clicks, scroll_clicks=scroll_clicks,
        window_title=window_title,
        app_path=app_path, app_args=app_args,
        command=command, interval=interval,
    )

    output = result.to_str()
    if len(output) > CHARACTER_LIMIT:
        output = output[:CHARACTER_LIMIT] + "\n[...truncated]"
    return output


class _DesktopStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="desktop_status",
    annotations={
        "title": "Desktop Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
)
async def desktop_status() -> str:
    """Check desktop automation status: enabled state, active sessions, config."""
    config = _get_config()
    backend = _get_backend()

    lines = [
        f"Enabled: {config.enabled}",
        f"Virtual display: {config.virtual_display.enabled}",
        f"  Resolution: {config.virtual_display.width}x{config.virtual_display.height}",
        f"Permission tier: {config.permission_tier}",
        f"Active sessions: {len(backend._sessions)}",
    ]

    for sid, session in backend._sessions.items():
        bounds = session.display_bounds
        lines.append(
            f"  [{sid}] device={session.device_name} "
            f"bounds=({bounds.x},{bounds.y} {bounds.width}x{bounds.height}) "
            f"vd={'yes' if session.virtual_display else 'no'}"
        )

    # Screenshot count
    if config.screenshot_dir.exists():
        count = len(list(config.screenshot_dir.glob("*.jpg")))
        lines.append(f"Screenshots stored: {count}/{config.max_screenshots_retained}")

    # Driver check (if virtual display enabled)
    if config.virtual_display.enabled:
        try:
            from cohort.desktop.virtual_display import check_driver
            info = check_driver()
            lines.append(
                f"Parsec VDD: {'ready' if info['installed'] else 'not found'}"
                + (f" v{info['version']}" if info['version'] else "")
            )
        except Exception:
            lines.append("Parsec VDD: check failed")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def _dispatch(
    backend: DesktopBackend,
    action: str,
    session_id: str,
    **kwargs,
) -> DesktopResult:
    """Route action string to backend method."""

    # Session lifecycle
    if action == "start_session":
        return await backend.start_session(session_id)
    if action == "stop_session":
        return await backend.stop_session(session_id)

    # Observe
    if action == "screenshot":
        return await backend.screenshot(session_id)
    if action == "screenshot_window":
        return await backend.screenshot_window(session_id, kwargs["window_title"])
    if action == "screenshot_region":
        return await backend.screenshot_region(
            session_id, kwargs["x"], kwargs["y"],
            kwargs["width"], kwargs["height"],
        )
    if action == "list_windows":
        return await backend.list_windows(session_id)
    if action == "get_active_window":
        return await backend.get_active_window(session_id)
    if action == "get_mouse_position":
        return await backend.get_mouse_position(session_id)
    if action == "get_clipboard":
        return await backend.get_clipboard(session_id)

    # Interact
    if action == "click":
        return await backend.click(
            session_id, kwargs["x"], kwargs["y"],
            kwargs["button"], kwargs["clicks"],
        )
    if action == "double_click":
        return await backend.click(session_id, kwargs["x"], kwargs["y"], "left", 2)
    if action == "right_click":
        return await backend.click(session_id, kwargs["x"], kwargs["y"], "right", 1)
    if action == "type_text":
        return await backend.type_text(
            session_id, kwargs["text"], kwargs["interval"],
        )
    if action == "press_key":
        return await backend.press_key(session_id, kwargs["key_combo"])
    if action == "mouse_move":
        return await backend.mouse_move(session_id, kwargs["x"], kwargs["y"])
    if action == "mouse_drag":
        return await backend.mouse_drag(
            session_id,
            kwargs["x"], kwargs["y"],
            kwargs["end_x"], kwargs["end_y"],
        )
    if action == "scroll":
        return await backend.scroll(
            session_id, kwargs["x"], kwargs["y"], kwargs["scroll_clicks"],
        )
    if action == "focus_window":
        return await backend.focus_window(session_id, kwargs["window_title"])
    if action == "set_clipboard":
        return await backend.set_clipboard(session_id, kwargs["text"])

    # Advanced
    if action == "launch_app":
        return await backend.launch_app(
            session_id, kwargs["app_path"], kwargs["app_args"],
        )
    if action == "close_window":
        return await backend.close_window(session_id, kwargs["window_title"])
    if action == "resize_window":
        return await backend.resize_window(
            session_id, kwargs["window_title"],
            kwargs["x"], kwargs["y"],
            kwargs["width"], kwargs["height"],
        )
    if action == "minimize_window":
        return await backend.minimize_window(session_id, kwargs["window_title"])
    if action == "maximize_window":
        return await backend.maximize_window(session_id, kwargs["window_title"])
    if action == "run_command":
        return await backend.run_command(session_id, kwargs["command"])

    return DesktopResult(
        success=False,
        error=f"Unknown action: '{action}'. Use action='help' for catalog.",
        action=action,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
