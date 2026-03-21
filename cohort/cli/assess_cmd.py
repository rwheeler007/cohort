"""cohort assess -- agent assessment browsing and reporting CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cohort.cli._base import agent_not_found, format_output, resolve_agents_dir


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _assessments_dir() -> Path:
    return _project_root() / "data" / "assessments"


def _results_dir() -> Path:
    return _project_root() / "data" / "assessment_results"


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_agents_list(agents: list, results_available: dict) -> str:
    """Pretty-print agents with assessment availability."""
    lines: list[str] = [
        f"\n  Agent Assessments ({len(agents)})",
        "  " + "-" * 60,
    ]
    for a in sorted(agents):
        has_questions = (_assessments_dir() / a).is_dir()
        has_results = a in results_available
        q_icon = "[Q]" if has_questions else "[--]"
        r_icon = "[R]" if has_results else "[--]"

        score = ""
        if has_results:
            result = results_available[a]
            pct = result.get("score_pct", 0)
            score = f"  {pct:.0f}%"

        lines.append(f"  {q_icon}{r_icon} {a:<30s}{score}")

    lines.append("\n  Legend: [Q] = questions available, [R] = results available")
    return "\n".join(lines)


def _format_result(agent_id: str, result: dict) -> str:
    """Pretty-print assessment result for one agent."""
    lines: list[str] = [
        f"\n  Assessment Results: {agent_id}",
        "  " + "-" * 55,
    ]

    score = result.get("score_pct", 0)
    total = result.get("total", 0)
    correct = result.get("correct", 0)
    lines.append(f"  Score:     {score:.1f}% ({correct}/{total})")

    model = result.get("model", "?")
    lines.append(f"  Model:     {model}")

    timestamp = result.get("timestamp", "?")
    lines.append(f"  Timestamp: {timestamp[:16] if len(timestamp) > 16 else timestamp}")

    # By difficulty
    by_diff = result.get("by_difficulty", {})
    if by_diff:
        lines.append("\n  By Difficulty:")
        for diff, stats in sorted(by_diff.items()):
            if isinstance(stats, dict):
                d_total = stats.get("total", 0)
                d_correct = stats.get("correct", 0)
                d_pct = (d_correct / d_total * 100) if d_total else 0
                bar_len = 15
                filled = int(d_pct / 100 * bar_len)
                bar = "#" * filled + "." * (bar_len - filled)
                lines.append(f"    [{bar}] {d_pct:5.1f}%  {diff} ({d_correct}/{d_total})")

    # By category
    by_cat = result.get("by_category", {})
    if by_cat:
        lines.append("\n  By Category:")
        for cat, stats in sorted(by_cat.items()):
            if isinstance(stats, dict):
                c_total = stats.get("total", 0)
                c_correct = stats.get("correct", 0)
                c_pct = (c_correct / c_total * 100) if c_total else 0
                lines.append(f"    {c_pct:5.1f}%  {cat} ({c_correct}/{c_total})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_assess_list(args: argparse.Namespace) -> int:
    """List agents with assessment data."""
    from cohort.agent_store import AgentStore

    agents_dir = resolve_agents_dir()
    store = AgentStore(agents_dir=agents_dir)
    all_agents = [a.agent_id for a in store.list_agents()]

    # Check for results
    results_available = {}
    results_dir = _results_dir()
    if results_dir.exists():
        for result_file in results_dir.glob("*_results.json"):
            agent_id = result_file.stem.replace("_results", "")
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
                results_available[agent_id] = data
            except Exception:
                pass

    json_flag = getattr(args, "json", False)
    if json_flag:
        data = []
        for a in sorted(all_agents):
            has_q = (_assessments_dir() / a).is_dir()
            result = results_available.get(a)
            data.append({
                "agent_id": a,
                "has_questions": has_q,
                "has_results": a in results_available,
                "score": result.get("score_pct") if result else None,
            })
        format_output(data, json_flag=True)
    else:
        print(_format_agents_list(all_agents, results_available))
    return 0


def _cmd_assess_show(args: argparse.Namespace) -> int:
    """Show assessment results for an agent."""
    agent_id = args.agent_id
    results_dir = _results_dir()

    # Try multiple result file patterns
    result_file = results_dir / f"{agent_id}_results.json"
    if not result_file.exists():
        # Try subdirectories (model-specific)
        for subdir in results_dir.iterdir() if results_dir.exists() else []:
            if subdir.is_dir():
                candidate = subdir / f"{agent_id}_results.json"
                if candidate.exists():
                    result_file = candidate
                    break

    if not result_file.exists():
        print(f"  [X] No assessment results for: {agent_id}", file=sys.stderr)
        print(f"      Run: python tools/agent_assessor.py {agent_id}", file=sys.stderr)
        return 1

    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [X] Failed to load results: {e}", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        print(_format_result(agent_id, result))
    return 0


def _cmd_assess_summary(args: argparse.Namespace) -> int:
    """Show summary of all assessment results."""
    results_dir = _results_dir()

    if not results_dir.exists():
        print("  No assessment results found.")
        return 0

    results = []
    # Check root and subdirectories
    for pattern_dir in [results_dir] + [d for d in results_dir.iterdir() if d.is_dir()]:
        for rf in pattern_dir.glob("*_results.json"):
            agent_id = rf.stem.replace("_results", "")
            try:
                data = json.loads(rf.read_text(encoding="utf-8"))
                results.append({
                    "agent_id": agent_id,
                    "score": data.get("score_pct", 0),
                    "model": data.get("model", "?"),
                    "total": data.get("total", 0),
                    "dir": pattern_dir.name,
                })
            except Exception:
                pass

    if not results:
        print("  No assessment results found.")
        return 0

    # Deduplicate (latest per agent)
    seen = {}
    for r in results:
        key = r["agent_id"]
        if key not in seen or r.get("dir", "") > seen[key].get("dir", ""):
            seen[key] = r
    results = sorted(seen.values(), key=lambda x: -x["score"])

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(results, json_flag=True)
    else:
        print(f"\n  Assessment Summary ({len(results)} agents)")
        print("  " + "-" * 55)
        for r in results:
            score = r["score"]
            bar_len = 15
            filled = int(score / 100 * bar_len)
            bar = "#" * filled + "." * (bar_len - filled)
            passed = "[OK]" if score >= 70 else "[X]"
            print(f"  {passed} [{bar}] {score:5.1f}%  {r['agent_id']}")
        avg = sum(r["score"] for r in results) / len(results)
        print(f"\n  Average: {avg:.1f}%  Model: {results[0].get('model', '?')}")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort assess`` command group."""

    assess_parser = subparsers.add_parser("assess", help="Agent assessment results")
    assess_sub = assess_parser.add_subparsers(dest="assess_command")

    # list
    list_p = assess_sub.add_parser("list", help="List agents with assessments")
    list_p.add_argument("--json", action="store_true", help="Output as JSON")

    # show
    show_p = assess_sub.add_parser("show", help="Show results for an agent")
    show_p.add_argument("agent_id", help="Agent ID")
    show_p.add_argument("--json", action="store_true", help="Output as JSON")

    # summary
    sum_p = assess_sub.add_parser("summary", help="Summary of all assessment results")
    sum_p.add_argument("--json", action="store_true", help="Output as JSON")

    assess_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch assess commands."""
    sub = getattr(args, "assess_command", None)
    if sub == "list":
        return _cmd_assess_list(args)
    elif sub == "show":
        return _cmd_assess_show(args)
    elif sub == "summary" or sub is None:
        return _cmd_assess_summary(args)
    else:
        print(f"Unknown assess subcommand: {sub}", file=sys.stderr)
        return 1
