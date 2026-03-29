"""Integration tests for inventory query pipeline.

Tests the keyword gate, query function (with mocked LLM), and the
prompt injection format for both chat and code queue paths.
"""

from unittest.mock import patch

import pytest

from cohort.inventory_query import (
    IMPL_SIGNALS,
    _resolve_inventory,
    query_inventory,
    query_inventory_block,
    should_query_inventory,
)
from cohort.inventory_schema import InventoryEntry

# ── Keyword gate ─────────────────────────────────────────────────────────

class TestKeywordGate:
    def test_implementation_messages_pass(self):
        assert should_query_inventory("Can you build a new CLI tool?")
        assert should_query_inventory("I need to fix the rate limiter")
        assert should_query_inventory("Let's refactor the agent router")
        assert should_query_inventory("implement the inventory endpoint")

    def test_general_messages_blocked(self):
        assert not should_query_inventory("Hello, how are you?")
        assert not should_query_inventory("What's the status?")
        assert not should_query_inventory("Good morning team")
        assert not should_query_inventory("Thanks for the update")

    def test_empty_message_blocked(self):
        assert not should_query_inventory("")

    def test_all_signals_recognized(self):
        for signal in IMPL_SIGNALS:
            assert should_query_inventory(f"Please {signal} something"), (
                f"Signal '{signal}' should pass the gate"
            )


# ── Query function (mocked LLM) ─────────────────────────────────────────

@pytest.fixture
def mock_inventory() -> list[InventoryEntry]:
    return [
        InventoryEntry(
            id="llm-router", source_project="BOSS",
            entry_point="tools/llm_router/llm_router.py",
            keywords=["model", "inference", "routing"],
            description="Unified LLM routing", type="tool",
        ),
        InventoryEntry(
            id="rate-limiter", source_project="BOSS",
            entry_point="tools/rate_limiter.py",
            keywords=["rate", "limit", "throttle"],
            description="Sliding window rate limiter", type="tool",
        ),
        InventoryEntry(
            id="cli-first-development", source_project="BOSS",
            entry_point="golden_patterns/cli-first-development/",
            keywords=["CLI", "command", "testable"],
            description="Build CLI commands first, GUI wraps them", type="pattern",
        ),
    ]


class TestQueryInventory:
    @patch("cohort.inventory_query._llm_generate")
    def test_returns_llm_matches(self, mock_llm, mock_inventory):
        mock_llm.return_value = (
            "- **[tool: rate-limiter]** tools/rate_limiter.py "
            "-- directly implements rate limiting, consider extending"
        )
        result = query_inventory("fix the rate limiter", inventory=mock_inventory)
        assert "rate-limiter" in result
        assert "consider extending" in result

    @patch("cohort.inventory_query._llm_generate")
    def test_empty_query_returns_empty(self, mock_llm, mock_inventory):
        result = query_inventory("", inventory=mock_inventory)
        assert result == ""
        mock_llm.assert_not_called()

    @patch("cohort.inventory_query._llm_generate")
    def test_llm_failure_returns_empty(self, mock_llm, mock_inventory):
        mock_llm.return_value = None
        result = query_inventory("build something", inventory=mock_inventory)
        assert result == ""

    @patch("cohort.inventory_query._llm_generate")
    def test_budget_enforcement(self, mock_llm, mock_inventory):
        # Return a very long response that exceeds budget
        mock_llm.return_value = "- match one\n" * 200
        result = query_inventory("build a thing", inventory=mock_inventory)
        assert len(result) <= 1200  # MAX_INVENTORY_CHARS

    @patch("cohort.inventory_query._llm_generate")
    def test_no_inventory_returns_empty(self, mock_llm):
        result = query_inventory("build something", inventory=[])
        assert result == ""
        mock_llm.assert_not_called()


class TestQueryInventoryBlock:
    @patch("cohort.inventory_query._llm_generate")
    def test_wraps_in_section_headers(self, mock_llm, mock_inventory):
        mock_llm.return_value = "- **[tool: llm-router]** -- relevant match"
        result = query_inventory_block("build routing", inventory=mock_inventory)
        assert "=== ECOSYSTEM CAPABILITIES ===" in result
        assert "=== END ECOSYSTEM CAPABILITIES ===" in result
        assert "llm-router" in result

    @patch("cohort.inventory_query._llm_generate")
    def test_empty_result_returns_empty_string(self, mock_llm, mock_inventory):
        mock_llm.return_value = ""
        result = query_inventory_block("build something", inventory=mock_inventory)
        assert result == ""

    @patch("cohort.inventory_query._llm_generate")
    def test_no_matches_returns_empty(self, mock_llm, mock_inventory):
        mock_llm.return_value = None
        result = query_inventory_block("build something", inventory=mock_inventory)
        assert result == ""


# ── Resolve inventory fallback chain ─────────────────────────────────────

class TestResolveInventory:
    def test_uses_provided_inventory(self, mock_inventory):
        result = _resolve_inventory(mock_inventory, "http://unused:5100")
        assert len(result) == 3

    @patch("cohort.inventory_query._fetch_from_server")
    @patch("cohort.inventory_query._load_local_yaml")
    def test_tries_server_then_yaml(self, mock_yaml, mock_server):
        mock_server.return_value = []
        mock_yaml.return_value = [InventoryEntry(id="fallback", source_project="BOSS")]

        result = _resolve_inventory(None, "http://localhost:5100")
        mock_server.assert_called_once()
        mock_yaml.assert_called_once()
        assert len(result) == 1
        assert result[0].id == "fallback"

    @patch("cohort.inventory_query._fetch_from_server")
    @patch("cohort.inventory_query._load_local_yaml")
    def test_server_success_skips_yaml(self, mock_yaml, mock_server):
        mock_server.return_value = [{"id": "from-server", "type": "tool"}]

        result = _resolve_inventory(None, "http://localhost:5100")
        mock_server.assert_called_once()
        mock_yaml.assert_not_called()
        assert len(result) == 1


# ── InventoryEntry formatting ────────────────────────────────────────────

class TestInventoryLineFormat:
    def test_line_contains_all_fields(self):
        entry = InventoryEntry(
            id="comms-service", source_project="BOSS",
            entry_point="tools/comms_service/service.py",
            keywords=["email", "calendar", "send"],
            description="Email and calendar API",
            type="export",
        )
        line = entry.to_inventory_line()
        assert "[export: comms-service]" in line
        assert "tools/comms_service/service.py" in line
        assert "Email and calendar API" in line
        assert "email" in line
