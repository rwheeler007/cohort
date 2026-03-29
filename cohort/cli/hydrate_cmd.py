"""cohort hydrate -- channel context hydration for Claude Code sessions."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import channel_not_found, format_output, resolve_data_dir

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _build_chat(data_dir):
    """Construct a ChatManager from data_dir."""
    from cohort.chat import ChatManager
    from cohort.registry import create_storage

    storage = create_storage(data_dir)
    return ChatManager(storage)


def _cmd_hydrate_show(args: argparse.Namespace) -> int:
    """Build context for a channel (what Claude sees when joining mid-conversation)."""
    from cohort.context_hydration import hydrate_channel_context, invalidate_hydration

    data_dir = resolve_data_dir(args)

    try:
        chat = _build_chat(data_dir)
    except Exception as e:
        print(f"  [X] Failed to load chat backend: {e}", file=sys.stderr)
        return 1

    channel_id = args.channel_id

    # Check channel exists
    channel = chat.get_channel(channel_id)
    if channel is None:
        return channel_not_found(channel_id)

    # Force refresh if requested
    if getattr(args, "refresh", False):
        invalidate_hydration(channel_id)

    context = hydrate_channel_context(chat, channel_id)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({
            "channel_id": channel_id,
            "context": context,
            "chars": len(context) if context else 0,
        }, json_flag=True)
    else:
        if context:
            print(f"\n  Hydrated Context for #{channel_id} ({len(context)} chars)")
            print("  " + "=" * 55)
            limit = getattr(args, "limit", 2000)
            if len(context) > limit:
                print(context[:limit])
                print(f"\n  [...truncated at {limit} chars, use --limit to see more]")
            else:
                print(context)
        else:
            print(f"  #{channel_id} has no history to hydrate.")
    return 0


def _cmd_hydrate_invalidate(args: argparse.Namespace) -> int:
    """Clear the hydration cache for a channel."""
    from cohort.context_hydration import invalidate_hydration

    channel_id = args.channel_id
    invalidate_hydration(channel_id)
    print(f"  [OK] Invalidated hydration cache for #{channel_id}")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort hydrate`` command group."""

    hyd_parser = subparsers.add_parser("hydrate", help="Channel context hydration")
    hyd_sub = hyd_parser.add_subparsers(dest="hydrate_command")

    # show
    show_p = hyd_sub.add_parser("show", help="Build and show channel context")
    show_p.add_argument("channel_id", help="Channel ID")
    show_p.add_argument("--refresh", action="store_true", help="Force cache refresh")
    show_p.add_argument("--limit", type=int, default=2000, help="Max chars (default: 2000)")
    show_p.add_argument("--json", action="store_true", help="Output as JSON")
    show_p.add_argument("--data-dir", default="data", help="Data directory")

    # invalidate
    inv_p = hyd_sub.add_parser("invalidate", help="Clear hydration cache for a channel")
    inv_p.add_argument("channel_id", help="Channel ID")

    hyd_parser.add_argument("--json", action="store_true", help="Output as JSON")
    hyd_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch hydrate commands."""
    sub = getattr(args, "hydrate_command", None)
    if sub == "show":
        return _cmd_hydrate_show(args)
    elif sub == "invalidate":
        return _cmd_hydrate_invalidate(args)
    elif sub is None:
        print("  Usage: python -m cohort hydrate {show|invalidate} <channel_id>")
        return 0
    else:
        print(f"Unknown hydrate subcommand: {sub}", file=sys.stderr)
        return 1
