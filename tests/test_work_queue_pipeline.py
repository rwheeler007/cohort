"""Tests for work queue pipeline wiring: deliverables gate + auto-review callback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cohort.work_queue import WorkQueue, WorkItem


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def wq(tmp_path: Path) -> WorkQueue:
    """Fresh in-memory WorkQueue backed by tmp_path."""
    return WorkQueue(tmp_path)


def _enqueue_with_deliverables(wq: WorkQueue, deliverables=None) -> WorkItem:
    """Helper: enqueue an item, optionally with deliverables."""
    item = wq.enqueue(description="Test item", requester="test", priority="medium")
    if deliverables is not None:
        item.deliverables = deliverables
        wq._save_to_disk()
    return item


# =====================================================================
# Deliverables gate tests
# =====================================================================

def test_claim_next_blocked_if_deliverables_not_finalized(wq: WorkQueue) -> None:
    """Item with deliverables defined but not finalized is skipped by claim_next."""
    tracker = MagicMock()
    tracker.is_finalized.return_value = False
    wq.set_deliverable_tracker(tracker)

    item = _enqueue_with_deliverables(wq, deliverables=[{"id": "d1", "description": "check"}])

    result = wq.claim_next()
    assert result["item"] is None
    tracker.is_finalized.assert_called_once_with(item.id)


def test_claim_next_passes_if_deliverables_finalized(wq: WorkQueue) -> None:
    """Item with finalized deliverables is claimable."""
    tracker = MagicMock()
    tracker.is_finalized.return_value = True
    wq.set_deliverable_tracker(tracker)

    _enqueue_with_deliverables(wq, deliverables=[{"id": "d1", "description": "check"}])

    result = wq.claim_next()
    assert result["item"] is not None
    assert result["item"]["status"] == "active"


def test_claim_next_passes_if_no_deliverables(wq: WorkQueue) -> None:
    """Item without deliverables passes the gate (backward compat)."""
    tracker = MagicMock()
    tracker.is_finalized.return_value = False
    wq.set_deliverable_tracker(tracker)

    wq.enqueue(description="No deliverables", requester="test")

    result = wq.claim_next()
    assert result["item"] is not None
    # Tracker should not even be consulted since deliverables list is empty
    tracker.is_finalized.assert_not_called()


def test_claim_next_passes_if_no_tracker_wired(wq: WorkQueue) -> None:
    """Without set_deliverable_tracker, gate is open by default."""
    _enqueue_with_deliverables(wq, deliverables=[{"id": "d1", "description": "check"}])

    result = wq.claim_next()
    assert result["item"] is not None
    assert result["item"]["status"] == "active"


# =====================================================================
# On-complete callback tests
# =====================================================================

def test_submit_for_review_fires_callback(wq: WorkQueue) -> None:
    """submit_for_review() invokes the on_complete callback with the item."""
    callback = MagicMock()
    wq.set_on_complete_callback(callback)

    item = wq.enqueue(description="Reviewable", requester="test")
    wq.claim_next()  # -> active
    wq.submit_for_review(item.id)

    callback.assert_called_once()
    called_item = callback.call_args[0][0]
    assert called_item.id == item.id
    assert called_item.status == "reviewing"


def test_callback_exception_does_not_crash_queue(wq: WorkQueue) -> None:
    """If the callback raises, submit_for_review still succeeds."""
    callback = MagicMock(side_effect=RuntimeError("boom"))
    wq.set_on_complete_callback(callback)

    item = wq.enqueue(description="Safe", requester="test")
    wq.claim_next()
    result = wq.submit_for_review(item.id)

    assert result is not None
    assert result.status == "reviewing"
    callback.assert_called_once()


# =====================================================================
# Auto-review verdict tests (server-level function)
# =====================================================================

def test_auto_review_approved_stays_reviewing(wq: WorkQueue) -> None:
    """APPROVED verdict leaves the item in reviewing state."""
    item = wq.enqueue(description="Approve me", requester="test")
    wq.claim_next()
    wq.submit_for_review(item.id)

    from cohort.review_pipeline import PipelineVerdict, ReviewResult

    mock_reviews = [
        ReviewResult(stage_role="gate", agent_id="r1", verdict="approve",
                     summary="Good", issues=[])
    ]

    mock_pipeline = MagicMock()
    mock_pipeline.run_reviews.return_value = mock_reviews
    mock_pipeline.evaluate_verdict.return_value = PipelineVerdict.APPROVED

    with patch("cohort.server._review_pipeline", mock_pipeline), \
         patch("cohort.server._work_queue", wq), \
         patch("cohort.server._broadcast_work_queue"):
        from cohort.server import _run_auto_review_work_item
        _run_auto_review_work_item(item.to_dict())

    refreshed = wq.get_item(item.id)
    assert refreshed.status == "reviewing"


def test_auto_review_rejected_requeues(wq: WorkQueue) -> None:
    """REJECTED verdict rejects then requeues the item."""
    item = wq.enqueue(description="Reject me", requester="test")
    wq.claim_next()
    wq.submit_for_review(item.id)

    from cohort.review_pipeline import PipelineVerdict, ReviewResult

    mock_reviews = [
        ReviewResult(stage_role="gate", agent_id="r1", verdict="reject",
                     summary="Bad", issues=["bug"],
                     rejection_feedback="Fix the bug")
    ]

    mock_pipeline = MagicMock()
    mock_pipeline.run_reviews.return_value = mock_reviews
    mock_pipeline.evaluate_verdict.return_value = PipelineVerdict.REJECTED
    mock_pipeline.collect_rejection_feedback.return_value = "Fix the bug"

    with patch("cohort.server._review_pipeline", mock_pipeline), \
         patch("cohort.server._work_queue", wq), \
         patch("cohort.server._broadcast_work_queue"):
        from cohort.server import _run_auto_review_work_item
        _run_auto_review_work_item(item.to_dict())

    # Original item should be rejected
    original = wq.get_item(item.id)
    assert original.status == "rejected"

    # A new requeued item should exist
    requeued = [i for i in wq.list_items(status="queued") if i.requeued_from == item.id]
    assert len(requeued) == 1
    assert "Fix the bug" in requeued[0].metadata.get("requeue_feedback", "")


def test_auto_review_incomplete_no_transition(wq: WorkQueue) -> None:
    """INCOMPLETE verdict leaves item in reviewing, no transition."""
    item = wq.enqueue(description="Incomplete", requester="test")
    wq.claim_next()
    wq.submit_for_review(item.id)

    from cohort.review_pipeline import PipelineVerdict

    mock_pipeline = MagicMock()
    mock_pipeline.run_reviews.return_value = []
    mock_pipeline.evaluate_verdict.return_value = PipelineVerdict.INCOMPLETE

    with patch("cohort.server._review_pipeline", mock_pipeline), \
         patch("cohort.server._work_queue", wq), \
         patch("cohort.server._broadcast_work_queue"):
        from cohort.server import _run_auto_review_work_item
        _run_auto_review_work_item(item.to_dict())

    refreshed = wq.get_item(item.id)
    assert refreshed.status == "reviewing"
