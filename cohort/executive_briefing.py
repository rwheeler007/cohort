"""Cohort Executive Briefing -- deployment-specific activity report.

Gathers stats from Cohort's data sources and produces a structured
briefing with optional LLM-generated narratives.  Each deployment
produces its own unique briefing from its own activity.

LLM features (via local Ollama through LocalRouter):
- Executive summary (chief-of-staff voice)
- Agent narrative cards (first-person personality voice)
- Intel "why it matters" bullets per article

All LLM calls fall back to deterministic summaries if the local
model is unavailable.  Standalone module -- importable without the
server.  All data sources are injected via constructor; missing ones
degrade gracefully.

Usage::

    # Standalone
    from cohort.executive_briefing import ExecutiveBriefing
    briefing = ExecutiveBriefing(data_dir=Path("data"), chat=chat)
    report = briefing.generate(hours=24)
    print(report.to_text())

    # HTML report
    html_path = briefing.generate_html(hours=24)

    # Via HTTP
    POST /api/briefing/generate  {"hours": 24}
    POST /api/briefing/generate  {"hours": 24, "format": "html"}
    GET  /api/briefing/latest
    GET  /api/briefing/latest/html

    # Via CLI
    python -m cohort briefing generate --hours 24
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
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


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return html_mod.escape(str(text)) if text else ""


def _trunc(text: str, n: int = 90) -> str:
    """Truncate to first line, max n chars."""
    first = text.split("\n")[0].strip() if text else ""
    return (first[:n - 3] + "...") if len(first) > n else first


def _infer_stakeholder_agents(
    article: dict[str, Any],
    agents_list: list[dict[str, Any]],
) -> list[str]:
    """Infer which agents should be tagged for an article discussion.

    Uses a lightweight keyword match against agent group, skills, and
    domain expertise.  Returns up to 3 agent IDs sorted by relevance.
    """
    if not agents_list:
        return []

    title = article.get("title", "").lower()
    tags = [t.lower() for t in article.get("tags", [])]
    summary = article.get("summary", "").lower()
    text = f"{title} {summary} {' '.join(tags)}"

    scored: list[tuple[str, int]] = []
    for agent in agents_list:
        agent_id = agent.get("agent_id", "")
        if not agent_id:
            continue
        hits = 0

        # Match against agent skills
        for skill in agent.get("skills", []):
            if skill.lower() in text:
                hits += 2

        # Match against group/role
        group = agent.get("group", "").lower()
        if group and group in text:
            hits += 1

        # Match tags against agent ID keywords
        id_words = set(agent_id.replace("_", " ").replace("-", " ").split())
        for w in id_words:
            if len(w) > 2 and w in text:
                hits += 1

        if hits > 0:
            scored.append((agent_id, hits))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [agent_id for agent_id, _ in scored[:3]]


def _extract_yt_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    if not url:
        return None
    m = re.search(
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|v/))([a-zA-Z0-9_-]{11})",
        url,
    )
    return m.group(1) if m else None


# =====================================================================
# LLM Generation (optional -- falls back to deterministic)
# =====================================================================

_router_instance = None


def _get_router():
    """Lazy-load LocalRouter.  Returns None if unavailable."""
    global _router_instance
    if _router_instance is not None:
        return _router_instance
    try:
        from cohort.local.router import LocalRouter
        _router_instance = LocalRouter()
        return _router_instance
    except Exception:
        return None


def _llm_generate(prompt: str, max_tokens: int = 2000) -> str | None:
    """Call local LLM via LocalRouter.  Returns text or None."""
    router = _get_router()
    if router is None:
        return None
    try:
        result = router.route(
            prompt=prompt,
            task_type="general",
            temperature=0.35,
            response_mode="smart",  # no thinking, fast
        )
        if result and result.text:
            # Strip any <think> blocks if model ignores directive
            text = re.sub(r"<think>.*?</think>", "", result.text, flags=re.DOTALL)
            return text.strip()
        return None
    except Exception as exc:
        logger.debug("[!] LLM generation failed: %s", exc)
        return None


def _generate_exec_summary(
    total_agents: int,
    busy_agents: int,
    total_msgs: int,
    active_channels: int,
    wq_total: int,
    wq_completed: int,
    intel_count: int,
) -> str:
    """Generate an LLM executive summary.  Falls back to bullet points."""
    prompt = (
        "You are writing the executive summary for a daily system briefing "
        "of a multi-agent orchestration platform called Cohort.\n\n"
        f"Team: {total_agents} agents ({busy_agents} busy)\n"
        f"Communication: {total_msgs} messages across {active_channels} channels\n"
        f"Work queue: {wq_total} items total, {wq_completed} completed this period\n"
        f"Intel feed: {intel_count} articles\n\n"
        "Write 3-4 sentences summarizing the state of the system for the owner. "
        "Tone: professional but conversational, like a chief of staff morning brief. "
        "Focus on what matters and what needs attention. Under 80 words. "
        "Do not use hashtags or emojis."
    )

    result = _llm_generate(prompt)
    if result:
        return result

    # Deterministic fallback
    parts = []
    if total_agents:
        parts.append(f"{total_agents} agents registered ({busy_agents} busy).")
    if total_msgs:
        parts.append(
            f"{total_msgs} messages across {active_channels} channels "
            "in the reporting period."
        )
    if wq_total:
        parts.append(
            f"Work queue: {wq_total} items total, "
            f"{wq_completed} completed this period."
        )
    if intel_count:
        parts.append(f"{intel_count} intel articles in the feed.")
    return " ".join(parts) if parts else "No significant activity in the reporting period."


def _generate_agent_narrative(agent: dict[str, Any]) -> str:
    """Generate first-person agent narrative.  Falls back to stats summary."""
    name = agent.get("name", agent.get("agent_id", "Agent"))
    status = agent.get("status", "idle")
    group = agent.get("group", "")
    completed = agent.get("tasks_completed", 0)
    skills = agent.get("skills", [])
    current_task = agent.get("current_task")

    prompt = (
        f"You are {name}, an AI agent on the Cohort platform. "
        f"Your role/group: {group}. "
        f"Skills: {', '.join(skills[:5]) if skills else 'general'}.\n\n"
        f"Status: {'busy' if status == 'busy' else 'idle'}\n"
        f"Tasks completed: {completed}\n"
        f"Current task: {current_task or 'none'}\n\n"
        "Write 2-3 sentences in first person summarizing your status "
        "for the daily briefing. Be concise, use personality, mention "
        "what you could be doing if idle. Keep it under 50 words. "
        "Do not use hashtags or emojis."
    )

    result = _llm_generate(prompt)
    if result:
        return result

    # Deterministic fallback
    if status == "busy" and current_task:
        return f"Currently working on: {_trunc(str(current_task), 80)}."
    if completed:
        return f"Completed {completed} tasks. Standing by for new assignments."
    return "On standby. Available for tasking."


def _generate_intel_summaries(
    articles: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Generate 'why it matters' bullets for ALL intel articles.

    High-relevance articles (score >= 5) get full 3-bullet analysis
    (WHAT/WHY/ACTION).  Low-relevance articles get a single-line
    opportunity note -- these are cheap to generate but can surface
    cross-domain gems that would otherwise be missed.

    Returns dict mapping article ID -> list of bullet strings.
    Falls back to empty dict if LLM unavailable.
    """
    if not articles:
        return {}

    # Split into tiers
    high = [a for a in articles if a.get("relevance_score", 0) >= 5]
    low = [a for a in articles if a.get("relevance_score", 0) < 5]

    summaries: dict[str, list[str]] = {}

    # --- High relevance: full 3-bullet analysis ---
    if high:
        lines = []
        for i, a in enumerate(high, 1):
            title = _trunc(a.get("title", "Untitled"), 80)
            tags = a.get("tags", [])
            tag_str = ", ".join(tags[:3]) if tags else "general"
            lines.append(f'{i}. "{title}" [{tag_str}]')

        prompt = (
            "You are an intelligence analyst writing for a tech team's daily briefing. "
            "For each article below, write exactly 3 bullet points:\n"
            "- WHAT: One sentence summary\n"
            "- WHY: Why it matters to a software development team\n"
            "- ACTION: What to consider doing about it\n\n"
            "Format: Number, then 3 lines starting with '- WHAT:', '- WHY:', '- ACTION:'.\n"
            "Keep each bullet under 20 words. No preamble.\n\n"
            + "\n".join(lines)
        )

        result = _llm_generate(prompt, max_tokens=4000)
        if result:
            _parse_numbered_bullets(result, high, summaries)

    # --- Low relevance: single-line opportunity note ---
    if low:
        lines = []
        for i, a in enumerate(low, 1):
            title = _trunc(a.get("title", "Untitled"), 80)
            tags = a.get("tags", [])
            tag_str = ", ".join(tags[:3]) if tags else "general"
            lines.append(f'{i}. "{title}" [{tag_str}]')

        prompt = (
            "You are an intelligence analyst scanning peripheral articles for "
            "hidden opportunities. For each article below, write ONE line:\n"
            "- If there's any angle relevant to software teams, AI, or "
            "automation, note the opportunity in under 15 words.\n"
            "- If truly irrelevant, write 'skip'.\n\n"
            "Format: Number. Your one-line note (or 'skip').\n"
            "No preamble.\n\n"
            + "\n".join(lines)
        )

        result = _llm_generate(prompt, max_tokens=2000)
        if result:
            for ln in result.strip().split("\n"):
                ln = ln.strip()
                if not ln:
                    continue
                num_match = re.match(r"^(\d+)[.):\-\s]+", ln)
                if num_match:
                    idx = int(num_match.group(1)) - 1
                    text = ln[num_match.end():].strip()
                    if (
                        0 <= idx < len(low)
                        and text.lower() != "skip"
                        and len(text) > 3
                    ):
                        aid = low[idx].get("id", "")
                        if aid:
                            summaries[aid] = [text]

    return summaries


def _parse_numbered_bullets(
    result: str,
    articles: list[dict[str, Any]],
    summaries: dict[str, list[str]],
) -> None:
    """Parse numbered bullet responses into the summaries dict."""
    current_idx = -1
    current_bullets: list[str] = []

    for ln in result.strip().split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        num_match = re.match(r"^(\d+)[.):\-\s]", ln)
        if num_match and not ln.startswith("- "):
            if current_idx >= 0 and current_bullets:
                aid = (
                    articles[current_idx].get("id", "")
                    if current_idx < len(articles)
                    else ""
                )
                if aid:
                    summaries[aid] = current_bullets[:]
            current_idx = int(num_match.group(1)) - 1
            current_bullets = []
            rest = ln[num_match.end():].strip()
            if rest.startswith("- "):
                current_bullets.append(rest[2:].strip())
        elif ln.startswith("- "):
            bullet_text = ln[2:].strip()
            bullet_text = re.sub(
                r"^(WHAT|WHY|ACTION|What|Why|Action):\s*", "", bullet_text
            )
            if bullet_text:
                current_bullets.append(bullet_text)

    # Save last article
    if current_idx >= 0 and current_bullets:
        aid = (
            articles[current_idx].get("id", "")
            if current_idx < len(articles)
            else ""
        )
        if aid:
            summaries[aid] = current_bullets[:]


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

    def get_section(self, title: str) -> BriefingSection | None:
        """Find a section by title."""
        for s in self.sections:
            if s.title == title:
                return s
        return None


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
        self._data_dir = Path(data_dir)
        self._briefings_dir = self._data_dir / "briefings"
        self._reports_dir = self._data_dir / "reports"
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
        report.sections.append(self._intel_section())

        self._save_report(report)

        if post_to_channel:
            self._post_to_channel(report, channel_id)

        return report

    def generate_html(
        self,
        hours: int = 24,
        post_to_channel: bool = True,
        channel_id: str = "daily-digest",
    ) -> Path | None:
        """Generate a briefing and write an HTML report file.

        Returns the path to the HTML file, or None on failure.
        """
        report = self.generate(
            hours=hours,
            post_to_channel=post_to_channel,
            channel_id=channel_id,
        )
        return self._write_html(report)

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

    def get_latest_html(self) -> Path | None:
        """Return the path to the most recent HTML report, or None."""
        if not self._reports_dir.exists():
            return None
        files = sorted(
            self._reports_dir.glob("executive_briefing_*.html"), reverse=True
        )
        return files[0] if files else None

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
        items_detail: list[dict[str, Any]] = []

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
            items_detail.append({
                "id": getattr(item, "item_id", getattr(item, "id", "")),
                "description": getattr(item, "description", ""),
                "status": item.status,
                "priority": item.priority,
                "agent_id": item.agent_id or "",
                "created_at": getattr(item, "created_at", ""),
                "completed_at": getattr(item, "completed_at", ""),
            })

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
            "items": items_detail,
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
            "agents": agents,
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

    def _intel_section(self) -> BriefingSection:
        """Tech intel articles from RSS feeds."""
        from cohort.intel_fetcher import IntelFetcher

        fetcher = IntelFetcher(self._data_dir)
        articles = fetcher.get_articles(limit=50, max_age_days=30)

        if not articles:
            return BriefingSection(
                title="Intel Feed",
                content="No intel articles available.",
                data={"total": 0, "articles": []},
            )

        top = [a for a in articles if a.get("relevance_score", 0) >= 5]
        top.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)

        lines = [f"Total articles: {len(articles)} ({len(top)} scored 5+)"]
        for a in top[:5]:
            score = a.get("relevance_score", 0)
            lines.append(f"  [{score}/10] {_trunc(a.get('title', ''), 70)}")
            lines.append(f"         {a.get('source', '')} - {a.get('url', '')}")

        data = {
            "total": len(articles),
            "top_count": len(top),
            "articles": articles,
        }

        return BriefingSection(
            title="Intel Feed", content="\n".join(lines), data=data
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

    # =================================================================
    # HTML Report Generation
    # =================================================================

    def _write_html(self, report: BriefingReport) -> Path | None:
        """Render a BriefingReport as a self-contained HTML file."""
        try:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            path = self._reports_dir / f"executive_briefing_{date_str}.html"
            html = _build_html(report)
            path.write_text(html, encoding="utf-8")
            logger.info("[OK] HTML briefing saved: %s", path)
            return path
        except OSError as exc:
            logger.warning("[!] Failed to write HTML briefing: %s", exc)
            return None


# =====================================================================
# HTML Builder (standalone functions, no class needed)
# =====================================================================

_CSS = """\
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;\
--muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;\
--yellow:#d29922;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,\
BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;padding:24px;line-height:1.5}
h1{font-size:24px;margin-bottom:8px}
h2{font-size:18px;margin:24px 0 12px;color:var(--accent);\
border-bottom:1px solid var(--border);padding-bottom:8px}
.header{margin-bottom:24px}
.header .meta{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));\
gap:12px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}
.card .label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.card .value{font-size:28px;font-weight:600;margin-top:4px}
.card .sub{font-size:12px;color:var(--muted);margin-top:2px}
.good{color:var(--green)}.warn{color:var(--yellow)}.bad{color:var(--red)}
.muted-val{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:var(--card);color:var(--muted);text-transform:uppercase;\
font-size:11px;letter-spacing:.5px;padding:8px 12px;text-align:left;\
border-bottom:2px solid var(--border);position:sticky;top:0;z-index:1}
td{padding:8px 12px;border-bottom:1px solid var(--border)}
tr:hover td{background:rgba(88,166,255,.04)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.tabs{position:relative}
.tab-bar{display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:16px}
.tab-btn{padding:10px 20px;cursor:pointer;color:var(--muted);font-size:14px;\
font-weight:600;background:none;border:none;border-bottom:2px solid transparent;\
margin-bottom:-2px;font-family:inherit}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-panel{display:none}
.tab-panel.active{display:block}
.table-wrap{overflow-x:auto;margin-bottom:16px}
.masthead{text-align:center;border-bottom:3px double var(--border);\
padding:16px 0;margin-bottom:24px}
.masthead h1{font-size:28px;letter-spacing:2px;text-transform:uppercase;\
color:var(--text);margin:0}
.masthead .dateline{color:var(--muted);font-size:13px;margin-top:4px}
.exec-summary{font-size:15px;line-height:1.7;padding:16px 20px;\
background:var(--card);border:1px solid var(--border);border-radius:8px;\
margin-bottom:24px;font-style:italic}
.agent-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));\
gap:16px;margin-bottom:24px}
.agent-card{background:var(--card);border:1px solid var(--border);\
border-radius:8px;padding:16px;transition:border-color 0.2s;\
display:flex;flex-direction:column}
.agent-card:hover{border-color:var(--accent)}
.agent-card .agent-name{font-weight:700;font-size:14px;margin-bottom:2px}
.agent-card .agent-role{color:var(--muted);font-size:11px;margin-bottom:8px}
.agent-card .agent-body{flex:1}
.agent-card .agent-footer{font-size:11px;color:var(--muted);margin-top:8px;\
padding-top:8px;border-top:1px solid var(--border);\
display:flex;justify-content:space-between;align-items:center}
.agent-card .agent-stats{font-size:11px;color:var(--muted)}
.assign-btn{padding:3px 10px;font-size:10px;font-weight:600;\
color:var(--accent);background:transparent;border:1px solid var(--accent);\
border-radius:10px;cursor:pointer;transition:all 0.2s;white-space:nowrap}
.assign-btn:hover{background:var(--accent);color:var(--bg)}
.idle-agents-compact{margin-bottom:24px}
.idle-agent-row{display:flex;align-items:center;gap:12px;\
padding:8px 12px;background:var(--card);border:1px solid var(--border);\
border-radius:6px;margin-bottom:4px;font-size:13px}
.idle-agent-row:hover{border-color:var(--accent)}
.idle-agent-row .agent-name{font-weight:600;min-width:160px}
.idle-agent-row .agent-role{color:var(--muted);font-size:11px;flex:1}
.idle-agent-row .assign-btn{flex-shrink:0}
.task-modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;\
background:rgba(0,0,0,.6);z-index:1000;justify-content:center;align-items:center}
.task-modal-overlay.active{display:flex}
.task-modal{background:var(--bg);border:1px solid var(--border);\
border-radius:12px;padding:24px;width:520px;max-width:90vw;max-height:80vh;\
overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,.4)}
.task-modal h3{margin:0 0 16px;font-size:16px;color:var(--text)}
.task-modal label{display:block;font-size:12px;color:var(--muted);\
margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}
.task-modal input,.task-modal textarea,.task-modal select{width:100%;\
padding:8px 12px;background:var(--card);border:1px solid var(--border);\
border-radius:6px;color:var(--text);font-family:inherit;font-size:13px;\
margin-bottom:12px;box-sizing:border-box}
.task-modal textarea{min-height:100px;resize:vertical}
.task-modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:8px}
.task-modal-actions button{padding:8px 18px;border-radius:8px;font-size:13px;\
font-weight:600;cursor:pointer;border:1px solid var(--border)}
.task-modal-actions .btn-cancel{background:transparent;color:var(--muted)}
.task-modal-actions .btn-cancel:hover{color:var(--text)}
.task-modal-actions .btn-submit{background:var(--accent);color:var(--bg);\
border-color:var(--accent)}
.task-modal-actions .btn-submit:hover{opacity:.9}
.idle-tag{display:inline-block;padding:1px 6px;border-radius:8px;\
font-size:10px;font-weight:600;background:rgba(210,153,34,.15);\
color:var(--yellow);margin-left:6px}
.busy-tag{display:inline-block;padding:1px 6px;border-radius:8px;\
font-size:10px;font-weight:600;background:rgba(63,185,80,.15);\
color:var(--green);margin-left:6px}
.section-divider{text-align:center;color:var(--muted);margin:28px 0 16px;\
font-size:12px;letter-spacing:3px;text-transform:uppercase}
.section-divider::before,.section-divider::after{content:'';display:inline-block;\
width:40px;height:1px;background:var(--border);vertical-align:middle;margin:0 12px}
.article-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));\
gap:12px;margin-bottom:24px}
.article-card{background:var(--card);border:1px solid var(--border);\
border-radius:8px;padding:14px}
.article-card .article-title{font-weight:600;font-size:14px;margin-bottom:4px;line-height:1.4}
.article-card .article-title a{color:var(--accent);text-decoration:none}
.article-card .article-title a:hover{text-decoration:underline;color:var(--text)}
.article-card .article-meta{font-size:11px;color:var(--muted);margin-bottom:6px}
.article-card .article-summary{font-size:12px;line-height:1.5;color:var(--text)}
.article-tag{display:inline-block;padding:2px 8px;border-radius:10px;\
font-size:10px;font-weight:500;background:rgba(188,140,255,.12);\
color:var(--purple);margin-right:4px}
.score-badge{display:inline-block;padding:2px 6px;border-radius:10px;\
font-size:10px;font-weight:600}
.score-high{background:rgba(63,185,80,.15);color:var(--green)}
.score-med{background:rgba(210,153,34,.15);color:var(--yellow)}
.score-low{background:rgba(139,148,158,.15);color:var(--muted)}
.intel-hero-grid{display:grid;grid-template-columns:repeat(auto-fit,\
minmax(380px,1fr));gap:16px;margin-bottom:20px}
.article-card-hero{border-left:3px solid var(--accent);padding:20px}
.article-card-hero .article-title{font-size:17px;margin-bottom:6px}
.article-card-hero .article-summary{font-size:13px;line-height:1.6}
.video-row{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px;margin-bottom:16px}
.video-card{flex:0 0 260px;background:var(--card);border:1px solid var(--border);\
border-radius:8px;padding:14px;position:relative}
.video-card::before{content:'[>]';position:absolute;top:10px;right:12px;\
font-size:11px;color:var(--accent);font-weight:700}
.article-thumb img{max-width:100%;border-radius:4px;margin:6px 0}
.tier-scan{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.scan-chip{background:var(--card);border:1px solid var(--border);\
border-radius:16px;padding:6px 14px;font-size:12px;white-space:nowrap;\
text-decoration:none;color:var(--text)}
.scan-chip:hover{border-color:var(--accent)}
.article-footer{display:flex;gap:6px;margin-top:8px;padding-top:8px;\
border-top:1px solid var(--border);align-items:center}
.article-link-btn{padding:3px 10px;font-size:10px;font-weight:600;\
color:var(--accent);background:transparent;border:1px solid var(--accent);\
border-radius:10px;text-decoration:none;white-space:nowrap;transition:all 0.2s}
.article-link-btn:hover{background:var(--accent);color:var(--bg)}
.article-chat-btn{padding:3px 10px;font-size:10px;font-weight:600;\
color:var(--green);background:transparent;border:1px solid var(--green);\
border-radius:10px;cursor:pointer;white-space:nowrap;transition:all 0.2s;\
font-family:inherit}
.article-chat-btn:hover{background:var(--green);color:var(--bg)}
"""

_JS = """\
function switchTab(group,idx){
  var tabs=document.querySelector('[data-tgroup="'+group+'"]');
  if(!tabs)return;
  var btns=tabs.querySelectorAll('.tab-btn');
  var panels=tabs.querySelectorAll('.tab-panel');
  for(var i=0;i<btns.length;i++){
    btns[i].classList.toggle('active',i===idx);
    panels[i].classList.toggle('active',i===idx);
  }
}
function openTaskCreator(agentId, agentName, suggestion){
  var overlay=document.getElementById('task-modal-overlay');
  document.getElementById('tc-agent-id').value=agentId;
  document.getElementById('tc-agent-name').textContent=agentName;
  document.getElementById('tc-description').value=suggestion||'';
  document.getElementById('tc-priority').value='medium';
  document.getElementById('tc-status').textContent='';
  overlay.classList.add('active');
}
function closeTaskCreator(){
  document.getElementById('task-modal-overlay').classList.remove('active');
}
function submitTask(){
  var agentId=document.getElementById('tc-agent-id').value;
  var desc=document.getElementById('tc-description').value.trim();
  var priority=document.getElementById('tc-priority').value;
  var status=document.getElementById('tc-status');
  if(!desc){status.textContent='Description is required.';status.style.color='var(--red)';return;}
  var btn=document.querySelector('.btn-submit');
  btn.disabled=true;btn.textContent='Submitting...';
  fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({agent_id:agentId,description:desc,priority:priority,
      trigger_type:'manual',trigger_source:'briefing'})
  }).then(function(r){return r.json();}).then(function(data){
    if(data.error){status.textContent='Error: '+data.error;status.style.color='var(--red)';}
    else{status.textContent='Task created successfully.';status.style.color='var(--green)';
      setTimeout(closeTaskCreator,1200);}
  }).catch(function(e){status.textContent='Failed: '+e.message;status.style.color='var(--red)';
  }).finally(function(){btn.disabled=false;btn.textContent='Submit Task';});
}
document.addEventListener('click',function(e){
  if(e.target.id==='task-modal-overlay')closeTaskCreator();
  var btn=e.target.closest('.assign-btn');
  if(btn&&btn.dataset.agentId){
    openTaskCreator(btn.dataset.agentId,btn.dataset.agentName,btn.dataset.suggestion);
  }
});
document.addEventListener('click',function(e2){
  var cb=e2.target.closest('.article-chat-btn');
  if(!cb)return;
  var title=cb.dataset.title||'';
  var url=cb.dataset.url||'';
  var summary=cb.dataset.summary||'';
  var agents=(cb.dataset.agents||'').split(',').filter(Boolean);
  var slug='article-'+title.toLowerCase().replace(/[^a-z0-9]+/g,'-').slice(0,40);
  var mentions=agents.map(function(a){return '@'+a;}).join(' ');
  var seed='[Article: '+title+']('+url+')\n\n'+summary+'\n\n'
    +'Thoughts on this? '+mentions;
  cb.disabled=true;cb.textContent='Opening...';
  fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({channel:slug,sender:'user',message:seed,
      response_mode:'smarter'})
  }).then(function(r){return r.json();}).then(function(){
    window.open('/?channel='+encodeURIComponent(slug),'_blank');
  }).catch(function(e3){alert('Failed to start chat: '+e3.message);
  }).finally(function(){cb.disabled=false;cb.textContent='Start Chat';});
});
"""


def _build_html(report: BriefingReport) -> str:
    """Build a complete self-contained HTML briefing."""
    now = datetime.now()
    date_display = now.strftime("%A, %B %d, %Y")
    date_short = now.strftime("%a %b %d, %Y")
    gen_time = report.generated_at[:19] if report.generated_at else "unknown"

    # Extract sections by title
    wq = report.get_section("Work Queue")
    ca = report.get_section("Channel Activity")
    ts = report.get_section("Team Status")
    ds = report.get_section("Discussion Sessions")
    intel = report.get_section("Intel Feed")

    wq_data = wq.data if wq else {}
    ca_data = ca.data if ca else {}
    ts_data = ts.data if ts else {}
    intel_data = intel.data if intel else {}

    articles = intel_data.get("articles", [])
    intel_count = len(articles)

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Executive Briefing - {_esc(date_short)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="header">
<h1>Executive Briefing - {_esc(date_short)}</h1>
<div class="meta">Period: {_esc(report.period_start[:10])} to \
{_esc(report.period_end[:10])} &bull; Generated {_esc(gen_time)}</div>
</div>
<div class="tabs" data-tgroup="main">
<div class="tab-bar">
<button class="tab-btn active" onclick="switchTab('main',0)">Daily Briefing</button>
<button class="tab-btn" onclick="switchTab('main',1)">Work Queue</button>
<button class="tab-btn" onclick="switchTab('main',2)">Raw Stats</button>
<button class="tab-btn" onclick="switchTab('main',3)">Intel Feed ({intel_count})</button>
</div>""")

    # ---- Tab 1: Daily Briefing ----
    parts.append('<div class="tab-panel active">')
    parts.append(f"""<div class="masthead">
<h1>The Executive Briefing</h1>
<div class="dateline">{_esc(date_display)}</div>
</div>""")

    # Executive summary (LLM-generated with deterministic fallback)
    total_agents = ts_data.get("total", 0)
    busy_agents = ts_data.get("busy", 0)
    total_msgs = ca_data.get("total_messages", 0)
    active_ch = ca_data.get("active_channels", 0)
    wq_total = wq_data.get("total", 0)
    wq_completed = wq_data.get("completed_in_period", 0)

    exec_summary = _generate_exec_summary(
        total_agents=total_agents,
        busy_agents=busy_agents,
        total_msgs=total_msgs,
        active_channels=active_ch,
        wq_total=wq_total,
        wq_completed=wq_completed,
        intel_count=intel_count,
    )
    parts.append(
        f'<div class="exec-summary">{_esc(exec_summary)}</div>'
    )

    # Team cards -- split busy/active agents (full cards) from idle (compact rows)
    agents_list = ts_data.get("agents", [])
    if agents_list:
        busy_list = [a for a in agents_list if a.get("status") == "busy" or a.get("tasks_completed", 0) > 0]
        idle_list = [a for a in agents_list if a not in busy_list]

        parts.append('<div class="section-divider">Team Status</div>')

        # Active/busy agents get full cards
        if busy_list:
            parts.append('<div class="agent-grid">')
            for agent in busy_list:
                name = _esc(agent.get("name", agent.get("agent_id", "?")))
                agent_id = _esc(agent.get("agent_id", ""))
                agent_role = _esc(agent.get("group", ""))
                status = agent.get("status", "idle")
                completed = agent.get("tasks_completed", 0)
                tag = (
                    '<span class="busy-tag">BUSY</span>'
                    if status == "busy"
                    else '<span class="idle-tag">IDLE</span>'
                )
                narrative = _generate_agent_narrative(agent)
                narrative_esc = _esc(narrative)
                stats_parts = []
                if completed:
                    stats_parts.append(f"{completed} tasks completed")
                if agent_role:
                    stats_parts.append(agent_role)
                stats_line = " &bull; ".join(stats_parts) if stats_parts else ""
                parts.append(f"""<div class="agent-card">
<div class="agent-name">{name}{tag}</div>
<div class="agent-body">
<div class="agent-role">{agent_role}</div>
<div class="agent-voice" style="font-style:italic;font-size:13px;\
line-height:1.6;color:var(--text)">&ldquo;{narrative_esc}&rdquo;</div>
</div>
<div class="agent-footer"><span class="agent-stats">{stats_line}</span>\
<button class="assign-btn" data-agent-id="{agent_id}" \
data-agent-name="{name}" \
data-suggestion="{_esc(narrative)}">Assign Task</button></div>
</div>""")
            parts.append("</div>")

        # Idle agents get compact single-line rows
        if idle_list:
            label = f"Available ({len(idle_list)})"
            parts.append(f'<div style="color:var(--muted);font-size:11px;'
                         f'text-transform:uppercase;letter-spacing:1px;'
                         f'margin:12px 0 8px">{label}</div>')
            parts.append('<div class="idle-agents-compact">')
            for agent in idle_list:
                name = _esc(agent.get("name", agent.get("agent_id", "?")))
                agent_id = _esc(agent.get("agent_id", ""))
                agent_role = _esc(agent.get("group", ""))
                narrative = _generate_agent_narrative(agent)
                parts.append(
                    f'<div class="idle-agent-row">'
                    f'<span class="agent-name">{name}'
                    f'<span class="idle-tag">IDLE</span></span>'
                    f'<span class="agent-role">{agent_role}</span>'
                    f'<button class="assign-btn" data-agent-id="{agent_id}" '
                    f'data-agent-name="{name}" '
                    f'data-suggestion="{_esc(narrative)}">Assign Task</button>'
                    f'</div>'
                )
            parts.append("</div>")

    # Channel activity highlights
    ch_stats = ca_data.get("channel_stats", [])
    if ch_stats:
        parts.append('<div class="section-divider">Channel Activity</div>')
        parts.append('<div class="cards">')
        parts.append(f"""<div class="card">
<div class="label">Messages</div>
<div class="value">{total_msgs}</div>
<div class="sub">in {active_ch} channels</div>
</div>""")
        top_senders = ca_data.get("top_senders", {})
        if top_senders:
            top_name = next(iter(top_senders))
            top_count = top_senders[top_name]
            parts.append(f"""<div class="card">
<div class="label">Top Sender</div>
<div class="value" style="font-size:16px">{_esc(top_name)}</div>
<div class="sub">{top_count} messages</div>
</div>""")
        parts.append("</div>")

    # Intel preview (top 5 articles on the front page)
    top_articles = [a for a in articles if a.get("relevance_score", 0) >= 5]
    top_articles.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)

    # Generate "why it matters" summaries for ALL articles via LLM
    # High-relevance get full WHAT/WHY/ACTION; low get opportunity notes
    why_map: dict[str, list[str]] = {}
    if articles:
        why_map = _generate_intel_summaries(articles)

    if top_articles:
        parts.append('<div class="section-divider">Intelligence Desk</div>')

        # Hero articles (top 2)
        heroes = top_articles[:2]
        if heroes:
            parts.append('<div class="intel-hero-grid">')
            for a in heroes:
                parts.append(_article_card_html(
                    a, hero=True, why_map=why_map, agents_list=agents_list,
                ))
            parts.append("</div>")

        # Mid-tier (next 3)
        mid = top_articles[2:5]
        if mid:
            parts.append('<div class="article-grid">')
            for a in mid:
                parts.append(_article_card_html(
                    a, why_map=why_map, agents_list=agents_list,
                ))
            parts.append("</div>")

        if intel_count > 5:
            parts.append(
                f'<div style="text-align:right;margin-top:8px">'
                f'<a href="#" onclick="switchTab(\'main\',3);return false" '
                f'style="color:var(--accent);font-size:13px;text-decoration:none">'
                f"See full Intel Feed ({intel_count} articles) &rarr;</a></div>"
            )

    parts.append("</div>")  # end tab 1

    # ---- Tab 2: Work Queue ----
    parts.append('<div class="tab-panel">')
    status_counts = wq_data.get("status_counts", {})
    parts.append('<div class="cards">')
    for status_name, label in [
        ("queued", "Queued"),
        ("active", "Active"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]:
        count = status_counts.get(status_name, 0)
        css = ""
        if status_name == "failed" and count:
            css = " bad"
        elif status_name == "completed" and count:
            css = " good"
        parts.append(f"""<div class="card">
<div class="label">{label}</div>
<div class="value{css}">{count}</div>
</div>""")
    avg_tt = wq_data.get("avg_turnaround_seconds")
    if avg_tt is not None:
        parts.append(f"""<div class="card">
<div class="label">Avg Turnaround</div>
<div class="value" style="font-size:20px">{_esc(_fmt_duration(avg_tt))}</div>
</div>""")
    parts.append("</div>")

    # Work queue items table
    items = wq_data.get("items", [])
    if items:
        parts.append("<h2>Work Items</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append(
            "<tr><th>ID</th><th>Description</th><th>Status</th>"
            "<th>Priority</th><th>Agent</th></tr>"
        )
        for item in items:
            status_badge = _status_badge(item.get("status", ""))
            prio_badge = _priority_badge(item.get("priority", ""))
            desc = _esc(_trunc(item.get("description", ""), 80))
            parts.append(
                f"<tr><td>{_esc(str(item.get('id', ''))[:8])}</td>"
                f"<td>{desc}</td>"
                f"<td>{status_badge}</td>"
                f"<td>{prio_badge}</td>"
                f"<td>{_esc(item.get('agent_id', ''))}</td></tr>"
            )
        parts.append("</table></div>")

    # Agent workload
    agent_workload = wq_data.get("agent_workload", {})
    if agent_workload:
        parts.append("<h2>Agent Workload</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append("<tr><th>Agent</th><th>Items</th></tr>")
        for agent_id, count in sorted(
            agent_workload.items(), key=lambda x: x[1], reverse=True
        ):
            parts.append(
                f"<tr><td>{_esc(agent_id)}</td><td>{count}</td></tr>"
            )
        parts.append("</table></div>")

    if not items and not agent_workload:
        parts.append(
            '<div style="color:var(--muted);padding:24px;text-align:center">'
            "No work queue data available.</div>"
        )

    parts.append("</div>")  # end tab 2

    # ---- Tab 3: Raw Stats ----
    parts.append('<div class="tab-panel">')

    # Summary cards
    parts.append('<div class="cards">')
    parts.append(f"""<div class="card">
<div class="label">Agents</div>
<div class="value">{total_agents}</div>
<div class="sub">{busy_agents} busy, {ts_data.get('idle', 0)} idle</div>
</div>""")
    parts.append(f"""<div class="card">
<div class="label">Messages</div>
<div class="value">{total_msgs}</div>
<div class="sub">{active_ch} channels</div>
</div>""")
    parts.append(f"""<div class="card">
<div class="label">Work Queue</div>
<div class="value">{wq_total}</div>
<div class="sub">{wq_completed} completed</div>
</div>""")
    parts.append(f"""<div class="card">
<div class="label">Intel Articles</div>
<div class="value">{intel_count}</div>
<div class="sub">{intel_data.get('top_count', 0)} scored 5+</div>
</div>""")
    parts.append("</div>")

    # Channel stats table
    if ch_stats:
        parts.append("<h2>Channel Activity</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append(
            "<tr><th>Channel</th><th>Messages</th>"
            "<th>Participants</th><th>Top Sender</th></tr>"
        )
        for cs in ch_stats:
            parts.append(
                f"<tr><td>#{_esc(cs['channel'])}</td>"
                f"<td>{cs['message_count']}</td>"
                f"<td>{cs['unique_senders']}</td>"
                f"<td>{_esc(cs.get('top_sender') or '-')}</td></tr>"
            )
        parts.append("</table></div>")

    # Top senders table
    top_senders_data = ca_data.get("top_senders", {})
    if top_senders_data:
        parts.append("<h2>Top Senders</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append("<tr><th>Sender</th><th>Messages</th></tr>")
        for sender, count in sorted(
            top_senders_data.items(), key=lambda x: x[1], reverse=True
        ):
            parts.append(
                f"<tr><td>@{_esc(sender)}</td><td>{count}</td></tr>"
            )
        parts.append("</table></div>")

    # Team details
    if agents_list:
        parts.append("<h2>Team Details</h2>")
        parts.append('<div class="agent-grid">')
        for agent in agents_list:
            name = _esc(agent.get("name", agent.get("agent_id", "?")))
            agent_id = _esc(agent.get("agent_id", ""))
            status = agent.get("status", "idle")
            group = _esc(agent.get("group", ""))
            completed = agent.get("tasks_completed", 0)
            skills = agent.get("skills", [])
            tag = (
                '<span class="busy-tag">BUSY</span>'
                if status == "busy"
                else '<span class="idle-tag">IDLE</span>'
            )
            skills_html = ""
            if skills:
                skills_html = (
                    '<div style="margin-top:6px">'
                    + " ".join(
                        f'<span class="article-tag">{_esc(s)}</span>'
                        for s in skills[:5]
                    )
                    + "</div>"
                )
            parts.append(f"""<div class="agent-card">
<div class="agent-name">{name}{tag}</div>
<div class="agent-role">{agent_id}</div>
{skills_html}
<div class="agent-stats">{completed} tasks &bull; {group}</div>
</div>""")
        parts.append("</div>")

    parts.append("</div>")  # end tab 3

    # ---- Tab 4: Intel Feed ----
    parts.append('<div class="tab-panel">')
    if not articles:
        parts.append(
            '<div style="color:var(--muted);padding:24px;text-align:center">'
            "No intel articles available. Configure RSS feeds in the setup wizard "
            "and run a fetch to populate this tab.</div>"
        )
    else:
        # Group by source
        by_source: dict[str, list[dict[str, Any]]] = {}
        videos: list[dict[str, Any]] = []
        for a in articles:
            if _extract_yt_id(a.get("url", "")):
                videos.append(a)
            source = a.get("source", "Unknown")
            by_source.setdefault(source, []).append(a)

        # Stats row
        parts.append('<div class="cards">')
        parts.append(f"""<div class="card">
<div class="label">Total Articles</div>
<div class="value">{intel_count}</div>
</div>""")
        parts.append(f"""<div class="card">
<div class="label">Sources</div>
<div class="value">{len(by_source)}</div>
</div>""")
        scored_high = sum(
            1 for a in articles if a.get("relevance_score", 0) >= 7
        )
        parts.append(f"""<div class="card">
<div class="label">High Relevance</div>
<div class="value good">{scored_high}</div>
<div class="sub">score 7+</div>
</div>""")
        if videos:
            parts.append(f"""<div class="card">
<div class="label">Videos</div>
<div class="value">{len(videos)}</div>
</div>""")
        parts.append("</div>")

        # Videos section
        if videos:
            parts.append(
                '<div class="section-divider" style="font-size:11px;'
                'margin:16px 0 10px">Videos</div>'
            )
            parts.append('<div class="video-row">')
            for v in videos[:6]:
                yt_id = _extract_yt_id(v.get("url", ""))
                parts.append(f"""<div class="video-card">
<div class="article-title"><a href="{_esc(v.get('url', ''))}" target="_blank" \
rel="noopener">{_esc(_trunc(v.get('title', ''), 60))}</a> \
{_score_badge(v.get('relevance_score', 0))}</div>
<div class="article-meta">{_esc(v.get('source', ''))}</div>
{f'<div class="article-thumb"><img src="https://img.youtube.com/vi/{yt_id}/mqdefault.jpg" alt="thumbnail" onerror="this.style.display=\'none\'"></div>' if yt_id else ''}
</div>""")
            parts.append("</div>")

        # Articles by source
        for source_name in sorted(by_source.keys()):
            source_articles = by_source[source_name]
            source_articles.sort(
                key=lambda a: a.get("relevance_score", 0), reverse=True
            )
            parts.append(
                f'<h2>{_esc(source_name)} ({len(source_articles)})</h2>'
            )
            parts.append('<div class="article-grid">')
            for a in source_articles:
                parts.append(_article_card_html(
                    a, why_map=why_map, agents_list=agents_list,
                ))
            parts.append("</div>")

    parts.append("</div>")  # end tab 4

    # Close tabs + page
    # Task creator modal
    parts.append("""<div class="task-modal-overlay" id="task-modal-overlay">
<div class="task-modal">
<h3>Assign Task to <span id="tc-agent-name"></span></h3>
<input type="hidden" id="tc-agent-id">
<label>Description</label>
<textarea id="tc-description" placeholder="Describe what the agent should do..."></textarea>
<label>Priority</label>
<select id="tc-priority">
<option value="low">Low</option>
<option value="medium" selected>Medium</option>
<option value="high">High</option>
</select>
<div id="tc-status" style="font-size:12px;min-height:18px;margin-bottom:4px"></div>
<div class="task-modal-actions">
<button class="btn-cancel" onclick="closeTaskCreator()">Cancel</button>
<button class="btn-submit" onclick="submitTask()">Submit Task</button>
</div>
</div>
</div>""")
    parts.append(f"</div>\n<script>{_JS}</script>\n</body>\n</html>")

    return "\n".join(parts)


def _article_card_html(
    article: dict[str, Any],
    hero: bool = False,
    why_map: dict[str, list[str]] | None = None,
    agents_list: list[dict[str, Any]] | None = None,
) -> str:
    """Render a single article card with 'why it matters' bullets and actions."""
    title = _esc(article.get("title", ""))
    url = _esc(article.get("url", ""))
    source = _esc(article.get("source", ""))
    summary = _esc(_trunc(article.get("summary", ""), 200))
    score = article.get("relevance_score", 0)
    tags = article.get("tags", [])
    aid = article.get("id", "")

    card_class = "article-card article-card-hero" if hero else "article-card"
    tags_html = ""
    if tags:
        tags_html = (
            '<div style="margin-bottom:6px">'
            + " ".join(
                f'<span class="article-tag">{_esc(t)}</span>'
                for t in tags[:3]
            )
            + "</div>"
        )

    # "Why it matters" bullets from LLM
    why_html = ""
    if why_map and aid in why_map:
        bullets = why_map[aid]
        if bullets:
            why_html = (
                '<ul class="article-why-matters" style="margin:6px 0 4px 0;'
                "padding-left:16px;font-size:12px;line-height:1.6;"
                'color:#8cb4ff;font-style:italic;list-style:none">'
            )
            for bullet in bullets[:3]:
                why_html += (
                    f'<li style="padding:2px 0">'
                    f'<span style="color:var(--accent);font-style:normal;'
                    f'font-weight:700;margin-right:6px">&gt;</span>'
                    f"{_esc(bullet)}</li>"
                )
            why_html += "</ul>"

    # Stakeholder agents for "Start Chat"
    stakeholders = _infer_stakeholder_agents(article, agents_list or [])
    raw_title = article.get("title", "")
    raw_url = article.get("url", "")
    raw_summary = article.get("summary", "")[:200]

    # Use data attributes to avoid quoting hell in inline handlers
    footer_html = (
        '<div class="article-footer">'
        f'<a href="{url}" target="_blank" rel="noopener" '
        f'class="article-link-btn">Read Article</a>'
        f'<button class="article-chat-btn" '
        f'data-title="{_esc(raw_title)}" '
        f'data-url="{_esc(raw_url)}" '
        f'data-summary="{_esc(raw_summary)}" '
        f'data-agents="{_esc(",".join(stakeholders))}"'
        f">Start Chat</button>"
        "</div>"
    )

    return f"""<div class="{card_class}">
<div class="article-title"><a href="{url}" target="_blank" \
rel="noopener">{title}</a> {_score_badge(score)}</div>
<div class="article-meta">{source}</div>
{tags_html}
<div class="article-summary">{summary}</div>
{why_html}
{footer_html}
</div>"""


def _score_badge(score: int) -> str:
    """Render a relevance score badge."""
    if score >= 7:
        cls = "score-high"
    elif score >= 4:
        cls = "score-med"
    else:
        cls = "score-low"
    return f'<span class="score-badge {cls}">{score}/10</span>'


def _status_badge(status: str) -> str:
    """Render a work queue status badge."""
    colors = {
        "completed": "badge-green",
        "active": "badge-blue",
        "queued": "badge-yellow",
        "failed": "badge-red",
        "cancelled": "badge-purple",
    }
    cls = colors.get(status, "badge-yellow")
    return f'<span class="badge {cls}">{_esc(status)}</span>'


def _priority_badge(priority: str) -> str:
    """Render a priority badge."""
    colors = {
        "critical": "badge-red",
        "high": "badge-yellow",
        "medium": "badge-blue",
        "low": "badge-purple",
    }
    cls = colors.get(priority, "badge-blue")
    return f'<span class="badge {cls}">{_esc(priority)}</span>'
