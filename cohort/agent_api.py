"""Cohort Agent API -- Agent-as-a-Service server.

A FastAPI service that serves agent intelligence (configs, prompts, learned
facts) to cohort users over HTTP. This is the monetization layer: 5 hardcover
agents ship with pip install, free-tier users get 7 more from the Agent Store
(12 total), Pro-tier users get the full 22+ roster.

This server runs on YOUR infrastructure (not the user's machine). The local
cohort server (server.py) is the user-facing UI server. This is the agent
distribution endpoint.

Modeled on BOSS agent_gateway/service.py with tier-based access control.

Usage::

    python -m cohort serve-agents                    # default port 8200
    python -m cohort serve-agents --port 8200        # custom port
    python -m cohort serve-agents --agents-dir /path # custom agents dir

Endpoints::

    GET  /health                          -- Service health
    GET  /agents                          -- List available agents (tier-filtered)
    GET  /agents/{agent_id}/config        -- Agent config JSON
    GET  /agents/{agent_id}/prompt        -- Agent prompt markdown
    GET  /agents/{agent_id}/profile       -- Bundled config + prompt + recent facts
    GET  /agents/{agent_id}/status        -- Skill avg, last training, capabilities
    GET  /tiers                           -- What's in Free vs Pro vs Enterprise
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from cohort.agent_api_models import (
    AgentListItem,
    AgentListResponse,
    AgentProfileResponse,
    AgentStatusResponse,
    HealthResponse,
    TierAgent,
    TierInfo,
    TiersResponse,
)

# ---------------------------------------------------------------------------
# Environment and logging
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cohort.agent_api")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENTS_DIR = Path(os.getenv("COHORT_AGENT_API_DIR", "agents"))
AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# API keys: map key -> tier. Loaded from env or a keys file.
# Format: COHORT_AGENT_API_KEYS=key1:pro,key2:free,key3:enterprise
_API_KEYS: dict[str, str] = {}

# Rate limiting: per-key request counts
_request_counts: dict[str, list[float]] = {}
READ_RATE_LIMIT = 120  # max reads per minute per key

# Hardcover agents -- ship with `pip install cohort`, always available locally.
# These do NOT come from the Agent Store / Gateway API.
HARDCOVER_AGENTS: set[str] = {
    "cohort_orchestrator",      # multi-agent workflow engine
    "marketing_agent",          # strategy and growth
    "content_strategy_agent",   # content planning
    "analytics_agent",          # data and insights
    "python_developer",         # code development
}

# Free-tier agent IDs -- available from the Agent Store at no cost.
# Combined with hardcover agents, free users get 12 agents total.
FREE_TIER_AGENTS: set[str] = {
    # Hardcover (also listed here for visibility checks)
    *HARDCOVER_AGENTS,
    # Free Agent Store agents
    "web_developer",
    "javascript_developer",
    "security_agent",
    "qa_agent",
    "documentation_agent",
    "code_archaeologist",
    "setup_guide",
}

# Enterprise-only agents (factory layer -- not visible to free or pro)
ENTERPRISE_ONLY_AGENTS: set[str] = {
    "supervisor_agent",
}

# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


def _load_api_keys() -> None:
    """Load API keys from environment variable.

    Format: COHORT_AGENT_API_KEYS=key1:pro,key2:free
    """
    global _API_KEYS
    raw = os.getenv("COHORT_AGENT_API_KEYS", "")
    if not raw:
        return
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" in entry:
            key, tier = entry.rsplit(":", 1)
            _API_KEYS[key.strip()] = tier.strip().lower()
        elif entry:
            # Key without tier defaults to free
            _API_KEYS[entry.strip()] = "free"


def resolve_tier(api_key: str) -> str:
    """Resolve an API key to a tier. Returns 'anonymous' if no key."""
    if not api_key:
        return "anonymous"
    tier = _API_KEYS.get(api_key)
    if tier is None:
        raise HTTPException(403, "Invalid API key")
    return tier


def check_rate_limit(api_key: str) -> None:
    """Enforce per-key read rate limit."""
    if not api_key:
        return  # anonymous gets no rate limit (but limited agents)
    now = time.time()
    if api_key not in _request_counts:
        _request_counts[api_key] = []
    _request_counts[api_key] = [
        t for t in _request_counts[api_key] if now - t < 60
    ]
    if len(_request_counts[api_key]) >= READ_RATE_LIMIT:
        raise HTTPException(429, f"Rate limit exceeded: max {READ_RATE_LIMIT}/min")
    _request_counts[api_key].append(now)


# ---------------------------------------------------------------------------
# Agent helpers
# ---------------------------------------------------------------------------


def validate_agent_id(agent_id: str) -> Path:
    """Validate agent_id and return agent directory."""
    if not AGENT_NAME_PATTERN.match(agent_id):
        raise HTTPException(400, f"Invalid agent ID: {agent_id}")
    agent_dir = AGENTS_DIR / agent_id
    if not agent_dir.is_dir():
        raise HTTPException(404, f"Agent not found: {agent_id}")
    return agent_dir


def agent_visible_to_tier(agent_id: str, tier: str) -> bool:
    """Check if an agent is visible to a given tier."""
    # Enterprise-only agents are gated behind the enterprise tier
    if agent_id in ENTERPRISE_ONLY_AGENTS:
        return tier == "enterprise"
    if tier in ("pro", "enterprise"):
        return True
    # Free and anonymous only see free-tier agents
    return agent_id in FREE_TIER_AGENTS


def load_agent_config(agent_dir: Path) -> dict[str, Any]:
    """Load an agent's config JSON."""
    config_path = agent_dir / "agent_config.json"
    if not config_path.exists():
        raise HTTPException(404, f"No config found for agent: {agent_dir.name}")
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(500, f"Error reading config: {exc}")


def load_agent_prompt(agent_dir: Path) -> str | None:
    """Load an agent's prompt markdown."""
    prompt_path = agent_dir / "agent_prompt.md"
    if not prompt_path.exists():
        return None
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_agent_memory(agent_dir: Path) -> dict[str, Any]:
    """Load an agent's memory JSON."""
    memory_path = agent_dir / "memory.json"
    if not memory_path.exists():
        return {}
    try:
        return json.loads(memory_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def compute_skill_avg(config: dict[str, Any]) -> float:
    """Compute average skill level from agent config."""
    education = config.get("education", {})
    levels = education.get("skill_levels", {})
    if not levels:
        return 0.0
    return round(sum(levels.values()) / len(levels), 1)


def get_all_agent_dirs() -> list[Path]:
    """Get all valid agent directories."""
    if not AGENTS_DIR.is_dir():
        return []
    return sorted(
        d
        for d in AGENTS_DIR.iterdir()
        if d.is_dir() and (d / "agent_config.json").exists()
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

STARTUP_TIME: float = 0.0


@asynccontextmanager
async def lifespan(app):  # noqa: ANN001
    global STARTUP_TIME
    STARTUP_TIME = time.time()

    _load_api_keys()

    agent_dirs = get_all_agent_dirs()
    logger.info("[OK] Cohort Agent API started")
    logger.info("[*] Agents directory: %s", AGENTS_DIR.resolve())
    logger.info("[*] Found %d agents", len(agent_dirs))
    logger.info("[*] Free tier: %d agents", len(FREE_TIER_AGENTS))
    logger.info(
        "[*] API keys loaded: %d (%s)",
        len(_API_KEYS),
        ", ".join(f"{v}" for v in set(_API_KEYS.values())) if _API_KEYS else "none",
    )
    yield


app = FastAPI(
    title="Cohort Agent API",
    description="Agent-as-a-Service: agent intelligence for cohort users",
    version="0.1.0",
    lifespan=lifespan,
)

# Restrict CORS to localhost by default.  Override with COHORT_CORS_ORIGINS
# env var (comma-separated) for network-accessible deployments.
_cors_env = os.environ.get("COHORT_CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env
    else [
        "http://localhost:5100",
        "http://127.0.0.1:5100",
        "http://localhost:5200",
        "http://127.0.0.1:5200",
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type", "Authorization"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health():
    agent_dirs = get_all_agent_dirs()
    return HealthResponse(
        status="healthy",
        service="cohort-agent-api",
        version="0.1.0",
        uptime_seconds=round(time.time() - STARTUP_TIME, 1),
        agent_count=len(agent_dirs),
    )


@app.get("/agents", response_model=AgentListResponse)
async def list_agents(x_api_key: str = Header(default="")):
    tier = resolve_tier(x_api_key)
    check_rate_limit(x_api_key)

    agents: list[AgentListItem] = []
    for agent_dir in get_all_agent_dirs():
        agent_id = agent_dir.name
        if not agent_visible_to_tier(agent_id, tier):
            continue
        try:
            config = load_agent_config(agent_dir)
        except HTTPException:
            continue
        if agent_id in ENTERPRISE_ONLY_AGENTS:
            agent_tier = "enterprise"
        elif agent_id in FREE_TIER_AGENTS:
            agent_tier = "free"
        else:
            agent_tier = "pro"
        agents.append(
            AgentListItem(
                agent_id=agent_id,
                name=config.get("name", config.get("agent_name", agent_id)),
                role=config.get("role", ""),
                agent_type=config.get("agent_type", "specialist"),
                status=config.get("status", "active"),
                avatar=config.get("avatar", ""),
                tier=agent_tier,
            )
        )
    return AgentListResponse(agents=agents, total=len(agents), tier=tier)


@app.get("/agents/{agent_id}/config")
async def get_agent_config(agent_id: str, x_api_key: str = Header(default="")):
    tier = resolve_tier(x_api_key)
    check_rate_limit(x_api_key)

    agent_dir = validate_agent_id(agent_id)
    if not agent_visible_to_tier(agent_id, tier):
        tier_needed = "Enterprise" if agent_id in ENTERPRISE_ONLY_AGENTS else "Pro"
        raise HTTPException(403, f"Agent '{agent_id}' requires {tier_needed} tier")
    return load_agent_config(agent_dir)


@app.get("/agents/{agent_id}/prompt")
async def get_agent_prompt(agent_id: str, x_api_key: str = Header(default="")):
    tier = resolve_tier(x_api_key)
    check_rate_limit(x_api_key)

    agent_dir = validate_agent_id(agent_id)
    if not agent_visible_to_tier(agent_id, tier):
        tier_needed = "Enterprise" if agent_id in ENTERPRISE_ONLY_AGENTS else "Pro"
        raise HTTPException(403, f"Agent '{agent_id}' requires {tier_needed} tier")

    prompt = load_agent_prompt(agent_dir)
    if prompt is None:
        raise HTTPException(404, f"No prompt found for agent: {agent_id}")
    return {"agent_id": agent_id, "prompt": prompt}


@app.get("/agents/{agent_id}/profile", response_model=AgentProfileResponse)
async def get_agent_profile(agent_id: str, x_api_key: str = Header(default="")):
    """Bundled endpoint: config + prompt + recent facts in one call."""
    tier = resolve_tier(x_api_key)
    check_rate_limit(x_api_key)

    agent_dir = validate_agent_id(agent_id)
    if not agent_visible_to_tier(agent_id, tier):
        tier_needed = "Enterprise" if agent_id in ENTERPRISE_ONLY_AGENTS else "Pro"
        raise HTTPException(403, f"Agent '{agent_id}' requires {tier_needed} tier")

    config = load_agent_config(agent_dir)
    prompt = load_agent_prompt(agent_dir)
    memory = load_agent_memory(agent_dir)

    # Include learned facts from training (last 10) -- this is the value-add
    facts = memory.get("learned_facts", [])
    recent_facts = facts[-10:] if facts else []

    return AgentProfileResponse(
        agent_id=agent_id,
        config=config,
        prompt=prompt,
        recent_facts=recent_facts,
    )


@app.get("/agents/{agent_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(agent_id: str, x_api_key: str = Header(default="")):
    tier = resolve_tier(x_api_key)
    check_rate_limit(x_api_key)

    agent_dir = validate_agent_id(agent_id)
    if not agent_visible_to_tier(agent_id, tier):
        tier_needed = "Enterprise" if agent_id in ENTERPRISE_ONLY_AGENTS else "Pro"
        raise HTTPException(403, f"Agent '{agent_id}' requires {tier_needed} tier")

    config = load_agent_config(agent_dir)
    return AgentStatusResponse(
        agent_id=agent_id,
        name=config.get("name", config.get("agent_name", agent_id)),
        role=config.get("role", ""),
        agent_type=config.get("agent_type", "specialist"),
        skill_avg=compute_skill_avg(config),
        last_training=config.get("education", {}).get("last_training_date"),
        capabilities=config.get("capabilities", []),
    )


@app.get("/tiers", response_model=TiersResponse)
async def get_tiers():
    """Describe what's available in each tier."""
    all_dirs = get_all_agent_dirs()

    free_agents: list[TierAgent] = []
    pro_agents: list[TierAgent] = []
    enterprise_agents: list[TierAgent] = []

    for agent_dir in all_dirs:
        agent_id = agent_dir.name
        try:
            config = load_agent_config(agent_dir)
        except HTTPException:
            continue
        entry = TierAgent(
            agent_id=agent_id,
            name=config.get("name", config.get("agent_name", agent_id)),
            role=config.get("role", ""),
        )
        if agent_id in ENTERPRISE_ONLY_AGENTS:
            enterprise_agents.append(entry)
        elif agent_id in FREE_TIER_AGENTS:
            free_agents.append(entry)
        else:
            pro_agents.append(entry)

    all_non_enterprise = free_agents + pro_agents

    return TiersResponse(
        tiers=[
            TierInfo(
                tier="free",
                description="5 hardcover agents + 7 free from Agent Store (12 total). Includes roundtables, work queue, executive briefings, local inference.",
                agents=free_agents,
                agent_count=len(free_agents),
            ),
            TierInfo(
                tier="pro",
                description="Full Agent Store access (22+ agents). Website Creator, content pipeline, cloud model fallback. $49/mo.",
                agents=all_non_enterprise,
                agent_count=len(all_non_enterprise),
            ),
            TierInfo(
                tier="enterprise",
                description="Agent Factory, assessment & training, cron scheduling, SSO, SLA. Starting at $299/mo.",
                agents=all_non_enterprise + enterprise_agents,
                agent_count=len(all_non_enterprise) + len(enterprise_agents),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------


def serve_agents(
    host: str = "0.0.0.0",
    port: int = 8200,
    agents_dir: str | None = None,
) -> None:
    """Start the Agent API server."""
    global AGENTS_DIR
    if agents_dir:
        AGENTS_DIR = Path(agents_dir)

    import uvicorn

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )
