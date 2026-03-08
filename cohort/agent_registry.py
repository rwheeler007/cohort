"""Agent Registry -- Centralized agent profiles for Cohort.

Display metadata lives in each agent's ``agent_config.json``.  This
module provides a thin public API that delegates to
:class:`~cohort.agent_store.AgentStore`.

The only static entries are for non-file-backed virtual senders
(``user`` and ``system``).
"""

from __future__ import annotations

from typing import Any

from cohort.agent_store import AgentStore

# =====================================================================
# Static entries for virtual senders (not file-backed agents)
# =====================================================================

_LEGACY_REGISTRY: dict[str, dict[str, str]] = {
    "user": {
        "name": "User",
        "nickname": "User",
        "avatar": "U",
        "color": "#95E1D3",
        "role": "Operator",
        "group": "Operators",
    },
    "system": {
        "name": "System",
        "nickname": "System",
        "avatar": "SYS",
        "color": "#7F8C8D",
        "role": "System",
        "group": "Operators",
    },
}

# Backward-compatible public name
AGENT_REGISTRY = _LEGACY_REGISTRY

# =====================================================================
# Global store instance (lazy-initialized)
# =====================================================================

_store: AgentStore | None = None


def set_store(store: AgentStore) -> None:
    """Wire a global AgentStore.  Called by server.py during startup."""
    global _store  # noqa: PLW0603
    _store = store


def _get_store() -> AgentStore:
    """Return the global store, creating a fallback-only one if needed."""
    global _store  # noqa: PLW0603
    if _store is None:
        _store = AgentStore(fallback_registry=_LEGACY_REGISTRY)
    return _store


# =====================================================================
# Public API (unchanged signatures for backward compat)
# =====================================================================

def get_agent_profile(sender_id: str) -> dict[str, str]:
    """Return the visual profile for an agent, or a sensible default."""
    return _get_store().get_display_profile(sender_id)


def get_all_agents() -> dict[str, dict[str, str]]:
    """Return the agent registry, excluding hidden agents."""
    return _get_store().get_all_display_profiles()
