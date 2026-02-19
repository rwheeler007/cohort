"""Agent creation workflow for cohort.

Creates new agents with proper directory structure, configuration,
prompt, and memory files.

Phases: DEFINE -> SCAFFOLD -> GENERATE -> REGISTER -> VALIDATE
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from cohort.agent import AgentConfig, AgentEducation, AgentMemory
from cohort.agent_store import AgentStore

logger = logging.getLogger(__name__)


# =====================================================================
# Agent types
# =====================================================================

class AgentType(Enum):
    SPECIALIST = "specialist"
    ORCHESTRATOR = "orchestrator"
    INFRASTRUCTURE = "infrastructure"
    UTILITY = "utility"


# =====================================================================
# Agent spec (creation input)
# =====================================================================

@dataclass
class AgentSpec:
    """Input specification for creating a new agent."""

    name: str
    role: str
    primary_task: str
    agent_type: AgentType = AgentType.SPECIALIST
    personality: str = ""
    capabilities: list[str] = field(default_factory=list)
    domain_expertise: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    avatar: str = ""
    aliases: list[str] = field(default_factory=list)
    nickname: str = ""
    color: str = "#95A5A6"
    group: str = "Agents"

    @property
    def agent_id(self) -> str:
        """Derive snake_case agent_id from name."""
        return _to_snake_case(self.name)


def _to_snake_case(text: str) -> str:
    """Convert a display name to a snake_case identifier."""
    s = re.sub(r"[^\w\s]", "", text)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


# =====================================================================
# Prompt templates
# =====================================================================

_SPECIALIST_PROMPT = """\
# {name}

## Role
{role}

## Primary Task
{primary_task}

## Personality
{personality}

## Core Principles
- Deliver high-quality, production-ready work
- Communicate clearly and concisely
- Ask clarifying questions when requirements are ambiguous
- Follow established patterns and conventions in the codebase

## Capabilities
{capabilities_section}

## Domain Expertise
{expertise_section}

## Response Guidelines
- Keep responses focused and actionable
- Provide code examples when relevant
- Explain trade-offs when multiple approaches exist
- Flag potential issues or risks proactively
"""

_ORCHESTRATOR_PROMPT = """\
# {name}

## Role
{role}

## Primary Mission
{primary_task}

## Personality
{personality}

## Core Responsibilities
- Coordinate work across agents and services
- Route tasks to the most appropriate specialist
- Track progress and ensure deliverables meet quality standards
- Resolve conflicts and blockers

## Capabilities
{capabilities_section}

## Routing Guidelines
- Match tasks to agents based on capability overlap
- Prefer specialists for domain-specific work
- Escalate ambiguous requests for clarification
"""

_TEMPLATES = {
    AgentType.SPECIALIST: _SPECIALIST_PROMPT,
    AgentType.ORCHESTRATOR: _ORCHESTRATOR_PROMPT,
    AgentType.INFRASTRUCTURE: _SPECIALIST_PROMPT,
    AgentType.UTILITY: _SPECIALIST_PROMPT,
}


# =====================================================================
# Creator
# =====================================================================

class AgentCreator:
    """Creates new agents with proper directory structure.

    Parameters
    ----------
    store:
        :class:`~cohort.agent_store.AgentStore` for registering the
        new agent after creation.
    """

    def __init__(self, store: AgentStore) -> None:
        self._store = store

    def create_agent(self, spec: AgentSpec) -> AgentConfig:
        """Create a new agent from *spec*.

        Phases: DEFINE -> SCAFFOLD -> GENERATE -> REGISTER -> VALIDATE

        Returns the created :class:`AgentConfig`.

        Raises
        ------
        ValueError
            If the agent directory already exists or agents_dir is not set.
        """
        agent_id = spec.agent_id

        # DEFINE
        logger.info("[*] Creating agent: %s (%s)", spec.name, agent_id)

        if self._store._agents_dir is None:
            raise ValueError("AgentStore has no agents_dir -- cannot create agents")

        agent_dir = self._store._agents_dir / agent_id
        if agent_dir.exists():
            raise ValueError(f"Agent directory already exists: {agent_dir}")

        # SCAFFOLD
        agent_dir.mkdir(parents=True)
        logger.info("[OK] Scaffolded %s", agent_dir)

        # GENERATE
        config = self._generate_config(spec)
        config_path = agent_dir / "agent_config.json"
        config_path.write_text(
            json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        prompt = self._generate_prompt(spec)
        prompt_path = agent_dir / "agent_prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        memory = AgentMemory.create_empty(agent_id)
        memory.save(agent_dir / "memory.json")

        logger.info("[OK] Generated config, prompt, memory for %s", agent_id)

        # REGISTER
        self._store.register(config)
        logger.info("[OK] Registered %s in AgentStore", agent_id)

        # VALIDATE
        self._validate(agent_dir)
        logger.info("[OK] Validated %s", agent_id)

        return config

    def _generate_config(self, spec: AgentSpec) -> AgentConfig:
        """Build an AgentConfig from the spec."""
        now = datetime.now().isoformat()

        # Derive triggers from name if not provided
        triggers = spec.triggers or [spec.agent_id, spec.name.lower()]

        # Derive skill levels from domain expertise (all start at 0)
        skill_levels = {
            _to_snake_case(exp): 0 for exp in spec.domain_expertise
        }

        return AgentConfig(
            agent_id=spec.agent_id,
            name=spec.name,
            role=spec.role,
            primary_task=spec.primary_task,
            personality=spec.personality or f"Professional and thorough {spec.role.lower()}",
            agent_type=spec.agent_type.value,
            capabilities=spec.capabilities,
            domain_expertise=spec.domain_expertise,
            triggers=triggers,
            avatar=spec.avatar or spec.agent_id[:2].upper(),
            aliases=spec.aliases,
            nickname=spec.nickname or spec.name[:10],
            color=spec.color,
            group=spec.group,
            education=AgentEducation(
                specialty=spec.role,
                knowledge_areas=spec.domain_expertise,
                skill_levels=skill_levels,
            ),
            status="active",
            created_date=now,
            last_updated=now,
        )

    def _generate_prompt(self, spec: AgentSpec) -> str:
        """Build an agent_prompt.md from the spec."""
        template = _TEMPLATES.get(spec.agent_type, _SPECIALIST_PROMPT)

        caps_section = "\n".join(f"- {c}" for c in spec.capabilities) or "- General purpose"
        expertise_section = "\n".join(f"- {e}" for e in spec.domain_expertise) or "- General"

        return template.format(
            name=spec.name,
            role=spec.role,
            primary_task=spec.primary_task,
            personality=spec.personality or f"Professional and thorough {spec.role.lower()}",
            capabilities_section=caps_section,
            expertise_section=expertise_section,
        )

    def _validate(self, agent_dir: Path) -> None:
        """Check that all required files exist."""
        required = ["agent_config.json", "agent_prompt.md", "memory.json"]
        for filename in required:
            path = agent_dir / filename
            if not path.exists():
                raise FileNotFoundError(
                    f"Missing required file: {path}"
                )
