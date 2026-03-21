"""cohort inject -- test conversation injector CLI wrapper."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _tool_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "tools" / "conversation_injector.py"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_inject_list(_args: argparse.Namespace) -> int:
    """List available conversation suites."""
    return subprocess.run(
        [sys.executable, str(_tool_path()), "--list"],
    ).returncode


def _cmd_inject_run(args: argparse.Namespace) -> int:
    """Inject conversations into Cohort."""
    cmd = [sys.executable, str(_tool_path())]

    suite = getattr(args, "suite", None)
    if suite:
        cmd.extend(["--suite", suite])

    mode = getattr(args, "mode", "live")
    cmd.extend(["--mode", mode])

    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")

    return subprocess.run(cmd).returncode


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort inject`` command group."""

    inj_parser = subparsers.add_parser("inject", help="Inject test conversations")
    inj_sub = inj_parser.add_subparsers(dest="inject_command")

    # list
    inj_sub.add_parser("list", help="List available conversation suites")

    # run
    run_p = inj_sub.add_parser("run", help="Inject conversations (default)")
    run_p.add_argument("--suite", help="Run specific suite only")
    run_p.add_argument("--mode", choices=["live", "offline"], default="live",
                       help="live = POST to server, offline = write to data files")
    run_p.add_argument("--dry-run", action="store_true", help="Show what would be sent")


def handle(args: argparse.Namespace) -> int:
    """Dispatch inject commands."""
    if not _tool_path().exists():
        print(f"  [X] Tool not found: {_tool_path()}", file=sys.stderr)
        return 1

    sub = getattr(args, "inject_command", None)
    if sub == "list":
        return _cmd_inject_list(args)
    elif sub == "run" or sub is None:
        return _cmd_inject_run(args)
    else:
        print(f"Unknown inject subcommand: {sub}", file=sys.stderr)
        return 1
