"""Tests for tool permission resolution (cohort.tool_permissions).

Full coverage of the layered permission system: central defaults,
per-agent overrides, profile resolution, deny lists, MCP server
resolution, and backward compatibility.
"""

from __future__ import annotations

import json

import pytest

from cohort.agent import AgentConfig
from cohort.tool_permissions import (
    ResolvedPermissions,
    load_central_permissions,
    reload_central_permissions,
    resolve_permissions,
)

# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def central_config() -> dict:
    """Standard central tool_permissions config for testing."""
    return {
        "version": "1.0",
        "default_permission_mode": "acceptEdits",
        "default_max_turns": 10,
        "tool_profiles": {
            "readonly": {
                "allowed_tools": ["Read", "Glob", "Grep"],
                "permission_mode": "acceptEdits",
                "max_turns": 5,
            },
            "developer": {
                "allowed_tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                "permission_mode": "acceptEdits",
                "max_turns": 15,
            },
            "researcher": {
                "allowed_tools": ["WebSearch", "WebFetch", "Read"],
                "permission_mode": "acceptEdits",
                "max_turns": 5,
            },
            "minimal": {
                "allowed_tools": [],
                "permission_mode": None,
                "max_turns": 1,
            },
        },
        "agent_defaults": {
            "specialist": "developer",
            "orchestrator": "readonly",
            "utility": "minimal",
            "infrastructure": "readonly",
        },
        "mcp_servers": {
            "cohort": {
                "command": "python",
                "args": ["-m", "cohort.mcp.server"],
            },
            "local_llm": {
                "command": "python",
                "args": ["-m", "cohort.mcp.local_llm_server"],
            },
        },
        "denied_tools": ["TodoWrite"],
    }


def _make_agent(
    agent_id: str = "test_agent",
    agent_type: str = "specialist",
    tool_permissions: dict | None = None,
) -> AgentConfig:
    """Create a minimal AgentConfig for testing."""
    return AgentConfig(
        agent_id=agent_id,
        name="Test Agent",
        role="Tester",
        agent_type=agent_type,
        tool_permissions=tool_permissions or {},
    )


# =====================================================================
# Empty/missing central config
# =====================================================================


class TestEmptyCentral:
    def test_empty_central_returns_none(self):
        agent = _make_agent()
        result = resolve_permissions("test_agent", agent, {})
        assert result is None

    def test_none_central_returns_none(self):
        agent = _make_agent()
        result = resolve_permissions("test_agent", agent, None)
        # Uses get_central_permissions() which may or may not be loaded
        # but with no central config, should return None
        # Reset module state first
        import cohort.tool_permissions as tp
        tp._central_loaded = False
        tp._central_permissions = {}
        result = resolve_permissions("test_agent", agent, {})
        assert result is None

    def test_none_agent_config_returns_none(self):
        result = resolve_permissions("test_agent", None, {})
        assert result is None


# =====================================================================
# Profile resolution by agent_type
# =====================================================================


class TestProfileResolution:
    def test_specialist_gets_developer_profile(self, central_config):
        agent = _make_agent(agent_type="specialist")
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.profile_name == "developer"
        assert "Read" in result.allowed_tools
        assert "Write" in result.allowed_tools
        assert "Edit" in result.allowed_tools
        assert "Bash" in result.allowed_tools
        assert result.max_turns == 15

    def test_orchestrator_gets_readonly_profile(self, central_config):
        agent = _make_agent(agent_type="orchestrator")
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.profile_name == "readonly"
        assert "Read" in result.allowed_tools
        assert "Write" not in result.allowed_tools
        assert result.max_turns == 5

    def test_utility_gets_minimal_returns_none(self, central_config):
        agent = _make_agent(agent_type="utility")
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is None  # minimal = no tools

    def test_unknown_agent_type_falls_back_to_minimal(self, central_config):
        agent = _make_agent(agent_type="nonexistent_type")
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is None  # minimal profile


# =====================================================================
# Per-agent profile override
# =====================================================================


class TestPerAgentOverride:
    def test_agent_profile_overrides_type_default(self, central_config):
        agent = _make_agent(
            agent_type="specialist",  # would default to "developer"
            tool_permissions={"profile": "readonly"},
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.profile_name == "readonly"
        assert "Write" not in result.allowed_tools

    def test_agent_allowed_tools_override_replaces_profile(self, central_config):
        agent = _make_agent(
            tool_permissions={
                "profile": "developer",
                "allowed_tools_override": ["Read", "Grep"],
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.allowed_tools == ["Read", "Grep"]

    def test_agent_max_turns_override(self, central_config):
        agent = _make_agent(
            tool_permissions={
                "profile": "developer",
                "max_turns": 25,
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.max_turns == 25

    def test_agent_permission_mode_override(self, central_config):
        agent = _make_agent(
            tool_permissions={
                "profile": "developer",
                "permission_mode": "plan",
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.permission_mode == "plan"

    def test_unknown_profile_falls_back_to_minimal(self, central_config):
        agent = _make_agent(
            tool_permissions={"profile": "nonexistent_profile"},
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is None


# =====================================================================
# Deny lists
# =====================================================================


class TestDenyLists:
    def test_global_denied_tools_subtracted(self, central_config):
        # TodoWrite is globally denied
        agent = _make_agent(
            tool_permissions={
                "allowed_tools_override": ["Read", "TodoWrite", "Grep"],
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert "TodoWrite" not in result.allowed_tools
        assert "Read" in result.allowed_tools

    def test_agent_denied_tools_subtracted(self, central_config):
        agent = _make_agent(
            tool_permissions={
                "profile": "developer",
                "denied_tools": ["Bash", "Write"],
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert "Bash" not in result.allowed_tools
        assert "Write" not in result.allowed_tools
        assert "Read" in result.allowed_tools

    def test_combined_deny_lists(self, central_config):
        # Global denies TodoWrite, agent denies Bash
        agent = _make_agent(
            tool_permissions={
                "allowed_tools_override": ["Read", "Bash", "TodoWrite"],
                "denied_tools": ["Bash"],
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.allowed_tools == ["Read"]

    def test_all_tools_denied_returns_none(self, central_config):
        agent = _make_agent(
            tool_permissions={
                "allowed_tools_override": ["TodoWrite"],
                # TodoWrite is globally denied, so nothing left
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is None


# =====================================================================
# MCP server resolution
# =====================================================================


class TestMCPServers:
    def test_mcp_servers_resolved(self, central_config):
        agent = _make_agent(
            tool_permissions={
                "profile": "developer",
                "mcp_servers": ["cohort", "local_llm"],
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert len(result.mcp_servers) == 2
        names = [s["name"] for s in result.mcp_servers]
        assert "cohort" in names
        assert "local_llm" in names
        # Check resolved command/args
        llm_server = next(s for s in result.mcp_servers if s["name"] == "local_llm")
        assert llm_server["command"] == "python"
        assert llm_server["args"] == ["-m", "cohort.mcp.local_llm_server"]

    def test_unknown_mcp_server_skipped(self, central_config):
        agent = _make_agent(
            tool_permissions={
                "profile": "developer",
                "mcp_servers": ["cohort", "nonexistent_server"],
            },
        )
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert len(result.mcp_servers) == 1
        assert result.mcp_servers[0]["name"] == "cohort"

    def test_no_mcp_servers_by_default(self, central_config):
        agent = _make_agent()
        result = resolve_permissions("test_agent", agent, central_config)
        assert result is not None
        assert result.mcp_servers == []


# =====================================================================
# File loading
# =====================================================================


class TestFileLoading:
    def test_load_from_file(self, tmp_path):
        config = {
            "version": "1.0",
            "tool_profiles": {
                "developer": {
                    "allowed_tools": ["Read"],
                    "permission_mode": "acceptEdits",
                    "max_turns": 5,
                },
            },
            "agent_defaults": {"specialist": "developer"},
            "denied_tools": [],
        }
        (tmp_path / "tool_permissions.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

        import cohort.tool_permissions as tp
        tp._central_loaded = False
        tp._central_permissions = {}
        result = load_central_permissions(tmp_path)
        assert result["version"] == "1.0"
        assert "developer" in result["tool_profiles"]

    def test_missing_file_returns_empty(self, tmp_path):
        import cohort.tool_permissions as tp
        tp._central_loaded = False
        tp._central_permissions = {}
        result = load_central_permissions(tmp_path)
        assert result == {}

    def test_reload_clears_cache(self, tmp_path):
        import cohort.tool_permissions as tp
        tp._central_loaded = True
        tp._central_permissions = {"cached": True}

        result = reload_central_permissions(tmp_path)
        assert "cached" not in result


# =====================================================================
# Tool awareness prompt builder (via agent_router)
# =====================================================================


class TestToolAwareness:
    def test_build_tool_awareness_developer(self):
        from cohort.agent_router import _build_tool_awareness

        perms = ResolvedPermissions(
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=15,
            mcp_servers=[{"name": "local_llm", "command": "python", "args": []}],
            profile_name="developer",
        )
        text = _build_tool_awareness(perms)
        assert "=== AVAILABLE TOOLS ===" in text
        assert "Read" in text
        assert "Bash" in text
        assert "local_llm" in text
        assert "Max turns: 15" in text

    def test_build_tool_awareness_readonly(self):
        from cohort.agent_router import _build_tool_awareness

        perms = ResolvedPermissions(
            allowed_tools=["Read", "Glob", "Grep"],
            max_turns=5,
        )
        text = _build_tool_awareness(perms)
        assert "File tools" in text
        assert "Bash" not in text
        assert "Edit tools" not in text

    def test_build_tool_awareness_with_cohort_mcp(self):
        from cohort.agent_router import _build_tool_awareness

        perms = ResolvedPermissions(
            allowed_tools=["Read"],
            mcp_servers=[{"name": "cohort", "command": "python", "args": []}],
        )
        text = _build_tool_awareness(perms)
        assert "cohort" in text
        assert "team chat" in text


# =====================================================================
# MCP config temp file writer
# =====================================================================


class TestMCPConfigWriter:
    def test_write_mcp_config_creates_file(self):
        from cohort.agent_router import _write_mcp_config

        servers = [
            {"name": "test_server", "command": "python", "args": ["-m", "test"]},
        ]
        path = _write_mcp_config(servers)
        assert path is not None
        assert path.exists()

        content = json.loads(path.read_text())
        assert "mcpServers" in content
        assert "test_server" in content["mcpServers"]
        assert content["mcpServers"]["test_server"]["command"] == "python"

        # Cleanup
        path.unlink()

    def test_write_mcp_config_empty_list(self):
        from cohort.agent_router import _write_mcp_config

        path = _write_mcp_config([])
        assert path is not None
        content = json.loads(path.read_text())
        assert content["mcpServers"] == {}
        path.unlink()
