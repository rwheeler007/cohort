"""Tests for cohort.deliverables."""


import pytest

from cohort.deliverables import (
    MAX_DELIVERABLES_PER_ITEM,
    MAX_DESCRIPTION_LENGTH,
    Deliverable,
    DeliverableTracker,
    validate_deliverables,
)

# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def tracker(tmp_path):
    return DeliverableTracker(data_dir=tmp_path)


SAMPLE_DELIVERABLES = [
    {"id": "D1", "description": "API returns 200 on valid input", "category": "functional"},
    {"id": "D2", "description": "Input validation rejects XSS", "category": "security"},
    {"id": "D3", "description": "Unit tests cover edge cases", "category": "testing"},
]


# =====================================================================
# Deliverable dataclass
# =====================================================================

class TestDeliverable:
    def test_roundtrip(self):
        d = Deliverable(
            id="D1", description="Test deliverable",
            category="quality", source="qa_agent",
            status="pass", notes=["note1"],
            verified_at="2026-01-01", verified_by="qa",
        )
        as_dict = d.to_dict()
        restored = Deliverable.from_dict(as_dict)
        assert restored.id == "D1"
        assert restored.status == "pass"
        assert restored.notes == ["note1"]

    def test_defaults(self):
        d = Deliverable(id="D1", description="test")
        assert d.category == "functional"
        assert d.status == "pending"
        assert d.notes == []
        assert d.verified_at is None


# =====================================================================
# validate_deliverables
# =====================================================================

class TestValidation:
    def test_valid(self):
        errors = validate_deliverables(SAMPLE_DELIVERABLES)
        assert errors == []

    def test_empty_list(self):
        errors = validate_deliverables([])
        assert len(errors) == 1
        assert "At least one" in errors[0]

    def test_missing_id(self):
        errors = validate_deliverables([{"description": "no id"}])
        assert any("missing 'id'" in e for e in errors)

    def test_missing_description(self):
        errors = validate_deliverables([{"id": "D1"}])
        assert any("missing 'description'" in e for e in errors)

    def test_duplicate_ids(self):
        errors = validate_deliverables([
            {"id": "D1", "description": "first"},
            {"id": "D1", "description": "duplicate"},
        ])
        assert any("duplicate" in e for e in errors)

    def test_description_too_long(self):
        errors = validate_deliverables([
            {"id": "D1", "description": "x" * (MAX_DESCRIPTION_LENGTH + 1)},
        ])
        assert any("exceeds" in e for e in errors)

    def test_invalid_category(self):
        errors = validate_deliverables([
            {"id": "D1", "description": "ok", "category": "bogus"},
        ])
        assert any("invalid category" in e for e in errors)

    def test_invalid_status(self):
        errors = validate_deliverables([
            {"id": "D1", "description": "ok", "status": "bogus"},
        ])
        assert any("invalid status" in e for e in errors)

    def test_too_many_deliverables(self):
        many = [{"id": f"D{i}", "description": f"d{i}"} for i in range(MAX_DELIVERABLES_PER_ITEM + 1)]
        errors = validate_deliverables(many)
        assert any("Too many" in e for e in errors)

    def test_non_dict_element(self):
        errors = validate_deliverables(["not a dict"])
        assert any("must be a dict" in e for e in errors)


# =====================================================================
# DeliverableTracker.set_deliverables
# =====================================================================

class TestSetDeliverables:
    def test_set(self, tracker):
        result = tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        assert len(result) == 3
        assert result[0].id == "D1"

    def test_replace(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        new = [{"id": "D10", "description": "New deliverable"}]
        result = tracker.set_deliverables("task_1", new, append=False)
        assert len(result) == 1
        assert result[0].id == "D10"

    def test_append(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES[:1])
        result = tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES[1:], append=True)
        assert len(result) == 3

    def test_append_deduplicates(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES[:2])
        result = tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES, append=True)
        # D1, D2 already exist; only D3 should be added
        assert len(result) == 3

    def test_invalid_raises(self, tracker):
        with pytest.raises(ValueError, match="Invalid"):
            tracker.set_deliverables("task_1", [])

    def test_source_applied(self, tracker):
        result = tracker.set_deliverables(
            "task_1", [{"id": "D1", "description": "test"}], source="qa_agent",
        )
        assert result[0].source == "qa_agent"


# =====================================================================
# DeliverableTracker.finalize
# =====================================================================

class TestFinalize:
    def test_finalize(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        assert tracker.finalize("task_1") is True
        assert tracker.is_finalized("task_1") is True

    def test_finalize_already_finalized(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        tracker.finalize("task_1")
        assert tracker.finalize("task_1") is False

    def test_finalize_not_found(self, tracker):
        assert tracker.finalize("nonexistent") is False

    def test_is_finalized_not_found(self, tracker):
        assert tracker.is_finalized("nonexistent") is False


# =====================================================================
# DeliverableTracker.get_deliverables
# =====================================================================

class TestGetDeliverables:
    def test_get(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        result = tracker.get_deliverables("task_1")
        assert len(result) == 3

    def test_get_not_found(self, tracker):
        assert tracker.get_deliverables("nonexistent") == []


# =====================================================================
# DeliverableTracker.update_status
# =====================================================================

class TestUpdateStatus:
    def test_update(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        result = tracker.update_status("task_1", "D1", "pass", verified_by="qa")
        assert result is not None
        assert result.status == "pass"
        assert result.verified_by == "qa"
        assert result.verified_at is not None

    def test_update_with_notes(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        result = tracker.update_status("task_1", "D1", "fail", notes="Missing edge case")
        assert "Missing edge case" in result.notes

    def test_update_invalid_status(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        with pytest.raises(ValueError, match="Invalid status"):
            tracker.update_status("task_1", "D1", "bogus")

    def test_update_not_found_item(self, tracker):
        assert tracker.update_status("nonexistent", "D1", "pass") is None

    def test_update_not_found_deliverable(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        assert tracker.update_status("task_1", "D999", "pass") is None


# =====================================================================
# DeliverableTracker.evaluate_against_output
# =====================================================================

class TestEvaluateAgainstOutput:
    def test_without_evaluator(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        result = tracker.evaluate_against_output("task_1", "some output")
        assert result["total"] == 3
        assert result["pending"] == 3  # no evaluator, stays pending

    def test_with_evaluator(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)

        def eval_fn(deliverable, output):
            return "pass" if "200" in deliverable.description else "fail"

        result = tracker.evaluate_against_output("task_1", "output", eval_fn)
        assert result["passed"] == 1  # Only D1 mentions "200"
        assert result["failed"] == 2

    def test_evaluator_exception(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES[:1])

        def bad_eval(deliverable, output):
            raise RuntimeError("eval crashed")

        result = tracker.evaluate_against_output("task_1", "output", bad_eval)
        assert result["total"] == 1  # should not crash

    def test_not_found(self, tracker):
        result = tracker.evaluate_against_output("nonexistent", "output")
        assert "error" in result

    def test_all_passed_flag(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES[:1])
        tracker.update_status("task_1", "D1", "pass")
        result = tracker.evaluate_against_output("task_1", "output")
        assert result["all_passed"] is True


# =====================================================================
# DeliverableTracker.generate_report
# =====================================================================

class TestGenerateReport:
    def test_report(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        tracker.update_status("task_1", "D1", "pass")
        tracker.update_status("task_1", "D2", "fail", notes="XSS not blocked")
        tracker.finalize("task_1")

        report = tracker.generate_report("task_1")
        assert report["finalized"] is True
        assert report["summary"]["total"] == 3
        assert report["summary"]["passed"] == 1
        assert report["summary"]["failed"] == 1
        assert report["summary"]["pending"] == 1
        assert report["verdict"] == "FAIL"
        assert len(report["remaining_issues"]) == 2  # D2 (fail) + D3 (pending)

    def test_report_all_pass(self, tracker):
        tracker.set_deliverables("task_1", [{"id": "D1", "description": "test"}])
        tracker.update_status("task_1", "D1", "pass")
        report = tracker.generate_report("task_1")
        assert report["verdict"] == "PASS"
        assert report["remaining_issues"] == []

    def test_report_not_found(self, tracker):
        report = tracker.generate_report("nonexistent")
        assert "error" in report


# =====================================================================
# DeliverableTracker.remove
# =====================================================================

class TestRemove:
    def test_remove(self, tracker):
        tracker.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        assert tracker.remove("task_1") is True
        assert tracker.get_deliverables("task_1") == []

    def test_remove_not_found(self, tracker):
        assert tracker.remove("nonexistent") is False


# =====================================================================
# Persistence
# =====================================================================

class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        tracker1 = DeliverableTracker(data_dir=tmp_path)
        tracker1.set_deliverables("task_1", SAMPLE_DELIVERABLES)
        tracker1.update_status("task_1", "D1", "pass")
        tracker1.finalize("task_1")

        tracker2 = DeliverableTracker(data_dir=tmp_path)
        deliverables = tracker2.get_deliverables("task_1")
        assert len(deliverables) == 3
        assert deliverables[0].status == "pass"
        assert tracker2.is_finalized("task_1") is True

    def test_no_data_dir(self):
        tracker = DeliverableTracker()  # no persistence
        tracker.set_deliverables("t1", [{"id": "D1", "description": "test"}])
        assert len(tracker.get_deliverables("t1")) == 1
