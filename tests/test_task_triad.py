"""Tests for the trigger-action-outcome triad on Cohort tasks.

Covers:
  - TaskStore.create_task() with triad fields
  - TaskSchedule action_template / outcome_template
  - Triad validation gate in TaskExecutor
  - Briefing confirmation parsing with Tool/Outcome fields
  - extract_triad_from_brief() helper
  - Outcome verification on complete_task()
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cohort.task_store import TaskStore, TaskSchedule
from cohort.briefing import (
    parse_confirmation,
    extract_triad_from_brief,
    _infer_outcome_type,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path)


# =====================================================================
# TaskStore: create_task with triad
# =====================================================================

class TestCreateTaskTriad:
    """TaskStore.create_task() triad field handling."""

    def test_default_trigger_is_manual(self, store: TaskStore):
        task = store.create_task("test_agent", "Do something")
        trigger = task["trigger"]
        assert trigger["type"] == "manual"
        assert trigger["source"] == "user"
        assert trigger["fired_at"]  # non-empty ISO string

    def test_explicit_trigger(self, store: TaskStore):
        trigger = {"type": "scheduled", "source": "sched_abc123"}
        task = store.create_task("test_agent", "Do something", trigger=trigger)
        assert task["trigger"]["type"] == "scheduled"
        assert task["trigger"]["source"] == "sched_abc123"
        assert task["trigger"]["fired_at"]  # auto-filled

    def test_invalid_trigger_type_defaults_to_manual(self, store: TaskStore):
        trigger = {"type": "invalid_type", "source": "test"}
        task = store.create_task("test_agent", "Do something", trigger=trigger)
        assert task["trigger"]["type"] == "manual"

    def test_default_action_is_skeleton(self, store: TaskStore):
        task = store.create_task("test_agent", "Do something")
        action = task["action"]
        assert action["tool"] is None
        assert action["tool_ref"] is None
        assert action["parameters"] == {}

    def test_explicit_action(self, store: TaskStore):
        action = {"tool": "generate_report", "tool_ref": "tools/reporter.py"}
        task = store.create_task("test_agent", "Do something", action=action)
        assert task["action"]["tool"] == "generate_report"
        assert task["action"]["tool_ref"] == "tools/reporter.py"

    def test_default_outcome_is_skeleton(self, store: TaskStore):
        task = store.create_task("test_agent", "Do something")
        outcome = task["outcome"]
        assert outcome["type"] is None
        assert outcome["success_criteria"] is None
        assert outcome["artifact_ref"] is None
        assert outcome["verified"] is False

    def test_explicit_outcome(self, store: TaskStore):
        outcome = {
            "type": "report",
            "success_criteria": "HTML report generated",
        }
        task = store.create_task("test_agent", "Do something", outcome=outcome)
        assert task["outcome"]["type"] == "report"
        assert task["outcome"]["success_criteria"] == "HTML report generated"
        assert task["outcome"]["artifact_ref"] is None
        assert task["outcome"]["verified"] is False

    def test_full_triad(self, store: TaskStore):
        task = store.create_task(
            "test_agent",
            "Fetch RSS feeds",
            trigger={"type": "scheduled", "source": "sched_rss"},
            action={"tool": "fetch_rss", "tool_ref": "tools/rss_fetcher.py"},
            outcome={"type": "artifact", "success_criteria": "Feed data stored"},
        )
        assert task["trigger"]["type"] == "scheduled"
        assert task["action"]["tool"] == "fetch_rss"
        assert task["outcome"]["success_criteria"] == "Feed data stored"

    def test_triad_persists_to_disk(self, store: TaskStore):
        task = store.create_task(
            "test_agent",
            "Test persistence",
            trigger={"type": "mcp", "source": "mcp:boss"},
            action={"tool": "scan_code"},
            outcome={"type": "analysis", "success_criteria": "No critical issues"},
        )

        # Reload from disk
        store2 = TaskStore(store._data_dir)
        loaded = store2.get_task(task["task_id"])
        assert loaded is not None
        assert loaded["trigger"]["type"] == "mcp"
        assert loaded["action"]["tool"] == "scan_code"
        assert loaded["outcome"]["success_criteria"] == "No critical issues"


# =====================================================================
# TaskSchedule: action_template / outcome_template
# =====================================================================

class TestScheduleTemplates:
    """TaskSchedule triad templates and create_scheduled_task()."""

    def test_schedule_has_template_fields(self):
        sched = TaskSchedule(
            id="sched_test",
            agent_id="test_agent",
            description="Daily RSS fetch",
            priority="medium",
            schedule_type="cron",
            schedule_expr="0 9 * * *",
            action_template={"tool": "fetch_rss", "tool_ref": "tools/rss.py"},
            outcome_template={"type": "artifact", "success_criteria": "Feeds stored"},
        )
        assert sched.action_template["tool"] == "fetch_rss"
        assert sched.outcome_template["success_criteria"] == "Feeds stored"

    def test_schedule_templates_default_empty(self):
        sched = TaskSchedule(
            id="sched_test",
            agent_id="test_agent",
            description="Test",
            priority="medium",
            schedule_type="interval",
            schedule_expr="600",
        )
        assert sched.action_template == {}
        assert sched.outcome_template == {}

    def test_schedule_templates_roundtrip(self):
        sched = TaskSchedule(
            id="sched_test",
            agent_id="test_agent",
            description="Test",
            priority="medium",
            schedule_type="cron",
            schedule_expr="0 9 * * *",
            action_template={"tool": "analyze"},
            outcome_template={"type": "report"},
        )
        d = sched.to_dict()
        restored = TaskSchedule.from_dict(d)
        assert restored.action_template == {"tool": "analyze"}
        assert restored.outcome_template == {"type": "report"}

    def test_create_scheduled_task_populates_trigger(self, store: TaskStore):
        sched = TaskSchedule(
            id="sched_rss",
            agent_id="intel_agent",
            description="Fetch RSS",
            priority="medium",
            schedule_type="cron",
            schedule_expr="0 9 * * *",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        task = store.create_scheduled_task(sched)
        assert task["trigger"]["type"] == "scheduled"
        assert task["trigger"]["source"] == "sched_rss"

    def test_create_scheduled_task_uses_templates(self, store: TaskStore):
        sched = TaskSchedule(
            id="sched_report",
            agent_id="reporter",
            description="Generate report",
            priority="high",
            schedule_type="cron",
            schedule_expr="0 7 * * 1-5",
            action_template={"tool": "generate_report", "tool_ref": "tools/report.py"},
            outcome_template={"type": "report", "success_criteria": "HTML report exists"},
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        task = store.create_scheduled_task(sched)
        assert task["action"]["tool"] == "generate_report"
        assert task["outcome"]["success_criteria"] == "HTML report exists"
        # Scheduled tasks skip briefing -> assigned
        assert task["status"] == "assigned"


# =====================================================================
# Triad validation gate
# =====================================================================

class TestTriadValidation:
    """TaskExecutor._validate_triad() gate."""

    def _make_executor(self):
        from cohort.task_executor import TaskExecutor
        executor = TaskExecutor(
            data_layer=MagicMock(),
            chat=MagicMock(),
            settings={},
        )
        return executor

    def test_full_triad_passes(self):
        executor = self._make_executor()
        task = {
            "trigger": {"type": "manual", "source": "user"},
            "action": {"tool": "generate_report"},
            "outcome": {"success_criteria": "Report generated"},
        }
        warnings = executor._validate_triad(task)
        assert warnings == []

    def test_missing_trigger(self):
        executor = self._make_executor()
        task = {
            "trigger": {},
            "action": {"tool": "test"},
            "outcome": {"success_criteria": "done"},
        }
        warnings = executor._validate_triad(task)
        assert "Missing trigger type" in warnings

    def test_missing_action(self):
        executor = self._make_executor()
        task = {
            "trigger": {"type": "manual"},
            "action": {"tool": None},
            "outcome": {"success_criteria": "done"},
        }
        warnings = executor._validate_triad(task)
        assert "Missing action tool binding" in warnings

    def test_missing_outcome(self):
        executor = self._make_executor()
        task = {
            "trigger": {"type": "manual"},
            "action": {"tool": "test"},
            "outcome": {},
        }
        warnings = executor._validate_triad(task)
        assert "Missing outcome success criteria" in warnings

    def test_all_missing(self):
        executor = self._make_executor()
        task = {}  # no triad at all (legacy task)
        warnings = executor._validate_triad(task)
        assert len(warnings) == 3

    def test_none_triad_fields(self):
        executor = self._make_executor()
        task = {"trigger": None, "action": None, "outcome": None}
        warnings = executor._validate_triad(task)
        assert len(warnings) == 3


# =====================================================================
# Briefing confirmation parsing
# =====================================================================

class TestConfirmationParsing:
    """parse_confirmation() with Tool and Outcome fields."""

    def test_classic_confirmation_still_works(self):
        msg = (
            "Sounds good.\n\n"
            "---TASK_CONFIRMED---\n"
            "Goal: Fetch the RSS feeds\n"
            "Approach: Use the RSS fetcher script\n"
            "Scope: tools/rss_fetcher.py\n"
            "Acceptance: Feed data stored in data/feeds/\n"
            "---END_CONFIRMED---\n"
        )
        result = parse_confirmation(msg)
        assert result is not None
        assert result["goal"] == "Fetch the RSS feeds"
        assert "tool" not in result  # old-style, no tool field

    def test_confirmation_with_tool_and_outcome(self):
        msg = (
            "---TASK_CONFIRMED---\n"
            "Goal: Generate daily code health report\n"
            "Approach: Run code health scanner on all Python files\n"
            "Scope: src/ directory\n"
            "Acceptance: HTML report saved to data/reports/\n"
            "Tool: code_health_scan via tools/code_health/scanner.py\n"
            "Outcome: HTML report file at data/reports/code_health_YYYY-MM-DD.html\n"
            "---END_CONFIRMED---\n"
        )
        result = parse_confirmation(msg)
        assert result is not None
        assert result["goal"] == "Generate daily code health report"
        assert result["tool"] == "code_health_scan via tools/code_health/scanner.py"
        assert "HTML report file" in result["outcome"]

    def test_confirmation_with_only_tool(self):
        msg = (
            "---TASK_CONFIRMED---\n"
            "Goal: Analyze security\n"
            "Tool: bandit_scan\n"
            "---END_CONFIRMED---\n"
        )
        result = parse_confirmation(msg)
        assert result is not None
        assert result["tool"] == "bandit_scan"


# =====================================================================
# extract_triad_from_brief
# =====================================================================

class TestExtractTriadFromBrief:
    """extract_triad_from_brief() helper."""

    def test_extracts_action_from_tool(self):
        brief = {"goal": "Test", "tool": "fetch_rss"}
        action, outcome = extract_triad_from_brief(brief)
        assert action is not None
        assert action["tool"] == "fetch_rss"

    def test_extracts_outcome_from_acceptance(self):
        brief = {"goal": "Test", "acceptance": "Data stored in db"}
        action, outcome = extract_triad_from_brief(brief)
        assert action is None
        assert outcome is not None
        assert outcome["success_criteria"] == "Data stored in db"

    def test_extracts_both(self):
        brief = {
            "goal": "Test",
            "tool": "run_analysis",
            "acceptance": "Report generated",
            "outcome": "Analysis report in data/reports/",
        }
        action, outcome = extract_triad_from_brief(brief)
        assert action["tool"] == "run_analysis"
        assert outcome["success_criteria"] == "Report generated"

    def test_no_triad_fields(self):
        brief = {"goal": "Test", "approach": "Just do it"}
        action, outcome = extract_triad_from_brief(brief)
        assert action is None
        assert outcome is None

    def test_infer_outcome_type_report(self):
        assert _infer_outcome_type("Generate weekly summary report") == "report"

    def test_infer_outcome_type_artifact(self):
        assert _infer_outcome_type("Create PDF document") == "artifact"

    def test_infer_outcome_type_state_change(self):
        assert _infer_outcome_type("Update the database records") == "state_change"

    def test_infer_outcome_type_notification(self):
        assert _infer_outcome_type("Send alert email to team") == "notification"

    def test_infer_outcome_type_analysis(self):
        assert _infer_outcome_type("Scan codebase for vulnerabilities") == "analysis"

    def test_infer_outcome_type_default(self):
        assert _infer_outcome_type("Something vague") == "artifact"


# =====================================================================
# Outcome verification on complete_task()
# =====================================================================

class TestOutcomeVerification:
    """complete_task() outcome verification logic."""

    def test_verified_true_with_artifact_and_criteria(self, store: TaskStore):
        task = store.create_task(
            "test_agent", "Test task",
            outcome={"type": "report", "success_criteria": "Report exists"},
        )
        completed = store.complete_task(
            task["task_id"],
            output={"content": "Report content here"},
            artifact_ref="data/reports/test.html",
        )
        assert completed["outcome"]["verified"] is True
        assert completed["outcome"]["artifact_ref"] == "data/reports/test.html"

    def test_verified_true_with_content_and_criteria(self, store: TaskStore):
        task = store.create_task(
            "test_agent", "Test task",
            outcome={"type": "analysis", "success_criteria": "Analysis complete"},
        )
        completed = store.complete_task(
            task["task_id"],
            output={"content": "Analysis results: all good"},
        )
        assert completed["outcome"]["verified"] is True

    def test_verified_false_without_criteria(self, store: TaskStore):
        task = store.create_task("test_agent", "Test task")
        completed = store.complete_task(
            task["task_id"],
            output={"content": "Done"},
            artifact_ref="some/file.txt",
        )
        assert completed["outcome"]["verified"] is False

    def test_verified_false_without_output(self, store: TaskStore):
        task = store.create_task(
            "test_agent", "Test task",
            outcome={"type": "report", "success_criteria": "Report exists"},
        )
        completed = store.complete_task(task["task_id"])
        assert completed["outcome"]["verified"] is False


# =====================================================================
# Briefing prompt: triad-focused directive and pre-filled data
# =====================================================================

class TestBriefingPromptTriad:
    """build_briefing_prompt() surfaces pre-filled triad data."""

    def test_directive_mentions_triad(self):
        from cohort.briefing import BRIEFING_DIRECTIVE
        assert "TRIGGER-ACTION-OUTCOME" in BRIEFING_DIRECTIVE
        assert "Tool and Outcome fields are NOT optional" in BRIEFING_DIRECTIVE

    def test_prompt_includes_prefilled_action(self):
        from cohort.briefing import build_briefing_prompt
        task = {
            "agent_id": "test_agent",
            "description": "Run security scan",
            "priority": "high",
            "action": {"tool": "bandit_scan", "tool_ref": "tools/bandit.py"},
            "outcome": {},
        }
        prompt = build_briefing_prompt("You are a test agent.", task)
        assert "Action: bandit_scan (tools/bandit.py)" in prompt

    def test_prompt_includes_prefilled_outcome(self):
        from cohort.briefing import build_briefing_prompt
        task = {
            "agent_id": "test_agent",
            "description": "Generate report",
            "priority": "medium",
            "action": {},
            "outcome": {"success_criteria": "HTML report at data/reports/"},
        }
        prompt = build_briefing_prompt("You are a test agent.", task)
        assert "Expected Outcome: HTML report at data/reports/" in prompt

    def test_prompt_includes_both(self):
        from cohort.briefing import build_briefing_prompt
        task = {
            "agent_id": "test_agent",
            "description": "Fetch RSS",
            "priority": "medium",
            "action": {"tool": "fetch_rss"},
            "outcome": {"success_criteria": "Feed data stored"},
        }
        prompt = build_briefing_prompt("You are a test agent.", task)
        assert "Action: fetch_rss" in prompt
        assert "Expected Outcome: Feed data stored" in prompt

    def test_prompt_omits_empty_triad(self):
        from cohort.briefing import build_briefing_prompt
        task = {
            "agent_id": "test_agent",
            "description": "Do something",
            "priority": "medium",
        }
        prompt = build_briefing_prompt("You are a test agent.", task)
        assert "Action:" not in prompt
        assert "Expected Outcome:" not in prompt
