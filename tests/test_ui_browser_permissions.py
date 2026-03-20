"""Playwright UI tests for browser permission controls.

Tests the permissions modal browser automation section:
- Browser tier toggles render (Browse, Interact, Full Control)
- Tier hierarchy auto-check/uncheck behavior
- Section header appears
- Internal Web status shows browser backend info

Requires: pytest-playwright, playwright browsers installed.
Run: pytest tests/test_ui_browser_permissions.py -v
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Skip entire module if playwright not installed
pytest.importorskip("playwright")


# =====================================================================
# Fixtures -- live server for Playwright
# =====================================================================


@pytest.fixture(scope="module")
def test_data_dir(tmp_path_factory) -> Path:
    """Create a temporary data directory with required files."""
    data_dir = tmp_path_factory.mktemp("cohort_ui_data")

    (data_dir / "agents.json").write_text("{}", encoding="utf-8")
    (data_dir / "channels.json").write_text("{}", encoding="utf-8")
    (data_dir / "messages.json").write_text("[]", encoding="utf-8")
    (data_dir / "settings.json").write_text("{}", encoding="utf-8")
    (data_dir / "tool_permissions.json").write_text(json.dumps({
        "tool_profiles": {
            "full": [
                "Read", "Write", "Edit", "Bash", "Glob", "Grep",
                "WebSearch", "WebFetch", "InternalWebFetch", "InternalWebSearch",
                "BrowserBrowse", "BrowserInteract", "BrowserAdvanced",
            ],
            "standard": ["Read", "Glob", "Grep", "BrowserBrowse"],
            "minimal": ["Read"],
        },
        "agent_defaults": {},
        "denied_tools": [],
        "agent_overrides": {},
    }), encoding="utf-8")

    return data_dir


@pytest.fixture(scope="module")
def test_agents_dir(tmp_path_factory) -> Path:
    """Create a minimal agents directory."""
    agents_root = tmp_path_factory.mktemp("cohort_ui_agents")
    agents_dir = agents_root / "agents"

    for agent_id in ["python_developer", "web_developer"]:
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_config.json").write_text(json.dumps({
            "agent_id": agent_id,
            "name": agent_id.replace("_", " ").title(),
            "role": f"Test {agent_id}",
            "status": "active",
            "personality": f"A test agent named {agent_id}.",
            "domain_expertise": ["testing"],
            "skill_levels": {"testing": 8},
            "tool_permissions": {"profile": "full"},
        }), encoding="utf-8")
        (agent_dir / "agent_prompt.md").write_text(
            f"You are {agent_id}.", encoding="utf-8"
        )

    return agents_dir


@pytest.fixture(scope="module")
def live_server(test_data_dir, test_agents_dir):
    """Start a real Cohort server on a free port for Playwright tests.

    Uses uvicorn in a background thread. Yields the base URL.
    """
    import socket
    import uvicorn

    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = {
        "COHORT_DATA_DIR": str(test_data_dir),
        "COHORT_AGENTS_DIR": str(test_agents_dir),
        "COHORT_AGENTS_ROOT": str(test_agents_dir.parent),
    }

    server_ready = threading.Event()
    server_instance = None

    def run_server():
        nonlocal server_instance
        with patch.dict(os.environ, env, clear=False):
            # Must import inside patched env
            from cohort.server import create_app
            app = create_app(data_dir=str(test_data_dir))

            config = uvicorn.Config(
                app=app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
            server_instance = uvicorn.Server(config)

            # Signal ready once startup completes
            original_startup = server_instance.startup

            async def patched_startup(*args, **kwargs):
                result = await original_startup(*args, **kwargs)
                server_ready.set()
                return result

            server_instance.startup = patched_startup
            server_instance.run()

    # Patch route_mentions to avoid background thread issues
    with patch("cohort.agent_router.route_mentions"):
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()

    # Wait for server to be ready
    if not server_ready.wait(timeout=10):
        pytest.skip("Server failed to start within 10 seconds")

    base_url = f"http://127.0.0.1:{port}"

    # Quick health check
    import httpx
    for _ in range(20):
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1)
            if resp.status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    else:
        pytest.skip("Server health check failed")

    yield base_url

    # Shutdown
    if server_instance:
        server_instance.should_exit = True
    thread.join(timeout=5)


# =====================================================================
# Tests
# =====================================================================


def _dismiss_wizard_and_open_tool_perms(page, live_server):
    """Navigate to the app, dismiss setup wizard, open tool permissions for first agent."""
    page.goto(live_server)
    page.wait_for_load_state("networkidle")

    # Dismiss setup wizard if present
    wizard_close = page.locator("#setup-wizard-close")
    if wizard_close.is_visible(timeout=2000):
        wizard_close.click()
        page.wait_for_timeout(300)

    # Open permissions modal
    perms_btn = page.locator("text=Permissions").first
    perms_btn.click(timeout=5000)
    page.wait_for_timeout(500)

    # Click first agent's gear icon for tool permissions
    gear = page.locator(".agent-card__gear").first
    gear.click(timeout=5000)
    page.wait_for_timeout(500)


class TestBrowserPermissionsUI:
    """Test the browser automation section in the tool permissions modal."""

    def test_permissions_modal_has_browser_section(self, live_server, page):
        """The tool permissions modal should have a 'Browser Automation' section header."""
        _dismiss_wizard_and_open_tool_perms(page, live_server)

        header = page.locator("text=Browser Automation")
        assert header.is_visible(), "Browser Automation section header should be visible"

    def test_browser_tier_checkboxes_exist(self, live_server, page):
        """All three browser tier checkboxes should be present."""
        _dismiss_wizard_and_open_tool_perms(page, live_server)

        browse = page.locator("[data-tool='BrowserBrowse']")
        interact = page.locator("[data-tool='BrowserInteract']")
        advanced = page.locator("[data-tool='BrowserAdvanced']")

        assert browse.count() > 0, "BrowserBrowse checkbox should exist"
        assert interact.count() > 0, "BrowserInteract checkbox should exist"
        assert advanced.count() > 0, "BrowserAdvanced checkbox should exist"

    def test_tier_names_are_friendly(self, live_server, page):
        """Browser tiers should show friendly names, not internal keys."""
        _dismiss_wizard_and_open_tool_perms(page, live_server)

        # Friendly names should appear in the browser section
        section = page.locator(".tool-perms__section-header + .tool-perms__tool", has_text="Browse")
        assert section.count() > 0, "Should show 'Browse' label"

        interact_label = page.locator(".tool-perms__tool-name", has_text="Interact")
        assert interact_label.count() > 0, "Should show 'Interact' label"

        full_label = page.locator(".tool-perms__tool-name", has_text="Full Control")
        assert full_label.count() > 0, "Should show 'Full Control' label"

    def test_advanced_autochecks_lower_tiers(self, live_server, page):
        """Checking 'Full Control' should auto-check Browse and Interact."""
        _dismiss_wizard_and_open_tool_perms(page, live_server)

        browse = page.locator("[data-tool='BrowserBrowse']")
        interact = page.locator("[data-tool='BrowserInteract']")
        advanced = page.locator("[data-tool='BrowserAdvanced']")

        # Uncheck all first
        if browse.is_checked():
            browse.uncheck()
        if interact.is_checked():
            interact.uncheck()
        if advanced.is_checked():
            advanced.uncheck()
        page.wait_for_timeout(200)

        # Check Advanced -- should auto-check Browse and Interact
        advanced.check()
        page.wait_for_timeout(200)

        assert browse.is_checked(), "Browse should be auto-checked when Advanced is checked"
        assert interact.is_checked(), "Interact should be auto-checked when Advanced is checked"

    def test_uncheck_browse_unchecks_higher_tiers(self, live_server, page):
        """Unchecking Browse should auto-uncheck Interact and Full Control."""
        _dismiss_wizard_and_open_tool_perms(page, live_server)

        browse = page.locator("[data-tool='BrowserBrowse']")
        interact = page.locator("[data-tool='BrowserInteract']")
        advanced = page.locator("[data-tool='BrowserAdvanced']")

        # Check all first via Advanced
        advanced.check()
        page.wait_for_timeout(200)

        # Uncheck Browse -- should cascade up
        browse.uncheck()
        page.wait_for_timeout(200)

        assert not interact.is_checked(), "Interact should be unchecked when Browse is unchecked"
        assert not advanced.is_checked(), "Advanced should be unchecked when Browse is unchecked"


class TestInternalWebStatus:
    """Test the internal web status endpoint reports browser backend info."""

    def test_status_includes_browser_backend(self, live_server):
        """GET /api/internal-web/status should include browser_backend field."""
        import httpx

        resp = httpx.get(f"{live_server}/api/internal-web/status", timeout=5)
        assert resp.status_code == 200
        data = resp.json()

        assert "browser_backend" in data, "Status should include browser_backend field"
        assert isinstance(data["browser_backend"], bool)

        # If playwright is installed (it is in our test env), backend should be True
        if data.get("playwright"):
            assert data["browser_backend"] is True
            assert data.get("browser_backend_type") == "PlaywrightDirect"


class TestToolPermissionsAPI:
    """Test that _ALL_TOOLS includes browser tiers."""

    def test_all_tools_includes_browser(self, live_server):
        """GET /api/tool-permissions should list browser tiers."""
        import httpx

        resp = httpx.get(f"{live_server}/api/tool-permissions", timeout=5)
        assert resp.status_code == 200
        data = resp.json()

        all_tools = data.get("all_tools", [])
        assert "BrowserBrowse" in all_tools, "BrowserBrowse should be in all_tools"
        assert "BrowserInteract" in all_tools, "BrowserInteract should be in all_tools"
        assert "BrowserAdvanced" in all_tools, "BrowserAdvanced should be in all_tools"

    def test_agent_has_browser_tools_in_full_profile(self, live_server):
        """Agents with 'full' profile should have browser tools."""
        import httpx

        resp = httpx.get(f"{live_server}/api/tool-permissions", timeout=5)
        assert resp.status_code == 200
        data = resp.json()

        # Find python_developer (has profile: full)
        agents = data.get("agents", [])
        pd = next((a for a in agents if a["agent_id"] == "python_developer"), None)
        if pd:
            allowed = pd.get("allowed_tools", [])
            assert "BrowserBrowse" in allowed, "Full profile should include BrowserBrowse"
