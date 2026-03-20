"""cohort memory / cohort teach -- agent memory CLI."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from cohort.cli._base import agent_not_found, format_output, resolve_agents_dir, truncation_notice


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_facts(facts: list, agent_id: str, limit: int) -> str:
    """Pretty-print a list of LearnedFact objects."""
    if not facts:
        return f"  {agent_id} has no learned facts."

    total = len(facts)
    shown = facts[:limit]

    lines: list[str] = [
        f"\n  Learned Facts for {agent_id} ({total} total)",
        "  " + "-" * 55,
    ]
    for i, f in enumerate(shown, 1):
        conf = getattr(f, "confidence", "medium")
        source = getattr(f, "learned_from", "") or getattr(f, "source", "") or ""
        ts = getattr(f, "timestamp", "") or getattr(f, "date_added", "") or ""
        ts_short = ts[:10] if ts else ""

        conf_marker = {"high": "[+++]", "medium": "[++]", "low": "[+]"}.get(conf, "[?]")
        lines.append(f"  {i:3d}. {conf_marker} {f.fact}")
        if source or ts_short:
            parts = []
            if source:
                parts.append(f"from: {source}")
            if ts_short:
                parts.append(ts_short)
            lines.append(f"       ({', '.join(parts)})")

    notice = truncation_notice(len(shown), total)
    if notice:
        lines.append(notice)

    return "\n".join(lines)


def _format_stats(stats: dict) -> str:
    """Pretty-print memory stats for an agent."""
    lines: list[str] = [
        f"\n  Memory Stats for {stats.get('agent_id', 'unknown')}",
        "  " + "-" * 40,
    ]
    lines.append(f"  Learned facts:    {stats.get('learned_facts_count', 0)}")
    lines.append(f"  Working memory:   {stats.get('working_memory_count', 0)}")
    lines.append(f"  Active tasks:     {stats.get('active_tasks_count', 0)}")
    lines.append(f"  Collaborators:    {stats.get('collaborators_count', 0)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_memory(args: argparse.Namespace) -> int:
    """Show agent memory (learned facts)."""
    from cohort.agent_store import AgentStore

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    agent = store.get(args.agent_id)
    if agent is None:
        agent = store.get_by_alias(args.agent_id)
    if agent is None:
        return agent_not_found(args.agent_id)

    memory = store.load_memory(agent.agent_id)
    if memory is None:
        print(f"  {agent.agent_id} has no memory file.")
        return 0

    json_flag = getattr(args, "json", False)
    sub = getattr(args, "memory_command", None)

    if sub == "clean":
        return _cmd_memory_clean(args)
    elif sub == "stats":
        from cohort.memory_manager import MemoryManager
        mgr = MemoryManager(store)
        stats = mgr.get_stats(agent.agent_id)
        if json_flag:
            format_output(stats, json_flag=True)
        else:
            print(_format_stats(stats))
    else:
        # Default: show facts
        limit = getattr(args, "limit", 20)
        facts = memory.learned_facts
        if json_flag:
            format_output(facts[:limit], json_flag=True)
        else:
            print(_format_facts(facts, agent.agent_id, limit))

    return 0


def _cmd_memory_clean(args: argparse.Namespace) -> int:
    """Trim agent working memory."""
    from cohort.agent_store import AgentStore
    from cohort.memory_manager import MemoryManager

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    agent = store.get(args.agent_id)
    if agent is None:
        agent = store.get_by_alias(args.agent_id)
    if agent is None:
        return agent_not_found(args.agent_id)

    keep = getattr(args, "keep", 10)
    dry_run = getattr(args, "dry_run", False)

    mgr = MemoryManager(store)
    result = mgr.clean_agent(agent.agent_id, keep_last=keep, dry_run=dry_run)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result.__dict__ if hasattr(result, "__dict__") else result, json_flag=True)
    else:
        prefix = "[DRY RUN] " if dry_run else ""
        print(f"  {prefix}Cleaned {agent.agent_id}:")
        print(f"    Working memory removed: {result.working_memory_removed}")
        print(f"    Working memory kept:    {result.working_memory_kept}")
        if result.archive_path:
            print(f"    Archived to: {result.archive_path}")

    return 0


def _cmd_teach(args: argparse.Namespace) -> int:
    """Add a learned fact to an agent."""
    from cohort.agent import LearnedFact
    from cohort.agent_store import AgentStore
    from cohort.memory_manager import MemoryManager

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    agent = store.get(args.agent_id)
    if agent is None:
        agent = store.get_by_alias(args.agent_id)
    if agent is None:
        return agent_not_found(args.agent_id)

    fact = LearnedFact(
        fact=args.fact,
        learned_from=getattr(args, "source", "cli") or "cli",
        timestamp=datetime.now(timezone.utc).isoformat(),
        confidence=getattr(args, "confidence", "medium") or "medium",
    )

    mgr = MemoryManager(store)
    mgr.add_learned_fact(agent.agent_id, fact)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(fact, json_flag=True)
    else:
        print(f"  [OK] Taught {agent.agent_id}: {args.fact[:60]}")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort memory`` and ``cohort teach`` commands."""

    # -- cohort memory <agent> [facts|stats] -------------------------------
    memory_parser = subparsers.add_parser("memory", help="View agent memory")
    memory_parser.add_argument("agent_id", help="Agent ID or alias")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")

    facts_parser = memory_sub.add_parser("facts", help="Show learned facts (default)")
    facts_parser.add_argument("--limit", type=int, default=20, help="Max facts (default: 20)")
    facts_parser.add_argument("--json", action="store_true", help="Output as JSON")

    stats_parser = memory_sub.add_parser("stats", help="Show memory statistics")
    stats_parser.add_argument("--json", action="store_true", help="Output as JSON")

    clean_parser = memory_sub.add_parser("clean", help="Trim working memory")
    clean_parser.add_argument("--keep", type=int, default=10, help="Entries to keep (default: 10)")
    clean_parser.add_argument("--dry-run", action="store_true", help="Preview without trimming")
    clean_parser.add_argument("--json", action="store_true", help="Output as JSON")

    memory_parser.add_argument("--limit", type=int, default=20, help="Max facts (default: 20)")
    memory_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # -- cohort teach <agent> "<fact>" -------------------------------------
    teach_parser = subparsers.add_parser("teach", help="Teach an agent a new fact")
    teach_parser.add_argument("agent_id", help="Agent ID or alias")
    teach_parser.add_argument("fact", help="The fact to teach (in quotes)")
    teach_parser.add_argument("--source", "-s", default="cli", help="Source of the fact (default: cli)")
    teach_parser.add_argument("--confidence", "-c", choices=["high", "medium", "low"],
                              default="medium", help="Confidence level (default: medium)")
    teach_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch memory/teach commands."""
    if args.command == "memory":
        return _cmd_memory(args)
    elif args.command == "teach":
        return _cmd_teach(args)
    return 1
