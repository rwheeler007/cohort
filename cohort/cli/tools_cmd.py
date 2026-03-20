"""cohort tools -- tool permission inspection."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import agent_not_found, format_output, resolve_agents_dir


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_tools_permissions(args: argparse.Namespace) -> int:
    """Show tool permissions for an agent or all agents."""
    from cohort.agent_store import AgentStore
    from cohort.tool_permissions import resolve_permissions

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    agent_id = getattr(args, "agent_id", None)

    if agent_id:
        agent = store.get(agent_id)
        if agent is None:
            return agent_not_found(agent_id)
        agents_to_check = [agent]
    else:
        agents_to_check = store.list_agents()

    results: list[dict] = []
    for agent in agents_to_check:
        perms = resolve_permissions(agent.agent_id, agent)
        if perms is None:
            results.append({
                "agent_id": agent.agent_id,
                "profile": "none",
                "tools": [],
                "max_turns": 0,
            })
        else:
            results.append({
                "agent_id": agent.agent_id,
                "profile": perms.profile_name,
                "permission_mode": perms.permission_mode,
                "tools": perms.allowed_tools,
                "max_turns": perms.max_turns,
                "mcp_servers": len(perms.mcp_servers),
            })

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(results, json_flag=True)
    else:
        print(f"\n  Tool Permissions ({len(results)} agents)")
        print("  " + "-" * 60)
        for r in results:
            tools_str = ", ".join(r["tools"][:6])
            if len(r["tools"]) > 6:
                tools_str += f" (+{len(r['tools']) - 6} more)"
            profile = r.get("profile") or "legacy"
            print(f"  {r['agent_id']:25s}  [{profile:10s}]  {tools_str}")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort tools`` command."""

    tools_parser = subparsers.add_parser("tools", help="Tool permissions and configuration")
    tools_sub = tools_parser.add_subparsers(dest="tools_command")

    perms_parser = tools_sub.add_parser("permissions", help="Show tool permissions")
    perms_parser.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for all)")
    perms_parser.add_argument("--json", action="store_true", help="Output as JSON")

    tools_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch tools commands."""
    sub = getattr(args, "tools_command", None)
    if sub == "permissions" or sub is None:
        return _cmd_tools_permissions(args)
    else:
        print(f"Unknown tools subcommand: {sub}", file=sys.stderr)
        return 1
