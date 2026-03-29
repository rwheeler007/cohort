"""Tests for cohort.routing_history -- routing feedback loop."""

import pytest

from cohort.routing_history import RoutingHistory, RoutingOutcome

# =====================================================================
# RoutingOutcome
# =====================================================================

class TestRoutingOutcome:
    def test_defaults(self):
        o = RoutingOutcome(
            task_keywords=["python", "api"],
            agent_id="python_developer",
            score_at_routing=0.8,
            outcome="success",
        )
        assert o.timestamp  # auto-populated
        assert o.reassigned_to is None


# =====================================================================
# RoutingHistory (in-memory)
# =====================================================================

class TestRoutingHistory:
    def test_record_and_retrieve(self):
        h = RoutingHistory()
        h.record(RoutingOutcome(
            task_keywords=["python", "api"],
            agent_id="python_developer",
            score_at_routing=0.8,
            outcome="success",
        ))
        results = h.get_outcomes_for_agent("python_developer")
        assert len(results) == 1
        assert results[0].outcome == "success"

    def test_keyword_filtering(self):
        h = RoutingHistory()
        h.record(RoutingOutcome(
            task_keywords=["python", "api"],
            agent_id="dev",
            score_at_routing=0.8,
            outcome="success",
        ))
        h.record(RoutingOutcome(
            task_keywords=["javascript", "frontend"],
            agent_id="dev",
            score_at_routing=0.6,
            outcome="failed",
        ))
        # Filter by python keywords
        results = h.get_outcomes_for_agent("dev", keywords=["python"])
        assert len(results) == 1
        assert results[0].outcome == "success"

    def test_success_rate(self):
        h = RoutingHistory()
        for outcome in ["success", "success", "failed"]:
            h.record(RoutingOutcome(
                task_keywords=["python"],
                agent_id="dev",
                score_at_routing=0.5,
                outcome=outcome,
            ))
        rate = h.success_rate("dev")
        assert rate == pytest.approx(2 / 3)

    def test_success_rate_no_data(self):
        h = RoutingHistory()
        assert h.success_rate("unknown_agent") is None

    def test_adjusted_score_no_history(self):
        h = RoutingHistory()
        # No data -> returns base_score unchanged
        assert h.adjusted_score(0.7, "dev", ["python"]) == 0.7

    def test_adjusted_score_with_history(self):
        h = RoutingHistory()
        # All successes -> should boost score
        for _ in range(5):
            h.record(RoutingOutcome(
                task_keywords=["python"],
                agent_id="dev",
                score_at_routing=0.5,
                outcome="success",
            ))
        adjusted = h.adjusted_score(0.5, "dev", ["python"])
        assert adjusted > 0.5  # boosted

    def test_adjusted_score_capped(self):
        h = RoutingHistory()
        for _ in range(10):
            h.record(RoutingOutcome(
                task_keywords=["python"],
                agent_id="dev",
                score_at_routing=0.9,
                outcome="success",
            ))
        # Max adjustment is +0.15
        adjusted = h.adjusted_score(0.9, "dev", ["python"])
        assert adjusted <= 1.0

    def test_max_entries_pruning(self):
        h = RoutingHistory(max_entries=5)
        for i in range(10):
            h.record(RoutingOutcome(
                task_keywords=["test"],
                agent_id="dev",
                score_at_routing=0.5,
                outcome="success",
            ))
        assert len(h._entries) == 5


# =====================================================================
# Persistence
# =====================================================================

class TestRoutingHistoryPersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "routing_history.json"
        h1 = RoutingHistory(path=path)
        h1.record(RoutingOutcome(
            task_keywords=["python"],
            agent_id="dev",
            score_at_routing=0.8,
            outcome="success",
        ))
        # Load from same file
        h2 = RoutingHistory(path=path)
        assert len(h2._entries) == 1
        assert h2._entries[0].agent_id == "dev"

    def test_missing_file_no_error(self, tmp_path):
        path = tmp_path / "nonexistent" / "routing_history.json"
        h = RoutingHistory(path=path)
        assert len(h._entries) == 0
