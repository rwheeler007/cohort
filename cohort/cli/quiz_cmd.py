"""cohort quiz -- LinkedIn skill assessment quiz importer CLI wrapper."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _tool_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "tools" / "linkedin_quiz_importer.py"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_quiz_list(_args: argparse.Namespace) -> int:
    """List available LinkedIn quizzes."""
    return subprocess.run(
        [sys.executable, str(_tool_path()), "list"],
    ).returncode


def _cmd_quiz_preview(args: argparse.Namespace) -> int:
    """Preview parsed questions from a quiz."""
    return subprocess.run(
        [sys.executable, str(_tool_path()), "preview", args.quiz],
    ).returncode


def _cmd_quiz_import(args: argparse.Namespace) -> int:
    """Import quiz as agent assessment."""
    cmd = [sys.executable, str(_tool_path()), "import"]
    if getattr(args, "all", False):
        cmd.append("--all")
    else:
        cmd.append(args.agent)
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "force", False):
        cmd.append("--force")
    if getattr(args, "merge", False):
        cmd.append("--merge")
    return subprocess.run(cmd).returncode


def _cmd_quiz_status(_args: argparse.Namespace) -> int:
    """Show quiz import status."""
    return subprocess.run(
        [sys.executable, str(_tool_path()), "status"],
    ).returncode


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort quiz`` command group."""

    quiz_parser = subparsers.add_parser("quiz", help="LinkedIn skill assessment quiz importer")
    quiz_sub = quiz_parser.add_subparsers(dest="quiz_command")

    # list
    quiz_sub.add_parser("list", help="List available LinkedIn quizzes")

    # preview
    prev_p = quiz_sub.add_parser("preview", help="Preview parsed questions")
    prev_p.add_argument("quiz", help="Quiz name (e.g., python)")

    # import
    imp_p = quiz_sub.add_parser("import", help="Import quiz for an agent")
    imp_p.add_argument("agent", nargs="?", help="Agent ID (or --all)")
    imp_p.add_argument("--all", action="store_true", help="Import for all mapped agents")
    imp_p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    imp_p.add_argument("--force", action="store_true", help="Overwrite existing imports")
    imp_p.add_argument("--merge", action="store_true", help="Merge with existing assessment")

    # status
    quiz_sub.add_parser("status", help="Show import status")


def handle(args: argparse.Namespace) -> int:
    """Dispatch quiz commands."""
    if not _tool_path().exists():
        print(f"  [X] Tool not found: {_tool_path()}", file=sys.stderr)
        return 1

    sub = getattr(args, "quiz_command", None)
    if sub == "list" or sub is None:
        return _cmd_quiz_list(args)
    elif sub == "preview":
        return _cmd_quiz_preview(args)
    elif sub == "import":
        return _cmd_quiz_import(args)
    elif sub == "status":
        return _cmd_quiz_status(args)
    else:
        print(f"Unknown quiz subcommand: {sub}", file=sys.stderr)
        return 1
