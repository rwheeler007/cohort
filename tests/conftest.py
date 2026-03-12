"""Shared fixtures for Cohort core tests.

Provides data directories, storage backends, mock agent configs,
and pytest markers for the open source core library.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cohort.chat import ChatManager
from cohort.registry import JsonFileStorage


# =====================================================================
# pytest configuration
# =====================================================================

def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: mark test as integration test")


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
