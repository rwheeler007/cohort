"""Tests for the AgentCreator (cohort.agent_creator)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cohort.agent import AgentConfig, AgentMemory
from cohort.agent_creator import AgentCreator, AgentSpec, AgentType, _to_snake_case
from cohort.agent_store import AgentStore


@pytest.fixture
def setup(tmp_path):
    """Create an agents_dir and AgentStore for testing."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    store = AgentStore(agents_dir=agents_dir)
    creator = AgentCreator(store)
    return creator, store, agents_dir


# =====================================================================
# _to_snake_case
# =====================================================================

class TestToSnakeCase:
    def test_simple(self):
        assert _to_snake_case("Python Developer") == "python_developer"

    def test_camel_case(self):
        assert _to_snake_case("WebDeveloper") == "web_developer"

    def test_mixed(self):
        assert _to_snake_case("3D Design Agent") == "3_d_design_agent"

    def test_special_chars(self):
        assert _to_snake_case("Hello World!") == "hello_world"

    def test_already_snake(self):
        assert _to_snake_case("already_snake") == "already_snake"


# =====================================================================
# AgentSpec
# =====================================================================

class TestAgentSpec:
    def test_agent_id_derived(self):
        spec = AgentSpec(name="Python Developer", role="Dev", primary_task="Code")
        assert spec.agent_id == "python_developer"

    def test_defaults(self):
        spec = AgentSpec(name="Test", role="Tester", primary_task="Test things")
        assert spec.agent_type == AgentType.SPECIALIST
        assert spec.capabilities == []
        assert spec.color == "#95A5A6"
        assert spec.group == "Agents"


# =====================================================================
# AgentCreator.create_agent
# =====================================================================

class TestCreateAgent:
    def test_create_specialist(self, setup):
        creator, store, agents_dir = setup
        spec = AgentSpec(
            name="Python Developer",
            role="Senior Python Engineer",
            primary_task="Write Python code",
            capabilities=["python", "testing"],
            domain_expertise=["web frameworks", "data processing"],
        )
        config = creator.create_agent(spec)

        assert config.agent_id == "python_developer"
        assert config.name == "Python Developer"
        assert config.role == "Senior Python Engineer"
        assert config.capabilities == ["python", "testing"]
        assert config.status == "active"

        # Verify directory structure
        agent_dir = agents_dir / "python_developer"
        assert agent_dir.exists()
        assert (agent_dir / "agent_config.json").exists()
        assert (agent_dir / "agent_prompt.md").exists()
        assert (agent_dir / "memory.json").exists()

    def test_create_orchestrator(self, setup):
        creator, store, agents_dir = setup
        spec = AgentSpec(
            name="Project Manager",
            role="Orchestrator",
            primary_task="Coordinate team work",
            agent_type=AgentType.ORCHESTRATOR,
        )
        config = creator.create_agent(spec)
        assert config.agent_type == "orchestrator"

        # Prompt should use orchestrator template
        prompt = (agents_dir / "project_manager" / "agent_prompt.md").read_text(encoding="utf-8")
        assert "Primary Mission" in prompt
        assert "Routing Guidelines" in prompt

    def test_config_file_valid_json(self, setup):
        creator, _, agents_dir = setup
        spec = AgentSpec(name="Test Agent", role="Tester", primary_task="Test")
        creator.create_agent(spec)

        config_path = agents_dir / "test_agent" / "agent_config.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["agent_id"] == "test_agent"
        assert data["name"] == "Test Agent"

    def test_memory_file_valid(self, setup):
        creator, _, agents_dir = setup
        spec = AgentSpec(name="Test Agent", role="Tester", primary_task="Test")
        creator.create_agent(spec)

        mem = AgentMemory.load(agents_dir / "test_agent" / "memory.json")
        assert mem.agent_id == "test_agent"
        assert mem.working_memory == []
        assert mem.learned_facts == []

    def test_registered_in_store(self, setup):
        creator, store, _ = setup
        spec = AgentSpec(name="Test Agent", role="Tester", primary_task="Test")
        creator.create_agent(spec)

        loaded = store.load_agent("test_agent")
        assert loaded is not None
        assert loaded.name == "Test Agent"

    def test_duplicate_raises(self, setup):
        creator, _, _ = setup
        spec = AgentSpec(name="Test Agent", role="Tester", primary_task="Test")
        creator.create_agent(spec)

        with pytest.raises(ValueError, match="already exists"):
            creator.create_agent(spec)

    def test_no_agents_dir_raises(self, tmp_path):
        store = AgentStore(agents_dir=None)
        creator = AgentCreator(store)
        spec = AgentSpec(name="Test", role="Tester", primary_task="Test")

        with pytest.raises(ValueError, match="agents_dir"):
            creator.create_agent(spec)

    def test_derived_triggers(self, setup):
        creator, store, _ = setup
        spec = AgentSpec(name="Test Agent", role="Tester", primary_task="Test")
        config = creator.create_agent(spec)

        # Auto-derived triggers from name
        assert "test_agent" in config.triggers
        assert "test agent" in config.triggers

    def test_custom_triggers(self, setup):
        creator, store, _ = setup
        spec = AgentSpec(
            name="Test Agent",
            role="Tester",
            primary_task="Test",
            triggers=["qa", "testing"],
        )
        config = creator.create_agent(spec)
        assert config.triggers == ["qa", "testing"]

    def test_derived_avatar(self, setup):
        creator, _, _ = setup
        spec = AgentSpec(name="Python Developer", role="Dev", primary_task="Code")
        config = creator.create_agent(spec)
        assert config.avatar == "PY"  # first 2 chars of agent_id

    def test_custom_avatar(self, setup):
        creator, _, _ = setup
        spec = AgentSpec(
            name="Python Developer",
            role="Dev",
            primary_task="Code",
            avatar="PD",
        )
        config = creator.create_agent(spec)
        assert config.avatar == "PD"

    def test_education_populated(self, setup):
        creator, _, _ = setup
        spec = AgentSpec(
            name="Data Scientist",
            role="ML Engineer",
            primary_task="Build models",
            domain_expertise=["Machine Learning", "Statistics"],
        )
        config = creator.create_agent(spec)
        assert config.education.specialty == "ML Engineer"
        assert "Machine Learning" in config.education.knowledge_areas
        assert "machine_learning" in config.education.skill_levels
        assert config.education.skill_levels["machine_learning"] == 0

    def test_prompt_contains_capabilities(self, setup):
        creator, _, agents_dir = setup
        spec = AgentSpec(
            name="Web Dev",
            role="Frontend Engineer",
            primary_task="Build UIs",
            capabilities=["React", "TypeScript"],
        )
        creator.create_agent(spec)
        prompt = (agents_dir / "web_dev" / "agent_prompt.md").read_text(encoding="utf-8")
        assert "React" in prompt
        assert "TypeScript" in prompt
        assert "Frontend Engineer" in prompt
