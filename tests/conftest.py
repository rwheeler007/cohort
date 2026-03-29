"""Shared fixtures for Cohort core tests.

Provides data directories, storage backends, mock agent configs,
server clients, and pytest markers for the open source core library.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
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
    config.addinivalue_line("markers", "server: mark test as server integration test")
    config.addinivalue_line("markers", "socketio: mark test as Socket.IO integration test")
    config.addinivalue_line("markers", "agent_api: mark test as Agent API test")


# =====================================================================
# Core data fixtures
# =====================================================================

@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Temporary data directory with required subdirectories."""
    (tmp_path / "agents.json").write_text("{}", encoding="utf-8")
    (tmp_path / "channels.json").write_text("{}", encoding="utf-8")
    (tmp_path / "messages.json").write_text("[]", encoding="utf-8")
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
    "cohort_orchestrator",
    "ceo_agent",
    "supervisor_agent",  # enterprise-only (in ENTERPRISE_ONLY_AGENTS)
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
# HTTP server client (Starlette)
# =====================================================================

@pytest_asyncio.fixture
async def server_client(data_dir: Path, agents_dir: Path):
    """httpx.AsyncClient wired to the Starlette server via ASGITransport.

    Ensures messages.json is initialized as [] (required by JsonFileStorage).
    Patches route_mentions to prevent background thread spawning.
    """
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
        ) as client:
            yield client


# =====================================================================
# Agent API client (FastAPI)
# =====================================================================

_TEST_API_KEYS = "test-free-key:free,test-pro-key:pro,test-enterprise-key:enterprise"


@pytest_asyncio.fixture
async def agent_api_client(agents_dir: Path):
    """httpx.AsyncClient wired to the FastAPI Agent API via ASGITransport."""
    import cohort.agent_api as api_mod

    env = {
        "COHORT_AGENT_API_DIR": str(agents_dir),
        "COHORT_AGENT_API_KEYS": _TEST_API_KEYS,
    }
    with patch.dict(os.environ, env, clear=False):
        # Point module-level AGENTS_DIR at the test directory
        original_dir = api_mod.AGENTS_DIR
        api_mod.AGENTS_DIR = agents_dir
        api_mod._load_api_keys()

        transport = httpx.ASGITransport(app=api_mod.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client

        api_mod.AGENTS_DIR = original_dir


@pytest.fixture
def free_headers() -> dict[str, str]:
    """Headers with a free-tier API key."""
    return {"X-API-Key": "test-free-key"}


@pytest.fixture
def pro_headers() -> dict[str, str]:
    """Headers with a pro-tier API key."""
    return {"X-API-Key": "test-pro-key"}


@pytest.fixture
def enterprise_headers() -> dict[str, str]:
    """Headers with an enterprise-tier API key."""
    return {"X-API-Key": "test-enterprise-key"}


@pytest.fixture
def no_auth_headers() -> dict[str, str]:
    """Headers with no API key (anonymous)."""
    return {}
