"""Desktop automation backend using pyautogui + Win32 API + Parsec VDD.

Manages virtual display lifecycle per session and provides all observe,
interact, and advanced actions. Each session gets its own isolated virtual
monitor (via tools.virtual_display.VirtualDisplay).
"""

import asyncio
import ctypes
import ctypes.wintypes as wt
import logging
import os
import shlex
import subprocess
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pyautogui
import pyperclip
from PIL import Image

from cohort.desktop.config import DesktopConfig
from cohort.desktop.desktop_types import (
    DesktopResult,
    DesktopSession,
    DisplayBounds,
    WindowInfo,
)
from cohort.desktop.safety import (
    check_app_allowed,
    check_coordinates_in_bounds,
    check_key_combo_allowed,
    check_window_allowed,
    log_audit_entry,
    make_audit_entry,
)

log = logging.getLogger(__name__)

# Preserve pyautogui failsafe -- mouse to (0,0) aborts
pyautogui.FAILSAFE = True
# Reduce default pause between actions
pyautogui.PAUSE = 0.05

# Win32 constants
_SW_MINIMIZE = 6
_SW_MAXIMIZE = 3
_WM_CLOSE = 0x0010

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

# Win32 constants for capture
_PW_RENDERFULLCONTENT = 2  # PrintWindow: force DWM to render (needed for Electron/GPU apps)
_SRCCOPY = 0x00CC0020  # BitBlt raster operation


# ---------------------------------------------------------------------------
# DesktopBackend
# ---------------------------------------------------------------------------

class DesktopBackend:
    """Desktop automation backend with virtual display isolation.

    The virtual display (Parsec VDD) is created once and shared across
    all sessions.  Creating/destroying VDDs per session causes screen
    flashes because Windows re-enumerates the display topology each time.
    The shared VDD is created lazily on first session and kept alive
    until the backend shuts down.
    """

    def __init__(self, config: DesktopConfig):
        self._config = config
        self._sessions: Dict[str, DesktopSession] = {}
        self._lock = asyncio.Lock()
        self._last_screenshot: Dict[str, float] = {}  # session_id -> timestamp
        self._started = False
        # Shared virtual display — created once, reused by all sessions
        self._shared_vd = None  # Optional[VirtualDisplay]
        self._shared_vd_bounds: Optional[DisplayBounds] = None

    async def start(self) -> None:
        """Initialize the backend."""
        self._started = True
        log.info("DesktopBackend started (virtual_display=%s)",
                 self._config.virtual_display.enabled)

    async def stop(self) -> None:
        """Tear down all sessions and the shared virtual display."""
        for sid in list(self._sessions):
            await self._destroy_session(sid)
        # Tear down the shared VDD
        if self._shared_vd is not None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._shared_vd.stop)
                log.info("Shared virtual display stopped")
            except Exception as e:
                log.warning("Error stopping shared virtual display: %s", e)
            self._shared_vd = None
            self._shared_vd_bounds = None
        self._started = False
        log.info("DesktopBackend stopped")

    async def is_available(self) -> bool:
        """Check if the backend can operate."""
        return self._started and self._config.enabled

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self, session_id: str) -> DesktopResult:
        """Create a session with an optional virtual display."""
        async with self._lock:
            if session_id in self._sessions:
                s = self._sessions[session_id]
                return DesktopResult(
                    success=True,
                    data=(f"Session '{session_id}' already active on "
                          f"{s.device_name} ({s.display_bounds.width}x"
                          f"{s.display_bounds.height})"),
                    action="start_session",
                )

            if len(self._sessions) >= self._config.max_sessions:
                return DesktopResult(
                    success=False,
                    error=(f"Max sessions ({self._config.max_sessions}) reached. "
                           "Stop an existing session first."),
                    action="start_session",
                )

            session = await self._create_session(session_id)
            if session is None:
                return DesktopResult(
                    success=False,
                    error="Failed to create virtual display session",
                    action="start_session",
                )

            return DesktopResult(
                success=True,
                data=(f"Session '{session_id}' started on {session.device_name}\n"
                      f"Resolution: {session.display_bounds.width}x"
                      f"{session.display_bounds.height}\n"
                      f"Coordinates are relative to virtual display (0,0 = top-left)"),
                action="start_session",
            )

    async def stop_session(self, session_id: str) -> DesktopResult:
        """Tear down a session and its virtual display."""
        async with self._lock:
            if session_id not in self._sessions:
                return DesktopResult(
                    success=False,
                    error=f"No active session '{session_id}'",
                    action="stop_session",
                )
            await self._destroy_session(session_id)
            return DesktopResult(
                success=True,
                data=f"Session '{session_id}' stopped, virtual display removed",
                action="stop_session",
            )

    async def _get_or_create_session(self, session_id: str) -> DesktopSession:
        """Get existing session or auto-create on first action."""
        if session_id not in self._sessions:
            async with self._lock:
                if session_id not in self._sessions:
                    session = await self._create_session(session_id)
                    if session is None:
                        raise RuntimeError("Failed to create session")
        session = self._sessions[session_id]
        session.touch()
        return session

    async def _create_session(self, session_id: str) -> Optional[DesktopSession]:
        """Create a new session with virtual display."""
        vd_cfg = self._config.virtual_display
        session = DesktopSession(session_id=session_id)

        if vd_cfg.enabled:
            try:
                from cohort.desktop.virtual_display import VirtualDisplay
                vd = VirtualDisplay(vd_cfg.width, vd_cfg.height, vd_cfg.refresh_rate)

                # Start in executor to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, vd.start)

                session.virtual_display = vd
                session.device_name = vd.device_name

                # Get display bounds from Windows
                bounds = await loop.run_in_executor(
                    None, self._get_display_bounds, vd.device_name,
                )
                if bounds:
                    session.display_bounds = bounds
                else:
                    # Fallback: use configured dimensions at origin
                    session.display_bounds = DisplayBounds(
                        x=0, y=0, width=vd_cfg.width, height=vd_cfg.height,
                    )
                    log.warning("Could not determine display bounds for %s, "
                                "using fallback", vd.device_name)

                log.info("Created virtual display session '%s' on %s "
                         "at (%d,%d) %dx%d",
                         session_id, session.device_name,
                         session.display_bounds.x, session.display_bounds.y,
                         session.display_bounds.width, session.display_bounds.height)

            except Exception as e:
                log.error("Failed to create virtual display: %s", e)
                return None
        else:
            # Real desktop mode -- use primary monitor bounds
            w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            session.display_bounds = DisplayBounds(x=0, y=0, width=w, height=h)
            session.device_name = "real_desktop"
            log.info("Created real-desktop session '%s' (%dx%d)",
                     session_id, w, h)

        self._sessions[session_id] = session
        return session

    async def _destroy_session(self, session_id: str) -> None:
        """Tear down a session."""
        session = self._sessions.pop(session_id, None)
        if session and session.virtual_display:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, session.virtual_display.stop)
                log.info("Virtual display stopped for session '%s'", session_id)
            except Exception as e:
                log.warning("Error stopping virtual display: %s", e)

    # ------------------------------------------------------------------
    # Observe actions
    # ------------------------------------------------------------------

    async def screenshot(self, session_id: str) -> DesktopResult:
        """Capture the entire virtual display."""
        session = await self._get_or_create_session(session_id)
        bounds = session.display_bounds

        if not self._rate_limit_ok(session_id):
            return DesktopResult(
                success=False,
                error="Screenshot rate limit exceeded (1/sec)",
                action="screenshot",
            )

        try:
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(
                None, self._capture_screenshot, session_id,
                (bounds.x, bounds.y, bounds.width, bounds.height),
            )
            self._audit(session_id, "screenshot", {}, screenshot_path=str(path))
            return DesktopResult(
                success=True,
                data=f"Screenshot saved: {path}",
                action="screenshot",
            )
        except Exception as e:
            self._audit(session_id, "screenshot", {}, result_success=False)
            return DesktopResult(success=False, error=str(e), action="screenshot")

    async def screenshot_window(
        self, session_id: str, title_pattern: str,
    ) -> DesktopResult:
        """Capture a specific window by title pattern."""
        await self._get_or_create_session(session_id)

        if not self._rate_limit_ok(session_id):
            return DesktopResult(
                success=False, error="Screenshot rate limit exceeded",
                action="screenshot_window",
            )

        loop = asyncio.get_event_loop()
        win = await loop.run_in_executor(
            None, self._find_window, title_pattern,
        )
        if not win:
            return DesktopResult(
                success=False,
                error=f"No window matching '{title_pattern}'",
                action="screenshot_window",
            )

        try:
            path = await loop.run_in_executor(
                None, self._capture_window_direct, session_id, win,
            )
            self._audit(session_id, "screenshot_window",
                        {"title_pattern": title_pattern},
                        target_window=win.title, screenshot_path=str(path))
            return DesktopResult(
                success=True,
                data=f"Screenshot saved: {path}",
                action="screenshot_window",
                window_title=win.title,
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="screenshot_window",
            )

    async def screenshot_region(
        self, session_id: str, x: int, y: int, w: int, h: int,
    ) -> DesktopResult:
        """Capture a region (coordinates relative to virtual display)."""
        session = await self._get_or_create_session(session_id)
        bounds = session.display_bounds

        # Bounds check
        ok, reason = check_coordinates_in_bounds(x, y, bounds)
        if not ok:
            return DesktopResult(success=False, error=reason,
                                 action="screenshot_region")
        ok, reason = check_coordinates_in_bounds(x + w - 1, y + h - 1, bounds)
        if not ok:
            return DesktopResult(success=False, error=reason,
                                 action="screenshot_region")

        abs_x, abs_y = bounds.to_absolute(x, y)
        try:
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(
                None, self._capture_screenshot, session_id,
                (abs_x, abs_y, w, h),
            )
            self._audit(session_id, "screenshot_region",
                        {"x": x, "y": y, "w": w, "h": h},
                        screenshot_path=str(path))
            return DesktopResult(
                success=True, data=f"Screenshot saved: {path}",
                action="screenshot_region",
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="screenshot_region",
            )

    async def list_windows(self, session_id: str) -> DesktopResult:
        """List visible windows (optionally filtered to virtual display)."""
        session = await self._get_or_create_session(session_id)

        loop = asyncio.get_event_loop()
        all_windows = await loop.run_in_executor(None, self._enumerate_windows)

        # Filter to windows overlapping the virtual display bounds
        bounds = session.display_bounds
        if session.virtual_display:
            filtered = [w for w in all_windows if self._window_overlaps(w, bounds)]
        else:
            filtered = all_windows

        lines = [f"Found {len(filtered)} windows:"]
        for w in filtered[:50]:  # Cap output
            lines.append(f"  {w.to_str()}")

        self._audit(session_id, "list_windows", {})
        return DesktopResult(
            success=True, data="\n".join(lines), action="list_windows",
        )

    async def get_active_window(self, session_id: str) -> DesktopResult:
        """Get the currently focused window."""
        await self._get_or_create_session(session_id)

        loop = asyncio.get_event_loop()
        win = await loop.run_in_executor(None, self._get_foreground_window)

        if win:
            self._audit(session_id, "get_active_window", {},
                        target_window=win.title)
            return DesktopResult(
                success=True, data=win.to_str(),
                action="get_active_window", window_title=win.title,
            )
        return DesktopResult(
            success=True, data="No foreground window",
            action="get_active_window",
        )

    async def get_mouse_position(self, session_id: str) -> DesktopResult:
        """Get current mouse cursor position."""
        session = await self._get_or_create_session(session_id)
        pos = pyautogui.position()
        bounds = session.display_bounds
        # Convert to display-relative
        rel_x = pos.x - bounds.x
        rel_y = pos.y - bounds.y
        self._audit(session_id, "get_mouse_position", {})
        return DesktopResult(
            success=True,
            data=f"Position: ({rel_x}, {rel_y}) [absolute: ({pos.x}, {pos.y})]",
            action="get_mouse_position",
        )

    async def get_clipboard(self, session_id: str) -> DesktopResult:
        """Read clipboard text content."""
        await self._get_or_create_session(session_id)
        try:
            text = pyperclip.paste()
            self._audit(session_id, "get_clipboard", {})
            # Truncate for safety
            if len(text) > 10000:
                text = text[:10000] + "\n[...truncated]"
            return DesktopResult(
                success=True, data=f"Clipboard ({len(text)} chars):\n{text}",
                action="get_clipboard",
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="get_clipboard",
            )

    # ------------------------------------------------------------------
    # Interact actions
    # ------------------------------------------------------------------

    async def click(
        self, session_id: str, x: int, y: int,
        button: str = "left", clicks: int = 1,
    ) -> DesktopResult:
        """Mouse click at display-relative coordinates."""
        session = await self._get_or_create_session(session_id)
        bounds = session.display_bounds

        ok, reason = check_coordinates_in_bounds(x, y, bounds)
        if not ok:
            return DesktopResult(success=False, error=reason, action="click")

        abs_x, abs_y = bounds.to_absolute(x, y)

        # Window allowlist check (only on real desktop with non-empty allowlist)
        if not session.virtual_display and self._config.window_allowlist:
            loop = asyncio.get_event_loop()
            windows = await loop.run_in_executor(None, self._enumerate_windows)
            from cohort.desktop.safety import (
                check_coordinates_in_allowed_window,
            )
            ok, reason, _ = check_coordinates_in_allowed_window(
                abs_x, abs_y, windows, self._config.window_allowlist,
            )
            if not ok:
                return DesktopResult(success=False, error=reason, action="click")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: pyautogui.click(abs_x, abs_y, clicks=clicks, button=button),
            )
            self._audit(session_id, "click",
                        {"x": x, "y": y, "button": button, "clicks": clicks})
            return DesktopResult(
                success=True,
                data=f"Clicked ({x}, {y}) button={button} clicks={clicks}",
                action="click",
            )
        except pyautogui.FailSafeException:
            return DesktopResult(
                success=False,
                error="pyautogui failsafe triggered (mouse at 0,0)",
                action="click",
            )
        except Exception as e:
            return DesktopResult(success=False, error=str(e), action="click")

    async def type_text(
        self, session_id: str, text: str, interval: float = 0.02,
    ) -> DesktopResult:
        """Type text via keyboard."""
        await self._get_or_create_session(session_id)

        try:
            loop = asyncio.get_event_loop()
            # For ASCII text, use pyautogui.write (sends keypresses)
            # For Unicode, use clipboard paste
            if all(ord(c) < 128 for c in text):
                await loop.run_in_executor(
                    None, lambda: pyautogui.write(text, interval=interval),
                )
            else:
                # Unicode: copy to clipboard and paste
                pyperclip.copy(text)
                await loop.run_in_executor(
                    None, lambda: pyautogui.hotkey("ctrl", "v"),
                )

            self._audit(session_id, "type_text",
                        {"text": text[:100], "length": len(text)})
            return DesktopResult(
                success=True,
                data=f"Typed {len(text)} characters",
                action="type_text",
            )
        except pyautogui.FailSafeException:
            return DesktopResult(
                success=False,
                error="pyautogui failsafe triggered",
                action="type_text",
            )
        except Exception as e:
            return DesktopResult(success=False, error=str(e), action="type_text")

    async def press_key(self, session_id: str, key_combo: str) -> DesktopResult:
        """Press a key or key combination (e.g., 'ctrl+c', 'enter')."""
        await self._get_or_create_session(session_id)

        ok, reason = check_key_combo_allowed(
            key_combo, self._config.blocked_key_combos,
        )
        if not ok:
            self._audit(session_id, "press_key",
                        {"key_combo": key_combo}, result_success=False)
            return DesktopResult(success=False, error=reason, action="press_key")

        try:
            keys = [k.strip() for k in key_combo.split("+")]
            loop = asyncio.get_event_loop()
            if len(keys) == 1:
                await loop.run_in_executor(
                    None, lambda: pyautogui.press(keys[0]),
                )
            else:
                await loop.run_in_executor(
                    None, lambda: pyautogui.hotkey(*keys),
                )

            self._audit(session_id, "press_key", {"key_combo": key_combo})
            return DesktopResult(
                success=True, data=f"Pressed: {key_combo}",
                action="press_key",
            )
        except pyautogui.FailSafeException:
            return DesktopResult(
                success=False, error="pyautogui failsafe triggered",
                action="press_key",
            )
        except Exception as e:
            return DesktopResult(success=False, error=str(e), action="press_key")

    async def mouse_move(
        self, session_id: str, x: int, y: int,
    ) -> DesktopResult:
        """Move mouse cursor to display-relative coordinates."""
        session = await self._get_or_create_session(session_id)
        bounds = session.display_bounds

        ok, reason = check_coordinates_in_bounds(x, y, bounds)
        if not ok:
            return DesktopResult(success=False, error=reason, action="mouse_move")

        abs_x, abs_y = bounds.to_absolute(x, y)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: pyautogui.moveTo(abs_x, abs_y),
            )
            self._audit(session_id, "mouse_move", {"x": x, "y": y})
            return DesktopResult(
                success=True, data=f"Moved to ({x}, {y})",
                action="mouse_move",
            )
        except Exception as e:
            return DesktopResult(success=False, error=str(e), action="mouse_move")

    async def mouse_drag(
        self, session_id: str,
        x1: int, y1: int, x2: int, y2: int,
    ) -> DesktopResult:
        """Drag from (x1,y1) to (x2,y2) in display-relative coords."""
        session = await self._get_or_create_session(session_id)
        bounds = session.display_bounds

        for px, py, label in [(x1, y1, "start"), (x2, y2, "end")]:
            ok, reason = check_coordinates_in_bounds(px, py, bounds)
            if not ok:
                return DesktopResult(
                    success=False, error=f"Drag {label}: {reason}",
                    action="mouse_drag",
                )

        abs_x1, abs_y1 = bounds.to_absolute(x1, y1)
        abs_x2, abs_y2 = bounds.to_absolute(x2, y2)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: pyautogui.moveTo(abs_x1, abs_y1),
            )
            dx, dy = abs_x2 - abs_x1, abs_y2 - abs_y1
            await loop.run_in_executor(
                None, lambda: pyautogui.drag(dx, dy, duration=0.5),
            )
            self._audit(session_id, "mouse_drag",
                        {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
            return DesktopResult(
                success=True,
                data=f"Dragged from ({x1},{y1}) to ({x2},{y2})",
                action="mouse_drag",
            )
        except Exception as e:
            return DesktopResult(success=False, error=str(e), action="mouse_drag")

    async def scroll(
        self, session_id: str, x: int, y: int, clicks: int,
    ) -> DesktopResult:
        """Scroll mouse wheel at display-relative position."""
        session = await self._get_or_create_session(session_id)
        bounds = session.display_bounds

        ok, reason = check_coordinates_in_bounds(x, y, bounds)
        if not ok:
            return DesktopResult(success=False, error=reason, action="scroll")

        abs_x, abs_y = bounds.to_absolute(x, y)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: pyautogui.scroll(clicks, x=abs_x, y=abs_y),
            )
            direction = "up" if clicks > 0 else "down"
            self._audit(session_id, "scroll",
                        {"x": x, "y": y, "clicks": clicks})
            return DesktopResult(
                success=True,
                data=f"Scrolled {direction} {abs(clicks)} clicks at ({x},{y})",
                action="scroll",
            )
        except Exception as e:
            return DesktopResult(success=False, error=str(e), action="scroll")

    async def focus_window(
        self, session_id: str, title_pattern: str,
    ) -> DesktopResult:
        """Bring a window to the foreground."""
        await self._get_or_create_session(session_id)

        loop = asyncio.get_event_loop()
        win = await loop.run_in_executor(
            None, self._find_window, title_pattern,
        )
        if not win:
            return DesktopResult(
                success=False,
                error=f"No window matching '{title_pattern}'",
                action="focus_window",
            )

        # Allowlist check on real desktop
        session = self._sessions.get(session_id)
        if session and not session.virtual_display and self._config.window_allowlist:
            ok, reason = check_window_allowed(
                win.title, win.class_name, self._config.window_allowlist,
            )
            if not ok:
                return DesktopResult(
                    success=False, error=reason, action="focus_window",
                )

        try:
            await loop.run_in_executor(
                None, lambda: user32.SetForegroundWindow(win.hwnd),
            )
            self._audit(session_id, "focus_window",
                        {"title_pattern": title_pattern},
                        target_window=win.title)
            return DesktopResult(
                success=True,
                data=f"Focused: {win.title}",
                action="focus_window",
                window_title=win.title,
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="focus_window",
            )

    async def set_clipboard(self, session_id: str, text: str) -> DesktopResult:
        """Write text to clipboard."""
        await self._get_or_create_session(session_id)
        try:
            pyperclip.copy(text)
            self._audit(session_id, "set_clipboard",
                        {"length": len(text)})
            return DesktopResult(
                success=True,
                data=f"Clipboard set ({len(text)} chars)",
                action="set_clipboard",
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="set_clipboard",
            )

    # ------------------------------------------------------------------
    # Advanced actions
    # ------------------------------------------------------------------

    async def launch_app(
        self, session_id: str, app_path: str, args: List[str] = None,
    ) -> DesktopResult:
        """Launch an application (restricted by allowlist)."""
        await self._get_or_create_session(session_id)

        ok, reason = check_app_allowed(app_path, self._config.allowed_apps)
        if not ok:
            self._audit(session_id, "launch_app",
                        {"app_path": app_path}, result_success=False)
            return DesktopResult(success=False, error=reason, action="launch_app")

        try:
            cmd = [app_path] + (args or [])
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: subprocess.Popen(cmd),
            )

            # Auto-move to virtual display after brief startup delay
            session = self._sessions.get(session_id)
            if session and session.virtual_display is not None:
                await asyncio.sleep(2)
                app_name = os.path.basename(app_path).lower().replace(".exe", "")
                await loop.run_in_executor(
                    None, self._move_new_window_to_vdd, app_name, session,
                )

            self._audit(session_id, "launch_app",
                        {"app_path": app_path, "args": args or []})
            return DesktopResult(
                success=True,
                data=f"Launched: {app_path}",
                action="launch_app",
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="launch_app",
            )

    async def close_window(
        self, session_id: str, title_pattern: str,
    ) -> DesktopResult:
        """Close a window by sending WM_CLOSE."""
        await self._get_or_create_session(session_id)

        loop = asyncio.get_event_loop()
        win = await loop.run_in_executor(
            None, self._find_window, title_pattern,
        )
        if not win:
            return DesktopResult(
                success=False,
                error=f"No window matching '{title_pattern}'",
                action="close_window",
            )

        try:
            await loop.run_in_executor(
                None, lambda: user32.PostMessageW(win.hwnd, _WM_CLOSE, 0, 0),
            )
            self._audit(session_id, "close_window",
                        {"title_pattern": title_pattern},
                        target_window=win.title)
            return DesktopResult(
                success=True,
                data=f"Sent close to: {win.title}",
                action="close_window",
                window_title=win.title,
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="close_window",
            )

    async def resize_window(
        self, session_id: str, title_pattern: str,
        x: int, y: int, w: int, h: int,
    ) -> DesktopResult:
        """Move and resize a window."""
        session = await self._get_or_create_session(session_id)

        loop = asyncio.get_event_loop()
        win = await loop.run_in_executor(
            None, self._find_window, title_pattern,
        )
        if not win:
            return DesktopResult(
                success=False,
                error=f"No window matching '{title_pattern}'",
                action="resize_window",
            )

        # Translate to absolute if virtual display
        bounds = session.display_bounds
        abs_x, abs_y = bounds.to_absolute(x, y)

        try:
            await loop.run_in_executor(
                None, lambda: user32.MoveWindow(win.hwnd, abs_x, abs_y, w, h, True),
            )
            self._audit(session_id, "resize_window",
                        {"title_pattern": title_pattern,
                         "x": x, "y": y, "w": w, "h": h},
                        target_window=win.title)
            return DesktopResult(
                success=True,
                data=f"Resized {win.title} to ({x},{y}) {w}x{h}",
                action="resize_window",
                window_title=win.title,
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="resize_window",
            )

    async def minimize_window(
        self, session_id: str, title_pattern: str,
    ) -> DesktopResult:
        """Minimize a window."""
        return await self._show_window(session_id, title_pattern,
                                       _SW_MINIMIZE, "minimize_window")

    async def maximize_window(
        self, session_id: str, title_pattern: str,
    ) -> DesktopResult:
        """Maximize a window."""
        return await self._show_window(session_id, title_pattern,
                                       _SW_MAXIMIZE, "maximize_window")

    async def run_command(
        self, session_id: str, command: str,
    ) -> DesktopResult:
        """Execute a shell command (heavily restricted)."""
        await self._get_or_create_session(session_id)

        if not self._config.run_command_enabled:
            return DesktopResult(
                success=False,
                error="run_command is disabled in configuration",
                action="run_command",
            )

        if self._config.allowed_commands:
            cmd_base = shlex.split(command)[0] if command else ""
            allowed = any(
                fnmatch(cmd_base.lower(), pat.lower())
                for pat in self._config.allowed_commands
            )
            if not allowed:
                return DesktopResult(
                    success=False,
                    error=f"Command '{cmd_base}' not in allowed_commands list",
                    action="run_command",
                )

        try:
            parts = shlex.split(command)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    parts, capture_output=True, text=True,
                    timeout=30, shell=False,
                ),
            )
            output = result.stdout[:5000]
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr[:2000]}"

            self._audit(session_id, "run_command",
                        {"command": command, "returncode": result.returncode})
            return DesktopResult(
                success=result.returncode == 0,
                data=output,
                error=f"Exit code {result.returncode}" if result.returncode else "",
                action="run_command",
            )
        except subprocess.TimeoutExpired:
            return DesktopResult(
                success=False,
                error="Command timed out after 30 seconds",
                action="run_command",
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action="run_command",
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _show_window(
        self, session_id: str, title_pattern: str,
        show_cmd: int, action_name: str,
    ) -> DesktopResult:
        """Minimize/maximize a window via ShowWindow."""
        await self._get_or_create_session(session_id)

        loop = asyncio.get_event_loop()
        win = await loop.run_in_executor(
            None, self._find_window, title_pattern,
        )
        if not win:
            return DesktopResult(
                success=False,
                error=f"No window matching '{title_pattern}'",
                action=action_name,
            )

        try:
            await loop.run_in_executor(
                None, lambda: user32.ShowWindow(win.hwnd, show_cmd),
            )
            self._audit(session_id, action_name,
                        {"title_pattern": title_pattern},
                        target_window=win.title)
            return DesktopResult(
                success=True,
                data=f"{action_name}: {win.title}",
                action=action_name,
                window_title=win.title,
            )
        except Exception as e:
            return DesktopResult(
                success=False, error=str(e), action=action_name,
            )

    def _capture_screenshot(
        self, session_id: str, region: Tuple[int, int, int, int],
    ) -> Path:
        """Capture a screen region, downscale, save as JPEG.

        Args:
            region: (x, y, width, height) in absolute screen coords.

        Returns:
            Path to saved JPEG file.

        Uses PrintWindow composite for VDD sessions (pyautogui returns
        all-black on Parsec virtual displays without a connected viewer).
        """
        # Check if this session uses a virtual display
        session = self._sessions.get(session_id)
        if session and session.virtual_display is not None:
            return self._capture_vdd_composite(session_id, session.display_bounds)

        x, y, w, h = region
        img = self._capture_screen_bitblt(x, y, w, h)
        img = self._downscale(img)

        # Save into date-based subdirectory
        import datetime as _dt
        day_dir = self._config.screenshot_dir / _dt.date.today().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)

        ts = int(time.time() * 1000)
        filename = f"{session_id}_{ts}.jpg"
        img = self._stamp_screenshot(img, session_id, filename)
        path = day_dir / filename
        img.save(path, "JPEG", quality=90)

        self._last_screenshot[session_id] = time.time()

        # Auto-prune old screenshots
        self._prune_screenshots()

        return path

    def _gdi_bits_to_image(
        self, hdc_mem: int, hbm: int, width: int, height: int,
    ) -> Image.Image:
        """Read a GDI bitmap into a PIL Image."""
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height  # negative = top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB

        buf = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(hdc_mem, hbm, 0, height, buf, ctypes.byref(bmi), 0)

        img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
        return img.convert("RGB")

    def _capture_window(self, hwnd: int, width: int, height: int) -> Image.Image:
        """Capture a window without screen disruption.

        Uses BitBlt from the window DC (no repaint). If the result is
        all-black (DWM-redirected window), falls back to PrintWindow
        with flag 0 (light WM_PRINT, minimal visual disruption).
        """
        hdc_window = user32.GetDC(hwnd)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
        hbm = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
        gdi32.SelectObject(hdc_mem, hbm)

        try:
            # Try BitBlt first — no repaint, no disruption
            gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_window, 0, 0, _SRCCOPY)
            img = self._gdi_bits_to_image(hdc_mem, hbm, width, height)

            # Check if capture is all-black (BitBlt fails for some DWM windows)
            extrema = img.getextrema()
            is_black = all(lo == 0 and hi == 0 for lo, hi in extrema)

            if is_black:
                # Fallback: PrintWindow with PW_RENDERFULLCONTENT
                # Needed for Electron/GPU-accelerated apps (VS Code, Chrome)
                # that don't paint to the standard window DC.
                # Causes a minor per-window repaint but no global screen flash.
                user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT)
                img = self._gdi_bits_to_image(hdc_mem, hbm, width, height)

            return img
        finally:
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_window)

    def _capture_screen_bitblt(
        self, x: int, y: int, width: int, height: int,
    ) -> Image.Image:
        """Capture a screen region via BitBlt — no flicker, no pyautogui."""
        hdc_screen = user32.GetDC(0)  # 0 = entire virtual screen
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        gdi32.SelectObject(hdc_mem, hbm)

        try:
            gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, x, y, _SRCCOPY)
            return self._gdi_bits_to_image(hdc_mem, hbm, width, height)
        finally:
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)

    def _capture_window_direct(
        self, session_id: str, win: WindowInfo,
    ) -> Path:
        """Capture a single window via PrintWindow and save as JPEG."""
        img = self._capture_window(win.hwnd, win.width, win.height)
        img = self._downscale(img)

        ts = int(time.time() * 1000)
        filename = f"{session_id}_{ts}.jpg"
        path = self._config.screenshot_dir / filename
        img.save(path, "JPEG", quality=90)

        self._last_screenshot[session_id] = time.time()
        self._prune_screenshots()
        return path

    def _capture_vdd_composite(
        self, session_id: str, bounds: "DisplayBounds",
    ) -> Path:
        """Capture a virtual display by compositing all windows on it.

        Parsec VDD framebuffers are black without a connected viewer.
        Instead, we enumerate windows within the display bounds and
        capture each via PrintWindow, then composite onto a blank canvas.
        """
        canvas = self._vdd_background(bounds.width, bounds.height)

        # Get all windows, sorted back-to-front (reverse Z-order)
        windows = self._enumerate_windows()
        # Filter to windows overlapping the virtual display.
        # Skip desktop shell windows (Progman, WorkerW) — they span all
        # monitors and would paint a black rectangle over our VDD background.
        _SHELL_CLASSES = {"Progman", "WorkerW"}
        vd_windows = []
        for win in windows:
            if win.class_name in _SHELL_CLASSES:
                continue
            # Check if window overlaps the virtual display bounds
            if (win.x < bounds.x + bounds.width and
                    win.x + win.width > bounds.x and
                    win.y < bounds.y + bounds.height and
                    win.y + win.height > bounds.y):
                vd_windows.append(win)

        # Capture and composite each window (last = frontmost)
        for win in reversed(vd_windows):
            try:
                img = self._capture_window(win.hwnd, win.width, win.height)
                # Position relative to virtual display
                paste_x = win.x - bounds.x
                paste_y = win.y - bounds.y
                canvas.paste(img, (paste_x, paste_y))
            except Exception as e:
                log.warning("PrintWindow failed for '%s': %s", win.title, e)

        canvas = self._downscale(canvas)

        import datetime as _dt
        day_dir = self._config.screenshot_dir / _dt.date.today().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)

        ts = int(time.time() * 1000)
        filename = f"{session_id}_{ts}.jpg"
        canvas = self._stamp_screenshot(canvas, session_id, filename)
        path = day_dir / filename
        canvas.save(path, "JPEG", quality=90)

        self._last_screenshot[session_id] = time.time()
        self._prune_screenshots()
        return path

    _vdd_serial: int = 0

    def _next_vdd_serial(self) -> str:
        DesktopBackend._vdd_serial += 1
        return f"VDD-{DesktopBackend._vdd_serial:04d}"

    def _vdd_background(self, width: int, height: int) -> Image.Image:
        """Branded green-screen background for the virtual display.

        Shows the COHORT logo in copper/orange above 'VIRTUAL DISPLAY',
        the resolution, and a unique serial number per instance.
        """
        from PIL import ImageDraw, ImageFont

        bg_color = (0, 177, 64)  # chroma-key green
        canvas = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(canvas)

        # 100px coordinate grid — light lines with readable labels
        grid_color = (0, 200, 80)       # lighter green for visibility
        grid_label_shadow = (0, 120, 35)
        try:
            font_grid = ImageFont.truetype("consola.ttf", 13)
        except (OSError, IOError):
            font_grid = ImageFont.load_default()
        for x in range(100, width, 100):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
        for y in range(100, height, 100):
            draw.line([(0, y), (width, y)], fill=grid_color, width=1)
        # Labels at every grid intersection — no exclusion, branding draws on top
        for x in range(100, width, 100):
            for y in range(100, height, 100):
                label = f"{x},{y}"
                lb = draw.textbbox((0, 0), label, font=font_grid)
                lw, lh = lb[2] - lb[0], lb[3] - lb[1]
                # Bottom-right of the cell (just inside the gridline)
                lx = x - lw - 3
                ly = y - lh - 2
                draw.text((lx + 1, ly + 1), label, fill=grid_label_shadow, font=font_grid)
                draw.text((lx, ly), label, fill=(220, 255, 220), font=font_grid)

        # Fonts — Press Start 2P for COHORT brand, Consolas for the rest
        _font_dir = Path(__file__).parent
        _ps2p = str(_font_dir / "PressStart2P-Regular.ttf")
        try:
            font_brand = ImageFont.truetype(_ps2p, 47)
        except (OSError, IOError):
            try:
                font_brand = ImageFont.truetype("consolab.ttf", 56)
            except (OSError, IOError):
                font_brand = ImageFont.load_default()
        try:
            font_sub = ImageFont.truetype("consolab.ttf", 36)
            font_serial = ImageFont.truetype("consola.ttf", 16)
        except (OSError, IOError):
            font_sub = ImageFont.load_default()
            font_serial = font_sub

        cohort_color = (227, 155, 81)   # copper/orange from dashboard
        shadow_color = (0, 100, 30)
        white = (255, 255, 255)
        light_green = (200, 240, 200)

        # -- COHORT (Press Start 2P pixel font, copper) — centered in the 300 row --
        brand = "COHORT"
        bb = draw.textbbox((0, 0), brand, font=font_brand)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        bx = (width - bw) // 2
        cy = 300 + (100 - bh) // 2  # vertically centered in the 300-400 row

        draw.text((bx + 2, cy + 2), brand, fill=shadow_color, font=font_brand)
        draw.text((bx, cy), brand, fill=cohort_color, font=font_brand)

        # -- VIRTUAL DISPLAY — centered in the 400 row --
        vd_label = "VIRTUAL  DISPLAY"
        vb = draw.textbbox((0, 0), vd_label, font=font_sub)
        vw, vh = vb[2] - vb[0], vb[3] - vb[1]
        vx = (width - vw) // 2
        vy = 400 + (100 - vh) // 2  # vertically centered in the 400-500 row

        draw.text((vx + 1, vy + 1), vd_label, fill=shadow_color, font=font_sub)
        draw.text((vx, vy), vd_label, fill=white, font=font_sub)

        # -- Bottom info (larger) --
        try:
            font_bottom = ImageFont.truetype("consolab.ttf", 32)
        except (OSError, IOError):
            font_bottom = font_serial

        # Resolution (bottom-left)
        res = f"{width}\u00d7{height}"
        draw.text((14, height - 42), res, fill=light_green, font=font_bottom)

        # Serial number (bottom-right)
        serial = self._next_vdd_serial()
        sb = draw.textbbox((0, 0), serial, font=font_bottom)
        sw = sb[2] - sb[0]
        draw.text((width - sw - 14, height - 42), serial, fill=light_green, font=font_bottom)

        return canvas

    _screenshot_counter: int = 0

    def _stamp_screenshot(self, img: Image.Image, session_id: str, filename: str) -> Image.Image:
        """Wrap a screenshot with a red border and metadata bar.

        The original content pixels are untouched — the border and bar
        expand the canvas outward so nothing is covered.
        """
        import datetime

        from PIL import ImageDraw, ImageFont

        DesktopBackend._screenshot_counter += 1
        ref = f"CAP-{DesktopBackend._screenshot_counter:05d}"
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        border = 2
        w, h = img.size

        # Metadata font
        try:
            font_meta = ImageFont.truetype("consolab.ttf", 12)
        except (OSError, IOError):
            font_meta = ImageFont.load_default()

        meta = f"{filename}  |  {ts}  |  {session_id}  |  {ref}"
        mb = ImageDraw.Draw(img).textbbox((0, 0), meta, font=font_meta)
        bar_h = (mb[3] - mb[1]) + 8  # text height + padding

        # New canvas: original + border on all sides + metadata bar below
        framed_w = w + border * 2
        framed_h = h + border * 2 + bar_h
        framed = Image.new("RGB", (framed_w, framed_h), (255, 0, 0))  # red fill = border

        # Paste original content inside the border
        framed.paste(img, (border, border))

        # Black metadata bar at the bottom
        draw = ImageDraw.Draw(framed)
        bar_y = h + border * 2
        draw.rectangle([(0, bar_y), (framed_w, framed_h)], fill=(38, 38, 38))

        # Metadata text centered in the bar
        mw = mb[2] - mb[0]
        mx = (framed_w - mw) // 2
        my = bar_y + 4
        draw.text((mx, my), meta, fill=(220, 220, 220), font=font_meta)

        return framed

    def _downscale(self, img: Image.Image) -> Image.Image:
        """Downscale to max_dimension (Lanczos). 1024x768 already fits."""
        w, h = img.size
        max_dim = self._config.max_dimension
        if max(w, h) <= max_dim:
            return img
        scale = max_dim / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        return img.resize((new_w, new_h), Image.LANCZOS)

    def _rate_limit_ok(self, session_id: str) -> bool:
        """Check screenshot rate limit."""
        last = self._last_screenshot.get(session_id, 0)
        min_interval = self._config.max_screenshot_rate_ms / 1000.0
        return (time.time() - last) >= min_interval

    def _prune_screenshots(self) -> None:
        """Remove oldest screenshots if over retention limit."""
        max_count = self._config.max_screenshots_retained
        screenshots = sorted(
            self._config.screenshot_dir.rglob("*.jpg"),
            key=lambda p: p.stat().st_mtime,
        )
        if len(screenshots) > max_count:
            for old in screenshots[: len(screenshots) - max_count]:
                try:
                    old.unlink()
                    # Remove empty date directories
                    if old.parent != self._config.screenshot_dir:
                        try:
                            old.parent.rmdir()  # only succeeds if empty
                        except OSError:
                            pass
                except OSError:
                    pass

    def _enumerate_windows(self) -> List[WindowInfo]:
        """Enumerate all visible windows via Win32 EnumWindows."""
        windows = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True

            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            # Get class name
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)

            # Get window rect
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))

            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w > 0 and h > 0:
                windows.append(WindowInfo(
                    title=title,
                    class_name=cls_buf.value,
                    hwnd=hwnd,
                    x=rect.left,
                    y=rect.top,
                    width=w,
                    height=h,
                    is_visible=True,
                ))
            return True

        user32.EnumWindows(callback, 0)
        return windows

    def _move_new_window_to_vdd(
        self, app_hint: str, session: DesktopSession,
    ) -> None:
        """Move a recently launched app's window to the virtual display."""
        bounds = session.display_bounds
        for win in self._enumerate_windows():
            # Match by app name in title or class name
            if (app_hint in win.title.lower() or app_hint in win.class_name.lower()):
                # Only move if it's NOT already on the virtual display
                if (win.x < bounds.x or win.x >= bounds.x + bounds.width):
                    user32.MoveWindow(
                        win.hwnd, bounds.x, bounds.y,
                        min(win.width, bounds.width),
                        min(win.height, bounds.height),
                        True,
                    )
                    log.info("Moved '%s' to virtual display at (%d,%d)",
                             win.title, bounds.x, bounds.y)
                    return
        log.debug("No window found to move for '%s'", app_hint)

    def _find_window(self, title_pattern: str) -> Optional[WindowInfo]:
        """Find first window matching title pattern (fnmatch)."""
        for win in self._enumerate_windows():
            if fnmatch(win.title, title_pattern):
                return win
        return None

    def _get_foreground_window(self) -> Optional[WindowInfo]:
        """Get the currently focused window."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)

        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)

        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        return WindowInfo(
            title=buf.value,
            class_name=cls_buf.value,
            hwnd=hwnd,
            x=rect.left,
            y=rect.top,
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
            is_visible=True,
        )

    def _get_display_bounds(self, device_name: str) -> Optional[DisplayBounds]:
        """Get the bounding rectangle of a display device from Windows."""
        from cohort.desktop.virtual_display import DEVMODEW, ENUM_CURRENT_SETTINGS

        devmode = DEVMODEW()
        devmode.dmSize = ctypes.sizeof(DEVMODEW)

        if user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS,
                                       ctypes.byref(devmode)):
            return DisplayBounds(
                x=devmode.dmPositionX,
                y=devmode.dmPositionY,
                width=devmode.dmPelsWidth,
                height=devmode.dmPelsHeight,
            )
        return None

    @staticmethod
    def _window_overlaps(win: WindowInfo, bounds: DisplayBounds) -> bool:
        """Check if a window overlaps with display bounds."""
        return not (
            win.x + win.width <= bounds.x
            or win.x >= bounds.x + bounds.width
            or win.y + win.height <= bounds.y
            or win.y >= bounds.y + bounds.height
        )

    def _audit(
        self, session_id: str, action: str, params: dict,
        target_window: str = "", screenshot_path: str = "",
        result_success: bool = True,
    ) -> None:
        """Log an audit entry."""
        entry = make_audit_entry(
            session_id=session_id,
            action=action,
            params=params,
            target_window=target_window,
            screenshot_path=screenshot_path,
            result_success=result_success,
        )
        log_audit_entry(entry, self._config.audit_log)
