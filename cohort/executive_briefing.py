"""Cohort Executive Briefing -- deployment-specific activity report.

Deterministic (no LLM) report generator that gathers stats from
Cohort's data sources and produces a structured briefing.  Each
deployment produces its own unique briefing from its own activity.

Standalone module -- importable without the server.  All data sources
are injected via constructor; missing ones degrade gracefully.

Usage::

    # Standalone
    from cohort.executive_briefing import ExecutiveBriefing
    briefing = ExecutiveBriefing(data_dir=Path("data"), chat=chat)
    report = briefing.generate(hours=24)
    print(report.to_text())

    # Via HTTP
    POST /api/briefing/generate  {"hours": 24}
    GET  /api/briefing/latest

    # Via CLI
    python -m cohort briefing generate --hours 24
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


# =====================================================================
# Dataclasses
# =====================================================================


@dataclass
class BriefingSection:
    """A single section of the executive briefing."""

    title: str
    content: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BriefingSection:
        return cls(
            title=d.get("title", ""),
            content=d.get("content", ""),
            data=d.get("data", {}),
        )


@dataclass
class BriefingReport:
    """A complete executive briefing report."""

    id: str
    generated_at: str
    period_start: str
    period_end: str
    sections: list[BriefingSection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "generated_at": self.generated_at,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "sections": [s.to_dict() for s in self.sections],
            "metadata": self.metadata,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BriefingReport:
        sections = [BriefingSection.from_dict(s) for s in d.get("sections", [])]
        return cls(
            id=d.get("id", ""),
            generated_at=d.get("generated_at", ""),
            period_start=d.get("period_start", ""),
            period_end=d.get("period_end", ""),
            sections=sections,
            metadata=d.get("metadata", {}),
        )

    def to_text(self) -> str:
        """Render the report as plain text for channel posting."""
        ts = self.generated_at[:19] if self.generated_at else "unknown"
        start = self.period_start[:10] if self.period_start else "?"
        end = self.period_end[:10] if self.period_end else "?"
        lines = [
            "# Executive Briefing",
            f"Generated: {ts}",
            f"Period: {start} to {end}",
            "",
        ]
        for section in self.sections:
            lines.append(f"## {section.title}")
            lines.append(section.content)
            lines.append("")
        return "\n".join(lines)


# =====================================================================
# Core class
# =====================================================================


class ExecutiveBriefing:
    """Generates executive briefing reports from Cohort system state.

    Parameters
    ----------
    data_dir:
        Base data directory.  Reports stored at ``{data_dir}/briefings/``.
    chat:
        ChatManager for channel/message stats.
    work_queue:
        WorkQueue instance (optional).
    data_layer:
        CohortDataLayer for team snapshot (optional).
    orchestrator_getter:
        Callable returning the Orchestrator instance (optional).
        Using a getter because the orchestrator is lazy-loaded.
    """

    def __init__(
        self,
        data_dir: Path,
        chat: Any,
        work_queue: Any | None = None,
        data_layer: Any | None = None,
        orchestrator_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._briefings_dir = Path(data_dir) / "briefings"
        self._chat = chat
        self._work_queue = work_queue
        self._data_layer = data_layer
        self._orchestrator_getter = orchestrator_getter

    # =================================================================
    # Public API
    # =================================================================

    def generate(
        self,
        hours: int = 24,
        post_to_channel: bool = True,
        channel_id: str = "daily-digest",
    ) -> BriefingReport:
        """Generate a briefing covering the last *hours* of activity.

        Returns the structured report.  Optionally posts to a channel.
        """
        now = datetime.now()
        period_start = (now - timedelta(hours=hours)).isoformat()
        period_end = now.isoformat()

        report = BriefingReport(
            id=f"briefing_{uuid.uuid4().hex[:8]}",
            generated_at=_now_iso(),
            period_start=period_start,
            period_end=period_end,
            metadata={"hours": hours},
        )

        report.sections.append(self._work_queue_section(period_start))
        report.sections.append(self._channel_activity_section(period_start))
        report.sections.append(self._team_snapshot_section())
        report.sections.append(self._session_section())

        self._save_report(report)

        if post_to_channel:
            self._post_to_channel(report, channel_id)

        return report

    def get_latest(self) -> BriefingReport | None:
        """Return the most recent briefing from disk, or None."""
        if not self._briefings_dir.exists():
            return None
        files = sorted(self._briefings_dir.glob("briefing_*.json"), reverse=True)
        if not files:
            return None
        try:
            data = json.loads(files[0].read_text(encoding="utf-8"))
            return BriefingReport.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[!] Failed to load latest briefing: %s", exc)
            return None

    def list_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return metadata for recent briefings."""
        if not self._briefings_dir.exists():
            return []
        files = sorted(
            self._briefings_dir.glob("briefing_*.json"), reverse=True
        )[:limit]
        results = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "id": data.get("id", ""),
                    "generated_at": data.get("generated_at", ""),
                    "period_start": data.get("period_start", ""),
                    "period_end": data.get("period_end", ""),
                })
            except (json.JSONDecodeError, OSError):
                pass
        return results

    # =================================================================
    # Section gatherers
    # =================================================================

    def _work_queue_section(self, since: str) -> BriefingSection:
        """Work queue throughput and status breakdown."""
        if self._work_queue is None:
            return BriefingSection(
                title="Work Queue",
                content="Work queue not available.",
            )

        all_items = self._work_queue.list_items()
        if not all_items:
            return BriefingSection(
                title="Work Queue",
                content="No items in the work queue.",
                data={"total": 0},
            )

        since_bare = since[:19]

        status_counts: Counter[str] = Counter()
        priority_counts: Counter[str] = Counter()
        agent_workload: Counter[str] = Counter()
        completed_in_period: list[Any] = []

        for item in all_items:
            status_counts[item.status] += 1
            priority_counts[item.priority] += 1
            if item.agent_id:
                agent_workload[item.agent_id] += 1
            if (
                item.status == "completed"
                and item.completed_at
                and item.completed_at[:19] >= since_bare
            ):
                completed_in_period.append(item)

        # Average turnaround (claimed -> completed)
        durations: list[float] = []
        for item in completed_in_period:
            if item.claimed_at and item.completed_at:
                try:
                    claimed = datetime.fromisoformat(item.claimed_at)
                    completed = datetime.fromisoformat(item.completed_at)
                    durations.append((completed - claimed).total_seconds())
                except (ValueError, TypeError):
                    pass

        avg_turnaround = sum(durations) / len(durations) if durations else None

        lines = [f"Total items: {len(all_items)}"]
        for status in ("queued", "active", "completed", "failed", "cancelled"):
            count = status_counts.get(status, 0)
            if count:
                lines.append(f"  {status.capitalize()}: {count}")
        lines.append(f"Completed this period: {len(completed_in_period)}")
        if avg_turnaround is not None:
            lines.append(f"Avg turnaround: {_fmt_duration(avg_turnaround)}")
        if priority_counts:
            prio_str = ", ".join(
                f"{p}: {c}" for p, c in sorted(priority_counts.items())
            )
            lines.append(f"By priority: {prio_str}")

        data = {
            "total": len(all_items),
            "status_counts": dict(status_counts),
            "completed_in_period": len(completed_in_period),
            "avg_turnaround_seconds": avg_turnaround,
            "priority_counts": dict(priority_counts),
            "agent_workload": dict(agent_workload),
        }

        return BriefingSection(title="Work Queue", content="\n".join(lines), data=data)

    def _channel_activity_section(self, since: str) -> BriefingSection:
        """Message volume, active channels, top senders, mention patterns."""
        channels = self._chat.list_channels(include_archived=False)
        if not channels:
            return BriefingSection(
                title="Channel Activity",
                content="No active channels.",
            )

        # Parse the period boundary once; strip timezone for comparison
        # because ChatManager timestamps may be naive (no TZ suffix).
        since_bare = since[:19]  # "2026-03-04T07:00:00"

        channel_stats: list[dict[str, Any]] = []
        total_messages = 0
        sender_counts: Counter[str] = Counter()
        mention_counts: Counter[str] = Counter()

        for ch in channels:
            messages = self._chat.get_channel_messages(ch.id, limit=200)
            period_msgs = [
                m for m in messages if m.timestamp[:19] >= since_bare
            ]
            if not period_msgs:
                continue

            ch_senders: Counter[str] = Counter()
            for msg in period_msgs:
                if msg.sender == "system":
                    continue
                ch_senders[msg.sender] += 1
                sender_counts[msg.sender] += 1
                for mention in msg.metadata.get("mentions", []):
                    mention_counts[mention] += 1

            total_messages += len(period_msgs)
            channel_stats.append({
                "channel": ch.id,
                "message_count": len(period_msgs),
                "unique_senders": len(ch_senders),
                "top_sender": (
                    ch_senders.most_common(1)[0][0] if ch_senders else None
                ),
            })

        channel_stats.sort(key=lambda x: x["message_count"], reverse=True)

        lines = [
            f"Total messages: {total_messages} across "
            f"{len(channel_stats)} active channels"
        ]

        if channel_stats:
            lines.append("")
            lines.append("Most active channels:")
            for cs in channel_stats[:5]:
                lines.append(
                    f"  #{cs['channel']}: {cs['message_count']} messages "
                    f"({cs['unique_senders']} participants)"
                )

        top_senders = sender_counts.most_common(5)
        if top_senders:
            lines.append("")
            lines.append("Most active participants:")
            for sender, count in top_senders:
                lines.append(f"  @{sender}: {count} messages")

        top_mentioned = mention_counts.most_common(5)
        if top_mentioned:
            lines.append("")
            lines.append("Most mentioned:")
            for agent, count in top_mentioned:
                lines.append(f"  @{agent}: {count} mentions")

        data = {
            "total_messages": total_messages,
            "active_channels": len(channel_stats),
            "channel_stats": channel_stats[:10],
            "top_senders": dict(sender_counts.most_common(10)),
            "top_mentioned": dict(mention_counts.most_common(10)),
        }

        return BriefingSection(
            title="Channel Activity", content="\n".join(lines), data=data
        )

    def _team_snapshot_section(self) -> BriefingSection:
        """Current team state: busy/idle counts, per-agent completions."""
        if self._data_layer is None:
            return BriefingSection(
                title="Team Status",
                content="Team data not available.",
            )

        snapshot = self._data_layer.get_team_snapshot()
        agents = snapshot.get("agents", [])
        if not agents:
            return BriefingSection(
                title="Team Status",
                content="No agents registered.",
                data={"total": 0},
            )

        busy = snapshot.get("busy_count", 0)
        idle = snapshot.get("idle_count", 0)
        total = snapshot.get("total_agents", 0)

        lines = [f"Agents: {total} total ({busy} busy, {idle} idle)"]

        groups: dict[str, list[dict[str, Any]]] = {}
        for agent in agents:
            group = agent.get("group", "Other")
            groups.setdefault(group, []).append(agent)

        for group_name, group_agents in sorted(groups.items()):
            lines.append("")
            lines.append(f"  {group_name}:")
            for a in group_agents:
                mark = "[BUSY]" if a["status"] == "busy" else "[IDLE]"
                completed = a.get("tasks_completed", 0)
                name = a.get("name", a["agent_id"])
                lines.append(f"    {mark} {name} ({completed} completed)")

        data = {
            "total": total,
            "busy": busy,
            "idle": idle,
            "groups": {name: len(ags) for name, ags in groups.items()},
        }

        return BriefingSection(
            title="Team Status", content="\n".join(lines), data=data
        )

    def _session_section(self) -> BriefingSection:
        """Active discussion sessions, turn counts, participation."""
        orch = None
        if self._orchestrator_getter is not None:
            try:
                orch = self._orchestrator_getter()
            except Exception:
                pass

        if orch is None or not hasattr(orch, "sessions"):
            return BriefingSection(
                title="Discussion Sessions",
                content="Session tracking not available.",
            )

        sessions = orch.sessions
        if not sessions:
            return BriefingSection(
                title="Discussion Sessions",
                content="No active sessions.",
                data={"active": 0},
            )

        session_list: list[dict[str, Any]] = []
        for sid, session in sessions.items():
            session_list.append({
                "session_id": sid,
                "channel": session.channel_id,
                "topic": session.topic,
                "state": session.state,
                "turns": session.current_turn,
                "max_turns": session.max_turns,
                "participants": len(
                    getattr(session, "participants_contributed", [])
                ),
            })

        lines = [f"Sessions: {len(session_list)}"]
        for s in session_list:
            lines.append(
                f"  #{s['channel']}: \"{s['topic']}\" "
                f"({s['turns']}/{s['max_turns']} turns, "
                f"{s['participants']} participants, {s['state']})"
            )

        data = {"active_count": len(session_list), "sessions": session_list}

        return BriefingSection(
            title="Discussion Sessions", content="\n".join(lines), data=data
        )

    # =================================================================
    # Persistence & posting
    # =================================================================

    def _save_report(self, report: BriefingReport) -> None:
        """Save a report to disk as JSON."""
        try:
            self._briefings_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")
            filename = f"briefing_{date_str}_{report.id}.json"
            path = self._briefings_dir / filename
            path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("[OK] Briefing saved: %s", path)
        except OSError as exc:
            logger.warning("[!] Failed to save briefing: %s", exc)

    def _post_to_channel(
        self, report: BriefingReport, channel_id: str
    ) -> None:
        """Post the text rendering to a Cohort channel."""
        if self._chat.get_channel(channel_id) is None:
            self._chat.create_channel(
                name=channel_id,
                description="Executive briefing digest",
            )

        self._chat.post_message(
            channel_id=channel_id,
            sender="executive_briefing",
            content=report.to_text(),
            message_type="system",
        )
