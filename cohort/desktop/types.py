"""Type definitions for Desktop Computer Use MCP Server.

Mirrors the BrowserResult / BrowserActionInput pattern from
cohort/mcp/browser_backend.py for consistency across the ecosystem.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DesktopPermissionTier(str, Enum):
    """Permission tiers for desktop actions (cumulative hierarchy)."""
    OBSERVE = "desktop_observe"
    INTERACT = "desktop_interact"
    ADVANCED = "desktop_advanced"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WindowInfo:
    """Information about a visible window."""
    title: str
    class_name: str
    hwnd: int
    x: int
    y: int
    width: int
    height: int
    is_visible: bool

    def contains(self, px: int, py: int) -> bool:
        """Check if absolute screen coordinates fall within this window."""
        return (self.x <= px < self.x + self.width
                and self.y <= py < self.y + self.height)

    def to_str(self) -> str:
        return (f"{self.title} [{self.class_name}] "
                f"({self.x},{self.y} {self.width}x{self.height})")


@dataclass
class DisplayBounds:
    """Bounding rectangle for a display (real or virtual)."""
    x: int
    y: int
    width: int
    height: int

    def contains(self, px: int, py: int) -> bool:
        """Check if relative coordinates are within bounds (0-based)."""
        return 0 <= px < self.width and 0 <= py < self.height

    def to_absolute(self, rel_x: int, rel_y: int) -> Tuple[int, int]:
        """Convert display-relative coords to absolute screen coords."""
        return self.x + rel_x, self.y + rel_y


@dataclass
class DesktopResult:
    """Standard return from a desktop action -- mirrors BrowserResult."""
    success: bool
    data: str = ""
    error: str = ""
    action: str = ""
    window_title: str = ""

    def to_str(self) -> str:
        if not self.success:
            return f"Error: {self.error}"
        parts = []
        if self.window_title:
            parts.append(f"Window: {self.window_title}")
        if self.data:
            parts.append(self.data)
        return "\n".join(parts) if parts else "[OK]"


@dataclass
class DesktopSession:
    """Tracks a desktop session with its virtual display."""
    session_id: str
    device_name: Optional[str] = None
    display_bounds: Optional[DisplayBounds] = None
    virtual_display: Any = None  # VirtualDisplay instance (avoid circular import)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now()


@dataclass
class AuditEntry:
    """Single audit log entry for a desktop action."""
    timestamp: str
    session_id: str
    action: str
    params: Dict[str, Any]
    target_window: str
    screenshot_path: str
    result_success: bool

    def to_json(self) -> str:
        return json.dumps({
            "ts": self.timestamp,
            "session": self.session_id,
            "action": self.action,
            "params": self.params,
            "window": self.target_window,
            "screenshot": self.screenshot_path,
            "ok": self.result_success,
        })


# ---------------------------------------------------------------------------
# Pydantic input models (for MCP tool definitions)
# ---------------------------------------------------------------------------

class DesktopActionInput(BaseModel):
    """Input for the desktop_action MCP tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = Field(
        ...,
        description=(
            "Desktop action to perform. "
            "OBSERVE: screenshot, screenshot_window, screenshot_region, "
            "list_windows, get_active_window, get_mouse_position, "
            "get_clipboard. "
            "INTERACT: click, double_click, right_click, type_text, "
            "press_key, mouse_move, mouse_drag, scroll, focus_window, "
            "set_clipboard. "
            "ADVANCED: start_session, stop_session, launch_app, "
            "close_window, resize_window, minimize_window, "
            "maximize_window, run_command. "
            "Use action='help' for the full catalog with parameters."
        ),
    )
    session_id: str = Field(
        default="default",
        description="Session ID for virtual display isolation.",
    )
    x: int = Field(default=0, description="X coordinate (relative to virtual display).")
    y: int = Field(default=0, description="Y coordinate (relative to virtual display).")
    end_x: int = Field(default=0, description="End X for mouse_drag.")
    end_y: int = Field(default=0, description="End Y for mouse_drag.")
    width: int = Field(default=0, description="Width for screenshot_region / resize_window.")
    height: int = Field(default=0, description="Height for screenshot_region / resize_window.")
    text: str = Field(default="", description="Text for type_text / set_clipboard.")
    key_combo: str = Field(
        default="",
        description="Key combo for press_key (e.g., 'ctrl+c', 'enter', 'alt+tab').",
    )
    button: str = Field(default="left", description="Mouse button: left/right/middle.")
    clicks: int = Field(default=1, description="Click count (1=single, 2=double).")
    scroll_clicks: int = Field(
        default=0,
        description="Scroll wheel clicks (positive=up, negative=down).",
    )
    window_title: str = Field(
        default="",
        description="Window title pattern (fnmatch) for window-targeted actions.",
    )
    app_path: str = Field(default="", description="Application path for launch_app.")
    app_args: List[str] = Field(
        default_factory=list,
        description="Arguments for launch_app.",
    )
    command: str = Field(
        default="",
        description="Shell command for run_command (restricted).",
    )
    interval: float = Field(
        default=0.02,
        description="Delay between keystrokes for type_text (seconds).",
    )


class DesktopStatusInput(BaseModel):
    """Input for the desktop_status MCP tool (read-only)."""
    model_config = ConfigDict(extra="forbid")
