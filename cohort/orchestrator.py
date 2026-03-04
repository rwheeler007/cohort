"""Session management and speaker selection for cohort.

Provides :class:`Orchestrator` for managing multi-agent discussion
sessions with turn-based control, relevance scoring, and loop
prevention.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from cohort.chat import ChatManager, Message
from cohort.meeting import (
    STAKEHOLDER_THRESHOLDS,
    TOPIC_SHIFT_THRESHOLD,
    StakeholderStatus,
    calculate_composite_relevance,
    calculate_contribution_score,
    calculate_keyword_overlap,
    extract_keywords,
    identify_stakeholders_for_topic,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Enums
# =====================================================================

class SessionState(Enum):
    INITIALIZING = "initializing"
    ACTIVE = "active"
    PAUSED = "paused"
    CONCLUDING = "concluding"
    COMPLETED = "completed"


class TurnMode(Enum):
    STRICT = "strict"
    GUIDED = "guided"
    FREE = "free"


# =====================================================================
# Dataclasses
# =====================================================================

@dataclass
class TurnRecord:
    """Record of a single turn."""

    turn_number: int
    speaker: str
    message_id: str
    timestamp: str
    relevance_score: float
    was_recommended: bool
    topic_keywords: list[str] = field(default_factory=list)


@dataclass
class Session:
    """Active discussion session state."""

    session_id: str
    channel_id: str
    topic: str
    state: str
    turn_mode: str
    created_at: str
    created_by: str

    initial_agents: list[str] = field(default_factory=list)
    active_participants: dict[str, str] = field(default_factory=dict)

    current_turn: int = 0
    max_turns: int = 20
    turn_history: list[dict[str, Any]] = field(default_factory=list)

    current_topic_keywords: list[str] = field(default_factory=list)
    topic_history: list[dict[str, Any]] = field(default_factory=list)

    speaker_queue: list[str] = field(default_factory=list)
    last_speakers: list[str] = field(default_factory=list)

    total_messages: int = 0
    participants_contributed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(**data)


# =====================================================================
# Orchestrator
# =====================================================================

class Orchestrator:
    """Manages multi-agent discussion sessions.

    Integrates with the meeting module for relevance scoring and
    stakeholder management while providing higher-level session
    control and state management.

    Parameters
    ----------
    chat:
        :class:`~cohort.chat.ChatManager` for message operations.
    storage:
        Optional :class:`~cohort.registry.StorageBackend` for session
        persistence.  If *None*, sessions live only in memory.
    agents:
        Mapping of ``agent_id -> config dict`` used for scoring.
        Each value should have at minimum ``triggers`` and
        ``capabilities`` keys.
    on_event:
        Optional callback ``(event_name, data_dict) -> None`` for
        real-time notifications (e.g. WebSocket bridge).
    """

    def __init__(
        self,
        chat: ChatManager,
        *,
        storage: Any | None = None,
        agents: dict[str, dict[str, Any]] | None = None,
        on_event: Any | None = None,
    ) -> None:
        self.chat = chat
        self._storage = storage
        self._agents: dict[str, dict[str, Any]] = agents or {}
        self._on_event = on_event
        self.sessions: dict[str, Session] = {}

    # -- helpers --------------------------------------------------------

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self._on_event:
            self._on_event(event, data)

    def _agent_config(self, agent_id: str) -> dict[str, Any]:
        """Look up agent config from the supplied registry."""
        return self._agents.get(agent_id, {"triggers": [], "capabilities": []})

    # =========================================================================
    # SETUP HELPERS
    # =========================================================================

    def suggest_roundtable_config(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Parse natural language into roundtable configuration.

        Uses keyword extraction and stakeholder identification to suggest
        agents, topic, channel name, and max turns from a free-text
        description.

        Parameters
        ----------
        text:
            User's natural language description.
        context:
            Optional prior config to refine (for iterative updates).
        """
        ctx = context or {}
        keywords = extract_keywords(text)

        # Detect explicit agent mentions (e.g. "with security and python")
        explicit_agents: list[str] = []
        text_lower = text.lower()
        from cohort.agent_router import AGENT_ALIASES
        for alias, agent_id in AGENT_ALIASES.items():
            if alias in text_lower and agent_id not in explicit_agents:
                explicit_agents.append(agent_id)
        for agent_id in self._agents:
            name_parts = agent_id.lower().replace("_", " ").split()
            if any(part in text_lower for part in name_parts if len(part) > 2):
                if agent_id not in explicit_agents:
                    explicit_agents.append(agent_id)

        # Auto-identify from topic keywords
        auto_agents = identify_stakeholders_for_topic(
            keywords, self._agents, relevance_threshold=0.3,
        )[:8]

        # Merge: explicit first, then auto-suggestions (deduped)
        suggested = list(explicit_agents)
        for a in auto_agents:
            if a not in suggested:
                suggested.append(a)
        suggested = suggested[:8]

        # Generate channel name from topic
        topic = ctx.get("topic", text.strip())
        # Clean filler phrases
        import re
        filler = re.compile(
            r"^(i want to|i need to|let's|lets|we need to|we should|"
            r"can we|could we|please|discuss|talk about|work on|"
            r"figure out|review|set up a roundtable)\s+",
            re.IGNORECASE,
        )
        clean_topic = filler.sub("", topic).strip()
        if clean_topic:
            topic = clean_topic

        # Capitalize topic
        topic = topic[0].upper() + topic[1:] if topic else "Discussion"

        # Slugify for channel name
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40]
        channel_name = f"rt-{slug}"

        # Detect max_turns override ("30 turns", "max 50")
        max_turns = ctx.get("max_turns", 20)
        turns_match = re.search(r"(\d+)\s*turns?", text_lower)
        if turns_match:
            max_turns = min(int(turns_match.group(1)), 100)

        # Apply refinements from context
        if ctx.get("suggested_agents"):
            # Check for "add X" or "remove X" patterns
            add_match = re.search(r"(?:add|include|bring in)\s+(.+)", text_lower)
            remove_match = re.search(r"(?:remove|drop|exclude)\s+(.+)", text_lower)
            if add_match and not remove_match:
                # Keep existing + add new
                suggested = list(ctx["suggested_agents"])
                for a in explicit_agents:
                    if a not in suggested:
                        suggested.append(a)
            elif remove_match:
                suggested = [
                    a for a in ctx["suggested_agents"]
                    if a.lower().replace("_", " ") not in text_lower
                ]

        return {
            "topic": topic,
            "channel_name": channel_name,
            "suggested_agents": suggested,
            "max_turns": max_turns,
            "keywords": keywords,
        }

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    def start_session(
        self,
        channel_id: str,
        topic: str,
        initial_agents: list[str] | None = None,
        turn_mode: TurnMode = TurnMode.GUIDED,
        max_turns: int = 20,
        created_by: str = "system",
    ) -> Session:
        """Start a new discussion session."""
        session_id = f"s_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        topic_keywords = extract_keywords(topic)

        if not initial_agents:
            initial_agents = identify_stakeholders_for_topic(
                topic_keywords, self._agents, relevance_threshold=0.4
            )
            initial_agents = initial_agents[:6]

        active_participants = {
            agent_id: StakeholderStatus.ACTIVE.value
            for agent_id in initial_agents
        }

        session = Session(
            session_id=session_id,
            channel_id=channel_id,
            topic=topic,
            state=SessionState.ACTIVE.value,
            turn_mode=turn_mode.value,
            created_at=datetime.now().isoformat(),
            created_by=created_by,
            initial_agents=initial_agents,
            active_participants=active_participants,
            max_turns=max_turns,
            current_topic_keywords=topic_keywords,
            topic_history=[{
                "keywords": topic_keywords,
                "introduced_at": datetime.now().isoformat(),
                "introduced_by": created_by,
            }],
        )

        self.sessions[session_id] = session

        # Update channel to session mode
        channel = self.chat.get_channel(channel_id)
        if channel:
            channel.mode = "meeting"
            channel.meeting_context = {
                "session_id": session_id,
                "stakeholder_status": active_participants,
                "current_topic": {
                    "keywords": topic_keywords,
                    "primary_stakeholders": initial_agents,
                    "initiated_at": datetime.now().isoformat(),
                },
            }

        # Post system message
        participants_list = ", ".join(f"@{a}" for a in initial_agents)
        self.chat.post_message(
            channel_id=channel_id,
            sender="system",
            content=(
                f"[SESSION] Discussion started: **{topic}**\n\n"
                f"**Participants**: {participants_list}\n"
                f"**Mode**: {turn_mode.value}\n"
                f"**Session ID**: `{session_id}`"
            ),
            message_type="system",
        )

        self._emit("session_started", {
            "session_id": session_id,
            "channel_id": channel_id,
            "topic": topic,
            "participants": initial_agents,
            "turn_mode": turn_mode.value,
        })

        return session

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def get_session_for_channel(self, channel_id: str) -> Session | None:
        for session in self.sessions.values():
            if session.channel_id == channel_id and session.state == SessionState.ACTIVE.value:
                return session
        return None

    def pause_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or session.state != SessionState.ACTIVE.value:
            return False
        session.state = SessionState.PAUSED.value
        self.chat.post_message(
            channel_id=session.channel_id,
            sender="system",
            content="[SESSION] Discussion paused.",
            message_type="system",
        )
        self._emit("session_paused", {"session_id": session_id})
        return True

    def resume_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or session.state != SessionState.PAUSED.value:
            return False
        session.state = SessionState.ACTIVE.value
        self.chat.post_message(
            channel_id=session.channel_id,
            sender="system",
            content="[SESSION] Discussion resumed.",
            message_type="system",
        )
        self._emit("session_resumed", {"session_id": session_id})
        return True

    def end_session(self, session_id: str) -> dict[str, Any] | None:
        """End a session and generate summary."""
        session = self.sessions.get(session_id)
        if not session:
            return None

        session.state = SessionState.COMPLETED.value
        summary = self._generate_summary(session)

        # Reset channel mode
        channel = self.chat.get_channel(session.channel_id)
        if channel:
            channel.mode = "chat"
            channel.meeting_context = None

        # Post summary
        summary_text = self._format_summary(summary)
        self.chat.post_message(
            channel_id=session.channel_id,
            sender="system",
            content=f"[SESSION] Discussion concluded.\n\n{summary_text}",
            message_type="system",
        )

        self._emit("session_ended", {
            "session_id": session_id,
            "summary": summary,
        })

        del self.sessions[session_id]
        return summary

    # =========================================================================
    # TURN MANAGEMENT
    # =========================================================================

    def get_next_speaker(self, session_id: str) -> dict[str, Any] | None:
        """Get the recommended next speaker based on relevance scoring."""
        session = self.sessions.get(session_id)
        if not session or session.state != SessionState.ACTIVE.value:
            return None

        if session.current_turn >= session.max_turns:
            return None

        recent_messages = self.chat.get_channel_messages(
            session.channel_id, limit=15
        )

        scores: list[dict[str, Any]] = []
        for agent_id, status in session.active_participants.items():
            if status == StakeholderStatus.DORMANT.value:
                continue

            agent_config = self._agent_config(agent_id)

            relevance = calculate_composite_relevance(
                agent_id=agent_id,
                meeting_context={
                    "stakeholder_status": session.active_participants,
                    "current_topic": {"keywords": session.current_topic_keywords},
                },
                agent_config=agent_config,
                recent_messages=recent_messages,
            )

            composite_score = relevance.get("composite_total", 0)

            # Recency penalty
            recency_penalty = 0.0
            if agent_id in session.last_speakers:
                idx = session.last_speakers.index(agent_id)
                recency_penalty = 0.2 * (1 - idx / len(session.last_speakers))

            # First-contribution bonus
            contribution_bonus = 0.15 if agent_id not in session.participants_contributed else 0.0

            final_score = composite_score - recency_penalty + contribution_bonus

            scores.append({
                "agent_id": agent_id,
                "score": final_score,
                "composite_score": composite_score,
                "recency_penalty": recency_penalty,
                "contribution_bonus": contribution_bonus,
                "status": status,
                "phase": relevance.get("detected_phase", "unknown"),
                "breakdown": {
                    k: v for k, v in relevance.items()
                    if k not in ("composite_total", "detected_phase")
                },
            })

        if not scores:
            return None

        scores.sort(key=lambda x: x["score"], reverse=True)
        top = scores[0]

        threshold = STAKEHOLDER_THRESHOLDS.get(top["status"], 0.5)
        if top["score"] < threshold:
            return None

        return {
            "recommended_speaker": top["agent_id"],
            "relevance_score": top["score"],
            "phase": top["phase"],
            "reason": self._build_recommendation_reason(top),
            "alternatives": [s["agent_id"] for s in scores[1:4]],
            "all_scores": scores,
        }

    def _build_recommendation_reason(self, score_data: dict[str, Any]) -> str:
        agent_id = score_data["agent_id"]
        bd = score_data.get("breakdown", {})
        reasons: list[str] = []
        if bd.get("domain_expertise", 0) > 0.5:
            reasons.append("domain expertise matches current topic")
        if bd.get("complementary_value", 0) > 0.3:
            reasons.append("complements other active participants")
        if bd.get("phase_alignment", 0) > 0.6:
            reasons.append(f"relevant for {score_data.get('phase', 'current')} phase")
        if bd.get("data_ownership", 0) > 0.4:
            reasons.append("has relevant operational data")
        if score_data.get("contribution_bonus", 0) > 0:
            reasons.append("hasn't contributed yet")
        if not reasons:
            reasons.append("highest overall relevance score")
        return f"{agent_id}: " + ", ".join(reasons)

    def record_turn(
        self,
        session_id: str,
        speaker: str,
        message_id: str,
        was_recommended: bool = True,
    ) -> bool:
        """Record that an agent has taken a turn."""
        session = self.sessions.get(session_id)
        if not session:
            return False

        # Find the message to extract keywords
        message: Message | None = None
        for msg in self.chat.get_channel_messages(session.channel_id, limit=50):
            if msg.id == message_id:
                message = msg
                break

        message_keywords = extract_keywords(message.content) if message else []

        turn = TurnRecord(
            turn_number=session.current_turn + 1,
            speaker=speaker,
            message_id=message_id,
            timestamp=datetime.now().isoformat(),
            relevance_score=0.0,
            was_recommended=was_recommended,
            topic_keywords=message_keywords,
        )

        session.current_turn += 1
        session.turn_history.append(asdict(turn))
        session.total_messages += 1

        if speaker not in session.participants_contributed:
            session.participants_contributed.append(speaker)

        # Update last speakers (keep last 5)
        if speaker in session.last_speakers:
            session.last_speakers.remove(speaker)
        session.last_speakers.insert(0, speaker)
        session.last_speakers = session.last_speakers[:5]

        # Check for topic shift
        self._check_topic_shift(session, message_keywords)

        self._emit("turn_recorded", {
            "session_id": session_id,
            "turn": asdict(turn),
            "current_turn": session.current_turn,
            "max_turns": session.max_turns,
        })

        return True

    def extend_turns(self, session_id: str, additional_turns: int = 10) -> bool:
        session = self.sessions.get(session_id)
        if not session:
            return False
        session.max_turns += additional_turns
        return True

    def _check_topic_shift(
        self, session: Session, new_keywords: list[str]
    ) -> None:
        if not new_keywords or not session.current_topic_keywords:
            return
        overlap = calculate_keyword_overlap(new_keywords, session.current_topic_keywords)
        if overlap < TOPIC_SHIFT_THRESHOLD:
            session.topic_history.append({
                "keywords": new_keywords,
                "introduced_at": datetime.now().isoformat(),
                "overlap_with_previous": overlap,
            })
            session.current_topic_keywords = new_keywords

            new_stakeholders = identify_stakeholders_for_topic(
                new_keywords, self._agents, relevance_threshold=0.4
            )
            for agent_id in new_stakeholders:
                if agent_id not in session.active_participants:
                    session.active_participants[agent_id] = StakeholderStatus.ACTIVE.value
            for agent_id, status in session.active_participants.items():
                if agent_id not in new_stakeholders and status == StakeholderStatus.ACTIVE.value:
                    session.active_participants[agent_id] = StakeholderStatus.DORMANT.value

    # =========================================================================
    # GATING INTEGRATION
    # =========================================================================

    def should_agent_respond(
        self,
        session_id: str,
        agent_id: str,
        message_content: str = "",
    ) -> tuple[bool, str]:
        """Determine if an agent should respond in the session."""
        session = self.sessions.get(session_id)
        if not session:
            return (True, "no_session")

        if session.state != SessionState.ACTIVE.value:
            return (False, f"session_{session.state}")

        status = session.active_participants.get(agent_id)
        if not status:
            return (False, "not_participant")
        if status == StakeholderStatus.DORMANT.value:
            return (False, "dormant")

        # Strict mode: only recommended speaker
        if session.turn_mode == TurnMode.STRICT.value:
            rec = self.get_next_speaker(session_id)
            if rec and rec.get("recommended_speaker") != agent_id:
                return (False, "strict_mode_not_recommended")

        recent = self.chat.get_channel_messages(session.channel_id, limit=10)
        agent_config = self._agent_config(agent_id)

        score = calculate_contribution_score(
            agent_id=agent_id,
            proposed_message=message_content or "[considering response]",
            meeting_context={
                "stakeholder_status": session.active_participants,
                "current_topic": {"keywords": session.current_topic_keywords},
            },
            agent_config=agent_config,
            recent_messages=recent,
        )

        threshold = STAKEHOLDER_THRESHOLDS.get(status, 0.5)
        if score < threshold:
            return (False, f"score_{score:.2f}_below_threshold_{threshold:.2f}")
        return (True, f"score_{score:.2f}_passes_threshold_{threshold:.2f}")

    def add_participant(self, session_id: str, agent_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or session.state != SessionState.ACTIVE.value:
            return False
        if agent_id in session.active_participants:
            return False
        session.active_participants[agent_id] = StakeholderStatus.ACTIVE.value
        self.chat.post_message(
            channel_id=session.channel_id,
            sender="system",
            content=f"[SESSION] @{agent_id} has joined the discussion.",
            message_type="system",
        )
        return True

    def remove_participant(self, session_id: str, agent_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or agent_id not in session.active_participants:
            return False
        del session.active_participants[agent_id]
        return True

    def update_participant_status(
        self,
        session_id: str,
        agent_id: str,
        new_status: StakeholderStatus,
    ) -> bool:
        session = self.sessions.get(session_id)
        if not session or agent_id not in session.active_participants:
            return False
        session.active_participants[agent_id] = new_status.value
        return True

    # =========================================================================
    # SUMMARY AND REPORTING
    # =========================================================================

    def _generate_summary(self, session: Session) -> dict[str, Any]:
        all_messages = self.chat.get_channel_messages(session.channel_id, limit=500)
        session_start = datetime.fromisoformat(session.created_at)
        session_messages = [
            m for m in all_messages
            if datetime.fromisoformat(m.timestamp) >= session_start
        ]

        participation: dict[str, int] = {}
        for msg in session_messages:
            if msg.sender == "system":
                continue
            participation[msg.sender] = participation.get(msg.sender, 0) + 1

        all_kw: list[str] = []
        for msg in session_messages:
            if msg.sender != "system":
                all_kw.extend(extract_keywords(msg.content))

        keyword_freq: dict[str, int] = {}
        for kw in all_kw:
            keyword_freq[kw] = keyword_freq.get(kw, 0) + 1
        top_topics = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "session_id": session.session_id,
            "topic": session.topic,
            "duration_minutes": self._calculate_duration(session),
            "total_turns": session.current_turn,
            "total_messages": len(session_messages),
            "participants": {
                "invited": session.initial_agents,
                "contributed": session.participants_contributed,
                "message_counts": participation,
            },
            "topics_discussed": [t[0] for t in top_topics],
            "topic_shifts": len(session.topic_history) - 1,
            "turn_compliance": self._calculate_turn_compliance(session),
            "created_at": session.created_at,
            "completed_at": datetime.now().isoformat(),
        }

    def _calculate_duration(self, session: Session) -> int:
        start = datetime.fromisoformat(session.created_at)
        return int((datetime.now() - start).total_seconds() / 60)

    def _calculate_turn_compliance(self, session: Session) -> float:
        if not session.turn_history:
            return 1.0
        recommended = sum(1 for t in session.turn_history if t.get("was_recommended", False))
        return recommended / len(session.turn_history)

    def _format_summary(self, summary: dict[str, Any]) -> str:
        parts = [
            "**Session Summary**",
            f"- **Topic**: {summary['topic']}",
            f"- **Duration**: {summary['duration_minutes']} minutes",
            f"- **Total Turns**: {summary['total_turns']}",
            f"- **Total Messages**: {summary['total_messages']}",
            "",
            "**Participation**",
            f"- Invited: {len(summary['participants']['invited'])} agents",
            f"- Contributed: {len(summary['participants']['contributed'])} agents",
        ]
        if summary["participants"]["message_counts"]:
            parts.append("")
            parts.append("**Message Counts**")
            for agent, count in sorted(
                summary["participants"]["message_counts"].items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                parts.append(f"- @{agent}: {count}")
        if summary["topics_discussed"]:
            parts.append("")
            parts.append(f"**Key Topics**: {', '.join(summary['topics_discussed'][:5])}")
        parts.append("")
        parts.append(f"**Turn Compliance**: {summary['turn_compliance'] * 100:.0f}%")
        return "\n".join(parts)

    def get_status(self, session_id: str) -> dict[str, Any] | None:
        session = self.sessions.get(session_id)
        if not session:
            return None
        return {
            "session_id": session.session_id,
            "state": session.state,
            "topic": session.topic,
            "channel_id": session.channel_id,
            "current_turn": session.current_turn,
            "max_turns": session.max_turns,
            "turn_mode": session.turn_mode,
            "participants": {
                "active": [
                    k for k, v in session.active_participants.items()
                    if v == StakeholderStatus.ACTIVE.value
                ],
                "silent": [
                    k for k, v in session.active_participants.items()
                    if v == StakeholderStatus.APPROVED_SILENT.value
                ],
                "dormant": [
                    k for k, v in session.active_participants.items()
                    if v == StakeholderStatus.DORMANT.value
                ],
            },
            "contributed": session.participants_contributed,
            "current_topic_keywords": session.current_topic_keywords[:5],
            "topic_shifts": len(session.topic_history) - 1,
        }
