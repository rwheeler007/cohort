"""Integration tests for Cohort Agent API (FastAPI, port 8200).

Tests endpoint coverage, tier-based access control, error responses,
and rate limiting. Uses httpx.AsyncClient with ASGITransport for full
request/response testing without external dependencies.

All fixtures provided by conftest.py: agent_api_client, agents_dir,
free_headers, pro_headers, enterprise_headers, no_auth_headers.
"""

from __future__ import annotations

import pytest


# =====================================================================
# D2: Endpoint coverage
# =====================================================================


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_health_returns_200_with_required_keys(agent_api_client):
    """GET /health returns 200 with status, service, and version keys."""
    resp = await agent_api_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["service"] == "cohort-agent-api"
    assert "version" in body
    assert "uptime_seconds" in body
    assert "agent_count" in body
    assert body["agent_count"] == 5  # 5 mock agents in agents_dir


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_list_agents_with_pro_key(agent_api_client, pro_headers):
    """GET /agents with pro key returns agents list with tier info."""
    resp = await agent_api_client.get("/agents", headers=pro_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "agents" in body
    assert "total" in body
    assert "tier" in body
    assert body["tier"] == "pro"
    assert isinstance(body["agents"], list)
    assert body["total"] == len(body["agents"])


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_get_agent_config(agent_api_client, pro_headers):
    """GET /agents/{id}/config returns the agent config dict."""
    resp = await agent_api_client.get(
        "/agents/python_developer/config", headers=pro_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "python_developer"
    assert "name" in body
    assert "role" in body
    assert "skill_levels" in body


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_get_agent_prompt(agent_api_client, pro_headers):
    """GET /agents/{id}/prompt returns prompt text with agent_id."""
    resp = await agent_api_client.get(
        "/agents/python_developer/prompt", headers=pro_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "python_developer"
    assert "prompt" in body
    assert "python_developer" in body["prompt"]


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_get_agent_profile(agent_api_client, pro_headers):
    """GET /agents/{id}/profile returns bundled config + prompt + facts."""
    resp = await agent_api_client.get(
        "/agents/python_developer/profile", headers=pro_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "python_developer"
    assert "config" in body
    assert isinstance(body["config"], dict)
    assert "prompt" in body
    assert "recent_facts" in body
    assert isinstance(body["recent_facts"], list)


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_get_agent_status(agent_api_client, pro_headers):
    """GET /agents/{id}/status returns status with skill_avg."""
    resp = await agent_api_client.get(
        "/agents/python_developer/status", headers=pro_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "python_developer"
    assert "name" in body
    assert "role" in body
    assert "skill_avg" in body
    assert isinstance(body["skill_avg"], (int, float))
    assert "capabilities" in body


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_get_tiers(agent_api_client):
    """GET /tiers returns tier structure with free/pro/enterprise."""
    resp = await agent_api_client.get("/tiers")
    assert resp.status_code == 200
    body = resp.json()
    assert "tiers" in body
    tiers = body["tiers"]
    assert len(tiers) == 3
    tier_names = [t["tier"] for t in tiers]
    assert tier_names == ["free", "pro", "enterprise"]
    for tier in tiers:
        assert "description" in tier
        assert "agents" in tier
        assert "agent_count" in tier
        assert tier["agent_count"] == len(tier["agents"])


# =====================================================================
# D3: Tier gating
# =====================================================================


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_anonymous_sees_only_free_tier_agents(
    agent_api_client, no_auth_headers
):
    """Anonymous GET /agents returns only free-tier agents from mock dir.

    Mock agents_dir has 5 agents. Of those, 3 are in FREE_TIER_AGENTS:
    python_developer, web_developer, coding_orchestrator.
    cohort_orchestrator is enterprise-only, ceo_agent is pro-only.
    """
    resp = await agent_api_client.get("/agents", headers=no_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "anonymous"
    agent_ids = sorted(a["agent_id"] for a in body["agents"])
    assert agent_ids == ["coding_orchestrator", "python_developer", "web_developer"]
    assert body["total"] == 3


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_pro_key_sees_all_non_enterprise_agents(
    agent_api_client, pro_headers
):
    """Pro key GET /agents returns free + pro agents, excludes enterprise.

    Expected: python_developer, web_developer, coding_orchestrator (free)
    + ceo_agent (pro). cohort_orchestrator excluded (enterprise-only).
    """
    resp = await agent_api_client.get("/agents", headers=pro_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "pro"
    agent_ids = sorted(a["agent_id"] for a in body["agents"])
    assert agent_ids == [
        "ceo_agent",
        "coding_orchestrator",
        "python_developer",
        "web_developer",
    ]
    assert body["total"] == 4


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_enterprise_key_sees_all_agents(
    agent_api_client, enterprise_headers
):
    """Enterprise key GET /agents returns all 5 mock agents."""
    resp = await agent_api_client.get("/agents", headers=enterprise_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "enterprise"
    agent_ids = sorted(a["agent_id"] for a in body["agents"])
    assert agent_ids == [
        "ceo_agent",
        "coding_orchestrator",
        "cohort_orchestrator",
        "python_developer",
        "web_developer",
    ]
    assert body["total"] == 5


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_free_key_cannot_access_enterprise_agent_config(
    agent_api_client, free_headers
):
    """Free key GET /agents/cohort_orchestrator/config returns 403."""
    resp = await agent_api_client.get(
        "/agents/cohort_orchestrator/config", headers=free_headers
    )
    assert resp.status_code == 403


# =====================================================================
# D4: Error responses
# =====================================================================


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_nonexistent_agent_returns_404(agent_api_client, pro_headers):
    """GET /agents/nonexistent_xyz/config with pro key returns 404."""
    resp = await agent_api_client.get(
        "/agents/nonexistent_xyz/config", headers=pro_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_invalid_api_key_returns_403(agent_api_client):
    """Invalid API key returns 403 on a tier-gated endpoint."""
    headers = {"X-API-Key": "totally-bogus-key-12345"}
    resp = await agent_api_client.get("/agents", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_free_key_blocked_from_enterprise_agent(
    agent_api_client, free_headers
):
    """Free key accessing enterprise-only cohort_orchestrator/config gets 403."""
    resp = await agent_api_client.get(
        "/agents/cohort_orchestrator/config", headers=free_headers
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "Enterprise" in body["detail"]


# =====================================================================
# D5: Rate limiting
# =====================================================================


@pytest.mark.asyncio
@pytest.mark.agent_api
async def test_rate_limit_triggers_at_121_requests(
    agent_api_client, pro_headers
):
    """Send 121 rapid requests with pro key; request 121 gets 429.

    The rate limiter allows 120 requests per minute per key.
    We clear _request_counts first to guarantee a clean window.
    """
    import cohort.agent_api as api_mod

    api_mod._request_counts.clear()

    # Fire 120 requests (all should succeed)
    for _ in range(120):
        resp = await agent_api_client.get("/health", headers=pro_headers)
        # /health does not call check_rate_limit, so use /agents instead
    # Actually /health has no rate limit check -- use /agents
    api_mod._request_counts.clear()

    for i in range(121):
        resp = await agent_api_client.get("/agents", headers=pro_headers)
        if i < 120:
            assert resp.status_code == 200, f"Request {i} failed unexpectedly"

    # The 121st request (index 120) should be rate-limited
    assert resp.status_code == 429
    body = resp.json()
    assert "Rate limit" in body["detail"]
