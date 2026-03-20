"""cohort sessions -- channel session management CLI.

List, launch, and stop per-channel Claude Code sessions.

Examples::

    cohort sessions                          # List all active sessions
    cohort sessions list                     # Same
    cohort sessions list --channel general   # Filter by channel
    cohort sessions launch --channel general # Launch a session for #general
    cohort sessions stop --channel general   # Stop session(s) for #general
    cohort sessions config                   # Show session thresholds
"""

from __future__ import annotations

import argparse
import json as _json
import os
import subprocess
import sys
import time
from pathlib import Path

from cohort.cli._base import format_output


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_sessions(status: dict) -> str:
    """Pretty-print all active channel sessions."""
    channels = status.get("channels", {})
    thresholds = status.get("thresholds", {})
    total = status.get("total_sessions", 0)
    healthy = status.get("total_healthy", 0)
    queue_depth = status.get("total_queue_depth", 0)

    lines: list[str] = [
        "\n  Channel Sessions",
        "  " + "=" * 60,
        f"  Active: {healthy}/{total}  |  Queue depth: {queue_depth}"
        f"  |  Limit: {thresholds.get('limit', '?')}"
        f"  Warn: {thresholds.get('warn', '?')}"
        f"  Default: {thresholds.get('default', '?')}",
        "",
    ]

    if not channels:
        lines.append("  No active sessions.")
        return "\n".join(lines)

    for channel_id, ch_info in sorted(channels.items()):
        sessions = ch_info.get("sessions", [])
        q_depth = ch_info.get("queue_depth", 0)
        lines.append(f"  #{channel_id}  (queue: {q_depth})")
        for s in sessions:
            marker = "[OK]" if s.get("healthy") else "[X]"
            sid = s.get("session_id", "?")
            pid = s.get("pid") or "?"
            stale = s.get("stale_seconds", 0)
            lines.append(f"    {marker} {sid}  pid={pid}  stale={stale:.0f}s")
        lines.append("")

    return "\n".join(lines)


def _format_config(settings: dict) -> str:
    """Pretty-print session threshold configuration."""
    lines: list[str] = [
        "\n  Channel Session Config",
        "  " + "-" * 40,
        f"  channel_session_limit:   {settings.get('channel_session_limit', 5)}",
        f"  channel_session_warn:    {settings.get('channel_session_warn', 3)}",
        f"  channel_session_default: {settings.get('channel_session_default', 1)}",
        f"  channel_mode:            {settings.get('channel_mode', False)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_sessions_list(args: argparse.Namespace) -> int:
    """List all active channel sessions."""
    import urllib.request
    import urllib.error

    channel_filter = getattr(args, "channel", None)

    # Try server API first (preferred -- live data)
    try:
        url = "http://localhost:5100/api/channel/sessions"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = _json.loads(resp.read())

        # Filter by channel if requested
        if channel_filter and "channels" in status:
            filtered = {
                k: v for k, v in status["channels"].items()
                if k == channel_filter
            }
            status["channels"] = filtered
            status["total_sessions"] = sum(
                len(ch.get("sessions", [])) for ch in filtered.values()
            )
            status["total_healthy"] = sum(
                1 for ch in filtered.values()
                for s in ch.get("sessions", [])
                if s.get("healthy")
            )

        json_flag = getattr(args, "json", False)
        if json_flag:
            format_output(status, json_flag=True)
        else:
            print(_format_sessions(status))
        return 0

    except (urllib.error.URLError, OSError):
        print("  [X] Cohort server not running. Start with: cohort launch")
        return 1


def _cmd_sessions_launch(args: argparse.Namespace) -> int:
    """Launch a Claude Code channel session for a specific Cohort channel."""
    channel_id = args.channel

    if not channel_id:
        print("  [X] --channel is required")
        return 1

    # Find claude command
    claude_cmd = _find_claude_cmd()
    if not claude_cmd:
        print("  [X] Claude Code CLI not found. Install from: https://claude.com/claude-code")
        return 1

    # Build the plugin path
    plugin_dir = _find_plugin_dir()
    if not plugin_dir:
        print("  [X] Channel plugin not found at expected location")
        return 1

    server_name = f"cohort-ch-{channel_id}"
    env = {
        **os.environ,
        "CHANNEL_ID": channel_id,
        "COHORT_BASE_URL": getattr(args, "base_url", "http://localhost:5100"),
    }

    print(f"  [>>] Launching channel session for #{channel_id}...")
    print(f"  Server: {server_name}")
    print(f"  Plugin: {plugin_dir}")
    print()

    # Launch claude with the channel plugin
    cmd = [
        claude_cmd,
        "--dangerously-load-development-channels",
        f"server:{plugin_dir}",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=str(plugin_dir),
        )
        print(f"  [OK] Launched (pid={proc.pid})")
        print(f"  To stop: cohort sessions stop --channel {channel_id}")
        return 0

    except FileNotFoundError:
        print(f"  [X] Failed to launch: {claude_cmd} not found")
        return 1
    except Exception as e:
        print(f"  [X] Failed to launch: {e}")
        return 1


def _cmd_sessions_stop(args: argparse.Namespace) -> int:
    """Stop channel session(s) for a specific channel."""
    import urllib.request
    import urllib.error

    channel_id = args.channel
    if not channel_id:
        print("  [X] --channel is required")
        return 1

    # Get sessions for this channel
    try:
        url = "http://localhost:5100/api/channel/sessions"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = _json.loads(resp.read())
    except (urllib.error.URLError, OSError):
        print("  [X] Cohort server not running")
        return 1

    ch_info = status.get("channels", {}).get(channel_id, {})
    sessions = ch_info.get("sessions", [])
    if not sessions:
        print(f"  [!] No active sessions for #{channel_id}")
        return 0

    killed = 0
    for s in sessions:
        pid = s.get("pid")
        session_id = s.get("session_id", "?")
        if pid:
            try:
                os.kill(pid, 15)  # SIGTERM
                print(f"  [OK] Killed session {session_id} (pid={pid})")
                killed += 1
            except (ProcessLookupError, PermissionError):
                print(f"  [!] Session {session_id} (pid={pid}) already gone")

    # Unregister from bridge
    try:
        for s in sessions:
            sid = s.get("session_id")
            if sid:
                body = _json.dumps({
                    "channel_id": channel_id,
                    "session_id": sid,
                }).encode()
                # No unregister endpoint yet -- heartbeat will timeout
    except Exception:
        pass

    print(f"\n  Stopped {killed} session(s) for #{channel_id}")
    return 0


def _cmd_sessions_config(args: argparse.Namespace) -> int:
    """Show session threshold configuration."""
    import urllib.request
    import urllib.error

    try:
        url = "http://localhost:5100/api/settings"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            settings = _json.loads(resp.read())

        json_flag = getattr(args, "json", False)
        if json_flag:
            format_output({
                "channel_session_limit": settings.get("channel_session_limit", 5),
                "channel_session_warn": settings.get("channel_session_warn", 3),
                "channel_session_default": settings.get("channel_session_default", 1),
                "channel_mode": settings.get("channel_mode", False),
            }, json_flag=True)
        else:
            print(_format_config(settings))
        return 0

    except (urllib.error.URLError, OSError):
        print("  [X] Cohort server not running")
        return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_claude_cmd() -> str | None:
    """Find the Claude Code CLI binary."""
    import shutil

    # Check common locations
    for candidate in [
        shutil.which("claude"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\claude-code\claude.exe"),
        os.path.expanduser("~/.claude/local/claude"),
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _find_plugin_dir() -> Path | None:
    """Find the cohort-channel plugin directory."""
    # Relative to this file: cohort/cli/ -> cohort/ -> cohort-root/plugins/cohort-channel/
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "plugins" / "cohort-channel",
        Path("G:/cohort/plugins/cohort-channel"),
    ]
    for p in candidates:
        if p.exists() and (p / "src" / "index.ts").exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort sessions`` commands."""
    sessions_parser = subparsers.add_parser(
        "sessions", help="Manage channel Claude Code sessions"
    )
    sessions_sub = sessions_parser.add_subparsers(dest="sessions_command")

    # list (default)
    list_parser = sessions_sub.add_parser("list", help="List active sessions")
    list_parser.add_argument("--channel", help="Filter by channel ID")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # launch
    launch_parser = sessions_sub.add_parser("launch", help="Launch session for a channel")
    launch_parser.add_argument("--channel", required=True, help="Cohort channel ID")
    launch_parser.add_argument(
        "--base-url", default="http://localhost:5100",
        help="Cohort server URL (default: http://localhost:5100)",
    )

    # stop
    stop_parser = sessions_sub.add_parser("stop", help="Stop session(s) for a channel")
    stop_parser.add_argument("--channel", required=True, help="Cohort channel ID")

    # config
    config_parser = sessions_sub.add_parser("config", help="Show session thresholds")
    config_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch sessions commands."""
    sub = getattr(args, "sessions_command", None)
    if sub == "launch":
        return _cmd_sessions_launch(args)
    elif sub == "stop":
        return _cmd_sessions_stop(args)
    elif sub == "config":
        return _cmd_sessions_config(args)
    else:
        # Default: list
        return _cmd_sessions_list(args)
