"""cohort overnight -- overnight benchmark and assessment retest runners."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _tools_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "tools"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_overnight_benchmark(args: argparse.Namespace) -> int:
    """Run A/B benchmark overnight (Smart vs Smarter vs Smartest)."""
    script = _tools_dir() / "run_ab_benchmark_overnight.py"
    if not script.exists():
        print(f"  [X] Script not found: {script}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(script)]
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "scenarios", None):
        cmd.extend(["--scenarios", args.scenarios])

    print("  [>>] Starting overnight A/B benchmark...")
    return subprocess.run(cmd).returncode


def _cmd_overnight_retest(args: argparse.Namespace) -> int:
    """Re-test underperforming agents overnight."""
    script = _tools_dir() / "run_assessment_retest.py"
    if not script.exists():
        print(f"  [X] Script not found: {script}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(script)]
    if getattr(args, "threshold", None):
        cmd.extend(["--threshold", str(args.threshold)])
    if getattr(args, "model", None):
        cmd.extend(["--model", args.model])
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")

    print("  [>>] Starting overnight assessment retest...")
    return subprocess.run(cmd).returncode


def _cmd_overnight_assess(args: argparse.Namespace) -> int:
    """Run full agent assessment suite."""
    script = _tools_dir() / "agent_assessor.py"
    if not script.exists():
        print(f"  [X] Script not found: {script}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(script)]

    agent = getattr(args, "agent", None)
    if agent:
        cmd.append(agent)

    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "resume", False):
        cmd.append("--resume")
    if getattr(args, "report", False):
        cmd.append("--report")
    if getattr(args, "limit", None):
        cmd.extend(["--limit", str(args.limit)])
    if getattr(args, "difficulty", None):
        cmd.extend(["--difficulty", args.difficulty])
    if getattr(args, "model_name", None):
        cmd.extend(["--model", args.model_name])
    if getattr(args, "linkedin", False):
        cmd.append("--linkedin")

    return subprocess.run(cmd).returncode


def _cmd_overnight_import_benchmarks(args: argparse.Namespace) -> int:
    """Import benchmark datasets (CodeMMLU, CyberMetric, MMLU-Pro)."""
    script = _tools_dir() / "benchmark_importer.py"
    if not script.exists():
        print(f"  [X] Script not found: {script}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(script)]

    action = getattr(args, "action", "status")
    cmd.append(action)

    if action == "import":
        source = getattr(args, "source", None)
        if source:
            cmd.append(source)
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "force", False):
        cmd.append("--force")

    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort overnight`` command group."""

    on_parser = subparsers.add_parser("overnight", help="Overnight runners: benchmarks, retests, assessments")
    on_sub = on_parser.add_subparsers(dest="overnight_command")

    # benchmark (A/B)
    ab_p = on_sub.add_parser("benchmark", help="Run A/B benchmark overnight")
    ab_p.add_argument("--dry-run", action="store_true", help="Preview without running")
    ab_p.add_argument("--scenarios", help="Comma-separated scenario IDs")

    # retest
    rt_p = on_sub.add_parser("retest", help="Re-test underperforming agents")
    rt_p.add_argument("--threshold", type=int, help="Score threshold for retest (default: 90)")
    rt_p.add_argument("--model", help="Model to test with")
    rt_p.add_argument("--dry-run", action="store_true", help="Preview without running")

    # assess (full assessment run)
    as_p = on_sub.add_parser("assess", help="Run full agent assessment")
    as_p.add_argument("agent", nargs="?", help="Agent ID (default: all agents)")
    as_p.add_argument("--dry-run", action="store_true", help="Show questions without calling model")
    as_p.add_argument("--resume", action="store_true", help="Resume interrupted run")
    as_p.add_argument("--report", action="store_true", help="Show last results without running")
    as_p.add_argument("--limit", type=int, help="Max questions per agent")
    as_p.add_argument("--difficulty", choices=["intermediate", "advanced", "expert"],
                       help="Filter by difficulty")
    as_p.add_argument("--model", dest="model_name", help="Ollama model name")
    as_p.add_argument("--linkedin", action="store_true", help="Use LinkedIn assessment bank")

    # import-benchmarks
    ib_p = on_sub.add_parser("import-benchmarks", help="Import benchmark datasets")
    ib_p.add_argument("action", nargs="?", default="status",
                      choices=["list", "import", "status"],
                      help="Action (default: status)")
    ib_p.add_argument("source", nargs="?", help="Source to import (codemmlu, cybermetric, mmlu-pro, all)")
    ib_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    ib_p.add_argument("--force", action="store_true", help="Overwrite existing imports")


def handle(args: argparse.Namespace) -> int:
    """Dispatch overnight commands."""
    sub = getattr(args, "overnight_command", None)
    if sub == "benchmark":
        return _cmd_overnight_benchmark(args)
    elif sub == "retest":
        return _cmd_overnight_retest(args)
    elif sub == "assess":
        return _cmd_overnight_assess(args)
    elif sub == "import-benchmarks":
        return _cmd_overnight_import_benchmarks(args)
    elif sub is None:
        print("  Usage: python -m cohort overnight {benchmark|retest|assess|import-benchmarks}")
        print()
        print("  benchmark          Run A/B benchmark overnight (Smart vs Smarter vs Smartest)")
        print("  retest             Re-test agents below score threshold")
        print("  assess [agent]     Run full 100-question assessment")
        print("  import-benchmarks  Import CodeMMLU/CyberMetric/MMLU-Pro datasets")
        return 0
    else:
        print(f"Unknown overnight subcommand: {sub}", file=sys.stderr)
        return 1
