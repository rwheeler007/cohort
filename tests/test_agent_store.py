"""Tests for the AgentStore (cohort.agent_store)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cohort.agent import AgentConfig, AgentMemory, LearnedFact
from cohort.agent_store import AgentStore


# =====================================================================
# Fixtures
# =====================================================================

def _create_agent_dir(base: Path, agent_id: str, config_data: dict) -> Path:
    """Helper: create an agent directory with config file."""
    agent_dir = base / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_path = agent_dir / "agent_config.json"
    config_path.write_text(json.dumps(config_data), encoding="utf-8")
    return agent_dir


@pytest.fixture
def agents_dir(tmp_path):
    """Create a temp agents directory with two agents."""
    _create_agent_dir(tmp_path, "python_developer", {
        "name": "Python Developer",
        "role": "Senior Developer",
        "primary_task": "Write Python code",
        "capabilities": ["python", "testing"],
        "domain_expertise": ["web frameworks"],
        "triggers": ["python", "py"],
        "avatar": "PY",
        "aliases": ["pd", "pydev"],
        "nickname": "PyDev",
        "color": "#3498DB",
        "group": "Core Developers",
    })
    _create_agent_dir(tmp_path, "web_developer", {
        "name": "Web Developer",
        "role": "Frontend Developer",
        "capabilities": ["html", "css", "javascript"],
        "triggers": ["web", "frontend"],
        "avatar": "WD",
        "aliases": ["webdev"],
        "nickname": "WebDev",
        "color": "#E67E22",
        "group": "Core Developers",
        "hidden": True,
    })
    # Also write a prompt and memory for python_developer
    prompt_path = tmp_path / "python_developer" / "agent_prompt.md"
    prompt_path.write_text("# Python Developer\nYou are a Python expert.", encoding="utf-8")
    mem = AgentMemory.create_empty("python_developer")
    mem.save(tmp_path / "python_developer" / "memory.json")

    return tmp_path


# =====================================================================
# Loading tests
# =====================================================================

class TestLoading:
    def test_load_single_agent(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        agent = store.load_agent("python_developer")
        assert agent is not None
        assert agent.name == "Python Developer"
        assert agent.avatar == "PY"

    def test_load_nonexistent(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        assert store.load_agent("nonexistent") is None

    def test_load_cached(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        a1 = store.load_agent("python_developer")
        a2 = store.load_agent("python_developer")
        assert a1 is a2  # same object (cached)

    def test_reload_clears_cache(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        a1 = store.load_agent("python_developer")
        store.reload()
        a2 = store.load_agent("python_developer")
        assert a1 is not a2  # different objects after reload

    def test_list_agents_excludes_hidden(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        agents = store.list_agents()
        ids = [a.agent_id for a in agents]
        assert "python_developer" in ids
        assert "web_developer" not in ids  # hidden

    def test_list_agents_includes_hidden(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        agents = store.list_agents(include_hidden=True)
        ids = [a.agent_id for a in agents]
        assert "web_developer" in ids

    def test_no_agents_dir(self):
        store = AgentStore()
        assert store.list_agents() == []
        assert store.get("anything") is None


# =====================================================================
# Alias resolution
# =====================================================================

class TestAliasResolution:
    def test_direct_id(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        agent = store.get_by_alias("python_developer")
        assert agent is not None
        assert agent.agent_id == "python_developer"

    def test_alias_match(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        agent = store.get_by_alias("pd")
        assert agent is not None
        assert agent.agent_id == "python_developer"

    def test_alias_case_insensitive(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        agent = store.get_by_alias("PyDev")
        assert agent is not None
        assert agent.agent_id == "python_developer"

    def test_alias_not_found(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        assert store.get_by_alias("nonexistent_alias") is None


# =====================================================================
# Backward compat (dict interface)
# =====================================================================

class TestBackwardCompat:
    def test_as_config_dict(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        d = store.as_config_dict()
        assert "python_developer" in d
        assert d["python_developer"]["triggers"] == ["python", "py"]
        assert d["python_developer"]["capabilities"] == ["python", "testing"]

    def test_as_config_dict_empty(self):
        store = AgentStore()
        assert store.as_config_dict() == {}


# =====================================================================
# Display profiles
# =====================================================================

class TestDisplayProfiles:
    def test_from_config(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        profile = store.get_display_profile("python_developer")
        assert profile["name"] == "Python Developer"
        assert profile["avatar"] == "PY"
        assert profile["color"] == "#3498DB"

    def test_from_fallback(self):
        fallback = {
            "system": {
                "name": "System",
                "nickname": "System",
                "avatar": "SYS",
                "color": "#7F8C8D",
                "role": "System",
                "group": "Operators",
            },
        }
        store = AgentStore(fallback_registry=fallback)
        profile = store.get_display_profile("system")
        assert profile["name"] == "System"
        assert profile["avatar"] == "SYS"

    def test_default_profile(self):
        store = AgentStore()
        profile = store.get_display_profile("unknown_agent")
        assert profile["name"] == "unknown_agent"
        assert profile["avatar"] == "UN"
        assert profile["color"] == "#95A5A6"

    def test_get_all_profiles(self, agents_dir):
        fallback = {
            "system": {
                "name": "System",
                "nickname": "System",
                "avatar": "SYS",
                "color": "#7F8C8D",
                "role": "System",
                "group": "Operators",
            },
        }
        store = AgentStore(agents_dir=agents_dir, fallback_registry=fallback)
        profiles = store.get_all_display_profiles()
        # python_developer visible, web_developer hidden, system from fallback
        assert "python_developer" in profiles
        assert "web_developer" not in profiles
        assert "system" in profiles


# =====================================================================
# Memory
# =====================================================================

class TestMemory:
    def test_load_memory(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        mem = store.load_memory("python_developer")
        assert mem is not None
        assert mem.agent_id == "python_developer"

    def test_save_and_load_memory(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        mem = AgentMemory(
            agent_id="python_developer",
            learned_facts=[
                LearnedFact(
                    fact="GIL exists",
                    learned_from="test",
                    timestamp="2026-01-01",
                ),
            ],
        )
        store.save_memory("python_developer", mem)

        loaded = store.load_memory("python_developer")
        assert loaded is not None
        assert len(loaded.learned_facts) == 1
        assert loaded.learned_facts[0].fact == "GIL exists"

    def test_load_memory_no_file(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        # web_developer dir exists but has no memory.json
        mem = store.load_memory("web_developer")
        assert mem is not None
        assert mem.agent_id == "web_developer"
        assert mem.learned_facts == []

    def test_load_memory_no_dir(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        assert store.load_memory("nonexistent") is None

    def test_load_memory_no_agents_dir(self):
        store = AgentStore()
        assert store.load_memory("anything") is None


# =====================================================================
# Prompt
# =====================================================================

class TestPrompt:
    def test_get_prompt(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        prompt = store.get_prompt("python_developer")
        assert prompt is not None
        assert "Python Developer" in prompt
        assert "Python expert" in prompt

    def test_get_prompt_path(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        path = store.get_prompt_path("python_developer")
        assert path is not None
        assert path.exists()

    def test_get_prompt_missing(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        assert store.get_prompt("web_developer") is None  # no prompt file
        assert store.get_prompt_path("web_developer") is None

    def test_get_prompt_no_agents_dir(self):
        store = AgentStore()
        assert store.get_prompt("anything") is None
        assert store.get_prompt_path("anything") is None


# =====================================================================
# Registration
# =====================================================================

class TestRegistration:
    def test_register(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        new_config = AgentConfig(
            agent_id="new_agent",
            name="New Agent",
            role="Tester",
            capabilities=["testing"],
        )
        store.register(new_config)

        # Should be in cache
        assert store.get("new_agent") is not None

        # Should be on disk
        config_path = agents_dir / "new_agent" / "agent_config.json"
        assert config_path.exists()

    def test_unregister(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        store.load_agent("python_developer")  # load into cache
        result = store.unregister("python_developer")
        assert result is True
        # Removed from cache but files still on disk
        assert "python_developer" not in store._cache
        assert (agents_dir / "python_developer" / "agent_config.json").exists()

    def test_unregister_nonexistent(self, agents_dir):
        store = AgentStore(agents_dir=agents_dir)
        assert store.unregister("nonexistent") is False
