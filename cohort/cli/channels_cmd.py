"""cohort channels list / cohort channel read -- channel management CLI."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from cohort.cli._base import (
    channel_not_found,
    format_output,
    resolve_data_dir,
    truncation_notice,
)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_channel_list(channels: list) -> str:
    """Pretty-print a list of Channel objects."""
    if not channels:
        return "  No channels found."

    lines: list[str] = [f"\n  Channels ({len(channels)})", "  " + "-" * 55]
    for ch in sorted(channels, key=lambda c: c.name):
        status = ""
        if getattr(ch, "is_archived", False):
            status = " [archived]"
        elif getattr(ch, "is_locked", False):
            status = " [locked]"
        members = len(getattr(ch, "members", []))
        desc = ch.description[:50] + "..." if len(ch.description or "") > 50 else (ch.description or "")
        lines.append(f"  #{ch.name:25s}  {members:2d} members  {desc}{status}")

    return "\n".join(lines)


def _format_messages(messages: list, limit: int, channel_id: str) -> str:
    """Pretty-print a list of Message objects."""
    if not messages:
        return f"  No messages in #{channel_id}."

    lines: list[str] = [f"\n  #{channel_id} ({len(messages)} messages)", "  " + "-" * 55]
    for msg in messages:
        ts = getattr(msg, "timestamp", "")
        # Trim to just time if today
        try:
            dt = datetime.fromisoformat(ts)
            ts_display = dt.strftime("%H:%M") if dt.date() == datetime.now().date() else dt.strftime("%m/%d %H:%M")
        except (ValueError, TypeError):
            ts_display = ts[:16] if ts else ""

        sender = getattr(msg, "sender", "unknown")
        content = msg.content
        # Truncate long messages
        if len(content) > 200:
            content = content[:200] + "..."
        # Indent multiline content
        content = content.replace("\n", "\n          ")

        lines.append(f"  [{ts_display}] {sender}: {content}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_channels_list(args: argparse.Namespace) -> int:
    """List all channels."""
    from cohort.chat import ChatManager
    from cohort.registry import create_storage

    data_dir = resolve_data_dir(args)
    storage = create_storage(data_dir)
    chat = ChatManager(storage)

    include_archived = getattr(args, "all", False)
    channels = chat.list_channels(include_archived=include_archived)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(channels, json_flag=True)
    else:
        print(_format_channel_list(channels))

    return 0


def _cmd_channel_read(args: argparse.Namespace) -> int:
    """Read messages from a channel."""
    from cohort.chat import ChatManager
    from cohort.registry import create_storage

    data_dir = resolve_data_dir(args)
    storage = create_storage(data_dir)
    chat = ChatManager(storage)

    channel = chat.get_channel(args.channel_id)
    if channel is None:
        return channel_not_found(args.channel_id)

    limit = getattr(args, "limit", 20)
    messages = chat.get_channel_messages(args.channel_id, limit=limit)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(messages, json_flag=True)
    else:
        print(_format_messages(messages, limit, args.channel_id))

    return 0


def _cmd_channel_create(args: argparse.Namespace) -> int:
    """Create a new channel."""
    from cohort.chat import ChatManager
    from cohort.registry import create_storage

    data_dir = resolve_data_dir(args)
    storage = create_storage(data_dir)
    chat = ChatManager(storage)

    members = [m.strip() for m in args.members.split(",")] if getattr(args, "members", None) else None

    channel = chat.create_channel(
        name=args.name,
        description=getattr(args, "description", "") or "",
        members=members,
    )

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(channel, json_flag=True)
    else:
        print(f"  [OK] Created #{channel.name} (id: {channel.id})")

    return 0


def _cmd_channel_archive(args: argparse.Namespace) -> int:
    """Archive a channel."""
    from cohort.chat import ChatManager
    from cohort.registry import create_storage

    data_dir = resolve_data_dir(args)
    storage = create_storage(data_dir)
    chat = ChatManager(storage)

    channel = chat.get_channel(args.channel_id)
    if channel is None:
        return channel_not_found(args.channel_id)

    result = chat.archive_channel(args.channel_id, archived_by="cli")
    if result is None:
        print(f"[X] Failed to archive #{args.channel_id}", file=sys.stderr)
        return 1

    print(f"  [OK] Archived #{args.channel_id}")
    return 0


def _cmd_channel_search(args: argparse.Namespace) -> int:
    """Search messages across channels."""
    from cohort.chat import ChatManager
    from cohort.registry import create_storage

    data_dir = resolve_data_dir(args)
    storage = create_storage(data_dir)
    chat = ChatManager(storage)

    channel_id = getattr(args, "channel_id", None)
    results = chat.search_messages(args.query, channel_id=channel_id)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(results, json_flag=True)
    else:
        if not results:
            print(f"  No messages matching '{args.query}'")
        else:
            print(f"\n  Search: '{args.query}' ({len(results)} matches)")
            print("  " + "-" * 55)
            for msg in results[:20]:
                print(f"  #{msg.channel_id} [{msg.sender}]: {msg.content[:80]}")
            if len(results) > 20:
                print(f"\n  Showing 20 of {len(results)}.")

    return 0


def _cmd_channel_post(args: argparse.Namespace) -> int:
    """Post a message to a channel."""
    from cohort.chat import ChatManager
    from cohort.registry import create_storage

    data_dir = resolve_data_dir(args)
    storage = create_storage(data_dir)
    chat = ChatManager(storage)

    channel = chat.get_channel(args.channel_id)
    if channel is None:
        return channel_not_found(args.channel_id)

    sender = getattr(args, "sender", "user") or "user"
    msg = chat.post_message(
        channel_id=args.channel_id,
        sender=sender,
        content=args.message,
    )

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(msg, json_flag=True)
    else:
        print(f"  [OK] {msg.id} -> #{args.channel_id}")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort channels`` and ``cohort channel`` commands."""

    # -- cohort channels list ----------------------------------------------
    channels_parser = subparsers.add_parser("channels", help="List channels")
    channels_sub = channels_parser.add_subparsers(dest="channels_command")

    list_parser = channels_sub.add_parser("list", help="List all channels")
    list_parser.add_argument("--all", action="store_true", help="Include archived channels")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.add_argument("--data-dir", default="data", help="Data directory")

    # Default: 'cohort channels' = list
    channels_parser.add_argument("--all", action="store_true", help="Include archived channels")
    channels_parser.add_argument("--json", action="store_true", help="Output as JSON")
    channels_parser.add_argument("--data-dir", default="data", help="Data directory")

    # -- cohort channel read/create/post -----------------------------------
    channel_parser = subparsers.add_parser("channel", help="Channel operations")
    channel_sub = channel_parser.add_subparsers(dest="channel_command")

    # read
    read_parser = channel_sub.add_parser("read", help="Read messages from a channel")
    read_parser.add_argument("channel_id", help="Channel ID or name")
    read_parser.add_argument("--limit", type=int, default=20, help="Max messages (default: 20)")
    read_parser.add_argument("--json", action="store_true", help="Output as JSON")
    read_parser.add_argument("--data-dir", default="data", help="Data directory")

    # create
    create_parser = channel_sub.add_parser("create", help="Create a new channel")
    create_parser.add_argument("name", help="Channel name")
    create_parser.add_argument("--description", "-d", default="", help="Channel description")
    create_parser.add_argument("--members", "-m", default=None, help="Comma-separated member agent IDs")
    create_parser.add_argument("--json", action="store_true", help="Output as JSON")
    create_parser.add_argument("--data-dir", default="data", help="Data directory")

    # archive
    archive_parser = channel_sub.add_parser("archive", help="Archive a channel")
    archive_parser.add_argument("channel_id", help="Channel ID to archive")
    archive_parser.add_argument("--data-dir", default="data", help="Data directory")

    # search
    search_parser = channel_sub.add_parser("search", help="Search messages across channels")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--channel-id", default=None, help="Limit to a specific channel")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")
    search_parser.add_argument("--data-dir", default="data", help="Data directory")

    # post
    post_parser = channel_sub.add_parser("post", help="Post a message to a channel")
    post_parser.add_argument("channel_id", help="Channel ID or name")
    post_parser.add_argument("message", help="Message content")
    post_parser.add_argument("--sender", default="user", help="Sender ID (default: user)")
    post_parser.add_argument("--json", action="store_true", help="Output as JSON")
    post_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch channels/channel commands."""
    if args.command == "channels":
        sub = getattr(args, "channels_command", None)
        if sub == "list" or sub is None:
            return _cmd_channels_list(args)
        else:
            print(f"Unknown channels subcommand: {sub}", file=sys.stderr)
            return 1

    elif args.command == "channel":
        sub = getattr(args, "channel_command", None)
        if sub == "read":
            return _cmd_channel_read(args)
        elif sub == "create":
            return _cmd_channel_create(args)
        elif sub == "post":
            return _cmd_channel_post(args)
        elif sub == "archive":
            return _cmd_channel_archive(args)
        elif sub == "search":
            return _cmd_channel_search(args)
        else:
            print("Usage: python -m cohort channel [read|create|post|archive|search]", file=sys.stderr)
            return 1

    return 1
