"""cohort briefing list / cohort briefing show -- browse past briefings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cohort.cli._base import format_output, resolve_data_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_briefing_engine(data_dir: Path):
    """Create an ExecutiveBriefing instance."""
    from cohort.chat import ChatManager
    from cohort.executive_briefing import ExecutiveBriefing
    from cohort.registry import create_storage

    storage = create_storage(data_dir)
    chat = ChatManager(storage)

    work_queue = None
    try:
        from cohort.work_queue import WorkQueue
        work_queue = WorkQueue(data_dir)
    except Exception:
        pass

    return ExecutiveBriefing(data_dir=data_dir, chat=chat, work_queue=work_queue)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_briefing_list(args: argparse.Namespace) -> int:
    """List recent briefings."""
    data_dir = resolve_data_dir(args)
    engine = _get_briefing_engine(data_dir)

    limit = getattr(args, "limit", 10)
    reports = engine.list_reports(limit=limit)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(reports, json_flag=True)
    else:
        if not reports:
            print("  No briefings found.")
        else:
            print(f"\n  Recent Briefings ({len(reports)})")
            print("  " + "-" * 55)
            for r in reports:
                gen = r.get("generated_at", "")[:16]
                period = f"{r.get('period_start', '')[:10]} to {r.get('period_end', '')[:10]}"
                print(f"  {gen}  {period}  id: {r.get('id', '?')[:8]}")

    return 0


def _cmd_briefing_html(args: argparse.Namespace) -> int:
    """Show path to latest HTML briefing."""
    data_dir = resolve_data_dir(args)
    engine = _get_briefing_engine(data_dir)

    html_path = engine.get_latest_html()
    if html_path is None:
        print("  No HTML briefings found.", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({"path": str(html_path)}, json_flag=True)
    else:
        print(f"  Latest HTML briefing: {html_path}")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort briefings`` command."""

    # We use 'briefings' (plural) to avoid conflict with existing 'briefing'
    bp = subparsers.add_parser("briefings", help="Browse past briefing reports")
    bp_sub = bp.add_subparsers(dest="briefings_command")

    list_parser = bp_sub.add_parser("list", help="List recent briefings")
    list_parser.add_argument("--limit", type=int, default=10, help="Max reports (default: 10)")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.add_argument("--data-dir", default="data", help="Data directory")

    html_parser = bp_sub.add_parser("html", help="Show path to latest HTML briefing")
    html_parser.add_argument("--json", action="store_true", help="Output as JSON")
    html_parser.add_argument("--data-dir", default="data", help="Data directory")

    bp.add_argument("--json", action="store_true", help="Output as JSON")
    bp.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch briefings commands."""
    sub = getattr(args, "briefings_command", None)
    if sub == "list" or sub is None:
        return _cmd_briefing_list(args)
    elif sub == "html":
        return _cmd_briefing_html(args)
    else:
        print(f"Unknown briefings subcommand: {sub}", file=sys.stderr)
        return 1
