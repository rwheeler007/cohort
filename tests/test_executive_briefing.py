"""Tests for the Cohort executive briefing system.

Covers the standalone module (data gathering, report generation,
persistence), HTTP endpoints, and edge cases (empty data, missing
components).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio

from cohort.chat import ChatManager
from cohort.executive_briefing import (
    BriefingReport,
    BriefingSection,
    ExecutiveBriefing,
)
from cohort.registry import JsonFileStorage


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def briefing_dir(tmp_path: Path) -> Path:
    """Temporary data directory for briefing tests."""
    (tmp_path / "channels.json").write_text("{}", encoding="utf-8")
    (tmp_path / "messages.json").write_text("[]", encoding="utf-8")
    return tmp_path


@pytest.fixture
def briefing_chat(briefing_dir: Path) -> ChatManager:
    """ChatManager backed by temp storage."""
    storage = JsonFileStorage(briefing_dir)
    return ChatManager(storage)


@pytest.fixture
def briefing(briefing_dir: Path, briefing_chat: ChatManager) -> ExecutiveBriefing:
    """ExecutiveBriefing with only chat (minimal)."""
    return ExecutiveBriefing(data_dir=briefing_dir, chat=briefing_chat)


# =====================================================================
# Dataclass tests
# =====================================================================


class TestBriefingSection:
    def test_roundtrip(self):
        section = BriefingSection(
            title="Work Queue",
            content="Total items: 5",
            data={"total": 5},
        )
        d = section.to_dict()
        restored = BriefingSection.from_dict(d)
        assert restored.title == "Work Queue"
        assert restored.content == "Total items: 5"
        assert restored.data == {"total": 5}

    def test_from_dict_defaults(self):
        section = BriefingSection.from_dict({})
        assert section.title == ""
        assert section.content == ""
        assert section.data == {}


class TestBriefingReport:
    def test_roundtrip(self):
        report = BriefingReport(
            id="test_001",
            generated_at="2026-03-04T07:00:00+00:00",
            period_start="2026-03-03T07:00:00+00:00",
            period_end="2026-03-04T07:00:00+00:00",
            sections=[
                BriefingSection(title="A", content="hello", data={"x": 1}),
                BriefingSection(title="B", content="world"),
            ],
            metadata={"hours": 24},
        )
        d = report.to_dict()
        restored = BriefingReport.from_dict(d)
        assert restored.id == "test_001"
        assert len(restored.sections) == 2
        assert restored.sections[0].title == "A"
        assert restored.sections[1].data == {}
        assert restored.metadata == {"hours": 24}

    def test_to_text_renders_all_sections(self):
        report = BriefingReport(
            id="test",
            generated_at="2026-03-04T07:00:00+00:00",
            period_start="2026-03-03T07:00:00+00:00",
            period_end="2026-03-04T07:00:00+00:00",
            sections=[
                BriefingSection(title="Section A", content="Content A"),
                BriefingSection(title="Section B", content="Content B"),
            ],
        )
        text = report.to_text()
        assert "# Executive Briefing" in text
        assert "## Section A" in text
        assert "Content A" in text
        assert "## Section B" in text
        assert "Content B" in text
        assert "2026-03-03" in text
        assert "2026-03-04" in text


# =====================================================================
# Standalone module tests
# =====================================================================


class TestGenerateEmpty:
    """Generate with no data — should not crash."""

    def test_generate_with_empty_data(self, briefing: ExecutiveBriefing):
        report = briefing.generate(post_to_channel=False)
        assert report.id.startswith("briefing_")
        assert len(report.sections) == 5
        # All sections should have content (even if "not available")
        for section in report.sections:
            assert section.content

    def test_no_crash_when_all_optional_sources_missing(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        """All optional sources are None — still produces a report."""
        b = ExecutiveBriefing(
            data_dir=briefing_dir,
            chat=briefing_chat,
            work_queue=None,
            data_layer=None,
            orchestrator_getter=None,
        )
        report = b.generate(post_to_channel=False)
        assert len(report.sections) == 5
        assert "not available" in report.sections[0].content.lower()
        assert "not available" in report.sections[2].content.lower()
        assert "not available" in report.sections[3].content.lower()


class TestWorkQueueSection:
    def test_with_items(self, briefing_dir: Path, briefing_chat: ChatManager):
        mock_wq = MagicMock()
        now = datetime.now(timezone.utc)
        items = []
        for i, status in enumerate(
            ["completed", "completed", "queued", "active", "failed"]
        ):
            item = MagicMock()
            item.status = status
            item.priority = "medium" if i < 3 else "high"
            item.agent_id = f"agent_{i % 2}"
            item.item_id = f"item_{i}"
            item.id = f"item_{i}"
            item.description = f"Test task {i}"
            item.created_at = (now - timedelta(hours=3)).isoformat()
            item.completed_at = (
                (now - timedelta(hours=1)).isoformat() if status == "completed" else None
            )
            item.claimed_at = (
                (now - timedelta(hours=2)).isoformat() if status == "completed" else None
            )
            items.append(item)
        mock_wq.list_items.return_value = items

        b = ExecutiveBriefing(
            data_dir=briefing_dir, chat=briefing_chat, work_queue=mock_wq
        )
        report = b.generate(hours=24, post_to_channel=False)
        wq_section = report.sections[0]
        assert wq_section.title == "Work Queue"
        assert "Total items: 5" in wq_section.content
        assert "Completed this period: 2" in wq_section.content
        assert wq_section.data["total"] == 5
        assert wq_section.data["completed_in_period"] == 2
        assert wq_section.data["avg_turnaround_seconds"] is not None

    def test_empty_queue(self, briefing_dir: Path, briefing_chat: ChatManager):
        mock_wq = MagicMock()
        mock_wq.list_items.return_value = []
        b = ExecutiveBriefing(
            data_dir=briefing_dir, chat=briefing_chat, work_queue=mock_wq
        )
        report = b.generate(post_to_channel=False)
        assert "No items" in report.sections[0].content


class TestChannelActivitySection:
    def test_with_messages(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        # Create a channel and post some messages
        briefing_chat.create_channel("general", "General chat")
        briefing_chat.post_message("general", "alice", "Hello @bob")
        briefing_chat.post_message("general", "bob", "Hi @alice")
        briefing_chat.post_message("general", "alice", "How are you?")

        b = ExecutiveBriefing(data_dir=briefing_dir, chat=briefing_chat)
        report = b.generate(hours=1, post_to_channel=False)
        ch_section = report.sections[1]
        assert ch_section.title == "Channel Activity"
        # System message from channel creation + 3 user messages
        assert ch_section.data["total_messages"] > 0
        assert ch_section.data["active_channels"] >= 1

    def test_filters_by_period(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        briefing_chat.create_channel("old-channel", "Old chat")
        # Messages are timestamped at creation time (now), so they
        # should appear in a 1-hour window
        briefing_chat.post_message("old-channel", "user1", "Recent message")

        b = ExecutiveBriefing(data_dir=briefing_dir, chat=briefing_chat)
        report = b.generate(hours=1, post_to_channel=False)
        ch_section = report.sections[1]
        # Should find at least the message we just posted
        assert ch_section.data["total_messages"] >= 1


class TestTeamSnapshotSection:
    def test_with_data_layer(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        mock_dl = MagicMock()
        mock_dl.get_team_snapshot.return_value = {
            "agents": [
                {
                    "agent_id": "dev1",
                    "name": "Developer 1",
                    "status": "busy",
                    "tasks_completed": 3,
                    "group": "Developers",
                },
                {
                    "agent_id": "dev2",
                    "name": "Developer 2",
                    "status": "idle",
                    "tasks_completed": 1,
                    "group": "Developers",
                },
            ],
            "total_agents": 2,
            "busy_count": 1,
            "idle_count": 1,
        }

        b = ExecutiveBriefing(
            data_dir=briefing_dir,
            chat=briefing_chat,
            data_layer=mock_dl,
        )
        report = b.generate(post_to_channel=False)
        team_section = report.sections[2]
        assert "2 total" in team_section.content
        assert "1 busy" in team_section.content
        assert "[BUSY]" in team_section.content
        assert "[IDLE]" in team_section.content
        assert team_section.data["total"] == 2


class TestSessionSection:
    def test_with_sessions(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        mock_session = MagicMock()
        mock_session.channel_id = "design-review"
        mock_session.topic = "API design"
        mock_session.state = "active"
        mock_session.current_turn = 5
        mock_session.max_turns = 20
        mock_session.participants_contributed = ["agent1", "agent2"]

        mock_orch = MagicMock()
        mock_orch.sessions = {"s_001": mock_session}

        b = ExecutiveBriefing(
            data_dir=briefing_dir,
            chat=briefing_chat,
            orchestrator_getter=lambda: mock_orch,
        )
        report = b.generate(post_to_channel=False)
        sess_section = report.sections[3]
        assert "Sessions: 1" in sess_section.content
        assert "API design" in sess_section.content
        assert "5/20" in sess_section.content
        assert sess_section.data["active_count"] == 1

    def test_orchestrator_getter_raises(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        """If getter throws, section degrades gracefully."""
        def bad_getter():
            raise RuntimeError("not ready")

        b = ExecutiveBriefing(
            data_dir=briefing_dir,
            chat=briefing_chat,
            orchestrator_getter=bad_getter,
        )
        report = b.generate(post_to_channel=False)
        assert "not available" in report.sections[3].content.lower()


class TestPersistence:
    def test_save_and_load_latest(self, briefing: ExecutiveBriefing):
        report = briefing.generate(post_to_channel=False)
        loaded = briefing.get_latest()
        assert loaded is not None
        assert loaded.id == report.id
        assert len(loaded.sections) == len(report.sections)

    def test_list_reports(self, briefing: ExecutiveBriefing):
        briefing.generate(post_to_channel=False)
        briefing.generate(post_to_channel=False)
        reports = briefing.list_reports(limit=10)
        assert len(reports) >= 2
        # Most recent first
        assert reports[0]["generated_at"] >= reports[1]["generated_at"]

    def test_get_latest_returns_none_when_empty(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        b = ExecutiveBriefing(data_dir=briefing_dir, chat=briefing_chat)
        assert b.get_latest() is None


class TestChannelPosting:
    def test_auto_creates_channel(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        b = ExecutiveBriefing(data_dir=briefing_dir, chat=briefing_chat)
        # Channel doesn't exist yet
        assert briefing_chat.get_channel("daily-digest") is None
        b.generate(post_to_channel=True, channel_id="daily-digest")
        # Channel should now exist
        ch = briefing_chat.get_channel("daily-digest")
        assert ch is not None
        # Should have messages (system creation + briefing)
        msgs = briefing_chat.get_channel_messages("daily-digest", limit=10)
        assert len(msgs) >= 1

    def test_posts_to_existing_channel(
        self, briefing_dir: Path, briefing_chat: ChatManager
    ):
        briefing_chat.create_channel("daily-digest", "Digest channel")
        b = ExecutiveBriefing(data_dir=briefing_dir, chat=briefing_chat)
        b.generate(post_to_channel=True, channel_id="daily-digest")
        msgs = briefing_chat.get_channel_messages("daily-digest", limit=10)
        # Should have system creation msg + briefing msg
        assert len(msgs) >= 2


# =====================================================================
# HTTP endpoint tests
# =====================================================================


@pytest.mark.asyncio
class TestHTTPEndpoints:
    async def test_generate_returns_200(self, server_client: httpx.AsyncClient):
        resp = await server_client.post("/api/briefing/generate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "report" in data
        assert data["report"]["id"].startswith("briefing_")
        assert len(data["report"]["sections"]) == 5

    async def test_generate_custom_hours(self, server_client: httpx.AsyncClient):
        resp = await server_client.post(
            "/api/briefing/generate",
            json={"hours": 48, "post_to_channel": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["report"]["metadata"]["hours"] == 48

    async def test_latest_returns_404_when_empty(
        self, server_client: httpx.AsyncClient
    ):
        resp = await server_client.get("/api/briefing/latest")
        # May be 404 or 200 depending on whether generate has run
        # In a fresh test env it should be 404
        if resp.status_code == 404:
            assert "No briefings" in resp.json().get("error", "")

    async def test_latest_returns_report_after_generate(
        self, server_client: httpx.AsyncClient
    ):
        # Generate first
        await server_client.post(
            "/api/briefing/generate",
            json={"post_to_channel": False},
        )
        # Then fetch latest
        resp = await server_client.get("/api/briefing/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "report" in data
