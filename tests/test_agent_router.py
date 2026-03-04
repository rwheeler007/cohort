"""Tests for cohort.agent_router -- orchestrator-first routing and priority."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import cohort.agent_router as router_mod
from cohort.agent_store import AgentStore
from cohort.chat import ChatManager, Message
from cohort.registry import JsonFileStorage


# =====================================================================
# Fixtures
# =====================================================================

# Agent type declarations for test fixtures
_TEST_AGENT_TYPES = {
    "cohort_orchestrator": "orchestrator",
    "ceo_agent": "strategic",
    "python_developer": "specialist",
    "coding_orchestrator": "orchestrator",
}


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(tmp_path)


@pytest.fixture
def chat(storage: JsonFileStorage) -> ChatManager:
    return ChatManager(storage)


@pytest.fixture
def agents_root(tmp_path: Path) -> Path:
    """Create a minimal agents directory with prompt files and configs."""
    agents_dir = tmp_path / "agents"
    for agent_id in ("cohort_orchestrator", "ceo_agent", "python_developer", "coding_orchestrator"):
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_prompt.md").write_text(
            f"You are {agent_id}.", encoding="utf-8"
        )
        config = {
            "agent_id": agent_id,
            "name": agent_id.replace("_", " ").title(),
            "role": "Test Agent",
            "agent_type": _TEST_AGENT_TYPES.get(agent_id, "specialist"),
        }
        (agent_dir / "agent_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_router(chat: ChatManager, agents_root: Path):
    """Reset router state before each test, wiring an AgentStore."""
    store = AgentStore(agents_dir=agents_root / "agents")
    router_mod.setup_agent_router(
        chat, sio=None, agents_root=agents_root, store=store,
    )
    yield
    store = AgentStore(agents_dir=agents_root / "agents")
    router_mod.setup_agent_router(
        chat, sio=None, agents_root=agents_root, store=store,
    )


def _msg(
    content: str,
    sender: str = "user",
    channel: str = "dev",
    msg_id: str = "m1",
) -> Message:
    return Message(
        id=msg_id,
        channel_id=channel,
        sender=sender,
        content=content,
        timestamp="2026-01-01T00:00:00",
    )


def _get_queue() -> list:
    """Access the current router queue via the module (avoids stale refs)."""
    return router_mod._state.queue


# =====================================================================
# Orchestrator alias resolution
# =====================================================================

class TestOrchestratorAliasResolution:
    def test_boss_alias_resolves_to_orchestrator(self):
        assert router_mod.AGENT_ALIASES.get("boss") == "cohort_orchestrator"

    def test_resolve_boss_alias(self, agents_root):
        resolved = router_mod.resolve_agent_id("boss")
        assert resolved == "cohort_orchestrator"

    def test_resolve_orchestrator_direct(self, agents_root):
        resolved = router_mod.resolve_agent_id("cohort_orchestrator")
        assert resolved == "cohort_orchestrator"


# =====================================================================
# Orchestrator-first priority routing
# =====================================================================

class TestOrchestratorFirstPriority:
    """Orchestrator-type agents get priority boost when directly invoked
    (first mention) or when coordination keywords are present. Otherwise
    follows normal priority rules so more relevant agents respond first."""

    def test_orchestrator_first_mention_responds_first(self, chat):
        """Direct first-tag to an orchestrator gives it priority over other agents."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@cohort_orchestrator please get @ceo_agent and @python_developer input")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["cohort_orchestrator", "ceo_agent", "python_developer"])

        queue = _get_queue()
        assert len(queue) == 3
        assert queue[0]["agent_id"] == "cohort_orchestrator"
        assert queue[0]["priority"] == 2
        # Other agents should have higher priority numbers (lower urgency)
        for entry in queue[1:]:
            assert entry["priority"] > queue[0]["priority"]

    def test_orchestrator_with_coordination_keywords_responds_first(self, chat):
        """Coordination keywords boost orchestrator even when not first mention."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@python_developer @ceo_agent @cohort_orchestrator help coordinate")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["python_developer", "ceo_agent", "cohort_orchestrator"])

        queue = _get_queue()
        assert len(queue) == 3
        assert queue[0]["agent_id"] == "cohort_orchestrator"
        assert queue[0]["priority"] == 5

    def test_orchestrator_not_first_no_keywords_normal_priority(self, chat):
        """Orchestrator mentioned last without keywords gets normal priority --
        lets more relevant agents respond first."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@python_developer @cohort_orchestrator what do you think?")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["python_developer", "cohort_orchestrator"])

        queue = _get_queue()
        assert len(queue) == 2
        # python_developer was first mention -> priority 50
        # cohort_orchestrator was second mention, no keywords, no first-tag -> priority 40
        # Both get normal priority, orchestrator has no special boost
        orch_entry = next(e for e in queue if e["agent_id"] == "cohort_orchestrator")
        pd_entry = next(e for e in queue if e["agent_id"] == "python_developer")
        assert orch_entry["priority"] == pd_entry["priority"] - 10

    def test_orchestrator_solo_mention_first_tag(self, chat):
        """Solo orchestrator mention = first tag, gets boost."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@cohort_orchestrator what's the status?")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["cohort_orchestrator"])

        queue = _get_queue()
        assert len(queue) == 1
        assert queue[0]["agent_id"] == "cohort_orchestrator"
        assert queue[0]["priority"] == 2

    def test_orchestrator_first_mention_gets_extra_boost(self, chat):
        """Direct first-tag to orchestrator gets priority 2."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@cohort_orchestrator get @python_developer to review this")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["cohort_orchestrator", "python_developer"])

        queue = _get_queue()
        assert queue[0]["agent_id"] == "cohort_orchestrator"
        assert queue[0]["priority"] == 2

    def test_orchestrator_first_mention_plus_keywords_gets_best_priority(self, chat):
        """Direct first-tag + coordination keywords = priority 1 (absolute best)."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@cohort_orchestrator coordinate the team on this task")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["cohort_orchestrator", "python_developer"])

        queue = _get_queue()
        assert queue[0]["agent_id"] == "cohort_orchestrator"
        assert queue[0]["priority"] == 1


# =====================================================================
# @all routes to an orchestrator-type agent (discovered dynamically)
# =====================================================================

class TestAtAllRoutesToOrchestrator:
    def test_all_routes_to_orchestrator_agent(self, chat):
        chat.create_channel("dev", "Dev")
        msg = _msg("@all we need to discuss the architecture")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["all"])

        queue = _get_queue()
        assert len(queue) == 1
        # Should route to an orchestrator-type agent (first found alphabetically)
        assert queue[0]["agent_id"] in ("cohort_orchestrator", "coding_orchestrator")

    def test_all_routes_to_only_one_agent(self, chat):
        """@all should resolve to exactly one orchestrator, not broadcast."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@all coordinate the team")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["all"])

        queue = _get_queue()
        assert len(queue) == 1


# =====================================================================
# Orchestrator priority boost applies to orchestrator-type agents
# =====================================================================

class TestOrchestratorBoost:
    def test_orchestrator_gets_priority_boost_on_coordination_keywords(self, chat):
        chat.create_channel("dev", "Dev")
        msg = _msg("@cohort_orchestrator coordinate this workflow task")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["cohort_orchestrator"])

        queue = _get_queue()
        assert len(queue) == 1
        # With orchestrator boost, priority should be well below default 50
        assert queue[0]["priority"] < 50
        assert queue[0]["agent_id"] == "cohort_orchestrator"

    def test_orchestrator_priority_boost_on_delegation_keywords(self, chat):
        chat.create_channel("dev", "Dev")
        msg = _msg("@cohort_orchestrator please delegate this task to the team")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["cohort_orchestrator"])

        queue = _get_queue()
        assert len(queue) == 1
        assert queue[0]["priority"] < 50

    def test_any_orchestrator_type_gets_boost(self, chat):
        """coding_orchestrator also has agent_type=orchestrator, should get boost."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@coding_orchestrator coordinate this workflow")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["coding_orchestrator"])

        queue = _get_queue()
        assert len(queue) == 1
        assert queue[0]["agent_id"] == "coding_orchestrator"
        assert queue[0]["priority"] < 50
