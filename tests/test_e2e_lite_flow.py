"""End-to-end integration tests for the LiteBackend message flow.

Tests the complete path that the VS Code extension uses:
  cohort_json.py subprocess → LiteBackend → storage → read back

Also tests file watcher triggers (storage file modification detection)
and the full session/task/condensation/briefing flows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

from cohort.mcp.lite_backend import LiteBackend


# =====================================================================
# Fixtures
# =====================================================================

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "cohort-vscode" / "scripts" / "cohort_json.py"


@pytest.fixture
def e2e_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory with required files."""
    (tmp_path / "channels.json").write_text("{}", encoding="utf-8")
    (tmp_path / "messages.json").write_text("[]", encoding="utf-8")
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest_asyncio.fixture
async def backend(e2e_data_dir: Path) -> LiteBackend:
    """LiteBackend pointed at temp dir."""
    return LiteBackend(data_dir=e2e_data_dir)


def _run_cohort_json(data_dir: Path, command: str, *args: str) -> dict | list:
    """Run cohort_json.py as a subprocess (like the extension does)."""
    if not SCRIPT_PATH.exists():
        pytest.skip(f"cohort_json.py not found at {SCRIPT_PATH}")

    env = {
        **os.environ,
        "COHORT_DATA_DIR": str(data_dir),
        # No COHORT_SERVER_URL — forces LiteBackend fallback
    }
    # Remove server URL if set to force lite mode
    env.pop("COHORT_SERVER_URL", None)

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(data_dir), command, *args],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError(
            f"cohort_json.py returned no output (code={result.returncode}): "
            f"{result.stderr[:200]}"
        )
    return json.loads(stdout)


# =====================================================================
# Subprocess flow tests (simulates what the VS Code extension does)
# =====================================================================

class TestSubprocessFlow:
    """Tests the cohort_json.py → LiteBackend → storage path."""

    def test_list_channels_empty(self, e2e_data_dir: Path):
        result = _run_cohort_json(e2e_data_dir, "list_channels")
        assert isinstance(result, list)

    def test_create_and_list_channel(self, e2e_data_dir: Path):
        result = _run_cohort_json(e2e_data_dir, "create_channel", "test-ch", "Test channel")
        assert isinstance(result, dict)

        channels = _run_cohort_json(e2e_data_dir, "list_channels")
        assert any(ch.get("id") == "test-ch" or ch.get("name") == "test-ch" for ch in channels)

    def test_post_and_read_message(self, e2e_data_dir: Path):
        # Create channel
        _run_cohort_json(e2e_data_dir, "create_channel", "msg-test", "Message test")

        # Post a message
        result = _run_cohort_json(e2e_data_dir, "post_message", "msg-test", "user", "Hello world")
        assert isinstance(result, dict)

        # Read it back
        messages = _run_cohort_json(e2e_data_dir, "read_channel", "msg-test", "100")
        assert isinstance(messages, list)
        user_msgs = [m for m in messages if m.get("sender") == "user"]
        assert len(user_msgs) >= 1
        assert user_msgs[-1]["content"] == "Hello world"

    def test_post_auto_creates_channel(self, e2e_data_dir: Path):
        """Posting to a non-existent channel should auto-create it."""
        _run_cohort_json(e2e_data_dir, "post_message", "auto-ch", "user", "Auto-created")

        channels = _run_cohort_json(e2e_data_dir, "list_channels")
        assert any(ch.get("id") == "auto-ch" or ch.get("name") == "auto-ch" for ch in channels)

    def test_multiple_messages_ordering(self, e2e_data_dir: Path):
        """Messages should come back in chronological order."""
        _run_cohort_json(e2e_data_dir, "create_channel", "order-ch", "Ordering test")

        for i in range(5):
            _run_cohort_json(e2e_data_dir, "post_message", "order-ch", "user", f"Message {i}")

        messages = _run_cohort_json(e2e_data_dir, "read_channel", "order-ch", "100")
        user_msgs = [m for m in messages if m.get("sender") == "user"]
        assert len(user_msgs) == 5
        for i, msg in enumerate(user_msgs):
            assert msg["content"] == f"Message {i}"

    def test_search_messages(self, e2e_data_dir: Path):
        _run_cohort_json(e2e_data_dir, "post_message", "search-ch", "user", "The quick brown fox")
        _run_cohort_json(e2e_data_dir, "post_message", "search-ch", "user", "Lazy dog sleeping")

        results = _run_cohort_json(e2e_data_dir, "search_messages", "fox")
        assert isinstance(results, list)
        assert any("fox" in m.get("content", "").lower() for m in results)

    def test_message_with_mentions_metadata(self, e2e_data_dir: Path):
        """Post a message with mentions metadata (like the extension does for DMs)."""
        metadata = json.dumps({"mentions": ["test_agent"], "response_mode": "smarter"})
        _run_cohort_json(
            e2e_data_dir, "post_message", "dm-test_agent", "user", "Hey agent", metadata
        )

        messages = _run_cohort_json(e2e_data_dir, "read_channel", "dm-test_agent", "100")
        user_msgs = [m for m in messages if m.get("sender") == "user"]
        assert len(user_msgs) >= 1
        msg_meta = user_msgs[-1].get("metadata", {})
        assert "test_agent" in msg_meta.get("mentions", [])


# =====================================================================
# Storage file change detection tests
# =====================================================================

class TestFileWatcherFlow:
    """Tests that storage writes produce detectable file changes."""

    def test_messages_json_modified_on_post(self, e2e_data_dir: Path):
        """Posting a message should modify messages.json (triggers file watcher)."""
        messages_path = e2e_data_dir / "messages.json"
        mtime_before = messages_path.stat().st_mtime

        # Small delay to ensure mtime changes
        time.sleep(0.05)

        _run_cohort_json(e2e_data_dir, "post_message", "watch-ch", "user", "Trigger watcher")

        mtime_after = messages_path.stat().st_mtime
        assert mtime_after > mtime_before, "messages.json should be modified after posting"

    def test_channels_json_modified_on_create(self, e2e_data_dir: Path):
        """Creating a channel should modify channels.json."""
        channels_path = e2e_data_dir / "channels.json"
        mtime_before = channels_path.stat().st_mtime

        time.sleep(0.05)

        _run_cohort_json(e2e_data_dir, "create_channel", "new-ch", "New channel")

        mtime_after = channels_path.stat().st_mtime
        assert mtime_after > mtime_before, "channels.json should be modified after channel creation"

    def test_message_content_persisted_to_disk(self, e2e_data_dir: Path):
        """Verify the actual JSON file contains the posted message."""
        _run_cohort_json(e2e_data_dir, "post_message", "persist-ch", "user", "Persisted message")

        raw = json.loads((e2e_data_dir / "messages.json").read_text(encoding="utf-8"))
        user_msgs = [m for m in raw if m.get("sender") == "user" and m.get("channel_id") == "persist-ch"]
        assert len(user_msgs) >= 1
        assert user_msgs[-1]["content"] == "Persisted message"


# =====================================================================
# Full LiteBackend feature tests (async, in-process)
# =====================================================================

class TestLiteBackendE2E:
    """Tests LiteBackend features end-to-end (in-process, no subprocess)."""

    @pytest.mark.asyncio
    async def test_session_full_flow(self, backend: LiteBackend):
        """Start session → messages posted → session tracked."""
        result = await backend.start_session(
            channel="e2e-session",
            agents=["agent_a", "agent_b"],
            prompt="Discuss the architecture",
        )
        assert result["success"] is True
        session_id = result["session_id"]

        # Session should be tracked
        status = await backend.get_session_status(session_id)
        assert status["status"] == "active"
        assert status["channel_id"] == "e2e-session"

        # Channel should have system message + user prompt
        msgs = await backend.get_messages("e2e-session", limit=50)
        assert len(msgs) >= 2
        system_msgs = [m for m in msgs if m.get("sender") == "system"]
        assert any("[SESSION]" in m.get("content", "") for m in system_msgs)

    @pytest.mark.asyncio
    async def test_task_full_lifecycle(self, backend: LiteBackend):
        """Create → list → update → complete → archive."""
        # Create
        result = await backend.create_task("test_agent", "Write tests", priority="high")
        assert result["success"] is True
        task_id = result["task_id"]

        # List
        tasks = await backend.get_task_queue()
        assert any(t["task_id"] == task_id for t in tasks)

        # Complete
        from cohort.task_store import TaskStore
        backend._task_store.complete_task(task_id, output={"content": "Done"})

        # Outputs for review
        outputs = await backend.get_outputs_for_review()
        assert any(t["task_id"] == task_id for t in outputs)

    @pytest.mark.asyncio
    async def test_work_queue_full_flow(self, backend: LiteBackend):
        """Enqueue → list → claim → update."""
        result = await backend.enqueue_work_item("Process data", requester="user")
        assert result["success"] is True
        item_id = result["item_id"]

        # Should appear in work queue
        queue = await backend.get_work_queue()
        assert len(queue) >= 1

        # Get item
        item = await backend.get_work_item(item_id)
        assert item is not None

    @pytest.mark.asyncio
    async def test_condense_full_flow(self, backend: LiteBackend):
        """Post many messages → condense → verify only recent remain."""
        channel = "condense-e2e"
        for i in range(10):
            await backend.post_message(channel, "user", f"Message {i}")

        msgs_before = await backend.get_messages(channel, limit=100)
        assert len(msgs_before) >= 10  # may include system msg from auto-create

        result = await backend.condense_channel(channel, keep_last=3)
        assert result["success"] is True
        assert result["archived_count"] >= 7

        msgs_after = await backend.get_messages(channel, limit=100)
        assert len(msgs_after) == 3

    @pytest.mark.asyncio
    async def test_briefing_generates_from_activity(self, backend: LiteBackend):
        """Post activity → generate briefing → verify content."""
        await backend.post_message("general", "user", "We need to ship the MVP by Friday")
        await backend.post_message("general", "dev_agent", "I'll have the PR ready tomorrow")
        await backend.post_message("design", "design_agent", "Mockups are done")

        result = await backend.generate_briefing(hours=24, post_to_channel=False)
        assert result["success"] is True
        assert "report" in result
        assert result["report"]["summary"]  # non-empty

        # Latest briefing should be cached
        latest = await backend.get_latest_briefing()
        assert latest is not None
        assert latest["summary"] == result["report"]["summary"]

    @pytest.mark.asyncio
    async def test_cross_channel_message_isolation(self, backend: LiteBackend):
        """Messages in one channel don't leak into another."""
        await backend.post_message("channel-a", "user", "Message for A")
        await backend.post_message("channel-b", "user", "Message for B")

        msgs_a = await backend.get_messages("channel-a", limit=100)
        msgs_b = await backend.get_messages("channel-b", limit=100)

        a_content = [m["content"] for m in msgs_a if m.get("sender") == "user"]
        b_content = [m["content"] for m in msgs_b if m.get("sender") == "user"]

        assert "Message for A" in a_content
        assert "Message for B" not in a_content
        assert "Message for B" in b_content
        assert "Message for A" not in b_content
