"""File-backed agent configuration and memory store for cohort.

Replaces the static ``AGENT_REGISTRY`` dict with a rich, file-backed
agent system.  Each agent lives in its own subdirectory under
``agents_dir`` containing:

- ``agent_config.json`` -- full configuration (identity, capabilities,
  education, display metadata)
- ``agent_prompt.md`` -- system prompt for Claude invocations
- ``memory.json`` -- runtime state (learned facts, working memory,
  collaborators)

Provides a backward-compatible ``as_config_dict()`` method so existing
code that expects ``dict[str, dict[str, Any]]`` continues to work.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cohort.agent import AgentConfig, AgentMemory
from cohort.personas import load_persona

# httpx is optional (part of claude extra) -- imported lazily in _load_from_remote


logger = logging.getLogger(__name__)


class AgentStore:
    """File-backed agent persistence with lazy loading.

    Parameters
    ----------
    agents_dir:
        Path to directory containing per-agent subdirectories.
        Each subdirectory should have ``agent_config.json``.
    fallback_registry:
        Legacy display-metadata dict (from ``agent_registry.py``) used
        when no config directory exists for a given agent.
    """

    def __init__(
        self,
        agents_dir: Path | None = None,
        fallback_registry: dict[str, dict[str, str]] | None = None,
        remote_url: str = "",
        api_key: str = "",
    ) -> None:
        self._agents_dir = Path(agents_dir) if agents_dir else None
        self._fallback: dict[str, dict[str, str]] = fallback_registry or {}
        self._remote_url = remote_url.rstrip("/") if remote_url else ""
        self._api_key = api_key
        self._cache: dict[str, AgentConfig] = {}
        self._loaded_all: bool = False

    # =====================================================================
    # Loading
    # =====================================================================

    def _ensure_all_loaded(self) -> None:
        """Scan agents_dir and load all agent configs (lazy, once).

        Also fetches the agent roster from the remote Gateway (if configured)
        so that all served agents appear in the Team Dashboard.
        """
        if self._loaded_all:
            return
        # 1. Load local agents from disk
        if self._agents_dir and self._agents_dir.is_dir():
            for child in sorted(self._agents_dir.iterdir()):
                config_path = child / "agent_config.json"
                if child.is_dir() and config_path.exists():
                    agent_id = child.name
                    if agent_id not in self._cache:
                        try:
                            config = AgentConfig.from_config_file(config_path)
                            if not config.persona_text:
                                persona = load_persona(agent_id)
                                if persona:
                                    config.persona_text = persona
                            self._cache[agent_id] = config
                        except Exception as exc:
                            logger.warning(
                                "[!] Failed to load agent %s: %s", agent_id, exc
                            )
        # 2. Sync from remote Gateway (fetch agents not already local)
        if self._remote_url:
            self._sync_from_gateway()
        self._loaded_all = True

    # Agent Store roster -- these are fetched from the remote Gateway API.
    # Includes hardcover agents so the store can push upgraded configs/facts
    # to replace the minimal versions that ship with `pip install cohort`.
    #
    # Tier gating is handled by agent_api.py (FREE_TIER_AGENTS vs Pro vs Enterprise).
    # This set only controls WHICH agents are eligible for remote sync.
    GATEWAY_AGENTS = frozenset({
        # Hardcover agents (ship locally but get upgrades from store)
        "cohort_orchestrator",
        "marketing_agent",
        "content_strategy_agent",
        "analytics_agent",
        "python_developer",
        # Free Store agents (available at no cost from Agent Store)
        "web_developer",
        "javascript_developer",
        "security_agent",
        "qa_agent",
        "documentation_agent",
        "code_archaeologist",
        "setup_guide",
        # Pro Store agents ($49/mo)
        "system_coder",
        "database_developer",
        "brand_design_agent",
        "campaign_orchestrator",
        "email_agent",
        "hardware_agent",
        "linkedin",
        "reddit",
        "media_production_agent",
        # Enterprise agent
        "supervisor_agent",
    })

    def _sync_from_gateway(self) -> None:
        """Fetch curated agents from the Gateway, upgrading existing configs."""
        try:
            import httpx  # noqa: F811
        except ImportError:
            return
        synced = 0
        for agent_id in self.GATEWAY_AGENTS:
            try:
                config = self._load_from_remote(agent_id)
                if config:
                    self._cache[agent_id] = config
                    synced += 1
            except Exception as exc:
                logger.debug("[!] Gateway fetch failed for %s: %s", agent_id, exc)
        if synced:
            logger.info("[OK] Gateway sync: %d agents synced", synced)

    def load_agent(self, agent_id: str) -> AgentConfig | None:
        """Load a single agent by ID.

        Resolution order:
        1. In-memory cache
        2. Local disk (agents_dir)
        3. Remote Agent API (if configured)
        """
        if agent_id in self._cache:
            return self._cache[agent_id]
        if self._agents_dir:
            config_path = self._agents_dir / agent_id / "agent_config.json"
            if config_path.exists():
                try:
                    config = AgentConfig.from_config_file(config_path)
                    # Load persona text (lightweight prompt for chat mode)
                    if not config.persona_text:
                        persona = load_persona(agent_id)
                        if persona:
                            config.persona_text = persona
                    self._cache[agent_id] = config
                    return config
                except Exception as exc:
                    logger.warning("[!] Failed to load agent %s: %s", agent_id, exc)
        # Remote fallback: fetch from Agent API if configured
        if self._remote_url:
            config = self._load_from_remote(agent_id)
            if config:
                self._cache[agent_id] = config
                return config
        return None

    def _load_from_remote(self, agent_id: str) -> AgentConfig | None:
        """Fetch agent config + prompt from the remote Agent API.

        Downloads the agent profile and caches it to local disk so the
        agent invocation pipeline (which reads files from disk) works
        unchanged. User-specific data (memory, inventory) is never
        overwritten if it already exists locally.
        """
        try:
            import httpx
        except ImportError:
            logger.warning(
                "[!] httpx not installed -- cannot fetch remote agents. "
                "Install with: pip install cohort[claude]"
            )
            return None

        url = f"{self._remote_url}/agents/{agent_id}/profile"
        try:
            resp = httpx.get(
                url,
                headers={"X-API-Key": self._api_key},
                timeout=15,
            )
            if resp.status_code == 403:
                logger.info(
                    "[!] Agent '%s' requires a higher tier API key", agent_id
                )
                return None
            if resp.status_code != 200:
                logger.debug(
                    "[!] Remote agent fetch failed for %s: HTTP %d",
                    agent_id, resp.status_code,
                )
                return None

            data = resp.json()
            config_dict = data.get("config", {})
            prompt_text = data.get("prompt")
            remote_facts = data.get("recent_facts", [])

            # Inject agent_id from the top-level response if missing
            if "agent_id" not in config_dict:
                config_dict["agent_id"] = data.get("agent_id", agent_id)

            # Parse into AgentConfig
            config = AgentConfig.from_dict(config_dict)

            # Cache to local disk for the invocation pipeline
            if self._agents_dir:
                agent_dir = self._agents_dir / agent_id
                agent_dir.mkdir(parents=True, exist_ok=True)

                # Always overwrite config (get latest intelligence)
                config_path = agent_dir / "agent_config.json"
                config_path.write_text(
                    json.dumps(config_dict, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                # Always overwrite prompt (get latest intelligence)
                if prompt_text:
                    prompt_path = agent_dir / "agent_prompt.md"
                    prompt_path.write_text(prompt_text, encoding="utf-8")

                # Merge remote facts into local memory (don't clobber user data)
                mem_path = agent_dir / "memory.json"
                if mem_path.exists():
                    try:
                        local_mem = json.loads(
                            mem_path.read_text(encoding="utf-8")
                        )
                    except (json.JSONDecodeError, OSError):
                        local_mem = {}
                else:
                    local_mem = {
                        "agent_id": agent_id,
                        "working_memory": [],
                        "learned_facts": [],
                        "known_paths": {},
                    }

                # Add remote facts that don't already exist locally
                existing_facts = {
                    f.get("fact", "") for f in local_mem.get("learned_facts", [])
                }
                for fact in remote_facts:
                    if fact.get("fact", "") not in existing_facts:
                        local_mem.setdefault("learned_facts", []).append(fact)

                mem_path.write_text(
                    json.dumps(local_mem, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            logger.info("[OK] Fetched agent '%s' from remote API", agent_id)
            return config

        except Exception as exc:
            logger.warning(
                "[!] Failed to fetch agent '%s' from remote: %s",
                agent_id, exc,
            )
            return None

    def reload(self) -> None:
        """Clear cache and re-scan from disk."""
        self._cache.clear()
        self._loaded_all = False

    # =====================================================================
    # Access
    # =====================================================================

    def get(self, agent_id: str) -> AgentConfig | None:
        """Get an agent config by ID (loads from disk if needed)."""
        return self.load_agent(agent_id)

    def list_agents(self, include_hidden: bool = False) -> list[AgentConfig]:
        """Return all loaded agents, optionally including hidden ones."""
        self._ensure_all_loaded()
        agents = list(self._cache.values())
        if not include_hidden:
            agents = [a for a in agents if not a.hidden]
        return sorted(agents, key=lambda a: a.agent_id)

    def get_by_alias(self, alias: str) -> AgentConfig | None:
        """Resolve an @mention alias to an agent config.

        Checks: exact agent_id match, then alias lists, then
        case-insensitive agent_id match.
        """
        normalized = alias.lower().replace("-", "_").replace(" ", "_")

        # Direct match
        agent = self.load_agent(normalized)
        if agent:
            return agent

        # Scan all agents for alias match
        self._ensure_all_loaded()
        for config in self._cache.values():
            if normalized in [a.lower() for a in config.aliases]:
                return config

        # Original casing match
        agent = self.load_agent(alias)
        if agent:
            return agent

        return None

    # =====================================================================
    # Backward compatibility (dict-based interface)
    # =====================================================================

    def as_config_dict(self) -> dict[str, dict[str, Any]]:
        """Return agents as ``dict[str, dict[str, Any]]`` for
        backward compatibility with Orchestrator and CohortDataLayer.

        Each entry contains ``triggers``, ``capabilities``, and other
        fields that existing scoring functions expect.
        """
        self._ensure_all_loaded()
        result: dict[str, dict[str, Any]] = {}
        for agent_id, config in self._cache.items():
            entry: dict[str, Any] = {
                "name": config.name,
                "role": config.role,
                "triggers": config.triggers,
                "capabilities": config.capabilities,
                "domain_expertise": config.domain_expertise,
                "agent_type": config.agent_type,
                "status": config.status,
                # Display metadata (used by team dashboard)
                "avatar": config.avatar,
                "nickname": config.nickname,
                "color": config.color,
                "group": config.group,
            }
            # Include scoring metadata if present
            if config.scoring_metadata:
                entry.update(config.scoring_metadata)
            result[agent_id] = entry
        return result

    # =====================================================================
    # Display profiles (replaces agent_registry.py)
    # =====================================================================

    def get_display_profile(self, sender_id: str) -> dict[str, str]:
        """Return display metadata for an agent, with fallback.

        Matches the interface of ``agent_registry.get_agent_profile()``.
        """
        normalized = sender_id.lower().replace(" ", "_").replace("-", "_")

        # Try from loaded configs
        agent = self.get(sender_id) or self.get(normalized)
        if agent:
            return agent.display_profile()

        # Check aliases
        agent = self.get_by_alias(sender_id)
        if agent:
            return agent.display_profile()

        # Fallback to legacy registry
        if sender_id in self._fallback:
            return dict(self._fallback[sender_id])
        if normalized in self._fallback:
            return dict(self._fallback[normalized])

        # Prefix match against legacy
        for key, profile in self._fallback.items():
            if key.startswith(normalized):
                return dict(profile)

        # Default
        initials = sender_id[:2].upper()
        return {
            "name": sender_id,
            "nickname": sender_id[:10],
            "avatar": initials,
            "color": "#95A5A6",
            "role": "Agent",
            "group": "Agents",
        }

    def get_all_display_profiles(self) -> dict[str, dict[str, str]]:
        """Return display profiles for all visible agents.

        Merges file-backed agents with legacy fallback entries.
        """
        result: dict[str, dict[str, str]] = {}

        # Legacy entries first (lower priority)
        for key, profile in self._fallback.items():
            if not profile.get("hidden"):
                result[key] = dict(profile)

        # File-backed agents override
        self._ensure_all_loaded()
        for agent_id, config in self._cache.items():
            if not config.hidden:
                result[agent_id] = config.display_profile()

        return result

    # =====================================================================
    # Memory
    # =====================================================================

    def load_memory(self, agent_id: str) -> AgentMemory | None:
        """Load an agent's memory from disk."""
        if not self._agents_dir:
            return None
        mem_path = self._agents_dir / agent_id / "memory.json"
        if not mem_path.exists():
            # Agent dir may exist without memory -- create empty
            agent_dir = self._agents_dir / agent_id
            if agent_dir.is_dir():
                return AgentMemory.create_empty(agent_id)
            return None
        return AgentMemory.load(mem_path)

    def save_memory(self, agent_id: str, memory: AgentMemory) -> None:
        """Save an agent's memory to disk."""
        if not self._agents_dir:
            return
        mem_path = self._agents_dir / agent_id / "memory.json"
        memory.save(mem_path)

    # =====================================================================
    # Prompt
    # =====================================================================

    def get_prompt(self, agent_id: str) -> str | None:
        """Load an agent's prompt markdown from disk."""
        path = self.get_prompt_path(agent_id)
        if not path:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("[!] Failed to read prompt for %s: %s", agent_id, exc)
            return None

    def get_prompt_path(self, agent_id: str) -> Path | None:
        """Return the path to an agent's prompt file, or None."""
        if not self._agents_dir:
            return None
        p = self._agents_dir / agent_id / "agent_prompt.md"
        return p if p.exists() else None

    # =====================================================================
    # Registration
    # =====================================================================

    def register(self, config: AgentConfig) -> None:
        """Add or update an agent in the store.

        Writes config to disk if ``agents_dir`` is set.
        """
        self._cache[config.agent_id] = config
        if self._agents_dir:
            agent_dir = self._agents_dir / config.agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)
            config_path = agent_dir / "agent_config.json"
            config_path.write_text(
                json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("[OK] Registered agent %s", config.agent_id)

    def unregister(self, agent_id: str) -> bool:
        """Remove an agent from the in-memory cache.

        Does NOT delete files from disk (safety measure).
        """
        if agent_id in self._cache:
            del self._cache[agent_id]
            return True
        return False


# =====================================================================
# Module-level singleton accessor
# =====================================================================

_global_store: AgentStore | None = None


def set_global_store(store: AgentStore) -> None:
    """Set the module-level AgentStore singleton.  Called during server startup."""
    global _global_store  # noqa: PLW0603
    _global_store = store


def get_store() -> AgentStore | None:
    """Return the module-level AgentStore singleton, or None if not initialized."""
    return _global_store
