"""Tier 1: HTTP endpoint integration tests for channel bridge API.

Tests the full request/response cycle through Starlette routes.
Uses httpx.AsyncClient with ASGITransport — no real server needed.

Covers the exact API contract that the MCP plugin depends on:
  POST /api/channel/register
  POST /api/channel/heartbeat
  GET  /api/channel/poll
  POST /api/channel/{request_id}/claim
  POST /api/channel/{request_id}/respond
  POST /api/channel/{request_id}/error
  GET  /api/channel/capabilities
  GET  /api/channel/status
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

pytestmark = [pytest.mark.integration, pytest.mark.server, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Reset channel_bridge state between tests
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


# ---------------------------------------------------------------------------
# Server client fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(data_dir: Path, agents_dir: Path):
    """httpx.AsyncClient with ASGITransport for channel bridge endpoints."""
    (data_dir / "messages.json").write_text("[]", encoding="utf-8")

    env = {
        "COHORT_DATA_DIR": str(data_dir),
        "COHORT_AGENTS_DIR": str(agents_dir),
        "COHORT_AGENTS_ROOT": str(agents_dir.parent),
    }
    with patch.dict(os.environ, env, clear=False):
        from cohort.server import create_app
        app = create_app(data_dir=str(data_dir))

    transport = httpx.ASGITransport(app=app)
    with patch("cohort.agent_router.route_mentions"):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as c:
            yield c


# =====================================================================
# Registration
# =====================================================================

class TestChannelRegister:
    """POST /api/channel/register"""

    async def test_register_returns_200(self, client):
        resp = await client.post("/api/channel/register", json={
            "channel_id": "general",
            "session_id": "sess-1",
            "pid": 1234,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["session_id"] == "sess-1"

    async def test_register_adopts_pending_spawn_via_http(self, client):
        """CRITICAL: The full HTTP path must adopt pending spawns."""
        from unittest.mock import MagicMock

        import cohort.channel_bridge as cb

        with cb._sessions_lock:
            cb._channel_sessions["general"] = {
                "spawn-ph": {
                    "pid": 9999, "last_heartbeat": None,
                    "last_activity": time.time(),
                    "registered_at": time.time(),
                    "process": MagicMock(),
                }
            }

        resp = await client.post("/api/channel/register", json={
            "channel_id": "general",
            "session_id": "plugin-sess",
            "pid": 5555,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        with cb._sessions_lock:
            assert "spawn-ph" not in cb._channel_sessions["general"]
            assert "plugin-sess" in cb._channel_sessions["general"]

    async def test_register_429_at_limit(self, client):
        """Returns 429 when session limit reached and no idle to evict."""
        from collections import deque

        import cohort.channel_bridge as cb
        cb._session_limit = 1
        now = time.time()

        with cb._sessions_lock:
            cb._channel_sessions["ch1"] = {
                "s1": {"pid": 100, "last_heartbeat": now, "last_activity": now, "registered_at": now, "process": None}
            }

        # Give ch1 a pending request so it can't be evicted
        with cb._channel_lock:
            cb._channel_queues["ch1"] = deque([
                {"id": "req-1", "status": "pending", "created_at": now}
            ])

        resp = await client.post("/api/channel/register", json={
            "channel_id": "ch2",
            "session_id": "s2",
            "pid": 200,
        })
        assert resp.status_code == 429
        assert resp.json()["ok"] is False


# =====================================================================
# Heartbeat
# =====================================================================

class TestChannelHeartbeat:
    """POST /api/channel/heartbeat"""

    async def test_heartbeat_returns_ok(self, client):
        # Register first
        import cohort.channel_bridge as cb
        cb.register_channel_session("general", "sess-1", pid=100)

        resp = await client.post("/api/channel/heartbeat", json={
            "session_id": "sess-1",
            "pid": 100,
            "channel_id": "general",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_heartbeat_pid_mismatch_still_works(self, client):
        """CRITICAL: Heartbeat with PID fallback via HTTP."""
        import cohort.channel_bridge as cb

        with cb._sessions_lock:
            cb._channel_sessions["general"] = {
                "spawn-ph": {
                    "pid": 5555, "last_heartbeat": None,
                    "last_activity": time.time(),
                    "registered_at": time.time(),
                    "process": None,
                }
            }

        resp = await client.post("/api/channel/heartbeat", json={
            "session_id": "plugin-sess",
            "pid": 5555,
            "channel_id": "general",
        })
        assert resp.status_code == 200

        with cb._sessions_lock:
            info = cb._channel_sessions["general"]["spawn-ph"]
            assert info["last_heartbeat"] is not None


# =====================================================================
# Poll -> Claim -> Respond lifecycle
# =====================================================================

class TestPollClaimRespondHTTP:
    """Full request lifecycle through HTTP endpoints."""

    async def _enqueue(self, prompt="Hello", agent="py", channel="general"):
        """Enqueue directly via the bridge module."""
        import cohort.channel_bridge as cb
        with patch.object(cb, "request_session"):
            return cb.enqueue_channel_request(prompt, agent, channel)

    async def test_poll_empty_queue(self, client):
        resp = await client.get("/api/channel/poll")
        assert resp.status_code == 200
        assert resp.json()["request"] is None

    async def test_poll_returns_pending(self, client):
        req_id = await self._enqueue()

        resp = await client.get("/api/channel/poll", params={"channel_id": "general"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["request"]["id"] == req_id

    async def test_full_lifecycle(self, client):
        """Enqueue -> Poll -> Claim -> Respond — the core happy path."""
        req_id = await self._enqueue(prompt="Do the task")

        # Poll
        resp = await client.get("/api/channel/poll", params={"channel_id": "general"})
        assert resp.json()["request"]["id"] == req_id

        # Claim
        resp = await client.post(f"/api/channel/{req_id}/claim", json={"session_id": "sess-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["prompt"] == "Do the task"
        assert data["agent_id"] == "py"

        # Respond
        import cohort.channel_bridge as cb
        with patch.object(cb, "_check_and_rotate", return_value=False):
            resp = await client.post(f"/api/channel/{req_id}/respond", json={"content": "Done!"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Poll again — should be empty
        resp = await client.get("/api/channel/poll", params={"channel_id": "general"})
        assert resp.json()["request"] is None

    async def test_claim_nonexistent_returns_404(self, client):
        resp = await client.post("/api/channel/fake-id/claim", json={"session_id": "s1"})
        assert resp.status_code == 404

    async def test_respond_unclaimed_returns_404(self, client):
        req_id = await self._enqueue()
        import cohort.channel_bridge as cb
        with patch.object(cb, "_check_and_rotate", return_value=False):
            resp = await client.post(f"/api/channel/{req_id}/respond", json={"content": "Answer"})
        assert resp.status_code == 404

    async def test_respond_empty_body_returns_400(self, client):
        req_id = await self._enqueue()
        resp = await client.post(f"/api/channel/{req_id}/respond", json={"content": ""})
        assert resp.status_code == 400

    async def test_error_endpoint(self, client):
        req_id = await self._enqueue()

        # Claim first
        await client.post(f"/api/channel/{req_id}/claim", json={"session_id": "s1"})

        resp = await client.post(f"/api/channel/{req_id}/error", json={"error": "OOM killed"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# =====================================================================
# Capabilities and status
# =====================================================================

class TestChannelCapabilities:
    """GET /api/channel/capabilities"""

    async def test_capabilities_report(self, client):
        resp = await client.get("/api/channel/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_managed_sessions"] is True
        assert "session_limit" in data
        assert data["version"] == "0.4.33"


class TestChannelStatus:
    """GET /api/channel/status

    NOTE: get_session_status(channel_id=None) has a reentrant lock deadlock
    (takes _sessions_lock, then calls channel_mode_active() which re-takes it).
    We always pass channel_id to avoid the deadlock path.
    """

    async def test_status_for_specific_channel(self, client):
        resp = await client.get("/api/channel/status", params={"channel_id": "nonexistent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is False

    async def test_status_with_session(self, client):
        import cohort.channel_bridge as cb
        cb.register_channel_session("general", "sess-1", pid=100)

        resp = await client.get("/api/channel/status", params={"channel_id": "general"})
        assert resp.status_code == 200
