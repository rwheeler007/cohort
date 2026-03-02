"""Tests for cohort.agent_router -- BOSS-first routing and priority."""

from pathlib import Path
from unittest.mock import patch

import pytest

import cohort.agent_router as router_mod
from cohort.chat import ChatManager, Message
from cohort.registry import JsonFileStorage


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(tmp_path)


@pytest.fixture
def chat(storage: JsonFileStorage) -> ChatManager:
    return ChatManager(storage)


@pytest.fixture
def agents_root(tmp_path: Path) -> Path:
    """Create a minimal agents directory with prompt files."""
    agents_dir = tmp_path / "agents"
    for agent_id in ("boss_agent", "ceo_agent", "python_developer", "coding_orchestrator"):
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_prompt.md").write_text(
            f"You are {agent_id}.", encoding="utf-8"
        )
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_router(chat: ChatManager, agents_root: Path):
    """Reset router state before each test."""
    router_mod.setup_agent_router(chat, sio=None, agents_root=agents_root)
    yield
    router_mod.setup_agent_router(chat, sio=None, agents_root=agents_root)


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
# BOSS alias resolution
# =====================================================================

class TestBossAliasResolution:
    def test_boss_alias_resolves(self):
        assert router_mod.AGENT_ALIASES.get("boss") == "boss_agent"

    def test_resolve_boss_by_alias(self, agents_root):
        resolved = router_mod.resolve_agent_id("boss")
        assert resolved == "boss_agent"

    def test_resolve_boss_direct(self, agents_root):
        resolved = router_mod.resolve_agent_id("boss_agent")
        assert resolved == "boss_agent"


# =====================================================================
# BOSS-first priority routing
# =====================================================================

class TestBossFirstPriority:
    """BOSS gets priority boost only when directly invoked (first mention)
    or when coordination keywords are present. Otherwise follows normal
    priority rules so more relevant agents can respond first."""

    def test_boss_first_mention_responds_first(self, chat):
        """Direct first-tag to BOSS gives it priority over other agents."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@boss_agent please get @ceo_agent and @python_developer input")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["boss_agent", "ceo_agent", "python_developer"])

        queue = _get_queue()
        assert len(queue) == 3
        assert queue[0]["agent_id"] == "boss_agent"
        assert queue[0]["priority"] == 2
        # Other agents should have higher priority numbers (lower urgency)
        for entry in queue[1:]:
            assert entry["priority"] > queue[0]["priority"]

    def test_boss_with_coordination_keywords_responds_first(self, chat):
        """Coordination keywords boost BOSS even when not first mention."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@python_developer @ceo_agent @boss_agent help coordinate")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["python_developer", "ceo_agent", "boss_agent"])

        queue = _get_queue()
        assert len(queue) == 3
        assert queue[0]["agent_id"] == "boss_agent"
        assert queue[0]["priority"] == 5

    def test_boss_not_first_no_keywords_normal_priority(self, chat):
        """BOSS mentioned last without keywords gets normal priority --
        lets more relevant agents respond first."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@python_developer @boss_agent what do you think?")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["python_developer", "boss_agent"])

        queue = _get_queue()
        assert len(queue) == 2
        # python_developer was first mention -> priority 50
        # boss_agent was second mention, no keywords, no first-tag -> priority 40
        # Both get normal priority, BOSS has no special boost
        boss_entry = next(e for e in queue if e["agent_id"] == "boss_agent")
        pd_entry = next(e for e in queue if e["agent_id"] == "python_developer")
        assert boss_entry["priority"] == pd_entry["priority"] - 10

    def test_boss_solo_mention_first_tag(self, chat):
        """Solo BOSS mention = first tag, gets boost."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@boss_agent what's the status?")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["boss_agent"])

        queue = _get_queue()
        assert len(queue) == 1
        assert queue[0]["agent_id"] == "boss_agent"
        assert queue[0]["priority"] == 2

    def test_boss_first_mention_gets_extra_boost(self, chat):
        """Direct first-tag to BOSS gets priority 2."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@boss_agent get @python_developer to review this")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["boss_agent", "python_developer"])

        queue = _get_queue()
        assert queue[0]["agent_id"] == "boss_agent"
        assert queue[0]["priority"] == 2

    def test_boss_first_mention_plus_keywords_gets_best_priority(self, chat):
        """Direct first-tag + coordination keywords = priority 1 (absolute best)."""
        chat.create_channel("dev", "Dev")
        msg = _msg("@boss_agent coordinate the team on this task")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["boss_agent", "python_developer"])

        queue = _get_queue()
        assert queue[0]["agent_id"] == "boss_agent"
        assert queue[0]["priority"] == 1


# =====================================================================
# @all routes to BOSS (not coding_orchestrator)
# =====================================================================

class TestAtAllRoutesToBoss:
    def test_all_routes_to_boss_agent(self, chat):
        chat.create_channel("dev", "Dev")
        msg = _msg("@all we need to discuss the architecture")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["all"])

        queue = _get_queue()
        assert len(queue) == 1
        assert queue[0]["agent_id"] == "boss_agent"

    def test_all_does_not_route_to_coding_orchestrator(self, chat):
        chat.create_channel("dev", "Dev")
        msg = _msg("@all coordinate the team")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["all"])

        queue = _get_queue()
        agent_ids = [e["agent_id"] for e in queue]
        assert "coding_orchestrator" not in agent_ids


# =====================================================================
# Orchestrator priority boost applies to boss_agent
# =====================================================================

class TestBossOrchestratorBoost:
    def test_boss_gets_priority_boost_on_coordination_keywords(self, chat):
        chat.create_channel("dev", "Dev")
        msg = _msg("@boss_agent coordinate this workflow task")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["boss_agent"])

        queue = _get_queue()
        assert len(queue) == 1
        # With orchestrator boost, priority should be well below default 50
        assert queue[0]["priority"] < 50
        assert queue[0]["agent_id"] == "boss_agent"

    def test_boss_priority_boost_on_delegation_keywords(self, chat):
        chat.create_channel("dev", "Dev")
        msg = _msg("@boss_agent please delegate this task to the team")

        with patch.object(router_mod, "_start_queue_processor"):
            router_mod.route_mentions(msg, ["boss_agent"])

        queue = _get_queue()
        assert len(queue) == 1
        assert queue[0]["priority"] < 50
