"""cohort schedule -- manage recurring task schedules."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output, resolve_data_dir, truncation_notice


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_schedule_list(schedules: list) -> str:
    """Pretty-print a list of TaskSchedule objects."""
    if not schedules:
        return "  No schedules found."

    lines: list[str] = [f"\n  Schedules ({len(schedules)})", "  " + "-" * 60]
    for s in schedules:
        enabled = "[ON]" if s.enabled else "[OFF]"
        streak = f" ({s.failure_streak} fails)" if s.failure_streak else ""
        runs = f"  runs: {s.run_count}" if s.run_count else ""
        desc = s.description[:50] + "..." if len(s.description) > 50 else s.description
        lines.append(f"  {enabled} {s.id[:8]}  {s.schedule_type:8s} {s.schedule_expr:15s}  {desc}")
        lines.append(f"         agent: {s.agent_id}  priority: {s.priority}{runs}{streak}")
        if s.next_run_at:
            lines.append(f"         next:  {s.next_run_at}")

    return "\n".join(lines)


def _format_schedule_detail(s) -> str:
    """Pretty-print a single schedule."""
    lines: list[str] = []
    lines.append(f"\n  Schedule: {s.id}")
    lines.append(f"  Agent:       {s.agent_id}")
    lines.append(f"  Description: {s.description}")
    lines.append(f"  Type:        {s.schedule_type}")
    lines.append(f"  Expression:  {s.schedule_expr}")
    lines.append(f"  Priority:    {s.priority}")
    lines.append(f"  Enabled:     {s.enabled}")
    lines.append(f"  Runs:        {s.run_count}")
    lines.append(f"  Last run:    {s.last_run_at or 'never'}")
    lines.append(f"  Last status: {s.last_status or 'n/a'}")
    lines.append(f"  Next run:    {s.next_run_at or 'not scheduled'}")
    lines.append(f"  Failures:    {s.failure_streak}/{s.max_failures}")
    lines.append(f"  Created:     {s.created_at}")
    lines.append(f"  Created by:  {s.created_by}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_schedule_list(args: argparse.Namespace) -> int:
    """List schedules."""
    from cohort.task_store import TaskStore

    data_dir = resolve_data_dir(args)
    ts = TaskStore(data_dir)
    enabled_only = getattr(args, "enabled", False)
    schedules = ts.list_schedules(enabled_only=enabled_only)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output([s.__dict__ for s in schedules], json_flag=True)
    else:
        print(_format_schedule_list(schedules))

    return 0


def _cmd_schedule_show(args: argparse.Namespace) -> int:
    """Show a single schedule."""
    from cohort.task_store import TaskStore

    data_dir = resolve_data_dir(args)
    ts = TaskStore(data_dir)
    sched = ts.get_schedule(args.schedule_id)

    if sched is None:
        # Try prefix match
        all_scheds = ts.list_schedules()
        matches = [s for s in all_scheds if s.id.startswith(args.schedule_id)]
        if len(matches) == 1:
            sched = matches[0]
        else:
            print(f"[X] Schedule '{args.schedule_id}' not found.", file=sys.stderr)
            return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(sched.__dict__, json_flag=True)
    else:
        print(_format_schedule_detail(sched))

    return 0


def _cmd_schedule_create(args: argparse.Namespace) -> int:
    """Create a new schedule."""
    from cohort.task_store import TaskStore

    data_dir = resolve_data_dir(args)
    ts = TaskStore(data_dir)

    try:
        sched = ts.create_schedule(
            agent_id=args.agent,
            description=args.description,
            schedule_type=args.type,
            schedule_expr=args.expr,
            priority=getattr(args, "priority", "medium") or "medium",
            created_by="cli",
        )
    except ValueError as e:
        print(f"[X] {e}", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(sched.__dict__, json_flag=True)
    else:
        print(f"  [OK] Created schedule: {sched.id[:8]}")
        print(f"       {sched.schedule_type} {sched.schedule_expr} -> {sched.agent_id}")
        if sched.next_run_at:
            print(f"       Next run: {sched.next_run_at}")

    return 0


def _cmd_schedule_toggle(args: argparse.Namespace) -> int:
    """Toggle a schedule's enabled state."""
    from cohort.task_store import TaskStore

    data_dir = resolve_data_dir(args)
    ts = TaskStore(data_dir)
    sched = ts.toggle_schedule(args.schedule_id)

    if sched is None:
        print(f"[X] Schedule '{args.schedule_id}' not found.", file=sys.stderr)
        return 1

    state = "enabled" if sched.enabled else "disabled"
    print(f"  [OK] Schedule {sched.id[:8]} {state}")
    return 0


def _cmd_schedule_delete(args: argparse.Namespace) -> int:
    """Delete a schedule."""
    from cohort.task_store import TaskStore

    data_dir = resolve_data_dir(args)
    ts = TaskStore(data_dir)
    ok = ts.delete_schedule(args.schedule_id)

    if not ok:
        print(f"[X] Schedule '{args.schedule_id}' not found.", file=sys.stderr)
        return 1

    print(f"  [OK] Schedule {args.schedule_id[:8]} deleted")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort schedule`` command group."""

    sched_parser = subparsers.add_parser("schedule", help="Manage recurring task schedules")
    sched_sub = sched_parser.add_subparsers(dest="schedule_command")

    # list
    list_parser = sched_sub.add_parser("list", help="List all schedules")
    list_parser.add_argument("--enabled", action="store_true", help="Only show enabled schedules")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.add_argument("--data-dir", default="data", help="Data directory")

    # show
    show_parser = sched_sub.add_parser("show", help="Show schedule details")
    show_parser.add_argument("schedule_id", help="Schedule ID (full or prefix)")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")
    show_parser.add_argument("--data-dir", default="data", help="Data directory")

    # create
    create_parser = sched_sub.add_parser("create", help="Create a new schedule")
    create_parser.add_argument("--agent", "-a", required=True, help="Agent ID to assign")
    create_parser.add_argument("--description", "-d", required=True, help="Task description")
    create_parser.add_argument("--type", "-t", choices=["once", "interval", "cron"], required=True,
                               help="Schedule type")
    create_parser.add_argument("--expr", "-e", required=True,
                               help="Schedule expression (seconds for interval, cron expr, or ISO date for once)")
    create_parser.add_argument("--priority", "-p", choices=["low", "medium", "high", "critical"],
                               default="medium", help="Priority (default: medium)")
    create_parser.add_argument("--json", action="store_true", help="Output as JSON")
    create_parser.add_argument("--data-dir", default="data", help="Data directory")

    # toggle
    toggle_parser = sched_sub.add_parser("toggle", help="Enable/disable a schedule")
    toggle_parser.add_argument("schedule_id", help="Schedule ID")
    toggle_parser.add_argument("--data-dir", default="data", help="Data directory")

    # delete
    delete_parser = sched_sub.add_parser("delete", help="Delete a schedule")
    delete_parser.add_argument("schedule_id", help="Schedule ID")
    delete_parser.add_argument("--data-dir", default="data", help="Data directory")

    # Default args for bare 'cohort schedule'
    sched_parser.add_argument("--json", action="store_true", help="Output as JSON")
    sched_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch schedule commands."""
    sub = getattr(args, "schedule_command", None)
    if sub == "list" or sub is None:
        return _cmd_schedule_list(args)
    elif sub == "show":
        return _cmd_schedule_show(args)
    elif sub == "create":
        return _cmd_schedule_create(args)
    elif sub == "toggle":
        return _cmd_schedule_toggle(args)
    elif sub == "delete":
        return _cmd_schedule_delete(args)
    else:
        print(f"Unknown schedule subcommand: {sub}", file=sys.stderr)
        return 1
