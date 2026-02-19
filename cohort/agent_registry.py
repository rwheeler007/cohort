"""
Agent Registry - Centralized agent profiles for Cohort.

Contains avatars, nicknames, colors, and roles for all agents
in the Cohort coding team dashboard.
"""

AGENT_REGISTRY: dict[str, dict[str, str]] = {
    # ======================================================================
    # HUMAN OPERATORS
    # ======================================================================
    "ryan_wheeler": {
        "name": "Ryan Wheeler",
        "nickname": "RyanW",
        "avatar": "RW",
        "color": "#008A00",
        "role": "Token Human",
        "group": "Operators",
    },
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

    # ======================================================================
    # LEADERSHIP / COORDINATION
    # ======================================================================
    "BOSS_agent": {
        "name": "BOSS Agent",
        "nickname": "BOSS",
        "avatar": "B",
        "color": "#FF6B6B",
        "role": "Orchestrator",
        "group": "Leadership",
        "hidden": True,  # Backend-only -- not shown in UI
    },
    "supervisor_agent": {
        "name": "Supervisor",
        "nickname": "Supervisor",
        "avatar": "SV",
        "color": "#E74C3C",
        "role": "Supervisor",
        "group": "Leadership",
    },
    "coding_orchestrator": {
        "name": "Coding Orchestrator",
        "nickname": "Orch",
        "avatar": "CO",
        "color": "#9B59B6",
        "role": "Project Manager",
        "group": "Leadership",
    },

    # ======================================================================
    # CORE DEVELOPERS
    # ======================================================================
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

    # ======================================================================
    # SPECIALISTS
    # ======================================================================
    "database_developer": {
        "name": "Database Developer",
        "nickname": "DBDev",
        "avatar": "DB",
        "color": "#2ECC71",
        "role": "Database Specialist",
        "group": "Specialists",
    },
    "qa_agent": {
        "name": "QA Agent",
        "nickname": "QA",
        "avatar": "QA",
        "color": "#27AE60",
        "role": "QA Specialist",
        "group": "Specialists",
    },
    "security_agent": {
        "name": "Security Agent",
        "nickname": "SecAgent",
        "avatar": "SEC",
        "color": "#C0392B",
        "role": "Security Specialist",
        "group": "Specialists",
    },
    "code_archaeologist": {
        "name": "Code Archaeologist",
        "nickname": "Archeo",
        "avatar": "CA",
        "color": "#8E44AD",
        "role": "Analyst",
        "group": "Specialists",
    },
    "devops_agent": {
        "name": "DevOps Agent",
        "nickname": "DevOps",
        "avatar": "DO",
        "color": "#16A085",
        "role": "DevOps Engineer",
        "group": "Specialists",
    },

    # ======================================================================
    # SUPPORT
    # ======================================================================
    "documentation_agent": {
        "name": "Documentation Agent",
        "nickname": "Docs",
        "avatar": "DC",
        "color": "#2980B9",
        "role": "Technical Writer",
        "group": "Support",
    },
    "sdk_educator_research": {
        "name": "SDK Educator",
        "nickname": "Educator",
        "avatar": "ED",
        "color": "#D35400",
        "role": "Educator",
        "group": "Support",
    },
}


def get_agent_profile(sender_id: str) -> dict[str, str]:
    """Return the visual profile for an agent, or a sensible default."""
    normalized = sender_id.lower().replace(" ", "_").replace("-", "_")

    # Exact match
    if sender_id in AGENT_REGISTRY:
        return AGENT_REGISTRY[sender_id]
    if normalized in AGENT_REGISTRY:
        return AGENT_REGISTRY[normalized]

    # Prefix match
    for key, profile in AGENT_REGISTRY.items():
        if key.startswith(normalized):
            return profile

    # Default
    initials = sender_id[:2].upper()
    return {
        "name": sender_id,
        "nickname": sender_id[:10],
        "avatar": initials,
        "color": "#95A5A6",
        "role": "Agent",
    }


def get_all_agents() -> dict[str, dict[str, str]]:
    """Return the agent registry, excluding hidden agents."""
    return {k: v for k, v in AGENT_REGISTRY.items() if not v.get("hidden")}
