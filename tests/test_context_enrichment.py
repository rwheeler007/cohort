"""Tests for channel discussion context enrichment and self-review loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# =====================================================================
# Context enrichment tests
# =====================================================================

class TestFilterMessages:
    """Tests for _filter_messages()."""

    def test_filter_removes_noise_senders(self):
        from cohort.context_enrichment import _filter_messages

        messages = [
            {"sender": "system", "content": "Task created by the scheduler system."},
            {"sender": "worker", "content": "Processing started for the item now."},
            {"sender": "alice", "content": "We should use a REST API for this integration."},
        ]
        result = _filter_messages(messages)
        assert len(result) == 1
        assert result[0]["sender"] == "alice"

    def test_filter_removes_short_messages(self):
        from cohort.context_enrichment import _filter_messages

        messages = [
            {"sender": "alice", "content": "ok"},
            {"sender": "bob", "content": "This is a meaningful design discussion message."},
        ]
        result = _filter_messages(messages)
        assert len(result) == 1
        assert result[0]["sender"] == "bob"

    def test_filter_removes_bracket_prefixed(self):
        from cohort.context_enrichment import _filter_messages

        messages = [
            {"sender": "alice", "content": "[OK] status check completed successfully"},
            {"sender": "bob", "content": "The architecture should use event sourcing for this."},
        ]
        result = _filter_messages(messages)
        assert len(result) == 1
        assert result[0]["sender"] == "bob"


class TestTranscriptBudget:
    """Test that transcript is truncated at budget."""

    def test_transcript_truncated_at_budget(self):
        from cohort.context_enrichment import (
            _filter_messages,
            MAX_TRANSCRIPT_CHARS,
        )

        # Create messages that exceed the budget
        long_content = "x" * 900  # Each message ~900+ chars with sender prefix
        messages = [
            {"sender": f"user{i}", "content": long_content}
            for i in range(20)
        ]
        filtered = _filter_messages(messages)
        # Build transcript same way the main function does
        transcript_parts = []
        total_chars = 0
        for msg in filtered:
            line = f"{msg['sender']}: {msg['content'][:800]}"
            if total_chars + len(line) > MAX_TRANSCRIPT_CHARS:
                break
            transcript_parts.append(line)
            total_chars += len(line)

        # Should have been truncated before including all messages
        assert len(transcript_parts) < len(filtered)
        assert total_chars <= MAX_TRANSCRIPT_CHARS


class TestEnrichChannelDiscussion:
    """Tests for enrich_channel_discussion()."""

    def test_enrich_returns_empty_if_no_channel_id(self):
        from cohort.context_enrichment import enrich_channel_discussion

        result = enrich_channel_discussion(None, "some task", MagicMock())
        assert result == ""

    def test_enrich_returns_empty_if_no_messages(self):
        from cohort.context_enrichment import enrich_channel_discussion

        chat = MagicMock()
        chat.get_channel_messages.return_value = []
        result = enrich_channel_discussion("ch-1", "some task", chat)
        assert result == ""

    @patch("cohort.context_enrichment._call_local_llm", side_effect=RuntimeError("LLM down"))
    def test_enrich_returns_empty_on_llm_failure(self, mock_llm):
        from cohort.context_enrichment import enrich_channel_discussion

        chat = MagicMock()
        msg = MagicMock()
        msg.to_dict.return_value = {
            "sender": "alice",
            "content": "We should use PostgreSQL for this feature integration.",
        }
        chat.get_channel_messages.return_value = [msg]

        result = enrich_channel_discussion("ch-1", "build database layer", chat)
        assert result == ""

    @patch("cohort.context_enrichment._call_local_llm")
    def test_enrich_returns_result_on_success(self, mock_llm):
        from cohort.context_enrichment import enrich_channel_discussion

        mock_llm.return_value = "## Design Decisions\nUse PostgreSQL.\n"

        chat = MagicMock()
        msg = MagicMock()
        msg.to_dict.return_value = {
            "sender": "alice",
            "content": "We should use PostgreSQL for this feature integration.",
        }
        chat.get_channel_messages.return_value = [msg]

        result = enrich_channel_discussion("ch-1", "build database layer", chat)
        assert "Design Decisions" in result
        assert "PostgreSQL" in result


# =====================================================================
# Self-review tests
# =====================================================================

class TestSelfReview:
    """Tests for TaskExecutor._run_self_review()."""

    def _make_executor(self):
        """Create a minimal TaskExecutor for testing."""
        from cohort.task_executor import TaskExecutor

        return TaskExecutor(
            data_layer=MagicMock(),
            chat=MagicMock(),
            settings={},
        )

    def test_self_review_skipped_if_no_deliverables(self):
        executor = self._make_executor()
        task = {"deliverables": []}
        result = executor._run_self_review(task, "some output")
        assert result == {}

    @patch("cohort.local.router.LocalRouter.route")
    def test_self_review_report_in_output(self, mock_route):
        """Self-review report should be returned when LLM produces valid JSON."""
        import json

        report = {
            "verdict": "PASS",
            "deliverables": {"D1": {"status": "pass", "notes": "Looks good"}},
            "remaining_issues": [],
            "summary": "All criteria met.",
        }
        mock_result = MagicMock()
        mock_result.text = json.dumps(report)
        mock_route.return_value = mock_result

        executor = self._make_executor()
        task = {
            "deliverables": [{"id": "D1", "description": "Create the module"}],
        }
        result = executor._run_self_review(task, "Module created successfully.")
        assert result.get("verdict") == "PASS"
        assert "D1" in result.get("deliverables", {})

    @patch("cohort.local.router.LocalRouter.route", side_effect=RuntimeError("boom"))
    def test_self_review_returns_empty_dict_on_llm_failure(self, mock_route):
        executor = self._make_executor()
        task = {
            "deliverables": [{"id": "D1", "description": "Create the module"}],
        }
        result = executor._run_self_review(task, "Module created successfully.")
        assert result == {}
