"""Tool permission resolution for Cohort agents.

Merges central defaults (data/tool_permissions.json) with per-agent
overrides (agent_config.json -> tool_permissions) to produce a resolved
set of allowed tools, permission mode, and MCP server configs.

Resolution order:
  1. Determine profile: agent override > agent_type default > "minimal"
  2. Apply profile values (allowed_tools, permission_mode, max_turns)
  3. Apply per-agent overrides (allowed_tools_override, denied_tools, etc.)
  4. Subtract global + agent denied_tools
  5. Return None if no tools remain (backward compatible)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level cache for central permissions
_central_permissions: dict[str, Any] = {}
_central_loaded: bool = False


@dataclass
class ResolvedPermissions:
    """Resolved tool permissions for an agent invocation."""

    allowed_tools: list[str]
    permission_mode: str | None = None
    max_turns: int = 10
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    profile_name: str = ""


def load_central_permissions(data_dir: Path | None = None) -> dict[str, Any]:
    """Load central tool_permissions.json from the data directory.

    Returns empty dict if file does not exist (backward compatible).
    Caches result module-level; call reload_central_permissions() to refresh.
    """
    global _central_permissions, _central_loaded

    if _central_loaded:
        return _central_permissions

    if data_dir is None:
        # Default: cohort/data/ relative to this file's package
        data_dir = Path(__file__).parent.parent / "data"

    perms_path = data_dir / "tool_permissions.json"
    if not perms_path.exists():
        logger.debug("[*] No tool_permissions.json at %s", perms_path)
        _central_permissions = {}
        _central_loaded = True
        return _central_permissions

    try:
        _central_permissions = json.loads(perms_path.read_text(encoding="utf-8"))
        _central_loaded = True
        logger.info("[OK] Loaded tool_permissions.json (%d profiles)",
                    len(_central_permissions.get("tool_profiles", {})))
    except Exception:
        logger.exception("[X] Failed to load tool_permissions.json")
        _central_permissions = {}
        _central_loaded = True

    return _central_permissions


def reload_central_permissions(data_dir: Path | None = None) -> dict[str, Any]:
    """Force reload of central permissions (e.g., after settings change)."""
    global _central_loaded
    _central_loaded = False
    return load_central_permissions(data_dir)


def get_central_permissions() -> dict[str, Any]:
    """Get cached central permissions. Loads on first call."""
    return load_central_permissions()


def resolve_permissions(
    agent_id: str,
    agent_config: Any | None,
    central: dict[str, Any] | None = None,
) -> ResolvedPermissions | None:
    """Resolve tool permissions for an agent.

    Args:
        agent_id: Agent identifier.
        agent_config: AgentConfig instance (or None if not loaded).
        central: Central permissions dict. If None, uses cached.

    Returns:
        ResolvedPermissions if the agent has tool access, None otherwise.
        None means the agent should use the legacy text-only path.
    """
    if central is None:
        central = get_central_permissions()

    # No central config -> no tools (backward compatible)
    if not central:
        return None

    profiles = central.get("tool_profiles", {})
    agent_defaults = central.get("agent_defaults", {})
    global_denied = set(central.get("denied_tools", []))
    mcp_server_defs = central.get("mcp_servers", {})

    # Extract per-agent tool_permissions
    agent_perms: dict[str, Any] = {}
    agent_type = "specialist"
    agent_group_key = ""
    if agent_config is not None:
        agent_perms = getattr(agent_config, "tool_permissions", {}) or {}
        agent_type = getattr(agent_config, "agent_type", "specialist") or "specialist"
        # Derive group key (e.g. "Core Developers" -> "core_developers")
        raw_group = getattr(agent_config, "group", "") or ""
        if raw_group:
            import re
            agent_group_key = re.sub(r"[^a-z0-9]+", "_", raw_group.lower()).strip("_")

    # Step 1: Determine profile name
    # Priority: agent override > group default > agent_type default > minimal
    profile_name = agent_perms.get("profile")
    if not profile_name and agent_group_key:
        profile_name = agent_defaults.get(agent_group_key)
    if not profile_name:
        profile_name = agent_defaults.get(agent_type, "minimal")

    # Step 2: Load profile
    profile = profiles.get(profile_name, profiles.get("minimal", {}))
    if not profile:
        return None

    # Step 3: Apply profile values
    allowed_tools = list(profile.get("allowed_tools", []))
    permission_mode = profile.get("permission_mode")
    max_turns = profile.get("max_turns", central.get("default_max_turns", 10))

    # Step 4: Apply per-agent overrides
    if agent_perms.get("allowed_tools_override") is not None:
        allowed_tools = list(agent_perms["allowed_tools_override"])

    if "permission_mode" in agent_perms:
        permission_mode = agent_perms["permission_mode"]

    if "max_turns" in agent_perms:
        max_turns = agent_perms["max_turns"]

    # Step 4.5: Apply central UI overrides (from dashboard gear icon)
    # These take precedence over agent_config.json overrides because the
    # dashboard is the operator's direct control surface.
    central_overrides = central.get("agent_overrides", {}).get(agent_id, {})
    if central_overrides.get("allowed_tools_override") is not None:
        allowed_tools = list(central_overrides["allowed_tools_override"])

    # Step 5: Subtract denied tools
    agent_denied = set(agent_perms.get("denied_tools", []))
    central_denied = set(central_overrides.get("denied_tools", []))
    agent_denied = agent_denied | central_denied
    all_denied = global_denied | agent_denied
    if all_denied:
        allowed_tools = [t for t in allowed_tools if t not in all_denied]

    # Step 6: Resolve MCP servers
    mcp_server_names = agent_perms.get("mcp_servers", [])
    resolved_mcp: list[dict[str, Any]] = []
    for name in mcp_server_names:
        server_def = mcp_server_defs.get(name)
        if server_def:
            resolved_mcp.append({
                "name": name,
                "command": server_def.get("command", "python"),
                "args": server_def.get("args", []),
            })
        else:
            logger.warning("[!] MCP server '%s' not found in central config", name)

    # Return None if no tools (backward compatible)
    if not allowed_tools:
        return None

    return ResolvedPermissions(
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        max_turns=max_turns,
        mcp_servers=resolved_mcp,
        profile_name=profile_name,
    )
