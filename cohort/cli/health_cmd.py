"""cohort health / cohort doctor / cohort status -- system health CLI."""

from __future__ import annotations

import argparse

from cohort.cli._base import format_output, resolve_data_dir

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_health(services: list) -> str:
    """Pretty-print service health check results."""
    if not services:
        return "  No services registered."

    lines: list[str] = ["\n  Service Health", "  " + "-" * 55]
    for svc in services:
        name = svc.get("name", svc.get("key", "unknown"))
        healthy = svc.get("healthy")
        port = svc.get("port", "")
        if healthy is True:
            marker = "[OK]"
        elif healthy is False:
            marker = "[X]"
        else:
            marker = "[?]"
        port_str = f":{port}" if port else ""
        lines.append(f"  {marker} {name:25s} {port_str}")

    return "\n".join(lines)


def _format_status(status: dict) -> str:
    """Pretty-print combined system status."""
    lines: list[str] = ["\n  Cohort Status", "  " + "=" * 55]

    # Server
    server = status.get("server", {})
    lines.append(f"\n  Server:    {'[OK] running' if server.get('up') else '[X] not running'}")
    if server.get("port"):
        lines.append(f"  Port:      {server['port']}")

    # Ollama
    ollama = status.get("ollama", {})
    lines.append(f"  Ollama:    {'[OK] reachable' if ollama.get('up') else '[X] not reachable'}")
    if ollama.get("models"):
        lines.append(f"  Models:    {len(ollama['models'])} loaded")

    # Agents
    agents = status.get("agents", {})
    lines.append(f"\n  Agents:    {agents.get('total', 0)} total, {agents.get('active', 0)} active")

    # Channels
    channels = status.get("channels", {})
    lines.append(f"  Channels:  {channels.get('total', 0)}")

    # Queue
    queue = status.get("queue", {})
    lines.append(f"  Queue:     {queue.get('queued', 0)} queued, {queue.get('active', 0)} active")

    return "\n".join(lines)


def _format_doctor(results: list) -> str:
    """Pretty-print doctor check results."""
    lines: list[str] = ["\n  Cohort Doctor", "  " + "-" * 55]
    all_ok = True
    for check in results:
        name = check["name"]
        ok = check["ok"]
        detail = check.get("detail", "")
        marker = "[OK]" if ok else "[X]"
        if not ok:
            all_ok = False
        line = f"  {marker} {name}"
        if detail:
            line += f"  -- {detail}"
        lines.append(line)

    lines.append("")
    if all_ok:
        lines.append("  All checks passed.")
    else:
        lines.append("  Some checks failed. Fix the issues above and re-run.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_health(args: argparse.Namespace) -> int:
    """Check service health."""
    from cohort.health_monitor import list_services

    services = list_services()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(services, json_flag=True)
    else:
        print(_format_health(services))

    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Show combined system status."""
    import urllib.error
    import urllib.request

    data_dir = resolve_data_dir(args)
    status: dict = {}

    # Server check
    port = 5100
    try:
        req = urllib.request.Request(f"http://localhost:{port}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            status["server"] = {"up": True, "port": port}
    except (urllib.error.URLError, OSError):
        status["server"] = {"up": False, "port": port}

    # Ollama check
    try:
        import json as _json
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = _json.loads(resp.read())
            models = [m.get("name", "") for m in body.get("models", [])]
            status["ollama"] = {"up": True, "models": models}
    except (urllib.error.URLError, OSError, Exception):
        status["ollama"] = {"up": False, "models": []}

    # Agents
    try:
        from cohort.agent_store import AgentStore
        from cohort.cli._base import resolve_agents_dir
        store = AgentStore(agents_dir=resolve_agents_dir())
        all_agents = store.list_agents(include_hidden=True)
        active = [a for a in all_agents if getattr(a, "status", "active") == "active"]
        status["agents"] = {"total": len(all_agents), "active": len(active)}
    except Exception:
        status["agents"] = {"total": 0, "active": 0}

    # Channels
    try:
        from cohort.chat import ChatManager
        from cohort.registry import create_storage
        storage = create_storage(data_dir)
        chat = ChatManager(storage)
        channels = chat.list_channels()
        status["channels"] = {"total": len(channels)}
    except Exception:
        status["channels"] = {"total": 0}

    # Queue
    try:
        from cohort.work_queue import WorkQueue
        queue = WorkQueue(data_dir)
        items = queue.list_items()
        queued = len([i for i in items if i.status == "queued"])
        active = len([i for i in items if i.status == "active"])
        status["queue"] = {"queued": queued, "active": active, "total": len(items)}
    except Exception:
        status["queue"] = {"queued": 0, "active": 0, "total": 0}

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(status, json_flag=True)
    else:
        print(_format_status(status))

    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run diagnostic checks."""
    import shutil
    import urllib.error
    import urllib.request

    results: list[dict] = []

    # 1. Python version
    import sys as _sys
    py_ver = f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}"
    py_ok = _sys.version_info >= (3, 10)
    results.append({"name": "Python >= 3.10", "ok": py_ok, "detail": py_ver})

    # 2. Agents directory
    from cohort.cli._base import resolve_agents_dir
    agents_dir = resolve_agents_dir()
    agents_ok = agents_dir.exists() and any(agents_dir.iterdir())
    agent_count = len(list(agents_dir.glob("*/agent_config.*"))) if agents_ok else 0
    results.append({"name": "Agents directory", "ok": agents_ok, "detail": f"{agent_count} configs found"})

    # 3. Data directory writable
    data_dir = resolve_data_dir(args)
    data_ok = data_dir.exists() or True  # will be created on first use
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".cohort_doctor_test"
        test_file.write_text("ok")
        test_file.unlink()
        data_ok = True
    except OSError:
        data_ok = False
    results.append({"name": "Data dir writable", "ok": data_ok, "detail": str(data_dir)})

    # 4. Ollama reachable
    ollama_ok = False
    ollama_detail = "not reachable"
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            import json as _json
            body = _json.loads(resp.read())
            models = [m.get("name", "") for m in body.get("models", [])]
            ollama_ok = True
            ollama_detail = f"{len(models)} models available"
    except (urllib.error.URLError, OSError):
        pass
    results.append({"name": "Ollama reachable", "ok": ollama_ok, "detail": ollama_detail})

    # 5. Server running
    server_ok = False
    try:
        req = urllib.request.Request("http://localhost:5100/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            server_ok = True
    except (urllib.error.URLError, OSError):
        pass
    results.append({"name": "Cohort server", "ok": server_ok,
                     "detail": "running on :5100" if server_ok else "not running (optional for file-backed ops)"})

    # 6. Git available
    git_ok = shutil.which("git") is not None
    results.append({"name": "Git available", "ok": git_ok, "detail": shutil.which("git") or "not found"})

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(results, json_flag=True)
    else:
        print(_format_doctor(results))

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort health``, ``cohort status``, ``cohort doctor``."""

    # health
    health_parser = subparsers.add_parser("health", help="Check service health")
    health_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # status
    status_parser = subparsers.add_parser("status", help="System status overview")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")
    status_parser.add_argument("--data-dir", default="data", help="Data directory")

    # doctor
    doctor_parser = subparsers.add_parser("doctor", help="Run diagnostic checks")
    doctor_parser.add_argument("--json", action="store_true", help="Output as JSON")
    doctor_parser.add_argument("--data-dir", default="data", help="Data directory")


def handle(args: argparse.Namespace) -> int:
    """Dispatch health/status/doctor commands."""
    if args.command == "health":
        return _cmd_health(args)
    elif args.command == "status":
        return _cmd_status(args)
    elif args.command == "doctor":
        return _cmd_doctor(args)
    return 1
