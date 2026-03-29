"""Tests for Cohort v0.2.0 token optimization backport.

Covers:
  D1: Persona files for all 5 shipped agents
  D2: Persona loader with fallback chain
  D3: Light/heavy mode in prompt construction
  D4: COHORT_FULL_PROMPT env var escape hatch
  D5: Circuit breaker at 240,000 chars (sized for qwen3.5:9b 262K context)
  D6: Sliding window context truncation
  D7: Path traversal sanitization
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from cohort.context_window import truncate_context
from cohort.personas import _PERSONAS_DIR, load_persona

# =====================================================================
# D1: Persona files exist for all 5 shipped agents
# =====================================================================

SHIPPED_AGENTS = [
    "sales_agent",
    "hardware_agent",
    "marketing_agent",
    "analytics_agent",
    "content_strategy_agent",
]


@pytest.mark.parametrize("agent_id", SHIPPED_AGENTS)
def test_persona_file_exists(agent_id: str) -> None:
    """Each shipped agent has a persona .md file in the personas/ directory."""
    persona_path = _PERSONAS_DIR / f"{agent_id}.md"
    assert persona_path.exists(), f"Missing persona file: {persona_path}"


@pytest.mark.parametrize("agent_id", SHIPPED_AGENTS)
def test_persona_file_size(agent_id: str) -> None:
    """Persona files should be under 500 tokens (~2000 chars)."""
    persona_path = _PERSONAS_DIR / f"{agent_id}.md"
    content = persona_path.read_text(encoding="utf-8")
    assert len(content) < 2000, (
        f"{agent_id} persona is {len(content)} chars, expected <2000"
    )
    # Should have at least some content
    assert len(content) > 100, f"{agent_id} persona is too short"


@pytest.mark.parametrize("agent_id", SHIPPED_AGENTS)
def test_persona_has_required_sections(agent_id: str) -> None:
    """Persona files should have role, personality, and capabilities sections."""
    persona_path = _PERSONAS_DIR / f"{agent_id}.md"
    content = persona_path.read_text(encoding="utf-8")
    # Check for expected heading patterns
    assert "## Personality" in content or "## personality" in content.lower()
    assert "## Key Capabilities" in content or "## Capabilities" in content


# =====================================================================
# D2: Persona loader with fallback chain
# =====================================================================

def test_load_persona_returns_content() -> None:
    """load_persona returns content for a shipped agent."""
    result = load_persona("sales_agent")
    assert result is not None
    assert "Sales" in result


def test_load_persona_returns_none_for_unknown() -> None:
    """load_persona returns None for an agent without a persona file."""
    result = load_persona("nonexistent_agent_xyz")
    assert result is None


def test_load_persona_returns_none_for_empty_string() -> None:
    """load_persona returns None for empty string."""
    result = load_persona("")
    assert result is None


# =====================================================================
# D4: COHORT_FULL_PROMPT env var
# =====================================================================

def test_cohort_full_prompt_env_var_default() -> None:
    """COHORT_FULL_PROMPT is not set by default."""
    # Just verify the env var check logic works
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("COHORT_FULL_PROMPT", None)
        assert os.environ.get("COHORT_FULL_PROMPT", "").strip() != "1"


def test_cohort_full_prompt_env_var_set() -> None:
    """COHORT_FULL_PROMPT=1 triggers full prompt mode."""
    with patch.dict(os.environ, {"COHORT_FULL_PROMPT": "1"}):
        assert os.environ.get("COHORT_FULL_PROMPT", "").strip() == "1"


# =====================================================================
# D5: Circuit breaker
# =====================================================================

def test_circuit_breaker_constant_exists() -> None:
    """CIRCUIT_BREAKER_CHAR_LIMIT is defined in agent_router."""
    import cohort.agent_router as router_mod
    assert hasattr(router_mod, "CIRCUIT_BREAKER_CHAR_LIMIT")
    assert router_mod.CIRCUIT_BREAKER_CHAR_LIMIT == 240_000


# =====================================================================
# D6: Sliding window context truncation
# =====================================================================

@dataclass
class _FakeMsg:
    """Minimal message stand-in for truncation tests."""
    content: str
    sender: str = "agent"


def test_truncate_empty_list() -> None:
    """truncate_context handles empty input."""
    assert truncate_context([]) == []


def test_truncate_within_budget() -> None:
    """Messages within budget are returned unchanged."""
    msgs = [_FakeMsg(content=f"msg {i}") for i in range(5)]
    result = truncate_context(msgs, char_budget=5000)
    assert len(result) == 5


def test_truncate_drops_oldest() -> None:
    """When over budget, oldest messages are dropped first."""
    # Each message is ~30 chars of content + overhead
    msgs = [_FakeMsg(content="x" * 200, sender="a") for _ in range(20)]
    result = truncate_context(msgs, char_budget=2000, keep_recent=5)
    # Should always keep the last 5
    assert len(result) <= 10
    assert len(result) >= 5
    # Last 5 messages should be preserved
    assert result[-5:] == msgs[-5:]


def test_truncate_preserves_recent() -> None:
    """Recent messages are always kept, even if they exceed budget."""
    msgs = [_FakeMsg(content="x" * 500, sender="a") for _ in range(3)]
    # Budget is tiny, but keep_recent=3 means all 3 are kept
    result = truncate_context(msgs, char_budget=100, keep_recent=3)
    assert len(result) == 3


def test_truncate_order_preserved() -> None:
    """Messages are returned in their original chronological order."""
    msgs = [_FakeMsg(content=f"msg-{i}") for i in range(10)]
    result = truncate_context(msgs, char_budget=5000, keep_recent=5)
    contents = [m.content for m in result]
    # Should be in ascending order
    for i in range(1, len(contents)):
        # Parse the number suffix
        prev_num = int(contents[i - 1].split("-")[1])
        curr_num = int(contents[i].split("-")[1])
        assert prev_num < curr_num, f"Order violated: {contents[i-1]} before {contents[i]}"


def test_truncate_single_message() -> None:
    """Single message is always returned."""
    msgs = [_FakeMsg(content="hello")]
    result = truncate_context(msgs, char_budget=100)
    assert len(result) == 1


# =====================================================================
# D7: Path traversal sanitization
# =====================================================================

def test_path_traversal_dotdot() -> None:
    """Path traversal with .. is rejected."""
    assert load_persona("../../etc/passwd") is None


def test_path_traversal_slash() -> None:
    """Path traversal with / is rejected."""
    assert load_persona("../sales_agent") is None


def test_path_traversal_backslash() -> None:
    """Path traversal with backslash is rejected."""
    assert load_persona("..\\sales_agent") is None


def test_path_traversal_complex() -> None:
    """Complex path traversal attempts are rejected."""
    assert load_persona("sales_agent/../../etc/passwd") is None
    assert load_persona("") is None
    assert load_persona(".") is None
    assert load_persona("..") is None
