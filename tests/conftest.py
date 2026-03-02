"""Shared fixtures for Cohort integration tests.

Provides async app factories, httpx clients, mock agent configs,
API key helpers, and pytest markers for both the Starlette server
and the FastAPI agent_api.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

from cohort.chat import ChatManager
from cohort.registry import JsonFileStorage


# =====================================================================
# pytest configuration
# =====================================================================

def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "server: mark test as Starlette server test")
    config.addinivalue_line("markers", "agent_api: mark test as FastAPI agent_api test")
    config.addinivalue_line("markers", "socketio: mark test as Socket.IO event test")


# =====================================================================
# Core data fixtures (shared across all test types)
# =====================================================================

@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Temporary data directory with required subdirectories."""
    (tmp_path / "agents.json").write_text("{}", encoding="utf-8")
    (tmp_path / "channels.json").write_text("{}", encoding="utf-8")
    (tmp_path / "messages.json").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture
def storage(data_dir: Path) -> JsonFileStorage:
    """JsonFileStorage backed by tmp_path."""
    return JsonFileStorage(data_dir)


@pytest.fixture
def chat(storage: JsonFileStorage) -> ChatManager:
    """ChatManager with ephemeral storage."""
    return ChatManager(storage)


# =====================================================================
# Mock agent directory structure
# =====================================================================

MOCK_AGENT_IDS = [
    "python_developer",
    "web_developer",
    "coding_orchestrator",
    "boss_agent",
    "ceo_agent",
]


def _make_agent_config(agent_id: str) -> dict:
    """Minimal valid agent_config.json for testing."""
    return {
        "agent_id": agent_id,
        "name": agent_id.replace("_", " ").title(),
        "role": f"Test {agent_id}",
        "status": "active",
        "personality": f"A test agent named {agent_id}.",
        "domain_expertise": ["testing"],
        "skill_levels": {"testing": 8},
    }


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    """Create a minimal agents directory with configs and prompts."""
    agents_root = tmp_path / "agents"
    for agent_id in MOCK_AGENT_IDS:
        agent_dir = agents_root / agent_id
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_config.json").write_text(
            json.dumps(_make_agent_config(agent_id), indent=2),
            encoding="utf-8",
        )
        (agent_dir / "agent_prompt.md").write_text(
            f"You are {agent_id}. Respond concisely for testing.",
            encoding="utf-8",
        )
    return agents_root


# =====================================================================
# API key fixtures (for agent_api tier testing)
# =====================================================================

FREE_KEY = "test-free-key"
PRO_KEY = "test-pro-key"
ENTERPRISE_KEY = "test-enterprise-key"
INVALID_KEY = "test-invalid-key"


@pytest.fixture
def api_keys_env() -> str:
    """API keys string in COHORT_AGENT_API_KEYS format."""
    return f"{FREE_KEY}:free,{PRO_KEY}:pro,{ENTERPRISE_KEY}:enterprise"


# =====================================================================
# Starlette server app factory
# =====================================================================

@pytest_asyncio.fixture
async def server_app(data_dir: Path, agents_dir: Path):
    """Create a Starlette server ASGI app with isolated data."""
    env = {
        "COHORT_DATA_DIR": str(data_dir),
        "COHORT_AGENTS_DIR": str(agents_dir),
        "COHORT_AGENTS_ROOT": str(agents_dir.parent),
    }
    with patch.dict(os.environ, env, clear=False):
        from cohort.server import create_app
        app = create_app(data_dir=str(data_dir))
    return app


@pytest_asyncio.fixture
async def server_client(server_app) -> AsyncGenerator[httpx.AsyncClient, None]:
    """httpx.AsyncClient wired to the Starlette server via ASGITransport."""
    transport = httpx.ASGITransport(app=server_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


# =====================================================================
# FastAPI agent_api app factory
# =====================================================================

@pytest_asyncio.fixture
async def agent_api_app(agents_dir: Path, api_keys_env: str):
    """Create a FastAPI agent_api app with isolated agents and keys."""
    import cohort.agent_api as api_mod

    env = {
        "COHORT_AGENT_API_DIR": str(agents_dir),
        "COHORT_AGENT_API_KEYS": api_keys_env,
    }
    with patch.dict(os.environ, env, clear=False):
        # Reset module-level state so lifespan re-reads env
        api_mod._API_KEYS.clear()
        api_mod.AGENTS_DIR = agents_dir
        api_mod._load_api_keys()
        yield api_mod.app


@pytest_asyncio.fixture
async def agent_api_client(agent_api_app) -> AsyncGenerator[httpx.AsyncClient, None]:
    """httpx.AsyncClient wired to the FastAPI agent_api via ASGITransport."""
    transport = httpx.ASGITransport(app=agent_api_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


# =====================================================================
# Convenience header fixtures for agent_api auth
# =====================================================================

@pytest.fixture
def free_headers() -> dict[str, str]:
    """Headers with free-tier API key."""
    return {"X-API-Key": FREE_KEY}


@pytest.fixture
def pro_headers() -> dict[str, str]:
    """Headers with pro-tier API key."""
    return {"X-API-Key": PRO_KEY}


@pytest.fixture
def enterprise_headers() -> dict[str, str]:
    """Headers with enterprise-tier API key."""
    return {"X-API-Key": ENTERPRISE_KEY}


@pytest.fixture
def no_auth_headers() -> dict[str, str]:
    """Headers with no API key (anonymous)."""
    return {}
