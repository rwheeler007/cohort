"""Tests for the MemoryManager (cohort.memory_manager)."""

from __future__ import annotations

import json

import pytest

from cohort.agent import AgentMemory, LearnedFact, WorkingMemoryEntry
from cohort.agent_store import AgentStore
from cohort.memory_manager import MemoryManager


@pytest.fixture
def setup(tmp_path):
    """Create agent dir with config, memory, and a MemoryManager."""
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / "test_agent"
    agent_dir.mkdir(parents=True)

    config = {
        "name": "Test Agent",
        "role": "Tester",
        "capabilities": ["testing"],
    }
    (agent_dir / "agent_config.json").write_text(
        json.dumps(config), encoding="utf-8",
    )

    # Pre-populate with 15 working memory entries
    entries = [
        WorkingMemoryEntry(
            timestamp=f"2026-01-{i+1:02d}T00:00:00",
            channel="general",
            input=f"question {i}",
            response=f"answer {i}",
        )
        for i in range(15)
    ]
    mem = AgentMemory(agent_id="test_agent", working_memory=entries)
    mem.save(agent_dir / "memory.json")

    archive_dir = tmp_path / "archives"
    store = AgentStore(agents_dir=agents_dir)
    mm = MemoryManager(store, archive_dir=archive_dir, keep_last=5)
    return mm, store, archive_dir


class TestAddWorkingMemory:
    def test_add_entry(self, setup):
        mm, store, _ = setup
        mm.add_working_memory("test_agent", WorkingMemoryEntry(
            timestamp="2026-02-01",
            channel="dev",
            input="new q",
            response="new a",
        ))
        mem = store.load_memory("test_agent")
        assert len(mem.working_memory) == 16
        assert mem.working_memory[-1].input == "new q"


class TestAddLearnedFact:
    def test_add_fact(self, setup):
        mm, store, _ = setup
        mm.add_learned_fact("test_agent", LearnedFact(
            fact="Python uses GIL",
            learned_from="teacher",
            timestamp="2026-02-01",
            confidence="high",
        ))
        mem = store.load_memory("test_agent")
        assert len(mem.learned_facts) == 1
        assert mem.learned_facts[0].fact == "Python uses GIL"


class TestCleanAgent:
    def test_clean_trims_working_memory(self, setup):
        mm, store, archive_dir = setup
        result = mm.clean_agent("test_agent")
        assert result.success is True
        assert result.working_memory_removed == 10
        assert result.working_memory_kept == 5
        assert result.archive_path is not None

        # Verify memory on disk
        mem = store.load_memory("test_agent")
        assert len(mem.working_memory) == 5
        assert len(mem.archive_history) == 1

    def test_clean_no_trim_needed(self, setup):
        mm, store, _ = setup
        result = mm.clean_agent("test_agent", keep_last=20)
        assert result.success is True
        assert result.working_memory_removed == 0
        assert result.working_memory_kept == 15

    def test_clean_dry_run(self, setup):
        mm, store, _ = setup
        result = mm.clean_agent("test_agent", dry_run=True)
        assert result.success is True
        assert result.working_memory_removed == 10
        # But memory is NOT changed
        mem = store.load_memory("test_agent")
        assert len(mem.working_memory) == 15

    def test_clean_nonexistent_agent(self, setup):
        mm, _, _ = setup
        result = mm.clean_agent("nonexistent")
        assert result.success is False
        assert result.error is not None

    def test_archive_file_created(self, setup):
        mm, _, archive_dir = setup
        mm.clean_agent("test_agent")
        assert archive_dir.exists()
        archives = list(archive_dir.glob("test_agent_archive_*.txt"))
        assert len(archives) == 1
        content = archives[0].read_text(encoding="utf-8")
        assert "question 0" in content


class TestCleanAll:
    def test_clean_all(self, setup):
        mm, _, _ = setup
        results = mm.clean_all()
        assert len(results) == 1
        assert results[0].agent_id == "test_agent"
        assert results[0].success is True


class TestStats:
    def test_get_stats(self, setup):
        mm, _, _ = setup
        stats = mm.get_stats("test_agent")
        assert stats["agent_id"] == "test_agent"
        assert stats["working_memory_count"] == 15
        assert stats["learned_facts_count"] == 0

    def test_get_stats_not_found(self, setup):
        mm, _, _ = setup
        stats = mm.get_stats("nonexistent")
        assert stats["error"] == "not_found"

    def test_get_all_stats(self, setup):
        mm, _, _ = setup
        all_stats = mm.get_all_stats()
        assert len(all_stats) == 1
