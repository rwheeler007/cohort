"""cohort discuss -- multi-agent roundtable from the CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import agent_not_found, format_output, require_ollama, resolve_agents_dir


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_result(result) -> str:
    """Pretty-print a CompiledResult."""
    lines: list[str] = []

    meta = getattr(result, "metadata", {}) or {}
    model = meta.get("model", "unknown")
    latency = meta.get("latency_ms", 0)
    lines.append(f"\n  Roundtable Discussion  (model: {model}, {latency}ms)")
    lines.append("  " + "=" * 60)

    agent_responses = getattr(result, "agent_responses", {})
    for agent_id, response in agent_responses.items():
        lines.append(f"\n  > {agent_id}:")
        # Indent response
        for rline in response.strip().splitlines():
            lines.append(f"    {rline}")

    synthesis = getattr(result, "synthesis", None)
    if synthesis:
        lines.append(f"\n  {'=' * 60}")
        lines.append("  SYNTHESIS:")
        for sline in synthesis.strip().splitlines():
            lines.append(f"    {sline}")

    error = getattr(result, "error", None)
    if error:
        lines.append(f"\n  [X] Error: {error}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def _cmd_discuss(args: argparse.Namespace) -> int:
    """Run a compiled roundtable discussion."""
    if not require_ollama():
        return 2

    from cohort.agent_store import AgentStore

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    # Parse agent list
    agent_ids = [a.strip() for a in args.agents.split(",")]

    # Validate agents exist
    for aid in agent_ids:
        agent = store.get(aid)
        if agent is None:
            agent = store.get_by_alias(aid)
        if agent is None:
            return agent_not_found(aid)

    if len(agent_ids) < 2:
        print("[X] Need at least 2 agents for a discussion.", file=sys.stderr)
        return 1
    if len(agent_ids) > 8:
        print("[X] Maximum 8 agents per discussion.", file=sys.stderr)
        return 1

    rounds = getattr(args, "rounds", 2)
    topic = args.topic
    context = getattr(args, "context", "") or ""

    print(f"  Starting discussion with {len(agent_ids)} agents...")
    print(f"  Topic: {topic}")
    print(f"  Agents: {', '.join(agent_ids)}")
    print(f"  Rounds: {rounds}")
    print()

    from cohort.compiled_roundtable import run_compiled_roundtable

    result = run_compiled_roundtable(
        agents=agent_ids,
        topic=topic,
        context=context,
        rounds=rounds,
        temperature=getattr(args, "temperature", 0.30),
    )

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        print(_format_result(result))

    return 0 if not getattr(result, "error", None) else 1


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort discuss`` command."""

    discuss_parser = subparsers.add_parser(
        "discuss", help="Run a multi-agent roundtable discussion (requires Ollama)",
    )
    discuss_parser.add_argument("topic", help="Discussion topic")
    discuss_parser.add_argument(
        "--agents", "-a", required=True,
        help="Comma-separated agent IDs (e.g. ceo_agent,python_developer,security_agent)",
    )
    discuss_parser.add_argument("--rounds", "-r", type=int, default=2, help="Discussion rounds (default: 2)")
    discuss_parser.add_argument("--context", "-c", default="", help="Additional context for the discussion")
    discuss_parser.add_argument("--temperature", "-t", type=float, default=0.30, help="LLM temperature (default: 0.30)")
    discuss_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch discuss command."""
    return _cmd_discuss(args)
