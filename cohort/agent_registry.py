"""Agent Registry -- Centralized agent profiles for Cohort.

This module maintains a legacy static registry for backward
compatibility, while delegating to :class:`~cohort.agent_store.AgentStore`
when one is configured.  File-backed agent configs take priority over
the static dict.
"""

from __future__ import annotations

from typing import Any

from cohort.agent_store import AgentStore

# =====================================================================
# Legacy static registry (fallback for agents without config directories)
# =====================================================================

_LEGACY_REGISTRY: dict[str, dict[str, str]] = {
    # -- Human operators --
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

    # -- Leadership --
    "cohort_orchestrator": {
        "name": "Cohort Orchestrator",
        "nickname": "Orch",
        "avatar": "CO",
        "color": "#C0392B",
        "role": "Orchestrator",
        "group": "Leadership",
    },
    "supervisor_agent": {
        "name": "Supervisor",
        "nickname": "Supervisor",
        "avatar": "SV",
        "color": "#E74C3C",
        "role": "Supervisor",
        "group": "Leadership",
    },

    # -- Core Developers --
    "python_developer": {
        "name": "Python Developer",
        "nickname": "PyDev",
        "avatar": "PY",
        "color": "#3498DB",
        "role": "Senior Developer",
        "group": "Core Developers",
    },
    "web_developer": {
        "name": "Web Developer",
        "nickname": "WebDev",
        "avatar": "WD",
        "color": "#E67E22",
        "role": "Frontend Developer",
        "group": "Core Developers",
    },
    "javascript_developer": {
        "name": "JavaScript Developer",
        "nickname": "JSDev",
        "avatar": "JS",
        "color": "#F1C40F",
        "role": "Senior Developer",
        "group": "Core Developers",
    },
    "system_coder": {
        "name": "System Coder",
        "nickname": "SysCoder",
        "avatar": "SC",
        "color": "#1ABC9C",
        "role": "Senior Developer",
        "group": "Core Developers",
    },
    "database_developer": {
        "name": "Database Developer",
        "nickname": "DBDev",
        "avatar": "DB",
        "color": "#2ECC71",
        "role": "Database Specialist",
        "group": "Core Developers",
    },

    # -- Quality & Security --
    "qa_agent": {
        "name": "QA Agent",
        "nickname": "QA",
        "avatar": "QA",
        "color": "#27AE60",
        "role": "QA Specialist",
        "group": "Quality & Security",
    },
    "security_agent": {
        "name": "Security Agent",
        "nickname": "SecAgent",
        "avatar": "SEC",
        "color": "#C0392B",
        "role": "Security Specialist",
        "group": "Quality & Security",
    },
    "code_archaeologist": {
        "name": "Code Archaeologist",
        "nickname": "Archeo",
        "avatar": "CA",
        "color": "#8E44AD",
        "role": "Analyst",
        "group": "Quality & Security",
    },

    # -- Marketing & Content --
    "marketing_agent": {
        "name": "Marketing Strategist",
        "nickname": "Marketing",
        "avatar": "MKT",
        "color": "#E74C3C",
        "role": "Growth Strategist",
        "group": "Marketing & Content",
    },
    "campaign_orchestrator": {
        "name": "Campaign Orchestrator",
        "nickname": "Campaigns",
        "avatar": "CMP",
        "color": "#D35400",
        "role": "Campaign Director",
        "group": "Marketing & Content",
    },
    "content_strategy_agent": {
        "name": "Content Strategy Agent",
        "nickname": "Content",
        "avatar": "CS",
        "color": "#2ECC71",
        "role": "Content Strategist",
        "group": "Marketing & Content",
    },
    "brand_design_agent": {
        "name": "Brand Design Agent",
        "nickname": "Brand",
        "avatar": "BRD",
        "color": "#9B59B6",
        "role": "Brand & Visual Design",
        "group": "Marketing & Content",
    },
    "media_production_agent": {
        "name": "Media Production Agent",
        "nickname": "Media",
        "avatar": "MED",
        "color": "#E67E22",
        "role": "Video & Media Producer",
        "group": "Marketing & Content",
    },
    "analytics_agent": {
        "name": "Analytics Agent",
        "nickname": "Analytics",
        "avatar": "BI",
        "color": "#3498DB",
        "role": "BI & Analytics",
        "group": "Marketing & Content",
    },

    # -- Social Media --
    "email_agent": {
        "name": "Email Marketing Specialist",
        "nickname": "Email",
        "avatar": "EM",
        "color": "#3498DB",
        "role": "Email Marketing",
        "group": "Social Media",
    },
    "linkedin": {
        "name": "LinkedIn Specialist",
        "nickname": "LinkedIn",
        "avatar": "LI",
        "color": "#0A66C2",
        "role": "LinkedIn Strategist",
        "group": "Social Media",
    },
    "reddit": {
        "name": "Reddit Specialist",
        "nickname": "Reddit",
        "avatar": "RD",
        "color": "#FF4500",
        "role": "Reddit Community",
        "group": "Social Media",
    },

    # -- Support --
    "hardware_agent": {
        "name": "Hardware Agent",
        "nickname": "Hardware",
        "avatar": "HW",
        "color": "#16A085",
        "role": "Hardware Specialist",
        "group": "Support",
    },
    "documentation_agent": {
        "name": "Documentation Agent",
        "nickname": "Docs",
        "avatar": "DC",
        "color": "#2980B9",
        "role": "Technical Writer",
        "group": "Support",
    },
    "setup_guide": {
        "name": "Setup Guide",
        "nickname": "Setup",
        "avatar": "SG",
        "color": "#16A085",
        "role": "Onboarding Guide",
        "group": "Support",
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
