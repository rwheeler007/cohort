"""Tests for cohort.approval_store."""

import json
import time
import threading
from pathlib import Path

import pytest

from cohort.approval_store import (
    ApprovalRequest,
    ApprovalStore,
    MAX_PENDING_PER_REQUESTER,
    MAX_PENDING_TOTAL,
    MAX_REQUESTS_PER_REQUESTER_PER_MINUTE,
    validate_approval_input,
    _check_json_depth,
    _clamp_timeout,
    _sanitize_description,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def store(tmp_path):
    return ApprovalStore(data_dir=tmp_path)


def _create_basic(store, **overrides):
    """Helper to create an approval with sensible defaults."""
    kwargs = dict(
        item_id="task_123",
        item_type="task",
        requester="agent_a",
        action_type="code_change",
        risk_level="medium",
        description="Test approval request for code changes",
    )
    kwargs.update(overrides)
    return store.create(**kwargs)


# =====================================================================
# ApprovalRequest dataclass
# =====================================================================

class TestApprovalRequest:
    def test_roundtrip(self):
        req = ApprovalRequest(
            id="test_1",
            item_id="wq_123",
            item_type="work_item",
            requester="agent_x",
            action_type="deploy",
            risk_level="high",
            description="Deploy to prod",
            details={"version": "1.0"},
            status="pending",
            created_at="2026-01-01T00:00:00+00:00",
        )
        d = req.to_dict()
        restored = ApprovalRequest.from_dict(d)
        assert restored.id == "test_1"
        assert restored.item_type == "work_item"
        assert restored.details == {"version": "1.0"}

    def test_is_expired_pending(self):
        req = ApprovalRequest(
            id="x", item_id="y", item_type="task", requester="a",
            action_type="custom", risk_level="low", description="test",
            details={}, status="pending",
            created_at="2020-01-01T00:00:00+00:00",
            timeout_seconds=1,
        )
        assert req.is_expired() is True

    def test_is_expired_not_pending(self):
        req = ApprovalRequest(
            id="x", item_id="y", item_type="task", requester="a",
            action_type="custom", risk_level="low", description="test",
            details={}, status="approved",
            created_at="2020-01-01T00:00:00+00:00",
            timeout_seconds=1,
        )
        assert req.is_expired() is False


# =====================================================================
# Validation helpers
# =====================================================================

class TestValidation:
    def test_valid_input(self):
        assert validate_approval_input("code_change", "medium", "A valid desc", {}) is None

    def test_invalid_action_type(self):
        err = validate_approval_input("bogus", "medium", "desc", {})
        assert err and "bogus" in err

    def test_invalid_risk_level(self):
        err = validate_approval_input("code_change", "extreme", "desc", {})
        assert err and "extreme" in err

    def test_description_too_long(self):
        err = validate_approval_input("code_change", "low", "x" * 600, {})
        assert err and "exceeds" in err

    def test_details_too_large(self):
        big = {"data": "x" * 20000}
        err = validate_approval_input("code_change", "low", "ok", big)
        assert err and "bytes" in err

    def test_details_too_deep(self):
        deep = {"a": {"b": {"c": {"d": "too deep"}}}}
        err = validate_approval_input("code_change", "low", "ok", deep)
        assert err and "depth" in err

    def test_sanitize_description(self):
        assert _sanitize_description("<b>bold</b>") == "bold"
        assert _sanitize_description("hello\x00world") == "helloworld"

    def test_clamp_timeout(self):
        assert _clamp_timeout("high", 10) == 30   # clamped to min
        assert _clamp_timeout("high", 999) == 300  # clamped to max
        assert _clamp_timeout("high", 60) == 60    # within range
        assert _clamp_timeout("high", None) == 120  # default

    def test_check_json_depth(self):
        assert _check_json_depth({"a": 1}, 3) is False
        assert _check_json_depth({"a": {"b": {"c": {"d": 1}}}}, 3) is True


# =====================================================================
# ApprovalStore CRUD
# =====================================================================

class TestApprovalStoreCRUD:
    def test_create(self, store):
        req = _create_basic(store)
        assert req.status == "pending"
        assert req.id.startswith("apr_")
        assert req.item_id == "task_123"

    def test_create_invalid_item_type(self, store):
        with pytest.raises(ValueError, match="item_type"):
            store.create(
                item_id="x", item_type="bogus", requester="a",
                action_type="custom", risk_level="low", description="test",
            )

    def test_create_invalid_input(self, store):
        with pytest.raises(ValueError):
            store.create(
                item_id="x", item_type="task", requester="a",
                action_type="bogus", risk_level="low", description="test",
            )

    def test_get(self, store):
        req = _create_basic(store)
        fetched = store.get(req.id)
        assert fetched is not None
        assert fetched.id == req.id

    def test_get_not_found(self, store):
        assert store.get("nonexistent") is None

    def test_resolve_approve(self, store):
        req = _create_basic(store, requester="agent_a")
        result = store.resolve(req.id, "approve", resolved_by="agent_b")
        assert result["status"] == "approved"

    def test_resolve_deny(self, store):
        req = _create_basic(store, requester="agent_a")
        result = store.resolve(req.id, "deny", resolved_by="agent_b")
        assert result["status"] == "denied"

    def test_resolve_invalid_action(self, store):
        req = _create_basic(store)
        result = store.resolve(req.id, "maybe", resolved_by="b")
        assert "error" in result

    def test_self_approval_prevented(self, store):
        req = _create_basic(store, requester="agent_a")
        result = store.resolve(req.id, "approve", resolved_by="agent_a")
        assert "error" in result
        assert "Requester" in result["error"]

    def test_resolve_already_resolved(self, store):
        req = _create_basic(store, requester="a")
        store.resolve(req.id, "approve", resolved_by="b")
        result = store.resolve(req.id, "approve", resolved_by="b")
        assert "error" in result
        assert "already" in result["error"].lower()

    def test_resolve_not_found(self, store):
        result = store.resolve("nonexistent", "approve", resolved_by="b")
        assert "error" in result

    def test_cancel(self, store):
        req = _create_basic(store, requester="agent_a")
        result = store.cancel(req.id, cancelled_by="agent_a")
        assert result["status"] == "cancelled"

    def test_cancel_not_requester(self, store):
        req = _create_basic(store, requester="agent_a")
        result = store.cancel(req.id, cancelled_by="agent_b")
        assert "error" in result

    def test_cancel_human_override(self, store):
        req = _create_basic(store, requester="agent_a")
        result = store.cancel(req.id, cancelled_by="human")
        assert result["status"] == "cancelled"

    def test_cancel_not_pending(self, store):
        req = _create_basic(store, requester="a")
        store.resolve(req.id, "approve", resolved_by="b")
        result = store.cancel(req.id, cancelled_by="a")
        assert "error" in result


# =====================================================================
# Listing & filtering
# =====================================================================

class TestApprovalStoreListing:
    def test_get_pending(self, store):
        _create_basic(store, item_id="t1", requester="a")
        _create_basic(store, item_id="t2", requester="a")
        req3 = _create_basic(store, item_id="t3", requester="a")
        store.resolve(req3.id, "approve", resolved_by="b")

        pending = store.get_pending()
        assert len(pending) == 2

    def test_get_pending_by_role(self, store):
        _create_basic(store, item_id="t1", reviewer_role="qa")
        _create_basic(store, item_id="t2", reviewer_role="security")

        qa_pending = store.get_pending(reviewer_role="qa")
        assert len(qa_pending) == 1

    def test_get_pending_count(self, store):
        _create_basic(store, item_id="t1")
        _create_basic(store, item_id="t2")
        assert store.get_pending_count() == 2

    def test_list_all(self, store):
        _create_basic(store, item_id="t1", requester="a")
        req2 = _create_basic(store, item_id="t2", requester="a")
        store.resolve(req2.id, "deny", resolved_by="b")

        all_items = store.list_all()
        assert len(all_items) == 2

    def test_list_all_filter_status(self, store):
        _create_basic(store, item_id="t1", requester="a")
        req2 = _create_basic(store, item_id="t2", requester="a")
        store.resolve(req2.id, "deny", resolved_by="b")

        denied = store.list_all(status="denied")
        assert len(denied) == 1

    def test_list_all_filter_item_type(self, store):
        _create_basic(store, item_id="t1", item_type="task")
        _create_basic(store, item_id="w1", item_type="work_item")

        tasks_only = store.list_all(item_type="task")
        assert len(tasks_only) == 1


# =====================================================================
# Rate limiting
# =====================================================================

class TestRateLimiting:
    def test_per_requester_pending_cap(self, store):
        # Create approvals from different requesters to avoid burst rate limit
        # but all targeting the same reviewer scenario
        for i in range(MAX_PENDING_PER_REQUESTER):
            store.create(
                item_id=f"t{i}", item_type="task", requester=f"agent_{i}",
                action_type="code_change", risk_level="medium",
                description=f"Test approval {i} for pending cap",
            )

        # Now the system-wide cap isn't hit, but let's test per-requester:
        # Reset store and test one requester hitting their cap
        store2 = ApprovalStore(data_dir=store._approvals_path.parent)
        store2._approvals = {}
        store2._loaded = True
        store2._request_timestamps = {}

        # Use different "minutes" by manipulating timestamps to avoid burst
        import time as _time
        for i in range(MAX_PENDING_PER_REQUESTER):
            store2.create(
                item_id=f"cap{i}", item_type="task", requester="flood_agent",
                action_type="code_change", risk_level="medium",
                description=f"Test approval {i} for cap test",
            )
            # Clear burst timestamps so burst rate doesn't trip
            store2._request_timestamps["flood_agent"] = []

        with pytest.raises(ValueError, match="pending"):
            store2.create(
                item_id="cap_over", item_type="task", requester="flood_agent",
                action_type="code_change", risk_level="medium",
                description="This should hit the pending cap",
            )

    def test_burst_rate(self, store):
        # Create and resolve to avoid pending cap
        for i in range(MAX_REQUESTS_PER_REQUESTER_PER_MINUTE):
            req = _create_basic(store, item_id=f"t{i}", requester="burst_agent")
            store.resolve(req.id, "approve", resolved_by="other")

        with pytest.raises(ValueError, match="requests/minute"):
            _create_basic(store, item_id="t_over", requester="burst_agent")


# =====================================================================
# Expiry
# =====================================================================

class TestExpiry:
    def test_expire_stale(self, store):
        req = store.create(
            item_id="t1", item_type="task", requester="a",
            action_type="custom", risk_level="critical",
            description="This should expire quickly",
            timeout=30,  # will be clamped to min 30
        )
        # Manually backdate
        req.created_at = "2020-01-01T00:00:00+00:00"

        expired = store.expire_stale()
        assert len(expired) == 1
        assert expired[0].status == "expired"

    def test_resolve_expired_request(self, store):
        req = _create_basic(store, requester="a")
        req.created_at = "2020-01-01T00:00:00+00:00"
        req.timeout_seconds = 1

        result = store.resolve(req.id, "approve", resolved_by="b")
        assert result.get("status") == "expired"


# =====================================================================
# Persistence
# =====================================================================

class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        store1 = ApprovalStore(data_dir=tmp_path)
        req = _create_basic(store1, item_id="persist_test")

        # Load into a new store instance
        store2 = ApprovalStore(data_dir=tmp_path)
        fetched = store2.get(req.id)
        assert fetched is not None
        assert fetched.item_id == "persist_test"

    def test_audit_trail_created(self, tmp_path):
        store = ApprovalStore(data_dir=tmp_path)
        req = _create_basic(store, requester="a")
        store.resolve(req.id, "approve", resolved_by="b")

        audit_path = tmp_path / "approval_audit.jsonl"
        assert audit_path.exists()
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 2  # created + approved
        assert json.loads(lines[0])["event_type"] == "approval_created"
        assert json.loads(lines[1])["event_type"] == "approval_approved"


# =====================================================================
# Thread safety
# =====================================================================

class TestThreadSafety:
    def test_concurrent_resolves(self, store):
        """Only one thread should successfully resolve a pending approval."""
        req = _create_basic(store, requester="agent_a")
        results = []

        def resolve_it(name):
            r = store.resolve(req.id, "approve", resolved_by=name)
            results.append(r)

        threads = [
            threading.Thread(target=resolve_it, args=(f"resolver_{i}",))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r.get("status") == "approved" and "error" not in r]
        errors = [r for r in results if "error" in r]
        assert len(successes) == 1
        assert len(errors) == 4
