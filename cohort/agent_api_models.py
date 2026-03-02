"""Pydantic models for the Cohort Agent API.

Request/response models for the agent-as-a-service endpoints.
Adapted from BOSS agent_gateway patterns for the cohort ecosystem.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# -- Health -----------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    uptime_seconds: float
    agent_count: int


# -- Agent list -------------------------------------------------------------

class AgentListItem(BaseModel):
    agent_id: str
    name: str
    role: str
    agent_type: str
    status: str
    avatar: str
    tier: str  # "free" or "pro"


class AgentListResponse(BaseModel):
    agents: list[AgentListItem]
    total: int
    tier: str  # caller's tier


# -- Agent profile (bundled) -----------------------------------------------

class AgentProfileResponse(BaseModel):
    agent_id: str
    config: dict[str, Any]
    prompt: str | None
    recent_facts: list[dict[str, Any]]


# -- Agent status -----------------------------------------------------------

class AgentStatusResponse(BaseModel):
    agent_id: str
    name: str
    role: str
    agent_type: str
    skill_avg: float
    last_training: str | None
    capabilities: list[str]


# -- Tier info --------------------------------------------------------------

class TierAgent(BaseModel):
    agent_id: str
    name: str
    role: str


class TierInfo(BaseModel):
    tier: str
    description: str
    agents: list[TierAgent]
    agent_count: int


class TiersResponse(BaseModel):
    tiers: list[TierInfo]
