"""cohort sessions -- channel session management CLI.

Inspect active Claude Code channel sessions, view queue depths,
and manage session lifecycle via the Cohort server.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
import urllib.error

from cohort.cli._base import format_output, require_server


# ---------------------------------------------------------------------------
# Server API helpers
# ---------------------------------------------------------------------------

def _api_get(path: str, port: int = 5100) -> dict | None:
    """GET a JSON endpoint from the Cohort server."""
    try:
        url = f"http://localhost:{port}{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _api_post(path: str, body: dict | None = None, port: int = 5100) -> dict | None:
    """POST to a JSON endpoint on the Cohort server."""
    try:
        url = f"http://localhost:{port}{path}"
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_status(data: dict) -> str:
    """Pretty-print the full sessions status."""
    lines: list[str] = []

    total = data.get("total_sessions", 0)
    healthy = data.get("total_healthy", 0)
    queue_depth = data.get("total_queue_depth", 0)
    thresholds = data.get("thresholds", {})

    lines.append(f"\n  Channel Sessions ({total} total, {healthy} healthy)")
    lines.append("  " + "-" * 55)

    # Thresholds
    lines.append(f"  Limit: {thresholds.get('limit', '?')}  "
                 f"Warn: {thresholds.get('warn', '?')}  "
                 f"Idle timeout: {thresholds.get('idle_timeout', '?')}s  "
                 f"Auto-launch: {'on' if thresholds.get('auto_launch') else 'off'}")
    lines.append(f"  Pending queue depth: {queue_depth}")
    lines.append("")

    channels = data.get("channels", {})
    if not channels:
        lines.append("  No active sessions.")
    else:
        for ch_id, ch_data in channels.items():
            sessions = ch_data.get("sessions", [])
            q_depth = ch_data.get("queue_depth", 0)
            healthy_count = sum(1 for s in sessions if s.get("healthy"))
            lines.append(f"  #{ch_id}  ({len(sessions)} sessions, "
                         f"{healthy_count} healthy, {q_depth} queued)")

            for s in sessions:
                sid = s.get("session_id", "?")[:12]
                pid = s.get("pid") or "?"
                stale = s.get("stale_seconds")
                health = "[OK]" if s.get("healthy") else "[X]"
                stale_str = f"{stale:.0f}s ago" if stale is not None else "never"
                lines.append(f"    {health} {sid}  pid={pid}  heartbeat={stale_str}")

    # Launch queue
    launch_queue = data.get("launch_queue", [])
    if launch_queue:
        lines.append("")
        lines.append(f"  Launch Queue ({len(launch_queue)} pending):")
        for item in launch_queue[:10]:
            ch = item.get("channel_id", "?")
            lines.append(f"    -> {ch}")

    return "\n".join(lines)


def _format_channel_detail(ch_id: str, ch_data: dict) -> str:
    """Pretty-print detail for a single channel."""
    sessions = ch_data.get("sessions", [])
    q_depth = ch_data.get("queue_depth", 0)

    lines: list[str] = [
        f"\n  Channel: #{ch_id}",
        "  " + "-" * 40,
        f"  Sessions: {len(sessions)}",
        f"  Queue depth: {q_depth}",
        "",
    ]

    for s in sessions:
        sid = s.get("session_id", "?")
        pid = s.get("pid") or "?"
        stale = s.get("stale_seconds")
        health = "[OK]" if s.get("healthy") else "[X]"
        registered = s.get("registered_at", "?")
        stale_str = f"{stale:.0f}s ago" if stale is not None else "never"

        lines.append(f"  {health} Session: {sid}")
        lines.append(f"     PID: {pid}")
        lines.append(f"     Registered: {registered}")
        lines.append(f"     Last heartbeat: {stale_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_status(args: argparse.Namespace) -> int:
    """Show all channel sessions status."""
    if not require_server():
        return 1

    data = _api_get("/api/channel/sessions")
    if data is None:
        print("  [X] Could not fetch session status from server.")
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(data, json_flag=True)
    else:
        print(_format_status(data))

    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Show detail for a specific channel's sessions."""
    if not require_server():
        return 1

    data = _api_get("/api/channel/sessions")
    if data is None:
        print("  [X] Could not fetch session status from server.")
        return 1

    channels = data.get("channels", {})
    ch_id = args.channel_id

    if ch_id not in channels:
        print(f"  [X] No active sessions for channel '{ch_id}'.")
        available = list(channels.keys())
        if available:
            print(f"  Active channels: {', '.join(available)}")
        return 1

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(channels[ch_id], json_flag=True)
    else:
        print(_format_channel_detail(ch_id, channels[ch_id]))

    return 0


def _cmd_reap(args: argparse.Namespace) -> int:
    """Force-reap idle sessions."""
    if not require_server():
        return 1

    timeout = getattr(args, "timeout", None)
    body = {}
    if timeout is not None:
        body["max_idle_seconds"] = timeout

    result = _api_post("/api/channel/sessions/reap", body)
    if result is None:
        # Server may not have the reap endpoint yet -- fall back to local
        print("  [!] Server reap endpoint not available.")
        print("  Idle sessions are automatically reaped by the background daemon.")
        return 0

    reaped = result.get("reaped", 0)
    print(f"  [OK] Reaped {reaped} idle session(s).")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    """Purge all session registrations."""
    if not require_server():
        return 1

    result = _api_post("/api/channel/sessions/purge")
    if result is None:
        print("  [!] Server purge endpoint not available.")
        return 1

    purged = result.get("purged", 0)
    print(f"  [OK] Purged {purged} session registration(s).")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort sessions`` commands."""
    sess_parser = subparsers.add_parser(
        "sessions", help="Channel session management"
    )
    sess_sub = sess_parser.add_subparsers(dest="sessions_command")

    # cohort sessions status (default)
    status_parser = sess_sub.add_parser("status", help="Show all sessions (default)")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # cohort sessions show <channel_id>
    show_parser = sess_sub.add_parser("show", help="Show sessions for a channel")
    show_parser.add_argument("channel_id", help="Channel ID to inspect")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # cohort sessions reap [--timeout SECONDS]
    reap_parser = sess_sub.add_parser("reap", help="Force-reap idle sessions")
    reap_parser.add_argument("--timeout", type=int,
                             help="Consider sessions idle after N seconds (default: server config)")

    # cohort sessions purge
    sess_sub.add_parser("purge", help="Purge all session registrations")

    # Default flags on parent
    sess_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch sessions commands."""
    sub = getattr(args, "sessions_command", None)
    if sub == "show":
        return _cmd_show(args)
    elif sub == "reap":
        return _cmd_reap(args)
    elif sub == "purge":
        return _cmd_purge(args)
    else:
        # Default: status
        return _cmd_status(args)
