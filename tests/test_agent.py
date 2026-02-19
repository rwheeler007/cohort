"""Tests for the agent data model (cohort.agent)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cohort.agent import (
    AgentConfig,
    AgentEducation,
    AgentMemory,
    LearnedFact,
    WorkingMemoryEntry,
)


# =====================================================================
# LearnedFact
# =====================================================================

class TestLearnedFact:
    def test_round_trip(self):
        fact = LearnedFact(
            fact="Python uses GIL for thread safety",
            learned_from="teacher_agent",
            timestamp="2026-01-01T00:00:00",
            confidence="high",
            session_id="EDU-001",
        )
        d = fact.to_dict()
        restored = LearnedFact.from_dict(d)
        assert restored.fact == fact.fact
        assert restored.confidence == "high"
        assert restored.session_id == "EDU-001"

    def test_defaults(self):
        fact = LearnedFact(
            fact="something",
            learned_from="self",
            timestamp="2026-01-01T00:00:00",
        )
        assert fact.confidence == "medium"
        assert fact.session_id == ""

    def test_from_dict_drops_unknown(self):
        fact = LearnedFact.from_dict({
            "fact": "test",
            "learned_from": "x",
            "timestamp": "2026-01-01",
            "unknown_field": 42,
        })
        assert fact.fact == "test"
        assert not hasattr(fact, "unknown_field")


# =====================================================================
# WorkingMemoryEntry
# =====================================================================

class TestWorkingMemoryEntry:
    def test_round_trip(self):
        entry = WorkingMemoryEntry(
            timestamp="2026-01-01T12:00:00",
            channel="general",
            input="What is Python?",
            response="A programming language.",
        )
        d = entry.to_dict()
        restored = WorkingMemoryEntry.from_dict(d)
        assert restored.channel == "general"
        assert restored.input == "What is Python?"


# =====================================================================
# AgentEducation
# =====================================================================

class TestAgentEducation:
    def test_round_trip(self):
        edu = AgentEducation(
            specialty="Python",
            last_training_date="2026-01-01",
            training_frequency_days=7,
            knowledge_areas=["web", "data"],
            skill_levels={"python": 8, "web": 6},
        )
        d = edu.to_dict()
        restored = AgentEducation.from_dict(d)
        assert restored.specialty == "Python"
        assert restored.skill_levels["python"] == 8

    def test_defaults(self):
        edu = AgentEducation()
        assert edu.training_frequency_days == 14
        assert edu.skill_levels == {}

    def test_empty_dict(self):
        edu = AgentEducation.from_dict({})
        assert edu.specialty == ""

    def test_none_input(self):
        edu = AgentEducation.from_dict(None)
        assert edu.specialty == ""

    def test_legacy_skill_level_field(self):
        edu = AgentEducation.from_dict({
            "skill_level": {"python": 5},
        })
        assert edu.skill_levels["python"] == 5


# =====================================================================
# AgentMemory
# =====================================================================

class TestAgentMemory:
    def test_round_trip(self):
        mem = AgentMemory(
            agent_id="python_developer",
            known_paths={"docs": "/path/to/docs"},
            learned_facts=[
                LearnedFact(
                    fact="GIL exists",
                    learned_from="teacher",
                    timestamp="2026-01-01",
                ),
            ],
            collaborators={"supervisor": {"relationship": "mentioned"}},
            working_memory=[
                WorkingMemoryEntry(
                    timestamp="2026-01-01",
                    channel="general",
                    input="hello",
                    response="hi",
                ),
            ],
        )
        d = mem.to_dict()
        assert isinstance(d["learned_facts"][0], dict)
        assert isinstance(d["working_memory"][0], dict)

        restored = AgentMemory.from_dict(d)
        assert restored.agent_id == "python_developer"
        assert len(restored.learned_facts) == 1
        assert isinstance(restored.learned_facts[0], LearnedFact)
        assert len(restored.working_memory) == 1
        assert isinstance(restored.working_memory[0], WorkingMemoryEntry)

    def test_create_empty(self):
        mem = AgentMemory.create_empty("test_agent")
        assert mem.agent_id == "test_agent"
        assert mem.learned_facts == []
        assert mem.working_memory == []

    def test_file_round_trip(self, tmp_path):
        mem = AgentMemory(
            agent_id="test_agent",
            learned_facts=[
                LearnedFact(
                    fact="test fact",
                    learned_from="unit_test",
                    timestamp="2026-01-01",
                ),
            ],
        )
        path = tmp_path / "test_agent" / "memory.json"
        mem.save(path)
        assert path.exists()

        loaded = AgentMemory.load(path)
        assert loaded.agent_id == "test_agent"
        assert len(loaded.learned_facts) == 1
        assert loaded.learned_facts[0].fact == "test fact"

    def test_load_missing_file(self, tmp_path):
        path = tmp_path / "missing_agent" / "memory.json"
        mem = AgentMemory.load(path)
        assert mem.agent_id == "missing_agent"
        assert mem.learned_facts == []

    def test_from_dict_none(self):
        mem = AgentMemory.from_dict(None)
        assert mem.agent_id == "unknown"


# =====================================================================
# AgentConfig
# =====================================================================

class TestAgentConfig:
    def test_minimal_creation(self):
        config = AgentConfig(
            agent_id="test_agent",
            name="Test Agent",
            role="Tester",
        )
        assert config.agent_id == "test_agent"
        assert config.avatar == "TE"
        assert config.nickname == "Test Agent"
        assert config.status == "active"
        assert config.version == "1.0"
        assert config.created_date  # auto-populated

    def test_round_trip(self):
        config = AgentConfig(
            agent_id="python_dev",
            name="Python Developer",
            role="Senior Developer",
            primary_task="Write Python code",
            capabilities=["python", "testing"],
            domain_expertise=["web", "data"],
            triggers=["python", "py"],
            avatar="PY",
            aliases=["pd", "pydev"],
            nickname="PyDev",
            color="#3498DB",
            group="Core Developers",
            education=AgentEducation(
                specialty="Python",
                skill_levels={"python": 8},
            ),
        )
        d = config.to_dict()
        restored = AgentConfig.from_dict(d)
        assert restored.agent_id == "python_dev"
        assert restored.capabilities == ["python", "testing"]
        assert restored.education.skill_levels["python"] == 8
        assert restored.avatar == "PY"
        assert restored.color == "#3498DB"

    def test_from_dict_legacy_format(self):
        """Test loading from legacy agent_config.json format."""
        legacy_data = {
            "agent_name": "Python Developer",
            "role": "Senior Python Software Engineer",
            "primary_task": "Write Python code",
            "personality": "Writes clean Python",
            "status": "active",
            "capabilities": ["python", "testing"],
            "education": {
                "specialty": "Python",
                "skill_levels": {"python": 8},
                "training_frequency_days": 7,
            },
        }
        config = AgentConfig.from_dict({**legacy_data, "agent_id": "python_developer"})
        assert config.name == "Python Developer"
        assert config.education.specialty == "Python"

    def test_from_config_file(self, tmp_path):
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        config_data = {
            "name": "Test Agent",
            "role": "Tester",
            "primary_task": "Test things",
            "capabilities": ["testing"],
        }
        config_path = agent_dir / "agent_config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        config = AgentConfig.from_config_file(config_path)
        assert config.agent_id == "test_agent"  # inferred from directory
        assert config.name == "Test Agent"

    def test_display_profile(self):
        config = AgentConfig(
            agent_id="web_dev",
            name="Web Developer",
            role="Frontend Dev",
            nickname="WebDev",
            avatar="WD",
            color="#E67E22",
            group="Core Developers",
        )
        profile = config.display_profile()
        assert profile["name"] == "Web Developer"
        assert profile["nickname"] == "WebDev"
        assert profile["avatar"] == "WD"
        assert profile["color"] == "#E67E22"
        assert profile["role"] == "Frontend Dev"
        assert profile["group"] == "Core Developers"

    def test_relevance_score_matching(self):
        config = AgentConfig(
            agent_id="python_dev",
            name="Python Developer",
            role="Developer",
            capabilities=["python programming", "testing"],
            domain_expertise=["web frameworks", "data processing"],
            triggers=["python", "pytest"],
        )
        score = config.relevance_score("python programming and testing")
        assert score > 0.0

    def test_relevance_score_no_match(self):
        config = AgentConfig(
            agent_id="python_dev",
            name="Python Developer",
            role="Developer",
            capabilities=["python"],
            triggers=["python"],
        )
        score = config.relevance_score("kubernetes deployment orchestration")
        assert score == 0.0

    def test_relevance_score_empty_topic(self):
        config = AgentConfig(
            agent_id="test",
            name="Test",
            role="Test",
            capabilities=["python"],
        )
        assert config.relevance_score("") == 0.0
        assert config.relevance_score("a") == 0.0  # too short, filtered

    def test_can_contribute_no_meeting(self):
        config = AgentConfig(agent_id="t", name="T", role="T")
        assert config.can_contribute({}) is True
        assert config.can_contribute({"other": "data"}) is True

    def test_can_contribute_meeting_context(self):
        config = AgentConfig(
            agent_id="python_dev",
            name="Python Developer",
            role="Developer",
            capabilities=["python programming"],
            triggers=["python"],
        )
        context = {
            "meeting_context": {
                "current_topic": {
                    "keywords": ["python", "programming"],
                },
            },
        }
        assert config.can_contribute(context) is True

    def test_from_dict_drops_unknown(self):
        config = AgentConfig.from_dict({
            "agent_id": "test",
            "name": "Test",
            "role": "T",
            "unknown_extra_field": True,
            "memory_system": {"description": "shared"},
        })
        assert config.agent_id == "test"

    def test_from_dict_none(self):
        config = AgentConfig.from_dict(None)
        assert config.agent_id == "unknown"

    def test_satisfies_agent_profile_protocol(self):
        """Verify AgentConfig satisfies the AgentProfile protocol."""
        from cohort.registry import AgentProfile

        config = AgentConfig(
            agent_id="test",
            name="Test",
            role="Tester",
            capabilities=["testing"],
        )
        assert isinstance(config, AgentProfile)
