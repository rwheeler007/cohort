"""Tests for cohort.review_pipeline."""

import json
from pathlib import Path

import pytest

from cohort.review_pipeline import (
    PipelineVerdict,
    ReviewPipeline,
    ReviewResult,
    ReviewStage,
    default_stages,
    parse_review_response,
)


# =====================================================================
# Fixtures
# =====================================================================

def _make_stage(role="test_gate", agent_id="test_agent", required=True):
    return ReviewStage(
        role=role,
        agent_id=agent_id,
        description=f"Test stage: {role}",
        required=required,
        system_prompt="You are a test reviewer.",
        review_prompt_template="Review:\n{description}\n{code}",
    )


def _make_reviewer(verdict="approve", issues=None):
    """Return a reviewer_fn that always returns a JSON response with the given verdict."""
    def reviewer_fn(stage, system_prompt, user_prompt):
        resp = {
            "verdict": verdict,
            "summary": f"Test {verdict} response",
            "issues": issues or [],
            "rejection_feedback": "Fix it" if verdict == "reject" else "",
        }
        return json.dumps(resp)
    return reviewer_fn


def _make_failing_reviewer():
    """Return a reviewer_fn that returns None (simulating LLM failure)."""
    def reviewer_fn(stage, system_prompt, user_prompt):
        return None
    return reviewer_fn


def _make_mixed_reviewer(verdicts):
    """Return a reviewer_fn that returns different verdicts per call."""
    call_count = {"n": 0}
    def reviewer_fn(stage, system_prompt, user_prompt):
        idx = call_count["n"]
        call_count["n"] += 1
        v = verdicts[idx] if idx < len(verdicts) else "approve"
        return json.dumps({"verdict": v, "summary": f"Review {idx}", "rejection_feedback": f"Feedback {idx}"})
    return reviewer_fn


TASK_CONTEXT = {
    "description": "Add user authentication",
    "deliverables": "D1: Login endpoint returns JWT",
    "code": "def login(): return jwt.encode(...)",
    "self_review": "All pass",
}


# =====================================================================
# ReviewStage
# =====================================================================

class TestReviewStage:
    def test_roundtrip(self):
        stage = _make_stage()
        d = stage.to_dict()
        restored = ReviewStage.from_dict(d)
        assert restored.role == "test_gate"
        assert restored.agent_id == "test_agent"


# =====================================================================
# ReviewResult
# =====================================================================

class TestReviewResult:
    def test_roundtrip(self):
        result = ReviewResult(
            stage_role="quality",
            agent_id="qa",
            verdict="approve",
            summary="Looks good",
            issues=[{"severity": "warning", "description": "minor"}],
        )
        d = result.to_dict()
        restored = ReviewResult.from_dict(d)
        assert restored.verdict == "approve"
        assert len(restored.issues) == 1


# =====================================================================
# parse_review_response
# =====================================================================

class TestParseReviewResponse:
    def test_plain_json(self):
        text = '{"verdict": "approve", "summary": "OK"}'
        result = parse_review_response(text, "agent_a")
        assert result is not None
        assert result.verdict == "approve"

    def test_markdown_fenced(self):
        text = '```json\n{"verdict": "reject", "summary": "Bad"}\n```'
        result = parse_review_response(text, "agent_b")
        assert result is not None
        assert result.verdict == "reject"

    def test_embedded_json(self):
        text = 'Here is my review:\n{"verdict": "needs_work", "summary": "Almost"}\nThanks!'
        result = parse_review_response(text, "agent_c")
        assert result is not None
        assert result.verdict == "needs_work"

    def test_unparseable(self):
        text = "I don't know how to respond in JSON"
        result = parse_review_response(text, "agent_d")
        assert result is None

    def test_defaults_to_needs_work(self):
        text = '{"summary": "no verdict field"}'
        result = parse_review_response(text, "agent_e")
        assert result is not None
        assert result.verdict == "needs_work"


# =====================================================================
# ReviewPipeline.run_reviews
# =====================================================================

class TestRunReviews:
    def test_all_approve(self):
        pipeline = ReviewPipeline(stages=[
            _make_stage("gate1", "a1"),
            _make_stage("gate2", "a2"),
            _make_stage("gate3", "a3"),
        ])
        results = pipeline.run_reviews(TASK_CONTEXT, _make_reviewer("approve"))
        assert len(results) == 3
        assert all(r.verdict == "approve" for r in results)

    def test_mixed_verdicts(self):
        pipeline = ReviewPipeline(stages=[
            _make_stage("gate1", "a1"),
            _make_stage("gate2", "a2"),
            _make_stage("gate3", "a3"),
        ])
        results = pipeline.run_reviews(
            TASK_CONTEXT,
            _make_mixed_reviewer(["approve", "reject", "approve"]),
        )
        assert len(results) == 3
        verdicts = [r.verdict for r in results]
        assert verdicts == ["approve", "reject", "approve"]

    def test_failed_reviewer_skipped(self):
        pipeline = ReviewPipeline(stages=[
            _make_stage("gate1", "a1"),
            _make_stage("gate2", "a2"),
        ])
        results = pipeline.run_reviews(TASK_CONTEXT, _make_failing_reviewer())
        assert len(results) == 0

    def test_stage_role_set_on_result(self):
        pipeline = ReviewPipeline(stages=[_make_stage("my_role", "a1")])
        results = pipeline.run_reviews(TASK_CONTEXT, _make_reviewer("approve"))
        assert results[0].stage_role == "my_role"


# =====================================================================
# ReviewPipeline.evaluate_verdict
# =====================================================================

class TestEvaluateVerdict:
    def test_all_approve(self):
        pipeline = ReviewPipeline(majority_threshold=2)
        reviews = [
            ReviewResult(stage_role="a", agent_id="1", verdict="approve"),
            ReviewResult(stage_role="b", agent_id="2", verdict="approve"),
            ReviewResult(stage_role="c", agent_id="3", verdict="approve"),
        ]
        assert pipeline.evaluate_verdict(reviews) == PipelineVerdict.APPROVED

    def test_majority_reject(self):
        pipeline = ReviewPipeline(majority_threshold=2)
        reviews = [
            ReviewResult(stage_role="a", agent_id="1", verdict="reject"),
            ReviewResult(stage_role="b", agent_id="2", verdict="needs_work"),
            ReviewResult(stage_role="c", agent_id="3", verdict="approve"),
        ]
        assert pipeline.evaluate_verdict(reviews) == PipelineVerdict.REJECTED

    def test_single_dissent_overridden(self):
        pipeline = ReviewPipeline(majority_threshold=2)
        reviews = [
            ReviewResult(stage_role="a", agent_id="1", verdict="reject"),
            ReviewResult(stage_role="b", agent_id="2", verdict="approve"),
            ReviewResult(stage_role="c", agent_id="3", verdict="approve"),
        ]
        assert pipeline.evaluate_verdict(reviews) == PipelineVerdict.APPROVED

    def test_incomplete_too_few_reviews(self):
        pipeline = ReviewPipeline(majority_threshold=2)
        reviews = [
            ReviewResult(stage_role="a", agent_id="1", verdict="approve"),
        ]
        assert pipeline.evaluate_verdict(reviews) == PipelineVerdict.INCOMPLETE

    def test_custom_threshold(self):
        pipeline = ReviewPipeline(majority_threshold=3)
        reviews = [
            ReviewResult(stage_role="a", agent_id="1", verdict="reject"),
            ReviewResult(stage_role="b", agent_id="2", verdict="reject"),
            ReviewResult(stage_role="c", agent_id="3", verdict="approve"),
        ]
        # 2 rejections < threshold of 3 -> approved
        assert pipeline.evaluate_verdict(reviews) == PipelineVerdict.APPROVED


# =====================================================================
# collect_rejection_feedback
# =====================================================================

class TestCollectRejectionFeedback:
    def test_combines_feedback(self):
        pipeline = ReviewPipeline()
        reviews = [
            ReviewResult(stage_role="a", agent_id="qa", verdict="reject", rejection_feedback="Missing tests"),
            ReviewResult(stage_role="b", agent_id="sec", verdict="approve"),
            ReviewResult(stage_role="c", agent_id="arch", verdict="needs_work", summary="Needs refactor"),
        ]
        feedback = pipeline.collect_rejection_feedback(reviews)
        assert "[qa] Missing tests" in feedback
        assert "[arch] Needs refactor" in feedback
        assert "sec" not in feedback

    def test_no_rejections(self):
        pipeline = ReviewPipeline()
        reviews = [
            ReviewResult(stage_role="a", agent_id="qa", verdict="approve"),
        ]
        feedback = pipeline.collect_rejection_feedback(reviews)
        assert "No feedback" in feedback


# =====================================================================
# Serialization & persistence
# =====================================================================

class TestPipelineSerialization:
    def test_roundtrip(self):
        pipeline = ReviewPipeline(
            stages=[_make_stage("g1", "a1"), _make_stage("g2", "a2")],
            majority_threshold=3,
        )
        d = pipeline.to_dict()
        restored = ReviewPipeline.from_dict(d)
        assert len(restored.stages) == 2
        assert restored.majority_threshold == 3

    def test_save_and_load_config(self, tmp_path):
        pipeline = ReviewPipeline(
            stages=[_make_stage("g1", "a1")],
            majority_threshold=1,
        )
        pipeline.save_config(tmp_path)
        loaded = ReviewPipeline.load_config(tmp_path)
        assert len(loaded.stages) == 1
        assert loaded.majority_threshold == 1

    def test_load_missing_config_uses_defaults(self, tmp_path):
        loaded = ReviewPipeline.load_config(tmp_path)
        assert len(loaded.stages) == 3  # default_stages


# =====================================================================
# default_stages
# =====================================================================

class TestDefaultStages:
    def test_has_three_stages(self):
        stages = default_stages()
        assert len(stages) == 3

    def test_roles(self):
        stages = default_stages()
        roles = [s.role for s in stages]
        assert "deliverables_gate" in roles
        assert "quality_review" in roles
        assert "architecture_review" in roles

    def test_architecture_optional(self):
        stages = default_stages()
        arch = [s for s in stages if s.role == "architecture_review"][0]
        assert arch.required is False
