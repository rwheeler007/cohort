"""cohort meet -- meeting mode, stakeholder gating, and session control CLI.

Offline commands (no server needed):
    stakeholders, relevance

Server commands (require Cohort server on port 5100):
    start, stop, pause, resume, status, promote, demote, add, remove,
    next, score, phase, extend, enable, disable
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

from cohort.cli._base import agent_not_found, format_output, require_server, resolve_agents_dir

# ---------------------------------------------------------------------------
# HTTP helper (thin, synchronous -- CLI doesn't need asyncio)
# ---------------------------------------------------------------------------

_BASE = "http://localhost:5100"


def _api(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make an HTTP request to the Cohort server and return parsed JSON."""
    url = f"{_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read())
        except Exception:
            err = {"error": str(exc)}
        return {"success": False, **err}
    except urllib.error.URLError:
        return {"success": False, "error": "Server not reachable"}


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


def _format_session_status(status: dict[str, Any]) -> str:
    """Pretty-print session status."""
    p = status.get("participants", {})
    active = p.get("active", [])
    silent = p.get("silent", [])
    dormant = p.get("dormant", [])
    kw = status.get("current_topic_keywords", [])

    lines = [
        "",
        f"  Session: {status.get('session_id', '?')}",
        f"  Channel: #{status.get('channel_id', '?')}    State: {status.get('state', '?').upper()}    Phase: {status.get('phase', '?')}",
        f"  Topic: {status.get('topic', '?')}",
        f"  Turn: {status.get('current_turn', 0)} / {status.get('max_turns', 20)}",
        f"  Keywords: {', '.join(kw[:5]) if kw else '(none)'}",
        "  " + "-" * 55,
    ]
    if active:
        lines.append(f"  ACTIVE ({len(active)}):     {', '.join(active)}")
    if silent:
        lines.append(f"  SILENT ({len(silent)}):     {', '.join(silent)}")
    if dormant:
        lines.append(f"  DORMANT ({len(dormant)}):    {', '.join(dormant)}")
    contributed = status.get("contributed", [])
    if contributed:
        lines.append(f"  Contributed:    {', '.join(contributed)}")
    return "\n".join(lines)


def _format_score(score: dict[str, Any]) -> str:
    """Pretty-print agent score breakdown."""
    dims = score.get("dimensions", {})
    lines = [
        "",
        f"  Agent: {score.get('agent_id', '?')}   Session: {score.get('session_id', '?')}",
        f"  Status: {score.get('status', '?')}   Phase: {score.get('phase', '?')}",
        f"  Composite: {score.get('composite_total', 0):.3f}",
        "  " + "-" * 45,
    ]
    for dim, val in dims.items():
        pct = float(val) * 100
        bar_len = 15
        filled = int(pct / 100 * bar_len)
        bar = "#" * filled + "." * (bar_len - filled)
        lines.append(f"  [{bar}] {pct:5.1f}%  {dim}")
    return "\n".join(lines)


def _format_next_speaker(rec: dict[str, Any]) -> str:
    """Pretty-print next speaker recommendation."""
    lines = [
        "",
        f"  Recommended: {rec.get('recommended_speaker', '?')}",
        f"  Score: {rec.get('relevance_score', 0):.3f}   Phase: {rec.get('phase', '?')}",
        f"  Reason: {rec.get('reason', '?')}",
    ]
    alts = rec.get("alternatives", [])
    if alts:
        lines.append(f"  Alternatives: {', '.join(alts)}")
    return "\n".join(lines)


def _format_phase(data: dict[str, Any]) -> str:
    """Pretty-print phase detection result."""
    lines = [
        "",
        f"  Phase: {data.get('phase', '?')}",
    ]
    evidence = data.get("evidence", [])
    if evidence:
        lines.append("  Evidence:")
        for e in evidence[:5]:
            kw = ", ".join(e.get("keywords", [])[:5])
            lines.append(f"    {e.get('sender', '?')}: {kw}")
    return "\n".join(lines)


def _format_meeting_context(ctx: dict[str, Any] | None) -> str:
    """Pretty-print meeting context."""
    if not ctx:
        return "  Meeting mode: OFF"
    ss = ctx.get("stakeholder_status", {})
    topic = ctx.get("current_topic", {})
    kw = topic.get("keywords", [])
    primary = topic.get("primary_stakeholders", [])
    lines = [
        "",
        "  Meeting mode: ON",
        f"  Keywords: {', '.join(kw[:5]) if kw else '(none)'}",
        f"  Primary: {', '.join(primary) if primary else '(none)'}",
        "  " + "-" * 45,
    ]
    for agent_id, status in ss.items():
        lines.append(f"  {status:25s}  {agent_id}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Offline command handlers (no server needed)
# ---------------------------------------------------------------------------

def _cmd_meet_stakeholders(args: argparse.Namespace) -> int:
    """Identify stakeholders for a topic."""
    from cohort.agent_store import AgentStore
    from cohort.meeting import (
        calculate_expertise_relevance,
        extract_keywords,
        identify_stakeholders_for_topic,
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
# Server command handlers
# ---------------------------------------------------------------------------

def _require(args: argparse.Namespace) -> bool:
    """Check server is up. Returns False (and prints error) if not."""
    return require_server(port=5100)


def _json_flag(args: argparse.Namespace) -> bool:
    return getattr(args, "json", False)


def _cmd_start(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    agents = args.agents.split(",") if hasattr(args, "agents") and args.agents else None
    body: dict[str, Any] = {
        "channel_id": args.channel,
        "topic": args.topic,
        "max_turns": getattr(args, "turns", 20),
    }
    if agents:
        body["initial_agents"] = agents
    result = _api("POST", "/api/sessions/start", body)
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        session = result.get("session", {})
        print(f"  [OK] Session started: {session.get('session_id', '?')}")
        print(f"  Channel: #{args.channel}  Topic: {args.topic}")
        parts = session.get("initial_agents", [])
        if parts:
            print(f"  Participants: {', '.join(parts)}")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("POST", f"/api/sessions/{args.session_id}/end")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print("  [OK] Session ended.")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_pause(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("POST", f"/api/sessions/{args.session_id}/pause")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print("  [OK] Session paused.")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("POST", f"/api/sessions/{args.session_id}/resume")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print("  [OK] Session resumed.")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    identifier = args.identifier
    # Try as session_id first (starts with s_), else try as channel_id
    if identifier.startswith("s_"):
        result = _api("GET", f"/api/sessions/{identifier}/status")
        data = result.get("status", result)
    else:
        result = _api("GET", f"/api/sessions/channel/{identifier}")
        if result.get("has_session"):
            session = result.get("session", {})
            session_id = session.get("session_id")
            if session_id:
                result2 = _api("GET", f"/api/sessions/{session_id}/status")
                data = result2.get("status", result2)
            else:
                data = session
        else:
            print(f"  No active session in #{identifier}")
            return 0

    if _json_flag(args):
        format_output(data, json_flag=True)
    elif data.get("session_id") or data.get("state"):
        print(_format_session_status(data))
    else:
        print(f"  [X] {data.get('error', 'Session not found')}", file=sys.stderr)
        return 1
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api(
        "PUT",
        f"/api/sessions/{args.session_id}/participants/{args.agent}/status",
        {"status": "active"},
    )
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(f"  [OK] {args.agent} -> ACTIVE")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_demote(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    target = getattr(args, "to", "silent") or "silent"
    result = _api(
        "PUT",
        f"/api/sessions/{args.session_id}/participants/{args.agent}/status",
        {"status": target},
    )
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(f"  [OK] {args.agent} -> {target.upper()}")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api(
        "POST",
        f"/api/sessions/{args.session_id}/participants",
        {"agent_id": args.agent},
    )
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(f"  [OK] {args.agent} added to session.")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("DELETE", f"/api/sessions/{args.session_id}/participants/{args.agent}")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(f"  [OK] {args.agent} removed from session.")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_next(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("GET", f"/api/sessions/{args.session_id}/next-speaker")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        rec = result.get("recommendation", result)
        print(_format_next_speaker(rec))
    else:
        print(f"  [X] {result.get('error', 'No speaker available')}", file=sys.stderr)
        return 1
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("GET", f"/api/sessions/{args.session_id}/score/{args.agent}")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(_format_score(result.get("score", result)))
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_phase(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("GET", f"/api/channels/{args.channel}/phase")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(_format_phase(result))
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_extend(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    turns = getattr(args, "turns", 10) or 10
    result = _api("POST", f"/api/sessions/{args.session_id}/extend", {"turns": turns})
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(f"  [OK] Extended by {turns} turns.")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_enable(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    agents = args.agents.split(",") if hasattr(args, "agents") and args.agents else []
    if not agents:
        print("  [X] --agents is required for enable.", file=sys.stderr)
        return 1
    topic = getattr(args, "topic", "") or ""
    result = _api(
        "POST",
        f"/api/channels/{args.channel}/meeting-mode",
        {"agents": agents, "topic": topic},
    )
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(f"  [OK] Meeting mode enabled on #{args.channel}")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    if not _require(args):
        return 1
    result = _api("DELETE", f"/api/channels/{args.channel}/meeting-mode")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        was = result.get("was_active", False)
        if was:
            print(f"  [OK] Meeting mode disabled on #{args.channel}")
        else:
            print(f"  Meeting mode was already off on #{args.channel}")
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    """Show meeting context for a channel."""
    if not _require(args):
        return 1
    result = _api("GET", f"/api/channels/{args.channel}/meeting-context")
    if _json_flag(args):
        format_output(result, json_flag=True)
    elif result.get("success"):
        print(_format_meeting_context(result.get("meeting_context")))
    else:
        print(f"  [X] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort meet`` command group."""

    meet_parser = subparsers.add_parser("meet", help="Meeting mode, stakeholder gating, and session control")
    meet_sub = meet_parser.add_subparsers(dest="meet_command")

    # -- Offline commands --------------------------------------------------

    # stakeholders
    sh_parser = meet_sub.add_parser("stakeholders", help="Identify stakeholders for a topic (offline)")
    sh_parser.add_argument("topic", help="Topic or question")
    sh_parser.add_argument("--threshold", type=float, default=0.15,
                           help="Minimum relevance threshold (default: 0.15)")
    sh_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # relevance
    rel_parser = meet_sub.add_parser("relevance", help="Show expertise relevance for an agent (offline)")
    rel_parser.add_argument("agent_id", help="Agent ID")
    rel_parser.add_argument("topic", help="Topic or question")
    rel_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # -- Session lifecycle -------------------------------------------------

    # start
    start_p = meet_sub.add_parser("start", help="Start a meeting session")
    start_p.add_argument("channel", help="Channel ID")
    start_p.add_argument("topic", help="Discussion topic")
    start_p.add_argument("--agents", help="Comma-separated agent IDs (auto-selects if omitted)")
    start_p.add_argument("--turns", type=int, default=20, help="Max turns (default: 20)")
    start_p.add_argument("--json", action="store_true", help="Output as JSON")

    # stop
    stop_p = meet_sub.add_parser("stop", help="End a meeting session")
    stop_p.add_argument("session_id", help="Session ID")
    stop_p.add_argument("--json", action="store_true", help="Output as JSON")

    # pause
    pause_p = meet_sub.add_parser("pause", help="Pause a meeting session")
    pause_p.add_argument("session_id", help="Session ID")
    pause_p.add_argument("--json", action="store_true", help="Output as JSON")

    # resume
    resume_p = meet_sub.add_parser("resume", help="Resume a paused meeting session")
    resume_p.add_argument("session_id", help="Session ID")
    resume_p.add_argument("--json", action="store_true", help="Output as JSON")

    # status
    status_p = meet_sub.add_parser("status", help="Show session status (accepts session_id or channel_id)")
    status_p.add_argument("identifier", help="Session ID (s_...) or channel ID")
    status_p.add_argument("--json", action="store_true", help="Output as JSON")

    # extend
    ext_p = meet_sub.add_parser("extend", help="Add more turns to a session")
    ext_p.add_argument("session_id", help="Session ID")
    ext_p.add_argument("--turns", type=int, default=10, help="Turns to add (default: 10)")
    ext_p.add_argument("--json", action="store_true", help="Output as JSON")

    # -- Participant management --------------------------------------------

    # promote
    prom_p = meet_sub.add_parser("promote", help="Set agent to ACTIVE stakeholder")
    prom_p.add_argument("session_id", help="Session ID")
    prom_p.add_argument("agent", help="Agent ID")
    prom_p.add_argument("--json", action="store_true", help="Output as JSON")

    # demote
    dem_p = meet_sub.add_parser("demote", help="Demote agent stakeholder status")
    dem_p.add_argument("session_id", help="Session ID")
    dem_p.add_argument("agent", help="Agent ID")
    dem_p.add_argument("--to", choices=["silent", "observer", "dormant"], default="silent",
                       help="Target status (default: silent)")
    dem_p.add_argument("--json", action="store_true", help="Output as JSON")

    # add
    add_p = meet_sub.add_parser("add", help="Add participant to session")
    add_p.add_argument("session_id", help="Session ID")
    add_p.add_argument("agent", help="Agent ID")
    add_p.add_argument("--json", action="store_true", help="Output as JSON")

    # remove
    rem_p = meet_sub.add_parser("remove", help="Remove participant from session")
    rem_p.add_argument("session_id", help="Session ID")
    rem_p.add_argument("agent", help="Agent ID")
    rem_p.add_argument("--json", action="store_true", help="Output as JSON")

    # -- Scoring & introspection -------------------------------------------

    # next
    next_p = meet_sub.add_parser("next", help="Recommended next speaker with scores")
    next_p.add_argument("session_id", help="Session ID")
    next_p.add_argument("--json", action="store_true", help="Output as JSON")

    # score
    score_p = meet_sub.add_parser("score", help="Full composite relevance breakdown")
    score_p.add_argument("session_id", help="Session ID")
    score_p.add_argument("agent", help="Agent ID")
    score_p.add_argument("--json", action="store_true", help="Output as JSON")

    # phase
    phase_p = meet_sub.add_parser("phase", help="Detect discussion phase (DISCOVER/PLAN/EXECUTE/VALIDATE)")
    phase_p.add_argument("channel", help="Channel ID")
    phase_p.add_argument("--json", action="store_true", help="Output as JSON")

    # context
    ctx_p = meet_sub.add_parser("context", help="Show meeting context for a channel")
    ctx_p.add_argument("channel", help="Channel ID")
    ctx_p.add_argument("--json", action="store_true", help="Output as JSON")

    # -- Standalone meeting mode -------------------------------------------

    # enable
    en_p = meet_sub.add_parser("enable", help="Enable meeting mode on a channel (no session)")
    en_p.add_argument("channel", help="Channel ID")
    en_p.add_argument("--agents", required=True, help="Comma-separated agent IDs")
    en_p.add_argument("--topic", default="", help="Optional topic for keyword scoring")
    en_p.add_argument("--json", action="store_true", help="Output as JSON")

    # disable
    dis_p = meet_sub.add_parser("disable", help="Disable meeting mode on a channel")
    dis_p.add_argument("channel", help="Channel ID")
    dis_p.add_argument("--json", action="store_true", help="Output as JSON")

    meet_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch meet commands."""
    sub = getattr(args, "meet_command", None)

    _dispatch = {
        "stakeholders": _cmd_meet_stakeholders,
        "relevance": _cmd_meet_relevance,
        "start": _cmd_start,
        "stop": _cmd_stop,
        "pause": _cmd_pause,
        "resume": _cmd_resume,
        "status": _cmd_status,
        "promote": _cmd_promote,
        "demote": _cmd_demote,
        "add": _cmd_add,
        "remove": _cmd_remove,
        "next": _cmd_next,
        "score": _cmd_score,
        "phase": _cmd_phase,
        "extend": _cmd_extend,
        "enable": _cmd_enable,
        "disable": _cmd_disable,
        "context": _cmd_context,
    }

    if sub in _dispatch:
        return _dispatch[sub](args)
    elif sub is None:
        cmds = "|".join(sorted(_dispatch.keys()))
        print(f"  Usage: python -m cohort meet {{{cmds}}}")
        return 0
    else:
        print(f"Unknown meet subcommand: {sub}", file=sys.stderr)
        return 1
