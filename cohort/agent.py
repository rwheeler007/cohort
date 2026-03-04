"""Core agent data model for cohort.

Defines the dataclasses that represent an agent's configuration,
memory, education, and knowledge.

An :class:`AgentConfig` instance satisfies the
:class:`~cohort.registry.AgentProfile` protocol, so it can be used
directly with the Orchestrator, meeting gating, and scoring functions.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any


# =====================================================================
# Stop words (shared with meeting.py -- duplicated to avoid circular import)
# =====================================================================

_STOP_WORDS: frozenset[str] = frozenset(
    "the a an is are was were in on at to for of and or but with from by "
    "this that these those be have has had do does did will would should "
    "could can may might must i you he she it we they my your his her its "
    "our their me him us them".split()
)


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from *text*."""
    words = re.findall(r"\b[a-z0-9_]+\b", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 3]


# =====================================================================
# LearnedFact
# =====================================================================

@dataclass
class LearnedFact:
    """A single piece of knowledge acquired by an agent."""

    fact: str
    learned_from: str
    timestamp: str
    confidence: str = "medium"  # "high" | "medium" | "low"
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearnedFact:
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid})


# =====================================================================
# WorkingMemoryEntry
# =====================================================================

@dataclass
class WorkingMemoryEntry:
    """A single interaction recorded in an agent's working memory."""

    timestamp: str
    channel: str
    input: str
    response: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkingMemoryEntry:
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid})


# =====================================================================
# AgentEducation
# =====================================================================

@dataclass
class AgentEducation:
    """Training and skill tracking for an agent."""

    specialty: str = ""
    last_training_date: str | None = None
    training_frequency_days: int = 14
    knowledge_areas: list[str] = field(default_factory=list)
    learning_history: list[dict[str, Any]] = field(default_factory=list)
    skill_levels: dict[str, int] = field(default_factory=dict)
    pending_curriculum: list[dict[str, Any]] = field(default_factory=list)
    training_type: str | None = None
    certification: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentEducation:
        if not data:
            return cls()
        valid = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid}
        # Normalize legacy field name
        if "skill_level" in data and "skill_levels" not in filtered:
            filtered["skill_levels"] = data["skill_level"]
        return cls(**filtered)


# =====================================================================
# AgentMemory
# =====================================================================

@dataclass
class AgentMemory:
    """Runtime state for a single agent.

    Contains transient working memory (trimmed by MemoryManager),
    persistent learned facts, collaborator tracking, and training
    history records.
    """

    agent_id: str
    known_paths: dict[str, str] = field(default_factory=dict)
    learned_facts: list[LearnedFact] = field(default_factory=list)
    active_tasks: list[dict[str, Any]] = field(default_factory=list)
    collaborators: dict[str, dict[str, Any]] = field(default_factory=dict)
    working_memory: list[WorkingMemoryEntry] = field(default_factory=list)
    learning_history: list[dict[str, Any]] = field(default_factory=list)
    archive_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Ensure nested dataclasses are plain dicts
        d["learned_facts"] = [f.to_dict() for f in self.learned_facts]
        d["working_memory"] = [e.to_dict() for e in self.working_memory]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentMemory:
        if not data:
            return cls(agent_id="unknown")
        d = dict(data)
        d["learned_facts"] = [
            LearnedFact.from_dict(f) for f in d.get("learned_facts", [])
        ]
        d["working_memory"] = [
            WorkingMemoryEntry.from_dict(e) for e in d.get("working_memory", [])
        ]
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def load(cls, path: Path) -> AgentMemory:
        """Load from a JSON file."""
        if not path.exists():
            return cls(agent_id=path.parent.name)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        """Write to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def create_empty(cls, agent_id: str) -> AgentMemory:
        """Create a blank memory for a new agent."""
        return cls(agent_id=agent_id)


# =====================================================================
# AgentConfig
# =====================================================================

@dataclass
class AgentConfig:
    """Full agent configuration.

    Satisfies the :class:`~cohort.registry.AgentProfile` protocol by
    implementing :meth:`relevance_score` and :meth:`can_contribute`.

    Absorbs display metadata (nickname, color, group) so there is a
    single source of truth per agent -- no separate registry needed.
    """

    # -- Identity (required) --
    agent_id: str
    name: str
    role: str
    primary_task: str = ""
    personality: str = ""
    agent_type: str = "specialist"  # specialist | orchestrator | supervisor | infrastructure | utility

    # -- Capabilities --
    capabilities: list[str] = field(default_factory=list)
    domain_expertise: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)

    # -- Display metadata (absorbed from agent_registry.py) --
    avatar: str = ""
    aliases: list[str] = field(default_factory=list)
    nickname: str = ""
    color: str = "#95A5A6"
    group: str = "Agents"
    hidden: bool = False

    # -- Task context --
    task_context: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)
    common_pitfalls: list[dict[str, str]] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)

    # -- Education --
    education: AgentEducation = field(default_factory=AgentEducation)

    # -- Optional --
    extended_personality: dict[str, Any] | None = None
    external_services: dict[str, Any] = field(default_factory=dict)

    # -- Model parameters (optional, per-agent LLM tuning) --
    model_params: dict[str, Any] = field(default_factory=dict)

    # -- Scoring metadata (optional, used by meeting.py) --
    scoring_metadata: dict[str, Any] = field(default_factory=dict)

    # -- Persona (light mode prompt, loaded from personas/ directory) --
    persona_text: str = ""

    # -- Status --
    status: str = "active"  # active | inactive | training
    created_date: str = ""
    last_updated: str = ""
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.avatar:
            self.avatar = self.agent_id[:2].upper()
        if not self.nickname:
            self.nickname = self.name[:10] if self.name else self.agent_id[:10]
        if not self.created_date:
            self.created_date = datetime.now().isoformat()
        if not self.last_updated:
            self.last_updated = self.created_date

    # -- AgentProfile Protocol --

    def relevance_score(self, topic: str) -> float:
        """Score how relevant this agent is to *topic* (0.0--1.0).

        Uses keyword overlap between topic and agent triggers +
        capabilities + domain_expertise.
        """
        topic_keywords = _extract_keywords(topic)
        if not topic_keywords:
            return 0.0

        agent_keywords: list[str] = []
        for trigger in self.triggers:
            agent_keywords.extend(_extract_keywords(str(trigger)))
        for cap in self.capabilities:
            agent_keywords.extend(_extract_keywords(str(cap)))
        for exp in self.domain_expertise:
            agent_keywords.extend(_extract_keywords(str(exp)))

        if not agent_keywords:
            return 0.0

        topic_set = set(topic_keywords)
        agent_set = set(agent_keywords)
        union = len(topic_set | agent_set)
        if not union:
            return 0.0
        return len(topic_set & agent_set) / union

    def can_contribute(self, context: dict[str, Any]) -> bool:
        """Check if this agent can contribute in *context*.

        Returns True for non-meeting contexts.  In meeting contexts,
        delegates to the meeting module's scoring.
        """
        if not context.get("meeting_context"):
            return True
        # In meeting mode, check relevance against topic keywords
        keywords = (
            context.get("meeting_context", {})
            .get("current_topic", {})
            .get("keywords", [])
        )
        if not keywords:
            return True
        return self.relevance_score(" ".join(keywords)) > 0.1

    # -- Serialization --

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["education"] = self.education.to_dict()
        # Flatten scoring_metadata back to top level for config compatibility
        sm = d.pop("scoring_metadata", {})
        d.update(sm)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentConfig:
        if not data:
            return cls(agent_id="unknown", name="Unknown", role="Agent")
        d = dict(data)
        # Normalize legacy field names
        if "agent_name" in d and "name" not in d:
            d["name"] = d.pop("agent_name")
        elif "agent_name" in d:
            d.pop("agent_name")
        # Collect scoring metadata into a single field
        _scoring_keys = ("complementary_agents", "data_sources", "phase_roles")
        scoring_md: dict[str, Any] = d.get("scoring_metadata", {})
        for sk in _scoring_keys:
            if sk in d:
                scoring_md[sk] = d.pop(sk)
        if scoring_md:
            d["scoring_metadata"] = scoring_md
        # Parse education sub-object
        d["education"] = AgentEducation.from_dict(d.get("education", {}))
        # Drop unknown keys
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def from_config_file(cls, path: Path) -> AgentConfig:
        """Load from an agent_config.json file.

        The ``agent_id`` is inferred from the parent directory name
        if not present in the JSON.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        if "agent_id" not in data:
            data["agent_id"] = path.parent.name
        return cls.from_dict(data)

    # -- Display profile (backward compat with agent_registry) --

    def display_profile(self) -> dict[str, str]:
        """Return display metadata matching agent_registry format."""
        return {
            "name": self.name,
            "nickname": self.nickname,
            "avatar": self.avatar,
            "color": self.color,
            "role": self.role,
            "group": self.group,
        }
