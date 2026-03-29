"""cohort queue list / cohort queue add -- work queue CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output, resolve_data_dir, truncation_notice

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

_PRIORITY_MARKERS = {
    "critical": "[!!!]",
    "high":     "[!!]",
    "medium":   "[!]",
    "low":      "[.]",
}

_STATUS_MARKERS = {
    "queued":    "[ ]",
    "active":    "[>>]",
    "completed": "[OK]",
    "failed":    "[X]",
    "cancelled": "[--]",
}


def _format_queue_list(items: list, limit: int) -> str:
    """Pretty-print a list of WorkItem objects."""
    if not items:
        return "  Work queue is empty."

    total = len(items)
    shown = items[:limit]

    lines: list[str] = [f"\n  Work Queue ({total} items)", "  " + "-" * 60]
    for item in shown:
        pri = _PRIORITY_MARKERS.get(item.priority, "[?]")
        sts = _STATUS_MARKERS.get(item.status, "[?]")
        agent = f" -> {item.agent_id}" if item.agent_id else ""
        desc = item.description[:60] + "..." if len(item.description) > 60 else item.description
        lines.append(f"  {sts} {pri} {item.id[:8]}  {desc}{agent}")

    notice = truncation_notice(len(shown), total)
    if notice:
        lines.append(notice)

    return "\n".join(lines)


def _format_queue_item(item) -> str:
    """Pretty-print a single WorkItem."""
    lines: list[str] = []
    lines.append(f"\n  ID:          {item.id}")
    lines.append(f"  Status:      {item.status}")
    lines.append(f"  Priority:    {item.priority}")
    lines.append(f"  Requester:   {item.requester}")
    lines.append(f"  Description: {item.description}")
    if item.agent_id:
        lines.append(f"  Assigned to: {item.agent_id}")
    lines.append(f"  Created:     {item.created_at}")
    if item.claimed_at:
        lines.append(f"  Claimed:     {item.claimed_at}")
    if item.completed_at:
        lines.append(f"  Completed:   {item.completed_at}")
    if item.result:
        lines.append(f"  Result:      {item.result[:200]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_queue_list(args: argparse.Namespace) -> int:
    """List work queue items."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)

    status_filter = getattr(args, "status", None)
    items = queue.list_items(status=status_filter)
    limit = getattr(args, "limit", 20)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(items[:limit], json_flag=True)
    else:
        print(_format_queue_list(items, limit))

    return 0


def _cmd_queue_show(args: argparse.Namespace) -> int:
    """Show a single work queue item."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)
    item = queue.get_item(args.item_id)

    if item is None:
        print(f"[X] Work item '{args.item_id}' not found.", file=sys.stderr)
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(item, json_flag=True)
    else:
        print(_format_queue_item(item))

    return 0


def _cmd_queue_add(args: argparse.Namespace) -> int:
    """Add an item to the work queue."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)

    item = queue.enqueue(
        description=args.description,
        requester=getattr(args, "requester", "cli") or "cli",
        priority=getattr(args, "priority", "medium") or "medium",
        agent_id=getattr(args, "agent", None),
    )

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(item, json_flag=True)
    else:
        print(f"  [OK] Queued: {item.id[:8]} ({item.priority}) {item.description[:60]}")

    return 0


def _cmd_queue_claim(args: argparse.Namespace) -> int:
    """Claim the next queued item."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)
    result = queue.claim_next()

    if "error" in result:
        print(f"  [X] {result['error']}", file=sys.stderr)
        if result.get("active_item"):
            active = result["active_item"]
            print(f"      Active: {active.get('id', '?')[:8]} - {active.get('description', '')[:60]}", file=sys.stderr)
        return 1

    item_dict = result.get("item")
    if item_dict is None:
        print("  No queued items to claim.")
        return 0

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(item_dict, json_flag=True)
    else:
        print(f"  [OK] Claimed: {item_dict.get('id', '?')[:8]} - {item_dict.get('description', '')[:60]}")

    return 0


def _cmd_queue_complete(args: argparse.Namespace) -> int:
    """Mark an active item as completed."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)
    result_text = getattr(args, "result", None)
    item = queue.complete(args.item_id, result=result_text)

    if item is None:
        print(f"[X] Item '{args.item_id}' not found or not active.", file=sys.stderr)
        return 1

    print(f"  [OK] Completed: {item.id[:8]}")
    return 0


def _cmd_queue_fail(args: argparse.Namespace) -> int:
    """Mark an active item as failed."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)
    reason = getattr(args, "reason", None)
    item = queue.fail(args.item_id, reason=reason)

    if item is None:
        print(f"[X] Item '{args.item_id}' not found or not active.", file=sys.stderr)
        return 1

    print(f"  [OK] Failed: {item.id[:8]}")
    return 0


def _cmd_queue_cancel(args: argparse.Namespace) -> int:
    """Cancel a queued or active item."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)
    item = queue.cancel(args.item_id)

    if item is None:
        print(f"[X] Item '{args.item_id}' not found or already terminal.", file=sys.stderr)
        return 1

    print(f"  [OK] Cancelled: {item.id[:8]}")
    return 0


def _cmd_queue_active(args: argparse.Namespace) -> int:
    """Show the currently active work item."""
    from cohort.work_queue import WorkQueue

    data_dir = resolve_data_dir(args)
    queue = WorkQueue(data_dir)
    item = queue.get_active()

    if item is None:
        print("  No active work item.")
        return 0

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(item, json_flag=True)
    else:
        print(_format_queue_item(item))

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort queue`` command group."""

    queue_parser = subparsers.add_parser("queue", help="Work queue operations")
    queue_sub = queue_parser.add_subparsers(dest="queue_command")

    # list
    list_parser = queue_sub.add_parser("list", help="List queue items")
    list_parser.add_argument("--status", choices=["queued", "active", "completed", "failed", "cancelled"],
                             default=None, help="Filter by status")
    list_parser.add_argument("--limit", type=int, default=20, help="Max items (default: 20)")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.add_argument("--data-dir", default="data", help="Data directory")

    # show
    show_parser = queue_sub.add_parser("show", help="Show a work item by ID")
    show_parser.add_argument("item_id", help="Work item ID (full or prefix)")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")
    show_parser.add_argument("--data-dir", default="data", help="Data directory")

    # add
    add_parser = queue_sub.add_parser("add", help="Add a work item to the queue")
    add_parser.add_argument("description", help="Task description")
    add_parser.add_argument("--priority", "-p", choices=["critical", "high", "medium", "low"],
                            default="medium", help="Priority (default: medium)")
    add_parser.add_argument("--agent", "-a", default=None, help="Assign to a specific agent")
    add_parser.add_argument("--requester", default="cli", help="Requester ID (default: cli)")
    add_parser.add_argument("--json", action="store_true", help="Output as JSON")
    add_parser.add_argument("--data-dir", default="data", help="Data directory")

    # active
    active_parser = queue_sub.add_parser("active", help="Show the currently active work item")
    active_parser.add_argument("--json", action="store_true", help="Output as JSON")
    active_parser.add_argument("--data-dir", default="data", help="Data directory")

    # claim
    claim_parser = queue_sub.add_parser("claim", help="Claim the next queued item")
    claim_parser.add_argument("--json", action="store_true", help="Output as JSON")
    claim_parser.add_argument("--data-dir", default="data", help="Data directory")

    # complete
    complete_parser = queue_sub.add_parser("complete", help="Mark an item as completed")
    complete_parser.add_argument("item_id", help="Work item ID")
    complete_parser.add_argument("--result", "-r", default=None, help="Result/output text")
    complete_parser.add_argument("--data-dir", default="data", help="Data directory")

    # fail
    fail_parser = queue_sub.add_parser("fail", help="Mark an item as failed")
    fail_parser.add_argument("item_id", help="Work item ID")
    fail_parser.add_argument("--reason", "-r", default=None, help="Failure reason")
    fail_parser.add_argument("--data-dir", default="data", help="Data directory")

    # cancel
    cancel_parser = queue_sub.add_parser("cancel", help="Cancel a queued or active item")
    cancel_parser.add_argument("item_id", help="Work item ID")
    cancel_parser.add_argument("--data-dir", default="data", help="Data directory")

    # Default args for bare 'cohort queue'
    queue_parser.add_argument("--json", action="store_true", help="Output as JSON")
    queue_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch queue commands."""
    sub = getattr(args, "queue_command", None)
    if sub == "list" or sub is None:
        return _cmd_queue_list(args)
    elif sub == "show":
        return _cmd_queue_show(args)
    elif sub == "add":
        return _cmd_queue_add(args)
    elif sub == "active":
        return _cmd_queue_active(args)
    elif sub == "claim":
        return _cmd_queue_claim(args)
    elif sub == "complete":
        return _cmd_queue_complete(args)
    elif sub == "fail":
        return _cmd_queue_fail(args)
    elif sub == "cancel":
        return _cmd_queue_cancel(args)
    else:
        print(f"Unknown queue subcommand: {sub}", file=sys.stderr)
        return 1
