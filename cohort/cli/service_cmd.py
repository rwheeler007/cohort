"""cohort service -- service registry, health checks, and lifecycle management."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_services(services: list) -> str:
    """Pretty-print service list with status."""
    if not services:
        return "  No services registered."

    lines: list[str] = [
        f"\n  Registered Services ({len(services)})",
        "  " + "-" * 60,
    ]
    for svc in services:
        key = svc.get("key", "?")
        name = svc.get("name", key)
        port = svc.get("port", "?")
        status = svc.get("status", "unknown")
        healthy = svc.get("healthy", None)

        icon = {"healthy": "[OK]", "unhealthy": "[X]", "unknown": "[?]", "stopped": "[--]"}.get(
            status, "[?]"
        )
        if healthy is True:
            icon = "[OK]"
        elif healthy is False:
            icon = "[X]"

        lines.append(f"  {icon} {key:<25s} :{port}  {name}")
    return "\n".join(lines)


def _format_check(result: dict) -> str:
    """Pretty-print health check result."""
    lines: list[str] = [
        "\n  Service Health Check",
        "  " + "-" * 50,
    ]
    for key, entry in result.items():
        if isinstance(entry, dict):
            healthy = entry.get("healthy", False)
            ms = entry.get("response_ms", 0)
            icon = "[OK]" if healthy else "[X]"
            lines.append(f"  {icon} {key:<25s} {ms:.0f}ms")
        else:
            lines.append(f"  [?] {key}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_service_list(args: argparse.Namespace) -> int:
    """List all registered services."""
    from cohort.health_monitor import list_services

    services = list_services()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(services, json_flag=True)
    else:
        print(_format_services(services))
    return 0


def _cmd_service_check(args: argparse.Namespace) -> int:
    """Run health checks on all services."""
    from cohort.health_monitor import run_service_checks

    print("  Checking services...")
    result = run_service_checks()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        services = result.get("services", result)
        print(_format_check(services))
    return 0


def _cmd_service_start(args: argparse.Namespace) -> int:
    """Start a service."""
    from cohort.health_monitor import start_service, get_service_entry

    entry = get_service_entry(args.service)
    if not entry:
        print(f"  [X] Service not found: {args.service}", file=sys.stderr)
        print("      Run 'python -m cohort service list' to see registered services.", file=sys.stderr)
        return 1

    print(f"  Starting {args.service}...")
    result = start_service(args.service)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        if result.get("success"):
            print(f"  [OK] {args.service} started on port {entry.get('port', '?')}")
        else:
            print(f"  [X] Failed: {result.get('error', 'unknown')}", file=sys.stderr)
            return 1
    return 0


def _cmd_service_stop(args: argparse.Namespace) -> int:
    """Stop a service."""
    from cohort.health_monitor import stop_service, get_service_entry

    entry = get_service_entry(args.service)
    if not entry:
        print(f"  [X] Service not found: {args.service}", file=sys.stderr)
        return 1

    print(f"  Stopping {args.service}...")
    result = stop_service(args.service)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        if result.get("success"):
            print(f"  [OK] {args.service} stopped.")
        else:
            print(f"  [X] Failed: {result.get('error', 'unknown')}", file=sys.stderr)
            return 1
    return 0


def _cmd_service_restart(args: argparse.Namespace) -> int:
    """Restart a service."""
    from cohort.health_monitor import restart_service, get_service_entry

    entry = get_service_entry(args.service)
    if not entry:
        print(f"  [X] Service not found: {args.service}", file=sys.stderr)
        return 1

    print(f"  Restarting {args.service}...")
    result = restart_service(args.service)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(result, json_flag=True)
    else:
        if result.get("success"):
            print(f"  [OK] {args.service} restarted.")
        else:
            print(f"  [X] Failed: {result.get('error', 'unknown')}", file=sys.stderr)
            return 1
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort service`` command group."""

    svc_parser = subparsers.add_parser("service", help="Service registry and lifecycle management")
    svc_sub = svc_parser.add_subparsers(dest="service_command")

    # list
    list_p = svc_sub.add_parser("list", help="List registered services (default)")
    list_p.add_argument("--json", action="store_true", help="Output as JSON")

    # check
    check_p = svc_sub.add_parser("check", help="Run health checks on all services")
    check_p.add_argument("--json", action="store_true", help="Output as JSON")

    # start
    start_p = svc_sub.add_parser("start", help="Start a service")
    start_p.add_argument("service", help="Service key")
    start_p.add_argument("--json", action="store_true", help="Output as JSON")

    # stop
    stop_p = svc_sub.add_parser("stop", help="Stop a service")
    stop_p.add_argument("service", help="Service key")
    stop_p.add_argument("--json", action="store_true", help="Output as JSON")

    # restart
    restart_p = svc_sub.add_parser("restart", help="Restart a service")
    restart_p.add_argument("service", help="Service key")
    restart_p.add_argument("--json", action="store_true", help="Output as JSON")

    svc_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch service commands."""
    sub = getattr(args, "service_command", None)
    if sub == "list" or sub is None:
        return _cmd_service_list(args)
    elif sub == "check":
        return _cmd_service_check(args)
    elif sub == "start":
        return _cmd_service_start(args)
    elif sub == "stop":
        return _cmd_service_stop(args)
    elif sub == "restart":
        return _cmd_service_restart(args)
    else:
        print(f"Unknown service subcommand: {sub}", file=sys.stderr)
        return 1
