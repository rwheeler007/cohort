"""cohort meet -- stakeholder gating, contribution scoring, and relevance CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import agent_not_found, format_output, resolve_agents_dir


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_stakeholders(stakeholders: list[str], scores: dict[str, float], topic: str) -> str:
    """Pretty-print stakeholder list with scores."""
    if not stakeholders:
        return f"  No stakeholders found for: {topic}"

    lines: list[str] = [
        f"\n  Stakeholders for: {topic} ({len(stakeholders)} agents)",
        "  " + "-" * 55,
    ]
    # Sort by score descending
    ranked = sorted(stakeholders, key=lambda a: scores.get(a, 0), reverse=True)
    for i, agent_id in enumerate(ranked, 1):
        score = scores.get(agent_id, 0)
        pct = score * 100
        bar_len = 15
        filled = int(pct / 100 * bar_len)
        bar = "#" * filled + "." * (bar_len - filled)
        lines.append(f"  {i:3d}. [{bar}] {pct:5.1f}%  {agent_id}")
    return "\n".join(lines)


def _format_relevance(score: float, agent_id: str, topic: str) -> str:
    """Pretty-print expertise relevance score."""
    pct = score * 100
    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "#" * filled + "." * (bar_len - filled)
    lines: list[str] = [
        f"\n  Expertise Relevance for {agent_id}",
        "  " + "-" * 50,
        f"  Topic: {topic}",
        f"  Score: [{bar}] {pct:.1f}%",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_meet_stakeholders(args: argparse.Namespace) -> int:
    """Identify stakeholders for a topic."""
    from cohort.agent_store import AgentStore
    from cohort.meeting import (
        identify_stakeholders_for_topic,
        extract_keywords,
        calculate_expertise_relevance,
    )

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    topic = args.topic
    threshold = getattr(args, "threshold", 0.15)
    topic_keywords = extract_keywords(topic)

    all_agents = store.list_agents()
    agents_dict = {}
    for a in all_agents:
        agents_dict[a.agent_id] = a.to_dict()

    results = identify_stakeholders_for_topic(
        topic_keywords=topic_keywords,
        agents=agents_dict,
        relevance_threshold=threshold,
    )

    # Calculate scores for display
    scores = {}
    for agent_id in results:
        config = agents_dict.get(agent_id, {})
        scores[agent_id] = calculate_expertise_relevance(config, topic_keywords)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output([{"agent_id": a, "score": scores.get(a, 0)} for a in results], json_flag=True)
    else:
        print(_format_stakeholders(results, scores, topic))
    return 0


def _cmd_meet_relevance(args: argparse.Namespace) -> int:
    """Calculate expertise relevance for an agent against a topic."""
    from cohort.agent_store import AgentStore
    from cohort.meeting import calculate_expertise_relevance, extract_keywords

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)

    agent = store.get(args.agent_id)
    if agent is None:
        agent = store.get_by_alias(args.agent_id)
    if agent is None:
        return agent_not_found(args.agent_id)

    topic = args.topic
    topic_keywords = extract_keywords(topic)
    agent_config = agent.to_dict()

    score = calculate_expertise_relevance(agent_config, topic_keywords)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({"agent_id": agent.agent_id, "topic": topic, "score": score}, json_flag=True)
    else:
        print(_format_relevance(score, agent.agent_id, topic))
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort meet`` command group."""

    meet_parser = subparsers.add_parser("meet", help="Stakeholder gating and relevance scoring")
    meet_sub = meet_parser.add_subparsers(dest="meet_command")

    # stakeholders
    sh_parser = meet_sub.add_parser("stakeholders", help="Identify stakeholders for a topic")
    sh_parser.add_argument("topic", help="Topic or question")
    sh_parser.add_argument("--threshold", type=float, default=0.15,
                           help="Minimum relevance threshold (default: 0.15)")
    sh_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # relevance
    rel_parser = meet_sub.add_parser("relevance", help="Show expertise relevance for an agent")
    rel_parser.add_argument("agent_id", help="Agent ID")
    rel_parser.add_argument("topic", help="Topic or question")
    rel_parser.add_argument("--json", action="store_true", help="Output as JSON")

    meet_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch meet commands."""
    sub = getattr(args, "meet_command", None)
    if sub == "stakeholders":
        return _cmd_meet_stakeholders(args)
    elif sub == "relevance":
        return _cmd_meet_relevance(args)
    elif sub is None:
        print("  Usage: python -m cohort meet {stakeholders|relevance}")
        return 0
    else:
        print(f"Unknown meet subcommand: {sub}", file=sys.stderr)
        return 1
