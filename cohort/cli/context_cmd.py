"""cohort context -- agent context inspection and channel hydration CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import agent_not_found, format_output, resolve_agents_dir


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_context_agent(args: argparse.Namespace) -> int:
    """Show what an agent sees about itself (memory, facts, profile)."""
    from cohort.agent_store import AgentStore
    from cohort.agent_context import load_agent_context

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    agent = store.get(args.agent_id)
    if agent is None:
        agent = store.get_by_alias(args.agent_id)
    if agent is None:
        return agent_not_found(args.agent_id)

    query = getattr(args, "query", "") or ""
    context = load_agent_context(agent.agent_id, query=query, agent_store=store)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({"agent_id": agent.agent_id, "context": context}, json_flag=True)
    else:
        print(f"\n  Agent Context for {agent.agent_id}")
        print("  " + "=" * 55)
        # Truncate if very long
        limit = getattr(args, "limit", 2000)
        if len(context) > limit:
            print(context[:limit])
            print(f"\n  [...truncated at {limit} chars, use --limit to see more]")
        else:
            print(context)
    return 0


def _cmd_context_user(args: argparse.Namespace) -> int:
    """Show the user profile block as seen by agents."""
    from cohort.agent_context import load_user_profile_block

    block = load_user_profile_block()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({"profile_block": block}, json_flag=True)
    else:
        if not block:
            print("  No user profile loaded.")
            print("  Create one: python -m cohort learn bootstrap --name 'Your Name' --role 'Your Role'")
        else:
            print(f"\n  User Profile Block ({len(block)} chars)")
            print("  " + "-" * 50)
            print(block)
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort context`` command group."""

    ctx_parser = subparsers.add_parser("context", help="Agent context inspection")
    ctx_sub = ctx_parser.add_subparsers(dest="context_command")

    # agent context
    agent_p = ctx_sub.add_parser("agent", help="Show agent's full context block")
    agent_p.add_argument("agent_id", help="Agent ID or alias")
    agent_p.add_argument("--query", "-q", help="Optional query to weight context retrieval")
    agent_p.add_argument("--limit", type=int, default=2000, help="Max chars to show (default: 2000)")
    agent_p.add_argument("--json", action="store_true", help="Output as JSON")

    # user profile block
    user_p = ctx_sub.add_parser("user", help="Show user profile as seen by agents")
    user_p.add_argument("--json", action="store_true", help="Output as JSON")

    ctx_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch context commands."""
    sub = getattr(args, "context_command", None)
    if sub == "agent":
        return _cmd_context_agent(args)
    elif sub == "user":
        return _cmd_context_user(args)
    elif sub is None:
        print("  Usage: python -m cohort context {agent|user}")
        return 0
    else:
        print(f"Unknown context subcommand: {sub}", file=sys.stderr)
        return 1
