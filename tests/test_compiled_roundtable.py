"""Tests for cohort.compiled_roundtable -- single-call multi-persona discussions."""

from unittest.mock import MagicMock, patch

import pytest

from cohort.compiled_roundtable import (
    MAX_AGENTS,
    CompiledResult,
    _estimate_tokens,
    _load_persona,
    build_compiled_prompt,
    parse_compiled_response,
    run_compiled_roundtable,
)

# =====================================================================
# Fixtures
# =====================================================================

SAMPLE_RESPONSE_STRICT = """\
> **python_developer**:
> I think we should use connection pooling for the database layer.
> This prevents creating a new connection per request which would
> be expensive under load.

> **security_agent**:
> Connection pooling is fine but we need to ensure credentials
> aren't leaked in pool metadata. Use parameterized queries.

> **synthesis**:
> Agreement on connection pooling. Open question on credential handling.
"""

SAMPLE_RESPONSE_LOOSE = """\
> python_developer:
> Connection pooling makes sense for performance.

> security_agent:
> Agreed, but watch for credential exposure.

> synthesis:
> Both agents support pooling with security caveats.
"""

SAMPLE_RESPONSE_MISSING_AGENT = """\
> **python_developer**:
> I support this approach.
"""


# =====================================================================
# Test: _estimate_tokens
# =====================================================================

class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens("") == 0

    def test_basic(self):
        # 40 chars / 4 = 10 tokens
        assert _estimate_tokens("a" * 40) == 10


# =====================================================================
# Test: build_compiled_prompt
# =====================================================================

class TestBuildCompiledPrompt:
    @patch("cohort.compiled_roundtable._load_persona")
    def test_basic_build(self, mock_persona):
        mock_persona.return_value = "You are a test agent."
        system, user, tokens = build_compiled_prompt(
            agents=["agent_a", "agent_b"],
            topic="Should we refactor?",
        )
        assert "agent_a" in system
        assert "agent_b" in system
        assert "discussion" in system.lower()
        assert "Should we refactor?" in user
        assert tokens > 0

    @patch("cohort.compiled_roundtable._load_persona")
    def test_context_included(self, mock_persona):
        mock_persona.return_value = "Test persona."
        system, user, tokens = build_compiled_prompt(
            agents=["a"],
            topic="Test topic",
            context="Prior discussion about APIs.",
        )
        assert "Prior discussion about APIs" in user

    @patch("cohort.compiled_roundtable._load_persona")
    def test_rounds_1(self, mock_persona):
        mock_persona.return_value = "Test."
        _, user, _ = build_compiled_prompt(
            agents=["a"], topic="T", rounds=1,
        )
        assert "Round 1" in user
        assert "Round 2" not in user

    @patch("cohort.compiled_roundtable._load_persona")
    def test_rounds_3(self, mock_persona):
        mock_persona.return_value = "Test."
        _, user, _ = build_compiled_prompt(
            agents=["a"], topic="T", rounds=3,
        )
        assert "Round 1" in user
        assert "Round 2" in user
        assert "Round 3" in user

    def test_too_many_agents(self):
        agents = [f"agent_{i}" for i in range(MAX_AGENTS + 1)]
        with pytest.raises(ValueError, match="max"):
            build_compiled_prompt(agents=agents, topic="Test")

    @patch("cohort.compiled_roundtable._load_persona")
    def test_token_budget_exceeded(self, mock_persona):
        # Return a huge persona to blow the budget
        mock_persona.return_value = "x" * 200000
        with pytest.raises(ValueError, match="budget"):
            build_compiled_prompt(
                agents=["a", "b", "c"],
                topic="Test",
            )


# =====================================================================
# Test: parse_compiled_response
# =====================================================================

class TestParseCompiledResponse:
    def test_strict_format(self):
        agents = ["python_developer", "security_agent"]
        responses, synthesis = parse_compiled_response(
            SAMPLE_RESPONSE_STRICT, agents,
        )
        assert "python_developer" in responses
        assert "security_agent" in responses
        assert "connection pooling" in responses["python_developer"].lower()
        assert synthesis is not None
        assert "pooling" in synthesis.lower()

    def test_loose_format(self):
        agents = ["python_developer", "security_agent"]
        responses, synthesis = parse_compiled_response(
            SAMPLE_RESPONSE_LOOSE, agents,
        )
        assert "python_developer" in responses
        assert "security_agent" in responses
        assert synthesis is not None

    def test_missing_agent(self):
        agents = ["python_developer", "security_agent"]
        responses, synthesis = parse_compiled_response(
            SAMPLE_RESPONSE_MISSING_AGENT, agents,
        )
        assert "python_developer" in responses
        assert "security_agent" not in responses
        assert synthesis is None

    def test_empty_response(self):
        responses, synthesis = parse_compiled_response("", ["a"])
        assert responses == {}
        assert synthesis is None

    def test_strips_quote_markers(self):
        text = """\
> **test_agent**:
> Line one
> Line two
"""
        responses, _ = parse_compiled_response(text, ["test_agent"])
        assert "test_agent" in responses
        # Should not have leading "> " in the output
        assert not responses["test_agent"].startswith(">")


# =====================================================================
# Test: _load_persona
# =====================================================================

class TestLoadPersona:
    @patch("cohort.agent_store.get_store")
    def test_store_with_persona_text(self, mock_get_store):
        mock_config = MagicMock()
        mock_config.persona_text = "I am the Python expert."
        mock_store = MagicMock()
        mock_store.get.return_value = mock_config
        mock_get_store.return_value = mock_store

        result = _load_persona("python_developer")
        assert result == "I am the Python expert."

    @patch("cohort.agent_store.get_store")
    def test_store_no_persona_falls_back_to_prompt(self, mock_get_store):
        mock_config = MagicMock()
        mock_config.persona_text = ""
        mock_store = MagicMock()
        mock_store.get.return_value = mock_config
        mock_store.get_prompt.return_value = "Full prompt text here that is longer"
        mock_get_store.return_value = mock_store

        result = _load_persona("test_agent")
        assert "Full prompt text" in result

    @patch("cohort.agent_store.get_store")
    def test_store_no_agent(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get.return_value = None
        mock_get_store.return_value = mock_store

        # Falls through to personas module, then to default
        result = _load_persona("nonexistent_agent")
        assert "nonexistent_agent" in result

    @patch("cohort.agent_store.get_store")
    def test_store_not_initialized(self, mock_get_store):
        mock_get_store.return_value = None
        result = _load_persona("some_agent")
        assert "some_agent" in result


# =====================================================================
# Test: run_compiled_roundtable
# =====================================================================

class TestRunCompiledRoundtable:
    @patch("cohort.compiled_roundtable._call_ollama")
    @patch("cohort.compiled_roundtable._load_persona")
    def test_successful_run(self, mock_persona, mock_ollama):
        mock_persona.return_value = "Test persona."
        mock_ollama.return_value = (
            SAMPLE_RESPONSE_STRICT,
            {"model": "qwen3:30b-a3b", "latency_ms": 5000,
             "temperature": 0.3, "tokens_in": 500, "tokens_out": 300},
        )

        result = run_compiled_roundtable(
            agents=["python_developer", "security_agent"],
            topic="Connection pooling strategy",
        )

        assert isinstance(result, CompiledResult)
        assert result.error is None
        assert "python_developer" in result.agent_responses
        assert "security_agent" in result.agent_responses
        assert result.synthesis is not None
        assert result.metadata["model"] == "qwen3:30b-a3b"

    @patch("cohort.compiled_roundtable._call_ollama")
    @patch("cohort.compiled_roundtable._load_persona")
    def test_ollama_error(self, mock_persona, mock_ollama):
        mock_persona.return_value = "Test."
        mock_ollama.return_value = ("", {"error": "Ollama is not running"})

        result = run_compiled_roundtable(
            agents=["a"], topic="Test",
        )
        assert result.error is not None
        assert "not running" in result.error

    def test_too_many_agents_returns_error(self):
        agents = [f"agent_{i}" for i in range(MAX_AGENTS + 1)]
        result = run_compiled_roundtable(agents=agents, topic="Test")
        assert result.error is not None
        assert "max" in result.error.lower()

    @patch("cohort.compiled_roundtable._call_ollama")
    @patch("cohort.compiled_roundtable._load_persona")
    def test_partial_response(self, mock_persona, mock_ollama):
        mock_persona.return_value = "Test."
        mock_ollama.return_value = (
            SAMPLE_RESPONSE_MISSING_AGENT,
            {"model": "test", "latency_ms": 100,
             "temperature": 0.3, "tokens_in": 100, "tokens_out": 50},
        )

        result = run_compiled_roundtable(
            agents=["python_developer", "security_agent"],
            topic="Test",
        )

        assert result.error is None
        assert "python_developer" in result.agent_responses
        assert "security_agent" not in result.agent_responses
        assert result.metadata["agents_parsed"] == 1
        assert result.metadata["agents_expected"] == 2

    @patch("cohort.compiled_roundtable._call_ollama")
    @patch("cohort.compiled_roundtable._load_persona")
    def test_metadata_fields(self, mock_persona, mock_ollama):
        mock_persona.return_value = "P"
        mock_ollama.return_value = (
            SAMPLE_RESPONSE_STRICT,
            {"model": "m", "latency_ms": 42,
             "temperature": 0.3, "tokens_in": 10, "tokens_out": 20},
        )
        result = run_compiled_roundtable(
            agents=["python_developer", "security_agent"],
            topic="Test",
        )
        assert "input_token_estimate" in result.metadata
        assert "agents_expected" in result.metadata
        assert "agents_parsed" in result.metadata
        assert "has_synthesis" in result.metadata
        assert "response_chars" in result.metadata
