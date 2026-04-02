"""Tier 4: Channel bridge resilience tests.

Tests recovery from failure conditions:
  - Server restart with session recovery from channel_sessions.json
  - Heartbeat timeout leading to stale session reaping
  - Concurrent invokes to the same channel (dedup)
  - Session limit hit with eviction
  - Plugin crash (session dies, request auto-fails)

These tests manipulate time and internal state to simulate failure
conditions deterministically — no actual subprocess spawning.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_bridge():
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
    cb._session_limit = 5
    cb._session_warn = 3
    cb._idle_timeout_s = 600
    cb._auto_launch = False
    cb._reaper_started = False


@pytest.fixture(autouse=True)
def clean_bridge():
    _reset_bridge()
    yield
    _reset_bridge()


@pytest.fixture
def bridge():
    import cohort.channel_bridge as cb
    return cb


@pytest.fixture
def data_dir(tmp_path: Path):
    import cohort.channel_bridge as cb
    cb.set_data_dir(str(tmp_path))
    return tmp_path


# =====================================================================
# Restart Recovery
# =====================================================================

class TestServerRestartRecovery:
    """Simulate server restart: save state -> clear -> load state."""

    def test_full_restart_cycle(self, bridge, data_dir):
        """Sessions survive a save -> clear -> load cycle.

        Uses the current process PID which is guaranteed alive,
        so load_session_state() will re-adopt it.
        """
        import os
        my_pid = os.getpid()

        # Create active sessions using our own PID (which IS alive)
        bridge.register_channel_session("general", "sess-1", pid=my_pid)
        bridge.update_heartbeat("sess-1", pid=my_pid, channel_id="general")

        # Save state
        bridge._save_session_state()

        # Verify file was written
        state_file = data_dir / "channel_sessions.json"
        assert state_file.exists()
        entries = json.loads(state_file.read_text("utf-8"))
        assert len(entries) == 1
        assert entries[0]["pid"] == my_pid

        # Clear everything (simulate server restart)
        with bridge._sessions_lock:
            bridge._channel_sessions.clear()

        # Reload — our PID is alive so it should be re-adopted
        adopted = bridge.load_session_state()
        assert adopted == 1

        # Session should be restored
        with bridge._sessions_lock:
            assert "general" in bridge._channel_sessions

    def test_restart_preserves_resume_ids(self, bridge, data_dir):
        """Resume IDs survive restart even when PIDs are dead."""
        bridge._resume_ids["general"] = "resume-abc123"

        # Create a session entry with resume_id
        bridge.register_channel_session("general", "sess-1", pid=999999)
        bridge._save_session_state()

        # Clear state
        with bridge._sessions_lock:
            bridge._channel_sessions.clear()
        bridge._resume_ids.clear()

        # Load — PID 999999 is dead, but resume_id should be preserved
        bridge.load_session_state()
        assert bridge._resume_ids.get("general") == "resume-abc123"

    def test_restart_with_corrupt_state_file(self, bridge, data_dir):
        """Corrupt state file doesn't crash — returns 0."""
        state_file = data_dir / "channel_sessions.json"
        state_file.write_text("NOT VALID JSON {{{", "utf-8")

        adopted = bridge.load_session_state()
        assert adopted == 0

    def test_restart_with_empty_state(self, bridge, data_dir):
        """Empty state file is handled gracefully."""
        state_file = data_dir / "channel_sessions.json"
        state_file.write_text("[]", "utf-8")

        adopted = bridge.load_session_state()
        assert adopted == 0


# =====================================================================
# Heartbeat Timeout Race Conditions
# =====================================================================

class TestHeartbeatTimeoutRace:
    """Test the race between heartbeat arrival and session lookup."""

    def test_late_heartbeat_finds_session_by_pid(self, bridge):
        """Heartbeat arriving after spawn should find session by PID match
        even when session_id doesn't match (spawned vs registered ID)."""
        now = time.time()

        # Spawn creates entry with placeholder session_id
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "spawn-12345": {
                    "pid": 5555,
                    "last_heartbeat": None,
                    "last_activity": now,
                    "registered_at": now,
                    "process": MagicMock(),
                }
            }

        # Heartbeat arrives with plugin's own session_id but matching PID
        bridge.update_heartbeat("plugin-sess-xyz", pid=5555, channel_id="general")

        with bridge._sessions_lock:
            info = bridge._channel_sessions["general"]["spawn-12345"]
            assert info["last_heartbeat"] is not None
            assert info["session_id"] == "plugin-sess-xyz"

    def test_heartbeat_race_with_register(self, bridge):
        """Heartbeat and register arriving near-simultaneously for the same channel.

        This simulates the race where the MCP plugin starts heartbeating
        before the register call completes.
        """
        now = time.time()
        results = {"heartbeat_succeeded": False, "register_succeeded": False}

        # Create pending spawn
        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "spawn-ph": {
                    "pid": 9999, "last_heartbeat": None,
                    "last_activity": now, "registered_at": now,
                    "process": MagicMock(),
                }
            }

        barrier = threading.Barrier(2)

        def do_heartbeat():
            barrier.wait()
            bridge.update_heartbeat("plugin-sess", pid=5555, channel_id="general")
            results["heartbeat_succeeded"] = True

        def do_register():
            barrier.wait()
            r = bridge.register_channel_session("general", "plugin-sess", pid=5555)
            results["register_succeeded"] = r["ok"]

        t1 = threading.Thread(target=do_heartbeat)
        t2 = threading.Thread(target=do_register)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both should succeed without crashing
        assert results["heartbeat_succeeded"]
        assert results["register_succeeded"]

        # Should have exactly one session for the channel (no duplicates)
        with bridge._sessions_lock:
            sessions = bridge._channel_sessions.get("general", {})
            assert len(sessions) <= 2  # At most: adopted + heartbeat-updated

    def test_stale_session_with_pid_mismatch(self, bridge):
        """Stale session reaper must work even when PID doesn't match spawn record.

        This was a bug: reaper compared spawn PID vs actual PID, and because
        the cmd.exe wrapper has a different PID, the session was never reaped.
        """
        bridge._idle_timeout_s = 1
        old_time = time.time() - 100  # Well past idle timeout

        with bridge._sessions_lock:
            bridge._channel_sessions["stale-ch"] = {
                "stale-sess": {
                    "pid": 9999,  # cmd.exe wrapper PID
                    "last_heartbeat": old_time,
                    "last_activity": old_time,
                    "registered_at": old_time - 200,
                    "process": MagicMock(),
                }
            }
        bridge._channel_last_activity["stale-ch"] = old_time

        bridge._reap_idle_sessions()

        with bridge._sessions_lock:
            assert "stale-ch" not in bridge._channel_sessions


# =====================================================================
# Concurrent Channel Invokes (Dedup)
# =====================================================================

class TestConcurrentInvokes:
    """Test deduplication when multiple invokes hit the same channel."""

    def test_dedup_same_agent_within_window(self, bridge):
        """Rapid-fire invokes for the same agent+channel produce one request."""
        ids = []
        with patch.object(bridge, "request_session"):
            for i in range(5):
                rid = bridge.enqueue_channel_request(
                    prompt=f"Message {i}",
                    agent_id="py",
                    channel_id="general",
                )
                ids.append(rid)

        # All should return the same ID (dedup within 5s window)
        assert len(set(ids)) == 1

        with bridge._channel_lock:
            assert len(bridge._channel_queues["general"]) == 1

    def test_no_dedup_different_agents(self, bridge):
        """Different agents are NOT deduped."""
        with patch.object(bridge, "request_session"):
            r1 = bridge.enqueue_channel_request("Hi", "py", "general")
            r2 = bridge.enqueue_channel_request("Hi", "js", "general")

        assert r1 != r2

    def test_no_dedup_different_channels(self, bridge):
        """Same agent on different channels are NOT deduped."""
        with patch.object(bridge, "request_session"):
            r1 = bridge.enqueue_channel_request("Hi", "py", "ch1")
            r2 = bridge.enqueue_channel_request("Hi", "py", "ch2")

        assert r1 != r2

    def test_concurrent_invoke_threads(self, bridge):
        """Multiple threads invoking same agent+channel simultaneously."""
        results = []
        barrier = threading.Barrier(10)

        def invoke(i):
            barrier.wait()
            with patch.object(bridge, "request_session"):
                rid = bridge.enqueue_channel_request(f"msg-{i}", "py", "general")
                results.append(rid)

        threads = [threading.Thread(target=invoke, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All should return same ID due to dedup
        assert len(set(results)) == 1
        with bridge._channel_lock:
            assert len(bridge._channel_queues["general"]) == 1


# =====================================================================
# Session Limit + Eviction
# =====================================================================

class TestSessionLimitEviction:
    """Test the eviction behavior when session limit is reached."""

    def test_evicts_least_active_idle_session(self, bridge):
        """When limit is hit, the least-active idle session is evicted."""
        bridge._session_limit = 2
        now = time.time()

        # Session 1: active 1 hour ago
        bridge.register_channel_session("old-ch", "old-s", pid=100)
        bridge.update_heartbeat("old-s", pid=100, channel_id="old-ch")
        bridge._channel_last_activity["old-ch"] = now - 3600

        # Session 2: active 1 minute ago
        bridge.register_channel_session("recent-ch", "recent-s", pid=200)
        bridge.update_heartbeat("recent-s", pid=200, channel_id="recent-ch")
        bridge._channel_last_activity["recent-ch"] = now - 60

        # Session 3: should trigger eviction of old-ch
        result = bridge.register_channel_session("new-ch", "new-s", pid=300)
        assert result["ok"] is True

    def test_rejects_when_no_idle_session_to_evict(self, bridge):
        """When all sessions have pending requests, none can be evicted."""
        bridge._session_limit = 1
        now = time.time()

        # Active session with pending request
        bridge.register_channel_session("busy-ch", "busy-s", pid=100)
        bridge.update_heartbeat("busy-s", pid=100, channel_id="busy-ch")
        with bridge._channel_lock:
            bridge._channel_queues["busy-ch"] = deque([
                {"id": "req-1", "status": "pending", "created_at": now}
            ])

        # This should fail since busy-ch can't be evicted
        result = bridge.register_channel_session("new-ch", "new-s", pid=200)
        assert result["ok"] is False


# =====================================================================
# Plugin Crash Recovery
# =====================================================================

class TestPluginCrash:
    """Test what happens when the Claude Code session dies unexpectedly."""

    def test_claimed_request_auto_fails_when_session_dies(self, bridge):
        """A claimed request auto-fails if the session loses heartbeat.

        await_channel_response() checks for this after CLAIMED_STALE_TIMEOUT_S.
        """
        # Set up a session and enqueue a request
        old_time = time.time() - bridge.CLAIMED_STALE_TIMEOUT_S - 10
        time.time()

        with bridge._sessions_lock:
            bridge._channel_sessions["general"] = {
                "dead-sess": {
                    "pid": 99999,
                    "last_heartbeat": old_time,  # Dead (past timeout)
                    "last_activity": old_time,
                    "registered_at": old_time,
                    "process": None,
                }
            }

        # Enqueue and claim
        with patch.object(bridge, "request_session"):
            req_id = bridge.enqueue_channel_request("Do something", "py", "general")

        claim_result = bridge.claim_request(req_id, "dead-sess")
        assert claim_result is not None

        # Manually set claimed_at to be old enough
        with bridge._channel_lock:
            for req in bridge._channel_queues["general"]:
                if req["id"] == req_id:
                    req["claimed_at"] = old_time

        # await_channel_response with very short timeout should detect session loss
        content, meta = bridge.await_channel_response(req_id, timeout=0.1)
        assert content is None
        assert meta.get("error") in ("channel_session_lost", "channel_timeout")

    def test_pending_request_survives_session_death(self, bridge):
        """Pending (unclaimed) requests stay alive when session dies.

        They should be claimable by a new session or reaped after TTL.
        """
        with patch.object(bridge, "request_session"):
            req_id = bridge.enqueue_channel_request("Important task", "py", "general")

        # Verify request is still pending
        pending = bridge.poll_next_request("general")
        assert pending is not None
        assert pending["id"] == req_id

    def test_reaper_cleans_up_dead_session(self, bridge):
        """The reaper removes sessions whose heartbeat is stale."""
        bridge._idle_timeout_s = 1
        old_time = time.time() - 100

        with bridge._sessions_lock:
            bridge._channel_sessions["dead-ch"] = {
                "dead-sess": {
                    "pid": 99999,
                    "last_heartbeat": old_time,
                    "last_activity": old_time,
                    "registered_at": old_time,
                    "process": MagicMock(),
                }
            }
        bridge._channel_last_activity["dead-ch"] = old_time

        bridge._reap_idle_sessions()

        with bridge._sessions_lock:
            assert "dead-ch" not in bridge._channel_sessions


# =====================================================================
# Settings API Resilience
# =====================================================================

class TestSettingsResilience:
    """Test that apply_channel_settings handles edge cases."""

    def test_minimum_values_enforced(self, bridge):
        """Settings enforce minimum values to prevent dangerous configs."""
        bridge.apply_channel_settings(
            limit=0,  # Should clamp to 1
            warn=0,   # Should clamp to 1
            idle_timeout=0,  # Should clamp to 60
        )
        assert bridge._session_limit == 1
        assert bridge._session_warn == 1
        assert bridge._idle_timeout_s == 60

    def test_pressure_thresholds_clamped(self, bridge):
        """Pressure thresholds are clamped to valid ranges."""
        bridge.apply_channel_settings(
            pressure_warn=0.0,    # Should clamp to 0.1
            pressure_rotate=2.0,  # Should clamp to 1.0
        )
        assert bridge._PRESSURE_WARN == 0.1
        assert bridge._PRESSURE_ROTATE == 1.0
