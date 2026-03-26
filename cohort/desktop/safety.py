"""Safety layer for Desktop Computer Use MCP Server.

Permission tiers, window allowlists, coordinate validation, key combo
blocking, and mandatory audit logging. Modeled on the browser_backend.py
permission system from cohort/mcp/browser_backend.py.
"""

import fnmatch
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from cohort.desktop.types import (
    AuditEntry,
    DisplayBounds,
    WindowInfo,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permission tier definitions
# ---------------------------------------------------------------------------

DESKTOP_PERMISSION_TIERS = {
    # desktop_observe (read-only)
    "screenshot": "desktop_observe",
    "screenshot_window": "desktop_observe",
    "screenshot_region": "desktop_observe",
    "list_windows": "desktop_observe",
    "get_active_window": "desktop_observe",
    "get_mouse_position": "desktop_observe",
    "get_clipboard": "desktop_observe",
    "accessibility_tree": "desktop_observe",
    "help": "desktop_observe",

    # desktop_interact (mouse/keyboard)
    "click": "desktop_interact",
    "double_click": "desktop_interact",
    "right_click": "desktop_interact",
    "type_text": "desktop_interact",
    "press_key": "desktop_interact",
    "mouse_move": "desktop_interact",
    "mouse_drag": "desktop_interact",
    "scroll": "desktop_interact",
    "focus_window": "desktop_interact",
    "set_clipboard": "desktop_interact",

    # desktop_advanced (session lifecycle, apps, commands)
    "start_session": "desktop_advanced",
    "stop_session": "desktop_advanced",
    "launch_app": "desktop_advanced",
    "close_window": "desktop_advanced",
    "resize_window": "desktop_advanced",
    "minimize_window": "desktop_advanced",
    "maximize_window": "desktop_advanced",
    "run_command": "desktop_advanced",
}

TIER_HIERARCHY = {
    "desktop_observe": {"desktop_observe"},
    "desktop_interact": {"desktop_observe", "desktop_interact"},
    "desktop_advanced": {"desktop_observe", "desktop_interact", "desktop_advanced"},
}


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

def check_desktop_permission(
    action: str, session_tier: str,
) -> Tuple[bool, str]:
    """Check if the session's tier allows the action.

    Returns:
        (allowed, reason) tuple.
    """
    required = DESKTOP_PERMISSION_TIERS.get(action)
    if required is None:
        return False, f"Unknown action: '{action}'"

    allowed_tiers = TIER_HIERARCHY.get(session_tier, set())
    if required in allowed_tiers:
        return True, ""

    return False, (
        f"Action '{action}' requires '{required}' tier, "
        f"but session has '{session_tier}'"
    )


def check_coordinates_in_bounds(
    x: int, y: int, bounds: DisplayBounds,
) -> Tuple[bool, str]:
    """Ensure coordinates are within the virtual display bounds.

    Coordinates are display-relative (0-based).

    Returns:
        (allowed, reason) tuple.
    """
    if bounds.contains(x, y):
        return True, ""
    return False, (
        f"Coordinates ({x}, {y}) outside display bounds "
        f"(0-{bounds.width}, 0-{bounds.height})"
    )


def check_window_allowed(
    window_title: str,
    window_class: str,
    allowlist: List[str],
) -> Tuple[bool, str]:
    """Check if a window matches the allowlist (fnmatch patterns).

    Empty allowlist = allow all (used when virtual display is active).

    Returns:
        (allowed, reason) tuple.
    """
    if not allowlist:
        return True, ""

    for pattern in allowlist:
        if fnmatch.fnmatch(window_title, pattern):
            return True, ""
        if fnmatch.fnmatch(window_class, pattern):
            return True, ""

    return False, (
        f"Window '{window_title}' [{window_class}] not in allowlist"
    )


def check_coordinates_in_allowed_window(
    x: int, y: int,
    windows: List[WindowInfo],
    allowlist: List[str],
) -> Tuple[bool, str, Optional[WindowInfo]]:
    """Find which window contains (x, y) and check the allowlist.

    Used when operating on the real desktop (no virtual display).

    Returns:
        (allowed, reason, matched_window) tuple.
    """
    if not allowlist:
        return True, "", None

    for win in windows:
        if win.contains(x, y):
            allowed, reason = check_window_allowed(
                win.title, win.class_name, allowlist,
            )
            if allowed:
                return True, "", win
            return False, reason, win

    return False, f"No window found at ({x}, {y})", None


def check_key_combo_allowed(
    key_combo: str, blocklist: List[str],
) -> Tuple[bool, str]:
    """Normalize and check key combo against blocklist.

    Normalization: lowercase, sorted parts, joined with '+'.

    Returns:
        (allowed, reason) tuple.
    """
    normalized = _normalize_key_combo(key_combo)

    for blocked in blocklist:
        if _normalize_key_combo(blocked) == normalized:
            return False, f"Key combo '{key_combo}' is blocked"

    return True, ""


def check_app_allowed(
    app_path: str, allowlist: List[str],
) -> Tuple[bool, str]:
    """Check executable against launch allowlist.

    Matches by basename (case-insensitive) or full path pattern.

    Returns:
        (allowed, reason) tuple.
    """
    if not allowlist:
        return False, "No applications are allowed (allowlist is empty)"

    app_lower = app_path.lower()
    basename = Path(app_path).name.lower()

    for pattern in allowlist:
        pat_lower = pattern.lower()
        if basename == pat_lower or fnmatch.fnmatch(app_lower, pat_lower):
            return True, ""

    return False, f"Application '{app_path}' not in allowlist"


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_audit_entry(entry: AuditEntry, audit_path: Path) -> None:
    """Append a JSONL audit entry. Mandatory on every action."""
    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")
    except OSError as e:
        log.error("Failed to write audit log: %s", e)


def make_audit_entry(
    session_id: str,
    action: str,
    params: dict,
    target_window: str = "",
    screenshot_path: str = "",
    result_success: bool = True,
) -> AuditEntry:
    """Create an AuditEntry with current timestamp."""
    return AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        action=action,
        params=params,
        target_window=target_window,
        screenshot_path=screenshot_path,
        result_success=result_success,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_key_combo(combo: str) -> str:
    """Normalize a key combo for comparison.

    'Ctrl+Alt+Delete' -> 'alt+ctrl+delete'
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    # Sort modifier keys, keep the final key last
    modifiers = sorted(p for p in parts[:-1])
    return "+".join(modifiers + [parts[-1]]) if parts else ""
