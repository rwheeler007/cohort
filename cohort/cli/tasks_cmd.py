"""cohort tasks list / cohort task <id> -- task management CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output, resolve_data_dir, truncation_notice

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

_STATUS_MARKERS = {
    "pending":    "[ ]",
    "assigned":   "[>]",
    "briefing":   "[...]",
    "executing":  "[>>]",
    "completed":  "[OK]",
    "failed":     "[X]",
    "archived":   "[--]",
}


def _format_task_list(tasks: list, limit: int) -> str:
    """Pretty-print a list of task dicts."""
    if not tasks:
        return "  No tasks found."

    total = len(tasks)
    shown = tasks[:limit]

    lines: list[str] = [f"\n  Tasks ({total} total)", "  " + "-" * 60]
    for t in shown:
        tid = t.get("id", "?")[:8]
        status = t.get("status", "pending")
        marker = _STATUS_MARKERS.get(status, "[?]")
        agent = t.get("agent_id", "")
        desc = t.get("description", "")
        if len(desc) > 55:
            desc = desc[:55] + "..."
        agent_str = f" -> {agent}" if agent else ""
        lines.append(f"  {marker} {tid}  {desc}{agent_str}")

    notice = truncation_notice(len(shown), total)
    if notice:
        lines.append(notice)

    return "\n".join(lines)


def _format_task_detail(task: dict) -> str:
    """Pretty-print a single task dict."""
    lines: list[str] = []
    lines.append(f"\n  Task: {task.get('id', '?')}")
    lines.append(f"  Status:      {task.get('status', 'unknown')}")
    lines.append(f"  Agent:       {task.get('agent_id', 'unassigned')}")
    lines.append(f"  Priority:    {task.get('priority', 'medium')}")
    lines.append(f"  Description: {task.get('description', '')}")
    lines.append(f"  Created:     {task.get('created_at', '')}")

    if task.get("schedule_id"):
        lines.append(f"  Schedule:    {task['schedule_id']}")

    output = task.get("output")
    if output:
        lines.append("\n  Output:")
        out_str = str(output)
        if len(out_str) > 500:
            out_str = out_str[:500] + "..."
        for ol in out_str.splitlines():
            lines.append(f"    {ol}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_tasks_list(args: argparse.Namespace) -> int:
    """List tasks."""
    from cohort.task_store import TaskStore

    data_dir = resolve_data_dir(args)
    ts = TaskStore(data_dir)

    status_filter = getattr(args, "status", None)
    limit = getattr(args, "limit", 20)
    tasks = ts.list_tasks(status_filter=status_filter, limit=limit)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(tasks, json_flag=True)
    else:
        print(_format_task_list(tasks, limit))

    return 0


def _cmd_task_show(args: argparse.Namespace) -> int:
    """Show a single task."""
    from cohort.task_store import TaskStore

    data_dir = resolve_data_dir(args)
    ts = TaskStore(data_dir)
    task = ts.get_task(args.task_id)

    if task is None:
        print(f"[X] Task '{args.task_id}' not found.", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(task, json_flag=True)
    else:
        print(_format_task_detail(task))

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort tasks`` and ``cohort task`` commands."""

    # -- cohort tasks list -------------------------------------------------
    tasks_parser = subparsers.add_parser("tasks", help="List tasks")
    tasks_sub = tasks_parser.add_subparsers(dest="tasks_command")

    list_parser = tasks_sub.add_parser("list", help="List all tasks")
    list_parser.add_argument("--status", default=None,
                             help="Filter by status (pending, assigned, executing, completed, failed)")
    list_parser.add_argument("--limit", type=int, default=20, help="Max tasks (default: 20)")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.add_argument("--data-dir", default="data", help="Data directory")

    tasks_parser.add_argument("--status", default=None, help="Filter by status")
    tasks_parser.add_argument("--limit", type=int, default=20, help="Max tasks (default: 20)")
    tasks_parser.add_argument("--json", action="store_true", help="Output as JSON")
    tasks_parser.add_argument("--data-dir", default="data", help="Data directory")

    # -- cohort task <id> --------------------------------------------------
    task_parser = subparsers.add_parser("task", help="Show task details")
    task_parser.add_argument("task_id", help="Task ID")
    task_parser.add_argument("--json", action="store_true", help="Output as JSON")
    task_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch tasks/task commands."""
    if args.command == "tasks":
        sub = getattr(args, "tasks_command", None)
        if sub == "list" or sub is None:
            return _cmd_tasks_list(args)
        else:
            print(f"Unknown tasks subcommand: {sub}", file=sys.stderr)
            return 1
    elif args.command == "task":
        return _cmd_task_show(args)
    return 1
