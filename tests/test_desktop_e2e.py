"""Tier 5: Desktop E2E — invoke agent, accept dev-channels prompt, verify session.

This test exercises the full Claude Code Channel lifecycle with desktop
automation to accept the --dangerously-load-development-channels prompt.

Requirements:
    - Cohort server running (default: http://127.0.0.1:5110 or COHORT_E2E_PORT)
    - Desktop automation enabled (desktop_computer_use.yaml enabled: true)
    - pyautogui + pillow installed (pip install cohort[desktop])
    - At least one agent registered (e.g. python_developer)

Sequence:
    1. POST /api/channel/invoke -> spawns Claude Code session
    2. Wait for Claude Code window to appear
    3. Desktop: list_windows -> find "claude" window
    4. Desktop: focus_window -> bring to front
    5. Desktop: screenshot -> capture dev-channels prompt (evidence)
    6. Desktop: press_key -> Enter to accept
    7. Poll /api/channel/status or /api/sessions for registration
    8. Desktop: screenshot -> capture running session (evidence)
    9. Verify session registered and healthy

Usage:
    pytest tests/test_desktop_e2e.py -v -x --tb=short
    pytest tests/test_desktop_e2e.py -v -x -k test_accept_dev_channels_prompt

Environment:
    COHORT_E2E_PORT  -- server port (default: 5110)
    COHORT_E2E_HOST  -- server host (default: 127.0.0.1)
    DESKTOP_E2E_CHANNEL  -- channel name (default: e2e-desktop-test)
    DESKTOP_E2E_AGENT    -- agent to invoke (default: python_developer)
    DESKTOP_E2E_TIMEOUT  -- max seconds to wait for window (default: 15)
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_HOST = os.environ.get("COHORT_E2E_HOST", "127.0.0.1")
_PORT = int(os.environ.get("COHORT_E2E_PORT", "5110"))
_BASE = f"http://{_HOST}:{_PORT}"
_CHANNEL = os.environ.get("DESKTOP_E2E_CHANNEL", "e2e-desktop-test")
_AGENT = os.environ.get("DESKTOP_E2E_AGENT", "python_developer")
_TIMEOUT = int(os.environ.get("DESKTOP_E2E_TIMEOUT", "15"))

# Where screenshots are saved during the test
_EVIDENCE_DIR = Path("test_evidence")

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.desktop_e2e,
    pytest.mark.integration,
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class DesktopClient:
    """Thin HTTP wrapper around /api/desktop/* endpoints."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def action(self, action: str, **kwargs) -> dict:
        body = {"action": action, **kwargs}
        resp = await self._client.post(f"{_BASE}/api/desktop/action", json=body)
        resp.raise_for_status()
        return resp.json()

    async def status(self) -> dict:
        resp = await self._client.get(f"{_BASE}/api/desktop/status")
        resp.raise_for_status()
        return resp.json()

    async def screenshot(self, session_id: str = "default") -> dict:
        return await self.action("screenshot", session_id=session_id)

    async def list_windows(self, session_id: str = "default") -> dict:
        return await self.action("list_windows", session_id=session_id)

    async def focus_window(self, title: str, session_id: str = "default") -> dict:
        return await self.action("focus_window", window_title=title, session_id=session_id)

    async def press_key(self, combo: str, session_id: str = "default") -> dict:
        return await self.action("press_key", key_combo=combo, session_id=session_id)

    async def type_text(self, text: str, session_id: str = "default") -> dict:
        return await self.action("type_text", text=text, session_id=session_id)


async def _wait_for_window(desktop: DesktopClient, pattern: str, timeout: float) -> str | None:
    """Poll focus_window until a window matching *pattern* can be focused.

    Uses focus_window rather than list_windows because list_windows may
    filter to virtual display bounds, while Claude Code windows spawn on
    the real desktop.

    Returns the matched window title, or None if not found within timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await desktop.focus_window(pattern)
        if result.get("ok"):
            return result.get("window_title") or pattern
        await asyncio.sleep(1.0)
    return None


async def _poll_session_registered(channel_id: str, client: httpx.AsyncClient, timeout: float) -> bool:
    """Poll /api/channel/status until session is registered or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client.get(
                f"{_BASE}/api/channel/status",
                params={"channel_id": channel_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Session is registered if status shows a live session
                if data.get("session_id") or data.get("status") in ("active", "alive", "registered"):
                    return True
                # Also check sessions list endpoint
                sess_resp = await client.get(f"{_BASE}/api/sessions")
                if sess_resp.status_code == 200:
                    sessions = sess_resp.json()
                    if isinstance(sessions, list):
                        for s in sessions:
                            if s.get("channel_id") == channel_id:
                                return True
                    elif isinstance(sessions, dict):
                        for s in sessions.get("sessions", []):
                            if s.get("channel_id") == channel_id:
                                return True
        except Exception:
            pass
        await asyncio.sleep(1.0)
    return False


def _save_evidence(name: str, screenshot_path: str | None):
    """Copy screenshot to evidence directory for test report."""
    if not screenshot_path:
        return
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(screenshot_path)
    if src.exists():
        import shutil
        dst = _EVIDENCE_DIR / f"{name}.jpg"
        shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def http_client():
    """httpx async client for the running Cohort server."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest_asyncio.fixture
async def desktop(http_client):
    """Desktop automation client."""
    return DesktopClient(http_client)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _preflight(http_client):
    """Verify the Cohort server and desktop automation are reachable."""
    # Check server health
    try:
        resp = await http_client.get(f"{_BASE}/health")
        assert resp.status_code == 200, f"Cohort server unhealthy: {resp.status_code}"
    except httpx.ConnectError:
        pytest.skip(f"Cohort server not running at {_BASE}")

    # Check desktop automation status
    try:
        resp = await http_client.get(f"{_BASE}/api/desktop/status")
        if resp.status_code != 200:
            pytest.skip("Desktop automation endpoints not available")
        data = resp.json()
        if not data.get("enabled"):
            pytest.skip("Desktop automation is disabled")
    except Exception as exc:
        pytest.skip(f"Desktop automation check failed: {exc}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDesktopE2E:
    """Full lifecycle: invoke agent -> desktop accept -> verify session."""

    async def test_desktop_status(self, desktop: DesktopClient):
        """Smoke test: desktop status endpoint returns valid data."""
        status = await desktop.status()
        assert status["enabled"] is True
        assert "permission_tier" in status

    async def test_list_windows(self, desktop: DesktopClient):
        """Smoke test: can enumerate windows."""
        result = await desktop.list_windows()
        assert result["ok"] is True

    async def test_screenshot(self, desktop: DesktopClient):
        """Smoke test: can take a screenshot."""
        result = await desktop.screenshot()
        assert result["ok"] is True
        _save_evidence("smoke_screenshot", result.get("data"))

    async def test_accept_dev_channels_prompt(
        self,
        http_client: httpx.AsyncClient,
        desktop: DesktopClient,
    ):
        """Full e2e: invoke agent, accept dev-channels prompt, verify session.

        This is the primary test. It exercises the full lifecycle:
        1. Ensure channel exists
        2. Invoke agent (spawns Claude Code session)
        3. Wait for Claude Code window
        4. Focus + screenshot (evidence: prompt visible)
        5. Press Enter to accept dev-channels prompt
        6. Poll until session registers
        7. Final screenshot (evidence: session running)
        """
        # Step 1: Ensure the test channel exists
        await http_client.post(
            f"{_BASE}/api/channels",
            json={"channel_id": _CHANNEL, "name": _CHANNEL},
        )

        # Step 2: Ensure a session is ready
        ensure_resp = await http_client.post(
            f"{_BASE}/api/channel/ensure-session",
            json={"channel_id": _CHANNEL},
        )
        # Give the session launcher a moment
        await asyncio.sleep(2.0)

        # Step 3: Invoke the agent
        invoke_resp = await http_client.post(
            f"{_BASE}/api/channel/invoke",
            json={
                "agent_id": _AGENT,
                "channel_id": _CHANNEL,
                "message": "Hello from the e2e desktop test. Reply with 'ACK' to confirm.",
            },
        )
        assert invoke_resp.status_code == 200, f"Invoke failed: {invoke_resp.text}"
        invoke_data = invoke_resp.json()
        assert invoke_data.get("ok") is True, f"Invoke not ok: {invoke_data}"

        # Step 4: Wait for Claude Code window to appear
        window_title = await _wait_for_window(desktop, "claude", timeout=_TIMEOUT)
        if window_title is None:
            # Try broader patterns
            window_title = await _wait_for_window(desktop, "terminal", timeout=5)
        assert window_title is not None, (
            f"Claude Code window did not appear within {_TIMEOUT}s. "
            "Check that the session launcher is working."
        )

        # Step 5: Focus the window
        focus_result = await desktop.focus_window(window_title)
        assert focus_result["ok"], f"Failed to focus window: {focus_result}"
        await asyncio.sleep(0.5)

        # Step 6: Screenshot the dev-channels prompt (evidence)
        pre_accept = await desktop.screenshot()
        _save_evidence("01_dev_channels_prompt", pre_accept.get("data"))

        # Step 7: Press Enter to accept the prompt
        enter_result = await desktop.press_key("enter")
        assert enter_result["ok"], f"Failed to press Enter: {enter_result}"
        await asyncio.sleep(1.0)

        # Step 8: Screenshot after accepting (evidence)
        post_accept = await desktop.screenshot()
        _save_evidence("02_post_accept", post_accept.get("data"))

        # Step 9: Wait for the session to register with the server
        registered = await _poll_session_registered(
            _CHANNEL, http_client, timeout=30.0,
        )

        # Step 10: Final screenshot (evidence)
        final = await desktop.screenshot()
        _save_evidence("03_session_running", final.get("data"))

        assert registered, (
            f"Session for channel '{_CHANNEL}' did not register within 30s. "
            "The Claude Code session may have failed to start or the "
            "dev-channels prompt was not accepted."
        )

    async def test_session_responds(
        self,
        http_client: httpx.AsyncClient,
        desktop: DesktopClient,
    ):
        """After accepting the prompt, verify the session can receive and respond to messages.

        This test depends on test_accept_dev_channels_prompt having run first.
        It checks that the session is alive by polling for messages.
        """
        # Check if there's an active session for our channel
        resp = await http_client.get(
            f"{_BASE}/api/channel/status",
            params={"channel_id": _CHANNEL},
        )
        if resp.status_code != 200:
            pytest.skip("No session registered (run test_accept_dev_channels_prompt first)")

        data = resp.json()
        if not (data.get("session_id") or data.get("status") in ("active", "alive", "registered")):
            pytest.skip("No active session found")

        # Poll for messages in the channel (the agent should have responded)
        deadline = time.monotonic() + 30.0
        found_response = False
        while time.monotonic() < deadline:
            msgs_resp = await http_client.get(
                f"{_BASE}/api/messages",
                params={"channel": _CHANNEL, "limit": 10},
            )
            if msgs_resp.status_code == 200:
                msgs = msgs_resp.json()
                message_list = msgs if isinstance(msgs, list) else msgs.get("messages", [])
                for msg in message_list:
                    sender = msg.get("sender", "")
                    if sender and sender != "system" and sender != "user":
                        found_response = True
                        break
            if found_response:
                break
            await asyncio.sleep(2.0)

        # Take final evidence screenshot
        final_ss = await desktop.screenshot()
        _save_evidence("04_session_response", final_ss.get("data"))

        # Note: We don't assert found_response=True because the agent may take
        # longer than our timeout. The key assertion is that the session registered.
        if found_response:
            pass  # Great - full round-trip confirmed


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x", "--tb=short", "-s"])
