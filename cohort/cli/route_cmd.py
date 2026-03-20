"""cohort route -- find the best agent for a task."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output, resolve_agents_dir


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_route_results(results: list, task: str) -> str:
    """Pretty-print ranked agent matches."""
    lines: list[str] = [f"\n  Best agents for: {task}", "  " + "-" * 55]

    if not results:
        lines.append("  No matching agents found.")
        return "\n".join(lines)

    for i, (agent, score) in enumerate(results, 1):
        pct = int(score * 100)
        bar_len = 20
        filled = int(score * bar_len)
        bar = "#" * filled + "." * (bar_len - filled)
        triggers = ", ".join(agent.triggers[:5]) if agent.triggers else ""
        lines.append(f"  {i}. [{bar}] {pct:3d}%  {agent.agent_id}")
        lines.append(f"     {agent.role}")
        if triggers:
            lines.append(f"     triggers: {triggers}")

    return "\n".join(lines)


def _format_partnerships(agent_id: str, consultations: list) -> str:
    """Pretty-print required consultations."""
    lines: list[str] = [f"\n  Partnerships for {agent_id}", "  " + "-" * 45]

    if not consultations:
        lines.append("  No required consultations.")
        return "\n".join(lines)

    for c in consultations:
        partner = c.get("partner", "unknown") if isinstance(c, dict) else str(c)
        reason = c.get("reason", "") if isinstance(c, dict) else ""
        lines.append(f"  -> {partner}")
        if reason:
            lines.append(f"     {reason}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_route(args: argparse.Namespace) -> int:
    """Find the best agent for a task description."""
    from cohort.agent_store import AgentStore
    from cohort.capability_router import find_agents_for_topic

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)
    all_agents = store.list_agents()

    max_results = getattr(args, "limit", 5)
    prefer_type = getattr(args, "type", None)

    results = find_agents_for_topic(
        all_agents,
        args.task,
        max_results=max_results,
        prefer_type=prefer_type,
    )

    json_flag = getattr(args, "json", False)
    if json_flag:
        data = [
            {"agent_id": a.agent_id, "role": a.role, "score": round(s, 4)}
            for a, s in results
        ]
        format_output(data, json_flag=True)
    else:
        print(_format_route_results(results, args.task))

    return 0


def _cmd_partnerships(args: argparse.Namespace) -> int:
    """Show partnership requirements for an agent on a task."""
    from cohort.agent_store import AgentStore
    from cohort.capability_router import find_required_consultations

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    agent = store.get(args.agent_id)
    if agent is None:
        from cohort.cli._base import agent_not_found
        return agent_not_found(args.agent_id)

    all_agents = store.list_agents()
    task = getattr(args, "task", "") or ""

    from cohort.capability_router import _extract_keywords
    task_kw = _extract_keywords(task) if task else []

    consultations = find_required_consultations(agent, task_kw, all_agents)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(consultations, json_flag=True)
    else:
        print(_format_partnerships(args.agent_id, consultations))

    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    """Show the full agent partnership graph."""
    from cohort.agent_store import AgentStore
    from cohort.capability_router import build_partnership_graph

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)
    all_agents = store.list_agents()

    graph = build_partnership_graph(all_agents)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(graph, json_flag=True)
    else:
        if not graph:
            print("  No partnerships configured.")
            return 0

        # Only show agents that have partnerships
        active = {k: v for k, v in graph.items() if v}
        print(f"\n  Partnership Graph ({len(active)} agents with partnerships)")
        print("  " + "-" * 55)
        for agent_id in sorted(active):
            edges = active[agent_id]
            partners = ", ".join(
                f"{e.get('partner', '?')} ({e.get('relationship', 'partner')})"
                for e in edges
            )
            print(f"  {agent_id}")
            for e in edges:
                rel = e.get("relationship", "partner")
                partner = e.get("partner_id", e.get("partner", "?"))
                print(f"    -> {partner:25s}  ({rel})")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort route``, ``cohort partnerships``, ``cohort graph``."""

    # route
    route_parser = subparsers.add_parser(
        "route", help="Find the best agent for a task",
    )
    route_parser.add_argument("task", help="Task description (natural language)")
    route_parser.add_argument("--limit", type=int, default=5, help="Max results (default: 5)")
    route_parser.add_argument("--type", choices=["specialist", "orchestrator", "supervisor", "infrastructure"],
                              default=None, help="Prefer a specific agent type")
    route_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # partnerships
    partner_parser = subparsers.add_parser(
        "partnerships", help="Show partnership requirements for an agent",
    )
    partner_parser.add_argument("agent_id", help="Agent ID")
    partner_parser.add_argument("--task", default="", help="Task context for consultation routing")
    partner_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # graph
    graph_parser = subparsers.add_parser(
        "graph", help="Show the full agent partnership graph",
    )
    graph_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch route/partnerships/graph commands."""
    if args.command == "route":
        return _cmd_route(args)
    elif args.command == "partnerships":
        return _cmd_partnerships(args)
    elif args.command == "graph":
        return _cmd_graph(args)
    return 1
