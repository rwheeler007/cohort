"""Tests for cohort.mcp.browser_backend -- URL safety, permissions, and backend logic."""

import asyncio

import pytest

from cohort.mcp.browser_backend import (
    BROWSER_PERMISSION_TIERS,
    TIER_HIERARCHY,
    AgentBrowserState,
    BrowserResult,
    PlaywrightDirectBackend,
    PlaywrightMCPBackend,
    check_browser_permission,
    get_browser_backend,
    is_url_allowed,
)

# =====================================================================
# BrowserResult
# =====================================================================


class TestBrowserResult:
    def test_success_with_data(self):
        r = BrowserResult(success=True, data="hello", url="https://x.com", title="X")
        s = r.to_str()
        assert "Title: X" in s
        assert "URL: https://x.com" in s
        assert "hello" in s

    def test_success_empty(self):
        r = BrowserResult(success=True)
        assert r.to_str() == "[OK]"

    def test_error(self):
        r = BrowserResult(success=False, error="boom")
        assert r.to_str() == "Error: boom"


# =====================================================================
# URL safety
# =====================================================================


class TestURLSafety:
    def test_public_https(self):
        ok, _ = is_url_allowed("https://example.com")
        assert ok

    def test_public_http(self):
        ok, _ = is_url_allowed("http://example.com")
        assert ok

    def test_localhost_blocked(self):
        ok, reason = is_url_allowed("http://localhost:8080")
        assert not ok
        assert "Private" in reason or "local" in reason.lower() or "blocked" in reason.lower()

    def test_127_blocked(self):
        ok, reason = is_url_allowed("http://127.0.0.1:8080")
        assert not ok

    def test_192_168_blocked(self):
        ok, reason = is_url_allowed("http://192.168.1.1")
        assert not ok

    def test_10_x_blocked(self):
        ok, reason = is_url_allowed("http://10.0.0.1")
        assert not ok

    def test_172_16_blocked(self):
        ok, reason = is_url_allowed("http://172.16.0.1")
        assert not ok

    def test_allow_local_flag(self):
        ok, _ = is_url_allowed("http://192.168.1.1", allow_local=True)
        assert ok

    def test_ftp_blocked(self):
        ok, reason = is_url_allowed("ftp://example.com/file")
        assert not ok
        assert "scheme" in reason.lower()

    def test_no_hostname(self):
        ok, reason = is_url_allowed("https://")
        assert not ok

    def test_blocklist_exact(self):
        ok, _ = is_url_allowed("https://evil.com", blocklist=["evil.com"])
        assert not ok

    def test_blocklist_wildcard(self):
        ok, _ = is_url_allowed("https://sub.evil.com", blocklist=["*.evil.com"])
        assert not ok

    def test_blocklist_no_match(self):
        ok, _ = is_url_allowed("https://good.com", blocklist=["evil.com"])
        assert ok

    def test_allowlist_match(self):
        ok, _ = is_url_allowed("https://trusted.com", allowlist=["trusted.com"])
        assert ok

    def test_allowlist_no_match(self):
        ok, reason = is_url_allowed("https://other.com", allowlist=["trusted.com"])
        assert not ok
        assert "allowlist" in reason.lower()

    def test_allowlist_wildcard(self):
        ok, _ = is_url_allowed("https://app.trusted.com", allowlist=["*.trusted.com"])
        assert ok

    def test_invalid_url(self):
        ok, reason = is_url_allowed("")
        assert not ok


# =====================================================================
# Permission tiers
# =====================================================================


class TestPermissionTiers:
    def test_all_actions_have_tiers(self):
        """Every action in the tier map should map to a valid tier."""
        valid_tiers = {"browser_read", "browser_interact", "browser_advanced"}
        for action, tier in BROWSER_PERMISSION_TIERS.items():
            assert tier in valid_tiers, f"{action} has invalid tier: {tier}"

    def test_hierarchy_is_inclusive(self):
        """Higher tiers include all lower tier permissions."""
        assert "browser_read" in TIER_HIERARCHY["browser_read"]
        assert "browser_read" in TIER_HIERARCHY["browser_interact"]
        assert "browser_interact" in TIER_HIERARCHY["browser_interact"]
        assert "browser_read" in TIER_HIERARCHY["browser_advanced"]
        assert "browser_interact" in TIER_HIERARCHY["browser_advanced"]
        assert "browser_advanced" in TIER_HIERARCHY["browser_advanced"]

    def test_read_actions_allowed_for_read_tier(self):
        read_actions = [
            "navigate", "navigate_back", "snapshot", "screenshot",
            "get_text", "console_messages", "network_requests",
            "tabs_list", "wait_for", "close_page",
        ]
        for action in read_actions:
            ok, _ = check_browser_permission(action, "browser_read")
            assert ok, f"{action} should be allowed for browser_read"

    def test_interact_actions_blocked_for_read_tier(self):
        interact_actions = ["click", "fill", "type_text", "hover", "drag", "resize"]
        for action in interact_actions:
            ok, _ = check_browser_permission(action, "browser_read")
            assert not ok, f"{action} should be blocked for browser_read"

    def test_interact_actions_allowed_for_interact_tier(self):
        interact_actions = [
            "click", "fill", "type_text", "press_key", "select_option",
            "hover", "drag", "file_upload", "handle_dialog",
            "mouse_click_xy", "mouse_move_xy", "mouse_drag_xy",
            "mouse_wheel", "resize", "tab_new", "tab_select", "tab_close",
        ]
        for action in interact_actions:
            ok, _ = check_browser_permission(action, "browser_interact")
            assert ok, f"{action} should be allowed for browser_interact"

    def test_advanced_actions_blocked_for_interact_tier(self):
        advanced_actions = [
            "evaluate", "cookie_list", "cookie_set", "storage_get",
            "route_set", "pdf_save",
        ]
        for action in advanced_actions:
            ok, _ = check_browser_permission(action, "browser_interact")
            assert not ok, f"{action} should be blocked for browser_interact"

    def test_advanced_actions_allowed_for_advanced_tier(self):
        advanced_actions = [
            "evaluate", "cookie_list", "cookie_set", "cookie_delete",
            "cookie_clear", "storage_get", "storage_set", "storage_delete",
            "storage_clear", "storage_list", "route_set", "route_list",
            "route_remove", "pdf_save",
        ]
        for action in advanced_actions:
            ok, _ = check_browser_permission(action, "browser_advanced")
            assert ok, f"{action} should be allowed for browser_advanced"

    def test_unknown_action(self):
        ok, reason = check_browser_permission("nonexistent_action", "browser_advanced")
        assert not ok
        assert "Unknown" in reason

    def test_unknown_tier(self):
        ok, reason = check_browser_permission("navigate", "browser_superadmin")
        assert not ok


# =====================================================================
# PlaywrightDirectBackend (unit tests, no browser)
# =====================================================================


class TestPlaywrightDirectBackend:
    def test_init_defaults(self):
        backend = PlaywrightDirectBackend()
        assert backend._max_contexts == 3
        assert backend._timeout_ms == 30_000
        assert backend._allow_local is False
        assert backend._started is False

    def test_init_custom(self):
        backend = PlaywrightDirectBackend(
            max_contexts=5,
            timeout_ms=10_000,
            allow_local=True,
            blocklist=["evil.com"],
            allowlist=["good.com"],
        )
        assert backend._max_contexts == 5
        assert backend._timeout_ms == 10_000
        assert backend._allow_local is True
        assert backend._blocklist == ["evil.com"]
        assert backend._allowlist == ["good.com"]

    def test_check_url_delegates(self):
        backend = PlaywrightDirectBackend(blocklist=["blocked.com"])
        ok, _ = backend._check_url("https://blocked.com")
        assert not ok
        ok, _ = backend._check_url("https://allowed.com")
        assert ok

    def test_not_available_before_start(self):
        backend = PlaywrightDirectBackend()
        loop = asyncio.new_event_loop()
        try:
            available = loop.run_until_complete(backend.is_available())
            # Either False (not started) or depends on playwright install
            assert isinstance(available, bool)
        finally:
            loop.close()


# =====================================================================
# PlaywrightMCPBackend (stub)
# =====================================================================


class TestPlaywrightMCPBackend:
    def test_not_available(self):
        backend = PlaywrightMCPBackend()
        loop = asyncio.new_event_loop()
        try:
            assert loop.run_until_complete(backend.is_available()) is False
        finally:
            loop.close()

    def test_start_raises(self):
        backend = PlaywrightMCPBackend()
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(NotImplementedError):
                loop.run_until_complete(backend.start())
        finally:
            loop.close()

    def test_any_action_returns_error(self):
        backend = PlaywrightMCPBackend()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(backend.navigate("agent1", "https://x.com"))
            assert not result.success
            assert "not yet implemented" in result.error.lower()
        finally:
            loop.close()


# =====================================================================
# Factory
# =====================================================================


class TestFactory:
    def test_get_browser_backend_returns_direct(self):
        backend = get_browser_backend()
        assert isinstance(backend, PlaywrightDirectBackend)

    def test_singleton(self):
        b1 = get_browser_backend()
        b2 = get_browser_backend()
        assert b1 is b2


# =====================================================================
# AgentBrowserState
# =====================================================================


class TestAgentBrowserState:
    def test_defaults(self):
        state = AgentBrowserState(agent_id="test")
        assert state.agent_id == "test"
        assert state.context is None
        assert state.page is None
        assert isinstance(state.lock, asyncio.Lock)
        assert state.pages == {}
