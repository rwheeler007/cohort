"""cohort benchmark -- A/B benchmark runner CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output, resolve_data_dir

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_scenarios(scenarios: list) -> str:
    """Pretty-print scenario list."""
    if not scenarios:
        return "  No benchmark scenarios defined."

    lines: list[str] = [
        f"\n  Benchmark Scenarios ({len(scenarios)})",
        "  " + "-" * 55,
    ]
    for s in scenarios:
        sid = s.get("id", "?")
        name = s.get("name", sid)
        agents = ", ".join(s.get("agents", []))
        lines.append(f"  {sid}")
        lines.append(f"    Name:   {name}")
        lines.append(f"    Agents: {agents}")
        criteria = s.get("criteria", [])
        if criteria:
            lines.append(f"    Criteria: {len(criteria)} rubric items")
    return "\n".join(lines)


def _format_runs(runs: list) -> str:
    """Pretty-print run list."""
    if not runs:
        return "  No benchmark runs found."

    lines: list[str] = [
        f"\n  Benchmark Runs ({len(runs)})",
        "  " + "-" * 60,
    ]
    for r in runs:
        rid = r.get("id", "?")[:12]
        scenario = r.get("scenario_id", "?")
        status = r.get("status", "?")
        started = r.get("started_at", "")[:16]
        status_icon = {"completed": "[OK]", "running": "[>>]", "pending": "[..]", "scored": "[++]"}.get(status, "[?]")
        lines.append(f"  {status_icon} {rid}  {scenario:<25s}  {status:<10s}  {started}")
    return "\n".join(lines)


def _format_run(run: dict) -> str:
    """Pretty-print a single run."""
    lines: list[str] = [
        f"\n  Benchmark Run: {run.get('id', '?')}",
        "  " + "-" * 55,
    ]
    lines.append(f"  Scenario:  {run.get('scenario_id', '?')}")
    lines.append(f"  Status:    {run.get('status', '?')}")
    lines.append(f"  Started:   {run.get('started_at', '?')}")
    lines.append(f"  Completed: {run.get('completed_at', '') or 'n/a'}")

    for arm_key in ("arm_a", "arm_b"):
        arm = run.get(arm_key, {})
        if arm:
            mode = arm.get("mode", "?")
            lines.append(f"\n  {arm_key.upper()} ({mode}):")
            responses = arm.get("responses", [])
            lines.append(f"    Responses: {len(responses)}")
            scores = arm.get("scores", {})
            if scores:
                for k, v in scores.items():
                    lines.append(f"    {k}: {v}")

    notes = run.get("notes", "")
    if notes:
        lines.append(f"\n  Notes: {notes}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_benchmark_scenarios(args: argparse.Namespace) -> int:
    """List available benchmark scenarios."""
    from cohort.benchmark import get_benchmark_runner

    data_dir = resolve_data_dir(args)
    runner = get_benchmark_runner(data_dir=data_dir)
    scenarios = runner.list_scenarios()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(scenarios, json_flag=True)
    else:
        print(_format_scenarios(scenarios))
    return 0


def _cmd_benchmark_runs(args: argparse.Namespace) -> int:
    """List recent benchmark runs."""
    from cohort.benchmark import get_benchmark_runner

    data_dir = resolve_data_dir(args)
    runner = get_benchmark_runner(data_dir=data_dir)
    limit = getattr(args, "limit", 20)
    runs = runner.list_runs(limit=limit)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(runs, json_flag=True)
    else:
        print(_format_runs(runs))
    return 0


def _cmd_benchmark_show(args: argparse.Namespace) -> int:
    """Show a specific benchmark run."""
    from cohort.benchmark import get_benchmark_runner

    data_dir = resolve_data_dir(args)
    runner = get_benchmark_runner(data_dir=data_dir)
    run = runner.get_run(args.run_id)

    if run is None:
        print(f"  [X] Run not found: {args.run_id}", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(run, json_flag=True)
    else:
        print(_format_run(run))
    return 0


def _cmd_benchmark_delete(args: argparse.Namespace) -> int:
    """Delete a benchmark run."""
    from cohort.benchmark import get_benchmark_runner

    data_dir = resolve_data_dir(args)
    runner = get_benchmark_runner(data_dir=data_dir)

    run = runner.get_run(args.run_id)
    if run is None:
        print(f"  [X] Run not found: {args.run_id}", file=sys.stderr)
        return 1

    runner._db.delete_run(args.run_id)
    if args.run_id in runner._runs:
        del runner._runs[args.run_id]
    print(f"  [OK] Deleted run: {args.run_id}")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort benchmark`` command group."""

    bench_parser = subparsers.add_parser("benchmark", help="A/B benchmark runner")
    bench_sub = bench_parser.add_subparsers(dest="benchmark_command")

    # scenarios
    sc_parser = bench_sub.add_parser("scenarios", help="List available scenarios")
    sc_parser.add_argument("--json", action="store_true", help="Output as JSON")
    sc_parser.add_argument("--data-dir", default="data", help="Data directory")

    # runs
    runs_parser = bench_sub.add_parser("runs", help="List recent runs")
    runs_parser.add_argument("--limit", type=int, default=20, help="Max runs (default: 20)")
    runs_parser.add_argument("--json", action="store_true", help="Output as JSON")
    runs_parser.add_argument("--data-dir", default="data", help="Data directory")

    # show
    show_parser = bench_sub.add_parser("show", help="Show a specific run")
    show_parser.add_argument("run_id", help="Run ID")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")
    show_parser.add_argument("--data-dir", default="data", help="Data directory")

    # delete
    del_parser = bench_sub.add_parser("delete", help="Delete a run")
    del_parser.add_argument("run_id", help="Run ID to delete")
    del_parser.add_argument("--data-dir", default="data", help="Data directory")

    bench_parser.add_argument("--json", action="store_true", help="Output as JSON")
    bench_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch benchmark commands."""
    sub = getattr(args, "benchmark_command", None)
    if sub == "scenarios" or sub is None:
        return _cmd_benchmark_scenarios(args)
    elif sub == "runs":
        return _cmd_benchmark_runs(args)
    elif sub == "show":
        return _cmd_benchmark_show(args)
    elif sub == "delete":
        return _cmd_benchmark_delete(args)
    else:
        print(f"Unknown benchmark subcommand: {sub}", file=sys.stderr)
        return 1
