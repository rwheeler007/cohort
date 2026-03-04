"""Cohort CLI entry point.

Usage::

    python -m cohort serve                  # start HTTP server
    python -m cohort serve --port 8080      # custom port
    python -m cohort serve --data-dir /tmp  # custom data directory

    python -m cohort say --sender architect --channel review --file conv.jsonl --message "Hello"
    python -m cohort gate --agent architect --channel review --file conv.jsonl --agents agents.json
    python -m cohort next-speaker --channel review --file conv.jsonl --agents agents.json
"""

import argparse
import json
import sys
from typing import Any


# =====================================================================
# CLI command handlers
# =====================================================================

def _cmd_gate(args: argparse.Namespace) -> int:
    """Should this agent respond? Exit 0 = speak, exit 1 = don't."""
    from cohort.chat import Channel, ChatManager
    from cohort.file_transport import JsonlFileStorage, load_agents_from_file
    from cohort.meeting import (
        STAKEHOLDER_THRESHOLDS,
        StakeholderStatus,
        calculate_contribution_score,
        extract_keywords,
        initialize_meeting_context,
        should_agent_speak,
    )

    storage = JsonlFileStorage(args.file)
    chat = ChatManager(storage)
    agents = load_agents_from_file(args.agents)

    if args.agent not in agents:
        print(f"[X] Agent '{args.agent}' not found in {args.agents}", file=sys.stderr)
        return 2

    messages = chat.get_channel_messages(args.channel, limit=15)
    if not messages:
        print(f"[X] No messages in channel '{args.channel}'", file=sys.stderr)
        return 1

    # Build meeting context from agents
    meeting_ctx = initialize_meeting_context(list(agents.keys()))
    recent_text = " ".join(m.content for m in messages[-5:])
    topic_kw = extract_keywords(recent_text)
    meeting_ctx["current_topic"]["keywords"] = topic_kw

    # Build or update channel
    channel = chat.get_channel(args.channel)
    if channel is None:
        channel = Channel(
            id=args.channel, name=args.channel, description="",
            created_at="", meeting_context=meeting_ctx,
        )
    else:
        channel.meeting_context = meeting_ctx

    agent_config = agents[args.agent]
    last_message = messages[-1]
    status = StakeholderStatus.ACTIVE.value
    threshold = STAKEHOLDER_THRESHOLDS[status]

    speak = should_agent_speak(
        args.agent, last_message, channel, chat, agent_config,
    )
    score = calculate_contribution_score(
        args.agent, "[considering response]",
        meeting_ctx, agent_config, messages,
    )

    decision = "SPEAK" if speak else "SILENT"
    reason = (
        f"score {score:.2f} >= threshold {threshold:.2f}"
        if speak
        else f"score {score:.2f} < threshold {threshold:.2f}"
    )

    if args.format == "json":
        print(json.dumps({
            "agent": args.agent,
            "score": round(score, 4),
            "threshold": threshold,
            "status": status,
            "speak": speak,
            "reason": reason,
        }))
    else:
        print(f"Agent:     {args.agent}")
        print(f"Score:     {score:.2f}")
        print(f"Threshold: {threshold:.2f} ({status})")
        print(f"Decision:  {decision}")
        print(f"Reason:    {reason}")

    return 0 if speak else 1


def _cmd_next_speaker(args: argparse.Namespace) -> int:
    """Who should talk next? Ranked by composite relevance."""
    from cohort.chat import ChatManager
    from cohort.file_transport import JsonlFileStorage, load_agents_from_file
    from cohort.meeting import (
        calculate_composite_relevance,
        extract_keywords,
        initialize_meeting_context,
    )

    storage = JsonlFileStorage(args.file)
    chat = ChatManager(storage)
    agents = load_agents_from_file(args.agents)

    messages = chat.get_channel_messages(args.channel, limit=15)
    if not messages:
        print(f"[X] No messages in channel '{args.channel}'", file=sys.stderr)
        return 1

    # Build meeting context
    meeting_ctx = initialize_meeting_context(list(agents.keys()))
    recent_text = " ".join(m.content for m in messages[-5:])
    topic_kw = extract_keywords(recent_text)
    meeting_ctx["current_topic"]["keywords"] = topic_kw

    # Score all agents
    scores: list[dict[str, Any]] = []
    for agent_id, agent_config in agents.items():
        relevance = calculate_composite_relevance(
            agent_id=agent_id,
            meeting_context=meeting_ctx,
            agent_config=agent_config,
            recent_messages=messages,
        )
        scores.append({
            "agent_id": agent_id,
            "score": relevance["composite_total"],
            "phase": relevance.get("detected_phase", "unknown"),
            "breakdown": {
                k: round(v, 3) for k, v in relevance.items()
                if k not in ("composite_total", "detected_phase")
            },
        })
    scores.sort(key=lambda x: x["score"], reverse=True)
    top = scores[: args.top]

    if args.format == "json":
        print(json.dumps(top, indent=2))
    else:
        print(f"Next speaker for channel '{args.channel}':")
        for i, entry in enumerate(top, 1):
            bd = entry["breakdown"]
            top_dims = sorted(bd.items(), key=lambda x: x[1], reverse=True)[:2]
            dims_str = ", ".join(f"{k}={v:.2f}" for k, v in top_dims)
            print(
                f"  {i}. {entry['agent_id']:20s}  "
                f"score={entry['score']:.2f}  "
                f"phase={entry['phase']}  ({dims_str})"
            )

    return 0


def _cmd_say(args: argparse.Namespace) -> int:
    """Append a message to the conversation."""
    from cohort.chat import ChatManager
    from cohort.file_transport import JsonlFileStorage

    storage = JsonlFileStorage(args.file)
    chat = ChatManager(storage)

    # Auto-create channel if needed
    if chat.get_channel(args.channel) is None:
        chat.create_channel(name=args.channel, description=args.channel)

    msg = chat.post_message(
        channel_id=args.channel,
        sender=args.sender,
        content=args.message,
    )
    print(f"[OK] {msg.id} -> #{args.channel}")
    return 0


# =====================================================================
# Main entry point
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="cohort -- multi-agent orchestration")
    sub = parser.add_subparsers(dest="command")

    # -- serve ----------------------------------------------------------
    serve_parser = sub.add_parser("serve", help="Start the HTTP server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    serve_parser.add_argument("--port", type=int, default=5100, help="Port")
    serve_parser.add_argument("--data-dir", default="data", help="Data directory")

    # -- gate -----------------------------------------------------------
    gate_parser = sub.add_parser("gate", help="Check if an agent should respond")
    gate_parser.add_argument("--agent", required=True, help="Agent ID to check")
    gate_parser.add_argument("--channel", required=True, help="Channel ID")
    gate_parser.add_argument("--file", required=True, help="Path to conversation .jsonl file")
    gate_parser.add_argument("--agents", required=True, help="Path to agents.json config")
    gate_parser.add_argument(
        "--format", choices=["json", "text"], default="text", help="Output format"
    )

    # -- next-speaker ---------------------------------------------------
    next_parser = sub.add_parser("next-speaker", help="Recommend who should talk next")
    next_parser.add_argument("--channel", required=True, help="Channel ID")
    next_parser.add_argument("--file", required=True, help="Path to conversation .jsonl file")
    next_parser.add_argument("--agents", required=True, help="Path to agents.json config")
    next_parser.add_argument("--top", type=int, default=3, help="Number of speakers to show")
    next_parser.add_argument(
        "--format", choices=["json", "text"], default="text", help="Output format"
    )

    # -- say ------------------------------------------------------------
    say_parser = sub.add_parser("say", help="Append a message to the conversation")
    say_parser.add_argument("--sender", required=True, help="Sender agent ID")
    say_parser.add_argument("--channel", required=True, help="Channel ID")
    say_parser.add_argument("--file", required=True, help="Path to conversation .jsonl file")
    say_parser.add_argument("--message", required=True, help="Message content")

    # -- serve-agents ---------------------------------------------------
    sa_parser = sub.add_parser(
        "serve-agents", help="Start the Agent API server (agent-as-a-service)"
    )
    sa_parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    sa_parser.add_argument("--port", type=int, default=8200, help="Port")
    sa_parser.add_argument("--agents-dir", default=None, help="Path to agents directory")

    # -- setup ------------------------------------------------------
    sub.add_parser(
        "setup",
        help="Interactive setup wizard (Ollama + model + content feeds)",
    )

    args = parser.parse_args()

    if args.command == "setup":
        from cohort.local.setup import run_setup

        sys.exit(run_setup())
    elif args.command == "serve":
        from cohort.server import serve

        print(f"[*] cohort server starting on {args.host}:{args.port}")
        print(f"[*] data dir: {args.data_dir}")
        serve(host=args.host, port=args.port, data_dir=args.data_dir)
    elif args.command == "gate":
        sys.exit(_cmd_gate(args))
    elif args.command == "next-speaker":
        sys.exit(_cmd_next_speaker(args))
    elif args.command == "say":
        sys.exit(_cmd_say(args))
    elif args.command == "serve-agents":
        from cohort.agent_api import serve_agents

        print(f"[*] cohort agent API starting on {args.host}:{args.port}")
        if args.agents_dir:
            print(f"[*] agents dir: {args.agents_dir}")
        serve_agents(host=args.host, port=args.port, agents_dir=args.agents_dir)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
