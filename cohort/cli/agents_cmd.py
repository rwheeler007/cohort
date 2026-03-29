"""cohort agents list / cohort agent <name> -- agent discovery CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import agent_not_found, format_output, resolve_agents_dir

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_agent_list(agents: list, verbose: bool = False) -> str:
    """Pretty-print a list of AgentConfig objects."""
    if not agents:
        return "  No agents found."

    lines: list[str] = []
    # Group by agent_type
    groups: dict[str, list] = {}
    for a in agents:
        key = getattr(a, "agent_type", "specialist") or "specialist"
        groups.setdefault(key, []).append(a)

    for group_name in sorted(groups):
        lines.append(f"\n  {group_name.upper()} ({len(groups[group_name])})")
        lines.append("  " + "-" * 50)
        for a in sorted(groups[group_name], key=lambda x: x.agent_id):
            status = getattr(a, "status", "active")
            marker = "[*]" if status == "active" else f"[{status}]"
            line = f"  {marker} {a.agent_id:30s}  {a.role}"
            if verbose:
                caps = ", ".join(getattr(a, "capabilities", [])[:3])
                if caps:
                    line += f"\n      capabilities: {caps}"
            lines.append(line)

    lines.append(f"\n  Total: {len(agents)} agents")
    return "\n".join(lines)


def _format_agent_detail(agent) -> str:
    """Pretty-print a single AgentConfig."""
    lines: list[str] = []
    lines.append(f"\n  Agent: {agent.agent_id}")
    lines.append(f"  Name:  {agent.name}")
    lines.append(f"  Role:  {agent.role}")
    lines.append(f"  Type:  {getattr(agent, 'agent_type', 'specialist')}")
    lines.append(f"  Status: {getattr(agent, 'status', 'active')}")

    if agent.personality:
        lines.append(f"\n  Personality: {agent.personality}")

    caps = getattr(agent, "capabilities", [])
    if caps:
        lines.append(f"\n  Capabilities ({len(caps)}):")
        for c in caps:
            lines.append(f"    - {c}")

    expertise = getattr(agent, "domain_expertise", [])
    if expertise:
        lines.append(f"\n  Domain Expertise ({len(expertise)}):")
        for e in expertise:
            lines.append(f"    - {e}")

    triggers = getattr(agent, "triggers", [])
    if triggers:
        lines.append(f"\n  Triggers: {', '.join(triggers)}")

    skills = getattr(agent, "education", None)
    if skills and getattr(skills, "skill_levels", None):
        lines.append("\n  Skill Levels:")
        for skill, level in sorted(skills.skill_levels.items(), key=lambda x: -x[1]):
            lvl = min(level, 10)
            bar = "#" * lvl + "." * (10 - lvl)
            lines.append(f"    [{bar}] {level:2d}/10  {skill}")

    partnerships = getattr(agent, "partnerships", {})
    if partnerships:
        lines.append("\n  Partnerships:")
        for partner, info in partnerships.items():
            rel = info.get("relationship", "collaborator") if isinstance(info, dict) else str(info)
            lines.append(f"    - {partner}: {rel}")

    pitfalls = getattr(agent, "common_pitfalls", [])
    if pitfalls:
        lines.append("\n  Common Pitfalls:")
        for p in pitfalls[:5]:
            if isinstance(p, dict):
                issue = p.get("issue", p.get("mistake", p.get("pitfall", "")))
                desc = p.get("description", "")
                lines.append(f"    [!] {issue}")
                if desc:
                    lines.append(f"        {desc}")
                if p.get("solution"):
                    lines.append(f"        -> {p['solution']}")
            else:
                lines.append(f"    [!] {p}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_agents_list(args: argparse.Namespace) -> int:
    """List all agents."""
    from cohort.agent_store import AgentStore

    agents_dir = resolve_agents_dir()
    if not agents_dir.exists():
        print(f"[X] Agents directory not found: {agents_dir}", file=sys.stderr)
        return 2

    store = AgentStore(agents_dir=agents_dir)
    agents = store.list_agents(include_hidden=getattr(args, "all", False))

    json_flag = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)

    if json_flag:
        format_output(agents, json_flag=True)
    else:
        print(_format_agent_list(agents, verbose=verbose))

    return 0


def _cmd_agent_create(args: argparse.Namespace) -> int:
    """Create a new agent from CLI args."""
    from cohort.agent_creator import AgentCreator, AgentSpec, AgentType
    from cohort.agent_store import AgentStore

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)
    creator = AgentCreator(store)

    # Parse agent type
    type_map = {
        "specialist": AgentType.SPECIALIST,
        "orchestrator": AgentType.ORCHESTRATOR,
        "supervisor": AgentType.SUPERVISOR,
        "infrastructure": AgentType.INFRASTRUCTURE,
        "utility": AgentType.UTILITY,
    }
    agent_type = type_map.get(getattr(args, "type", "specialist") or "specialist", AgentType.SPECIALIST)

    capabilities = [c.strip() for c in args.capabilities.split(",")] if getattr(args, "capabilities", None) else []
    domain = [d.strip() for d in args.domain.split(",")] if getattr(args, "domain", None) else []
    triggers = [t.strip() for t in args.triggers.split(",")] if getattr(args, "triggers", None) else []

    spec = AgentSpec(
        name=args.name,
        role=args.role,
        primary_task=getattr(args, "task", "") or args.role,
        agent_type=agent_type,
        personality=getattr(args, "personality", "") or "",
        capabilities=capabilities,
        domain_expertise=domain,
        triggers=triggers,
    )

    try:
        config = creator.create_agent(spec)
    except ValueError as e:
        print(f"[X] {e}", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(config, json_flag=True)
    else:
        print(f"  [OK] Created agent: {config.agent_id}")
        print(f"       Name: {config.name}")
        print(f"       Role: {config.role}")
        print(f"       Type: {config.agent_type}")
        print(f"       Dir:  {agents_dir / config.agent_id}")

    return 0


def _cmd_agent_show(args: argparse.Namespace) -> int:
    """Show details for a single agent."""
    from cohort.agent_store import AgentStore

    agents_dir = resolve_agents_dir()
    if not agents_dir.exists():
        print(f"[X] Agents directory not found: {agents_dir}", file=sys.stderr)
        return 2

    store = AgentStore(agents_dir=agents_dir)
    agent = store.get(args.agent_id)

    if agent is None:
        # Try alias lookup
        agent = store.get_by_alias(args.agent_id)

    if agent is None:
        return agent_not_found(args.agent_id)

    # --prompt: show the full system prompt
    if getattr(args, "prompt", False):
        prompt_text = store.get_prompt(agent.agent_id)
        if prompt_text is None:
            print(f"  [X] No prompt file found for {agent.agent_id}", file=sys.stderr)
            return 1
        json_flag = getattr(args, "json", False)
        if json_flag:
            format_output({"agent_id": agent.agent_id, "prompt": prompt_text}, json_flag=True)
        else:
            print(prompt_text)
        return 0

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(agent, json_flag=True)
    else:
        print(_format_agent_detail(agent))

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort agents`` and ``cohort agent`` commands."""

    # -- cohort agents list ------------------------------------------------
    agents_parser = subparsers.add_parser(
        "agents", help="List available agents",
    )
    agents_sub = agents_parser.add_subparsers(dest="agents_command")

    list_parser = agents_sub.add_parser("list", help="List all agents")
    list_parser.add_argument("--all", action="store_true", help="Include hidden agents")
    list_parser.add_argument("--verbose", "-v", action="store_true", help="Show capabilities")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Default: 'cohort agents' with no subcommand = list
    agents_parser.add_argument("--all", action="store_true", help="Include hidden agents")
    agents_parser.add_argument("--verbose", "-v", action="store_true", help="Show capabilities")
    agents_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # -- cohort agents create -----------------------------------------------
    create_parser = agents_sub.add_parser("create", help="Create a new agent")
    create_parser.add_argument("--name", required=True, help="Agent display name (e.g. 'Data Analyst')")
    create_parser.add_argument("--role", required=True, help="One-line role description")
    create_parser.add_argument("--task", default="", help="Primary task description")
    create_parser.add_argument("--type", choices=["specialist", "orchestrator", "supervisor", "infrastructure", "utility"],
                               default="specialist", help="Agent type (default: specialist)")
    create_parser.add_argument("--personality", default="", help="Personality description")
    create_parser.add_argument("--capabilities", default="", help="Comma-separated capabilities")
    create_parser.add_argument("--domain", default="", help="Comma-separated domain expertise areas")
    create_parser.add_argument("--triggers", default="", help="Comma-separated trigger keywords")
    create_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # -- cohort agent <name> -----------------------------------------------
    agent_parser = subparsers.add_parser(
        "agent", help="Show agent details",
    )
    agent_parser.add_argument("agent_id", help="Agent ID or alias (e.g. python_developer)")
    agent_parser.add_argument("--prompt", action="store_true", help="Show the full system prompt")
    agent_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch agents/agent commands."""
    if args.command == "agents":
        sub = getattr(args, "agents_command", None)
        if sub == "list" or sub is None:
            return _cmd_agents_list(args)
        elif sub == "create":
            return _cmd_agent_create(args)
        else:
            print(f"Unknown agents subcommand: {sub}", file=sys.stderr)
            return 1

    elif args.command == "agent":
        return _cmd_agent_show(args)

    return 1
