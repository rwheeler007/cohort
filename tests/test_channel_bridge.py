"""Tier 1: Channel bridge session lifecycle and request queue tests.

Tests the core bugs found on 2026-04-01:
  - PID mismatch: register_channel_session() must adopt pending spawned sessions
  - Session duplication: spawn loop must detect adopted/re-keyed sessions
  - Heartbeat race: update_heartbeat() must find sessions by PID or single-session fallback
  - Stale reaper: must reap sessions regardless of PID mismatch

Also tests the full request lifecycle: enqueue -> poll -> claim -> respond.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to reset module-level state between tests
# ---------------------------------------------------------------------------

def _reset_channel_bridge():
    """Reset all module-level state in channel_bridge for test isolation."""
    import cohort.channel_bridge as cb

    with cb._sessions_lock:
        cb._channel_sessions.clear()
    with cb._channel_lock:
        cb._channel_queues.clear()
    with cb._launch_lock:
        cb._launch_queue.clear()

    cb._channel_last_activity.clear()
    cb._channel_pressure.clear()
    cb._session_handoffs.clear()
    cb._rotation_log.clear()
    cb._resume_ids.clear()
    cb._state_file = None

    # Reset settings to defaults
    cb._session_limit = 5
    cb._session_warn = 3
    cb._idle_timeout_s = 600
    cb._auto_launch = False
    cb._reaper_started = False


@pytest.fixture(autouse=True)
def clean_bridge():
    """Reset channel bridge state before and after each test."""
    _reset_channel_bridge()
    yield
    _reset_channel_bridge()


@pytest.fixture
def bridge():
    """Import and return the channel_bridge module."""
    import cohort.channel_bridge as cb
    return cb


@pytest.fixture
def data_dir_for_bridge(tmp_path: Path):
    """Set up a temp data dir for session state persistence."""
    import cohort.channel_bridge as cb
    cb.set_data_dir(str(tmp_path))
    return tmp_path


# =====================================================================
# Session Lifecycle Tests
# =====================================================================

class TestRegisterChannelSession:
    """Tests for register_channel_session() — the adoption logic."""

    def test_register_creates_new_session(self, bridge):
        """Registering a session for a new channel creates it."""
        result = bridge.register_channel_session("general", "sess-1", pid=1234)
        assert result["ok"] is True
        assert result["channel_id"] == "general"
        assert result["session_id"] == "sess-1"

    def test_register_adopts_pending_spawn(self, bridge):
        """CRITICAL: register must adopt a pending spawned session (last_heartbeat=None).

        This is the exact bug from 2026-04-01: spawn records entry with PID X,
        plugin registers with PID Y. Without adoption, we get duplicates.
        """
        # Simulate what _spawn_channel_session() does: insert a placeholder
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {}
            bridge._channel_sessions["general"]["spawn-placeholder"] = {
                "pid": 9999,  # cmd.exe wrapper PID
                "registered_at": time.time(),
                "last_heartbeat": None,  # Never heartbeated = pending spawn
                "last_activity": time.time(),
                "process": MagicMock(),
            }

        # Plugin registers with a DIFFERENT PID (the actual Claude Code PID)
        result = bridge.register_channel_session("general", "plugin-sess-1", pid=5555)

        assert result["ok"] is True
        assert result["session_id"] == "plugin-sess-1"

        # Verify adoption: old key removed, new key exists
        with bridge._sessions_lock:
            sessions = bridge._channel_sessions["general"]
            assert "spawn-placeholder" not in sessions, "Old placeholder key must be removed"
            assert "plugin-sess-1" in sessions, "New session key must exist"
            info = sessions["plugin-sess-1"]
            assert info["pid"] == 5555, "PID must be updated to plugin's PID"
            assert info["last_heartbeat"] is not None, "Heartbeat must be set on adoption"
            assert info.get("process") is not None, "Process handle must be preserved"

    def test_register_does_not_adopt_active_session(self, bridge):
        """Should NOT adopt a session that already has a heartbeat."""
        now = time.time()
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {}
            bridge._channel_sessions["general"]["existing-sess"] = {
                "pid": 1111,
                "registered_at": now,
                "last_heartbeat": now,  # Already heartbeated = active
                "last_activity": now,
                "process": None,
            }

        result = bridge.register_channel_session("general", "new-sess", pid=2222)
        assert result["ok"] is True

        # Both sessions should exist (no adoption)
        with bridge._sessions_lock:
            sessions = bridge._channel_sessions["general"]
            assert "existing-sess" in sessions
            assert "new-sess" in sessions

    def test_register_enforces_session_limit(self, bridge):
        """Registration fails when session limit is reached and no idle sessions to evict."""
        bridge._session_limit = 2
        now = time.time()

        # Fill up to the limit with active sessions
        with bridge._sessions_lock:
            bridge._channel_sessions["ch1"] = {
                "s1": {"pid": 100, "last_heartbeat": now, "last_activity": now, "registered_at": now, "process": None}
            }
            bridge._channel_sessions["ch2"] = {
                "s2": {"pid": 200, "last_heartbeat": now, "last_activity": now, "registered_at": now, "process": None}
            }

        # Give both channels pending requests so they can't be evicted
        with bridge._channel_lock:
            bridge._channel_queues["ch1"] = deque([
                {"id": "req-1", "status": "pending", "created_at": now}
            ])
            bridge._channel_queues["ch2"] = deque([
                {"id": "req-2", "status": "pending", "created_at": now}
            ])

        result = bridge.register_channel_session("ch3", "s3", pid=300)
        assert result["ok"] is False
        assert result["error"] == "session_limit_reached"
        assert result["limit"] == 2

    def test_register_evicts_idle_session_at_limit(self, bridge):
        """When at limit, evicts the least-active idle session."""
        bridge._session_limit = 1
        now = time.time()

        # One active but idle session (no pending requests)
        with bridge._sessions_lock:
            bridge._channel_sessions["old-ch"] = {
                "old-s": {
                    "pid": 100, "last_heartbeat": now,
                    "last_activity": now - 3600,  # idle for 1 hour
                    "registered_at": now - 7200, "process": MagicMock(),
                }
            }
        bridge._channel_last_activity["old-ch"] = now - 3600

        result = bridge.register_channel_session("new-ch", "new-s", pid=200)
        assert result["ok"] is True


class TestUpdateHeartbeat:
    """Tests for update_heartbeat() — PID matching and single-session fallback."""

    def test_heartbeat_by_session_id(self, bridge):
        """Heartbeat updates when session_id matches."""
        now = time.time()
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "sess-1": {"pid": 100, "last_heartbeat": now - 10, "last_activity": now, "process": None}
            }

        bridge.update_heartbeat("sess-1", pid=100, channel_id="general")

        with bridge._sessions_lock:
            info = bridge._channel_sessions["general"]["sess-1"]
            assert info["last_heartbeat"] > now - 5

    def test_heartbeat_by_pid_match(self, bridge):
        """CRITICAL: Heartbeat finds session by PID when session_id doesn't match.

        This is the PID mismatch fallback from the 2026-04-01 fix.
        """
        now = time.time()
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "spawn-placeholder": {"pid": 5555, "last_heartbeat": None, "last_activity": now, "process": None}
            }

        # Plugin heartbeats with its own session_id but matching PID
        bridge.update_heartbeat("plugin-sess", pid=5555, channel_id="general")

        with bridge._sessions_lock:
            info = bridge._channel_sessions["general"]["spawn-placeholder"]
            assert info["last_heartbeat"] is not None
            assert info["session_id"] == "plugin-sess"

    def test_heartbeat_single_session_fallback(self, bridge):
        """CRITICAL: When one session exists with no heartbeat, assume it's the spawned one.

        Covers the case where both session_id AND PID don't match (wrapper PID).
        """
        now = time.time()
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "spawn-placeholder": {
                    "pid": 9999,  # cmd.exe wrapper PID — won't match
                    "last_heartbeat": None,
                    "last_activity": now,
                    "process": None,
                }
            }

        # Plugin heartbeats with completely different session_id AND PID
        bridge.update_heartbeat("plugin-sess", pid=5555, channel_id="general")

        with bridge._sessions_lock:
            info = bridge._channel_sessions["general"]["spawn-placeholder"]
            assert info["last_heartbeat"] is not None, "Single-session fallback must work"
            assert info["session_id"] == "plugin-sess"
            assert info["pid"] == 5555

    def test_heartbeat_ignores_unregistered_channel(self, bridge):
        """Heartbeat for a channel with no sessions is silently ignored."""
        bridge.update_heartbeat("unknown-sess", pid=100, channel_id="nonexistent")

        with bridge._sessions_lock:
            assert "nonexistent" not in bridge._channel_sessions

    def test_heartbeat_auto_registers_global_session(self, bridge):
        """Global sessions (backward compat) auto-register on first heartbeat."""
        bridge.update_heartbeat("global-sess", pid=100, channel_id=None)

        with bridge._sessions_lock:
            assert bridge._GLOBAL_KEY in bridge._channel_sessions
            assert "global-sess" in bridge._channel_sessions[bridge._GLOBAL_KEY]


class TestSessionHelpers:
    """Tests for _is_session_alive, _prune_stale_sessions, etc."""

    def test_session_alive_with_recent_heartbeat(self, bridge):
        info = {"last_heartbeat": time.time()}
        assert bridge._is_session_alive(info) is True

    def test_session_dead_with_no_heartbeat(self, bridge):
        info = {"last_heartbeat": None}
        assert bridge._is_session_alive(info) is False

    def test_session_dead_with_old_heartbeat(self, bridge):
        info = {"last_heartbeat": time.time() - bridge.HEARTBEAT_TIMEOUT_S - 10}
        assert bridge._is_session_alive(info) is False

    def test_prune_stale_sessions(self, bridge):
        """Stale sessions (2x heartbeat timeout) are pruned."""
        old_time = time.time() - bridge.HEARTBEAT_TIMEOUT_S * 3
        with bridge._sessions_lock:
            bridge._channel_sessions["ch1"] = {
                "stale": {"pid": 100, "last_heartbeat": old_time, "process": None}
            }
            bridge._prune_stale_sessions()
            assert "ch1" not in bridge._channel_sessions

    def test_prune_keeps_fresh_sessions(self, bridge):
        """Fresh sessions survive pruning."""
        with bridge._sessions_lock:
            bridge._channel_sessions["ch1"] = {
                "fresh": {"pid": 100, "last_heartbeat": time.time(), "process": None}
            }
            bridge._prune_stale_sessions()
            assert "ch1" in bridge._channel_sessions


class TestReapIdleSessions:
    """Tests for _reap_idle_sessions()."""

    def test_reaps_idle_sessions(self, bridge):
        """Sessions idle beyond timeout are reaped."""
        bridge._idle_timeout_s = 60
        now = time.time()

        with bridge._sessions_lock:
            bridge._channel_sessions["idle-ch"] = {
                "s1": {
                    "pid": 100, "last_heartbeat": now,
                    "registered_at": now - 300,
                    "last_activity": now - 300,
                    "process": MagicMock(),
                }
            }
        bridge._channel_last_activity["idle-ch"] = now - 300  # idle for 5 min

        bridge._reap_idle_sessions()

        with bridge._sessions_lock:
            assert "idle-ch" not in bridge._channel_sessions

    def test_does_not_reap_active_channel(self, bridge):
        """Channels with pending requests are not reaped."""
        bridge._idle_timeout_s = 1
        now = time.time()

        with bridge._sessions_lock:
            bridge._channel_sessions["active-ch"] = {
                "s1": {"pid": 100, "last_heartbeat": now, "registered_at": now, "last_activity": now, "process": None}
            }
        bridge._channel_last_activity["active-ch"] = now - 100

        # Add a pending request
        with bridge._channel_lock:
            bridge._channel_queues["active-ch"] = deque([
                {"id": "req-1", "status": "pending", "created_at": now}
            ])

        bridge._reap_idle_sessions()

        with bridge._sessions_lock:
            assert "active-ch" in bridge._channel_sessions

    def test_does_not_reap_global_sessions(self, bridge):
        """Global sessions (_global key) are never reaped."""
        bridge._idle_timeout_s = 1
        now = time.time()

        with bridge._sessions_lock:
            bridge._channel_sessions[bridge._GLOBAL_KEY] = {
                "g1": {"pid": 100, "last_heartbeat": now, "registered_at": now, "last_activity": now, "process": None}
            }
        bridge._channel_last_activity[bridge._GLOBAL_KEY] = now - 100

        bridge._reap_idle_sessions()

        with bridge._sessions_lock:
            assert bridge._GLOBAL_KEY in bridge._channel_sessions


class TestReapAbandonedRequests:
    """Tests for _reap_abandoned_requests()."""

    def test_reaps_old_pending_requests(self, bridge):
        """Requests pending beyond TTL are reaped."""
        old_time = time.time() - bridge.ABANDONED_REQUEST_TTL_S - 100

        with bridge._channel_lock:
            bridge._channel_queues["ch1"] = deque([
                {"id": "old-req", "status": "pending", "created_at": old_time}
            ])

        bridge._reap_abandoned_requests()

        with bridge._channel_lock:
            assert len(bridge._channel_queues["ch1"]) == 0

    def test_keeps_recent_pending_requests(self, bridge):
        """Recent pending requests survive reaping."""
        with bridge._channel_lock:
            bridge._channel_queues["ch1"] = deque([
                {"id": "new-req", "status": "pending", "created_at": time.time()}
            ])

        bridge._reap_abandoned_requests()

        with bridge._channel_lock:
            assert len(bridge._channel_queues["ch1"]) == 1

    def test_keeps_claimed_requests(self, bridge):
        """Claimed requests are never reaped regardless of age."""
        old_time = time.time() - bridge.ABANDONED_REQUEST_TTL_S - 100

        with bridge._channel_lock:
            bridge._channel_queues["ch1"] = deque([
                {"id": "claimed-req", "status": "claimed", "created_at": old_time}
            ])

        bridge._reap_abandoned_requests()

        with bridge._channel_lock:
            assert len(bridge._channel_queues["ch1"]) == 1


# =====================================================================
# Session State Persistence Tests
# =====================================================================

class TestSessionStatePersistence:
    """Tests for _save_session_state() and load_session_state()."""

    def test_save_and_load_roundtrip(self, bridge, data_dir_for_bridge):
        """Session state survives save -> load cycle."""
        now = time.time()
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "sess-1": {
                    "session_id": "sess-1",
                    "pid": 1234,
                    "last_heartbeat": now,
                    "last_activity": now,
                    "registered_at": now,
                    "workspace_path": "/tmp/test",
                    "process": None,
                }
            }

        bridge._save_session_state()

        # Verify file written
        state_file = data_dir_for_bridge / "channel_sessions.json"
        assert state_file.exists()
        entries = json.loads(state_file.read_text("utf-8"))
        assert len(entries) == 1
        assert entries[0]["channel_id"] == "general"
        assert entries[0]["pid"] == 1234

    def test_load_skips_dead_pids(self, bridge, data_dir_for_bridge):
        """Loading state skips entries with dead PIDs."""
        state_file = data_dir_for_bridge / "channel_sessions.json"
        state_file.write_text(json.dumps([{
            "channel_id": "general",
            "session_id": "dead-sess",
            "pid": 999999999,  # Very unlikely to be alive
            "resume_id": "resume-123",
        }]), "utf-8")

        adopted = bridge.load_session_state()

        # Should not adopt (PID dead) but should preserve resume_id
        assert adopted == 0
        assert bridge._resume_ids.get("general") == "resume-123"

    def test_load_with_no_state_file(self, bridge, data_dir_for_bridge):
        """Loading with no state file returns 0."""
        adopted = bridge.load_session_state()
        assert adopted == 0


# =====================================================================
# Request Queue Tests
# =====================================================================

class TestEnqueueChannelRequest:
    """Tests for enqueue_channel_request()."""

    def test_enqueue_creates_request(self, bridge):
        """Enqueuing a request creates it in the channel queue."""
        # Patch request_session to avoid actual spawning
        with patch.object(bridge, "request_session"):
            req_id = bridge.enqueue_channel_request(
                prompt="Hello agent",
                agent_id="python_developer",
                channel_id="general",
            )

        assert req_id.startswith("ch_")
        with bridge._channel_lock:
            q = bridge._channel_queues["general"]
            assert len(q) == 1
            assert q[0]["id"] == req_id
            assert q[0]["status"] == "pending"
            assert q[0]["prompt"] == "Hello agent"

    def test_enqueue_deduplicates_within_5s(self, bridge):
        """Duplicate requests for same agent+channel within 5s are skipped."""
        with patch.object(bridge, "request_session"):
            req1 = bridge.enqueue_channel_request(
                prompt="Hello",
                agent_id="py",
                channel_id="general",
            )
            req2 = bridge.enqueue_channel_request(
                prompt="Hello again",
                agent_id="py",
                channel_id="general",
            )

        assert req1 == req2  # Should return the existing request's ID

        with bridge._channel_lock:
            assert len(bridge._channel_queues["general"]) == 1

    def test_enqueue_allows_different_agents(self, bridge):
        """Different agents can enqueue to the same channel."""
        with patch.object(bridge, "request_session"):
            req1 = bridge.enqueue_channel_request("Hi", "py", "general")
            req2 = bridge.enqueue_channel_request("Hi", "js", "general")

        assert req1 != req2

        with bridge._channel_lock:
            assert len(bridge._channel_queues["general"]) == 2

    def test_enqueue_prepends_handoff(self, bridge):
        """If a handoff exists for the channel, it's prepended to the prompt."""
        bridge._session_handoffs["general"] = "HANDOFF: previous context here"

        with patch.object(bridge, "request_session"):
            req_id = bridge.enqueue_channel_request("New task", "py", "general")

        with bridge._channel_lock:
            req = bridge._channel_queues["general"][0]
            assert req["prompt"].startswith("HANDOFF: previous context here")
            assert "New task" in req["prompt"]

        # Handoff should be consumed
        assert "general" not in bridge._session_handoffs


class TestPollClaimRespond:
    """Tests for poll_next_request(), claim_request(), deliver_response()."""

    def _enqueue(self, bridge, prompt="test", agent="py", channel="general"):
        """Helper to enqueue without spawning."""
        with patch.object(bridge, "request_session"):
            return bridge.enqueue_channel_request(prompt, agent, channel)

    def test_poll_returns_pending_request(self, bridge):
        req_id = self._enqueue(bridge)

        result = bridge.poll_next_request(channel_id="general")
        assert result is not None
        assert result["id"] == req_id
        assert result["agent_id"] == "py"

    def test_poll_returns_none_when_empty(self, bridge):
        result = bridge.poll_next_request(channel_id="general")
        assert result is None

    def test_poll_all_channels(self, bridge):
        """Poll without channel_id searches all queues."""
        self._enqueue(bridge, channel="ch1")
        result = bridge.poll_next_request(channel_id=None)
        assert result is not None

    def test_claim_returns_full_prompt(self, bridge):
        req_id = self._enqueue(bridge, prompt="Do the thing")

        result = bridge.claim_request(req_id, session_id="sess-1")
        assert result is not None
        assert result["prompt"] == "Do the thing"
        assert result["agent_id"] == "py"

        # Request should now be claimed
        with bridge._channel_lock:
            for req in bridge._channel_queues["general"]:
                if req["id"] == req_id:
                    assert req["status"] == "claimed"
                    assert req["claimed_by"] == "sess-1"

    def test_claim_returns_none_for_nonexistent(self, bridge):
        result = bridge.claim_request("nonexistent-id")
        assert result is None

    def test_claim_returns_none_for_already_claimed(self, bridge):
        req_id = self._enqueue(bridge)
        bridge.claim_request(req_id, "sess-1")

        # Second claim should fail
        result = bridge.claim_request(req_id, "sess-2")
        assert result is None

    def test_deliver_response_completes_request(self, bridge):
        req_id = self._enqueue(bridge)
        bridge.claim_request(req_id, "sess-1")

        # Mock _check_and_rotate to avoid LLM calls
        with patch.object(bridge, "_check_and_rotate", return_value=False):
            success = bridge.deliver_response(req_id, "Here's the answer", metadata={"tokens": 50})

        assert success is True

    def test_deliver_response_fails_for_unclaimed(self, bridge):
        req_id = self._enqueue(bridge)

        with patch.object(bridge, "_check_and_rotate", return_value=False):
            success = bridge.deliver_response(req_id, "Answer")

        assert success is False


class TestChannelModeActive:
    """Tests for channel_mode_active()."""

    def test_returns_true_with_healthy_session(self, bridge):
        now = time.time()
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "s1": {"pid": 100, "last_heartbeat": now, "process": None}
            }

        assert bridge.channel_mode_active("general") is True

    def test_returns_false_with_no_sessions(self, bridge):
        assert bridge.channel_mode_active("general") is False

    def test_returns_false_with_dead_session(self, bridge):
        old = time.time() - bridge.HEARTBEAT_TIMEOUT_S - 10
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "s1": {"pid": 100, "last_heartbeat": old, "process": None}
            }

        assert bridge.channel_mode_active("general") is False

    def test_any_channel_check(self, bridge):
        """channel_mode_active(None) checks all channels."""
        now = time.time()
        with bridge._sessions_lock:
            bridge._channel_sessions["ch1"] = {
                "s1": {"pid": 100, "last_heartbeat": now, "process": None}
            }

        assert bridge.channel_mode_active(None) is True


# =====================================================================
# Concurrency Tests (Tier 4 basics)
# =====================================================================

class TestConcurrentRegistration:
    """Test thread-safety of session registration."""

    def test_concurrent_registers_no_duplicates(self, bridge):
        """Multiple threads registering for the same channel shouldn't create
        duplicate adoptions of the same pending spawn."""
        # Set up one pending spawn
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "spawn-1": {
                    "pid": 9999, "last_heartbeat": None,
                    "last_activity": time.time(), "registered_at": time.time(),
                    "process": MagicMock(),
                }
            }

        results = []
        barrier = threading.Barrier(3)

        def register(sess_id, pid):
            barrier.wait()
            r = bridge.register_channel_session("general", sess_id, pid)
            results.append(r)

        threads = [
            threading.Thread(target=register, args=(f"sess-{i}", 1000 + i))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should succeed (one adopts, others create new)
        assert all(r["ok"] for r in results)

        # Should not have spawn-1 key anymore (it was adopted)
        with bridge._sessions_lock:
            assert "spawn-1" not in bridge._channel_sessions.get("general", {})

    def test_concurrent_enqueue_dedup(self, bridge):
        """Concurrent enqueues for same agent+channel are deduplicated."""
        results = []
        barrier = threading.Barrier(5)

        def enqueue(i):
            barrier.wait()
            with patch.object(bridge, "request_session"):
                r = bridge.enqueue_channel_request(f"msg-{i}", "py", "general")
                results.append(r)

        threads = [threading.Thread(target=enqueue, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Due to dedup window, all should return the same request_id
        # (first one created, rest deduped within 5s)
        assert len(set(results)) == 1

        with bridge._channel_lock:
            assert len(bridge._channel_queues["general"]) == 1
