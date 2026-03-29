"""
Cohort Health Monitor -- Active service checking + lifecycle management.

Ported from BOSS smack_health.py, adapted for Cohort's Starlette server.
Provides:
  - Periodic health checks against service_registry.yaml endpoints
  - Service start/stop/restart via subprocess management
  - State persistence to data/services/health_monitor/state.json
  - Windows-native port management (netstat, taskkill, phantom socket handling)
"""

import json
import logging
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

COHORT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = COHORT_ROOT / "data" / "services" / "health_monitor"
STATE_PATH = DATA_DIR / "state.json"


def configure_health_monitor(data_dir: str | Path) -> None:
    """Override the default data directory for health monitor state."""
    global DATA_DIR, STATE_PATH
    DATA_DIR = Path(data_dir) / "services" / "health_monitor"
    STATE_PATH = DATA_DIR / "state.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY_PATH = DATA_DIR / "service_registry.json"
LOG_DIR = DATA_DIR / "logs"

# Ensure dirs exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Child process tracking
# ---------------------------------------------------------------------------

_child_processes: Dict[str, subprocess.Popen] = {}


def cleanup_child_services():
    """Terminate all tracked child processes. Call on server shutdown."""
    for key, proc in list(_child_processes.items()):
        try:
            if proc.poll() is None:
                logger.info(f"[*] Terminating child service {key} (PID {proc.pid})...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except Exception as e:
            logger.warning(f"[!] Failed to clean up {key}: {e}")
    _child_processes.clear()


# ---------------------------------------------------------------------------
# YAML / JSON helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_json(path: Path, data: dict):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"[X] Failed to save {path}: {e}")


# ---------------------------------------------------------------------------
# Service Registry
# ---------------------------------------------------------------------------

def get_service_registry() -> dict:
    """Load the service registry. Returns {service_key: {...}}."""
    reg = _load_json(REGISTRY_PATH)
    return reg.get("services", {})


def get_service_entry(service_key: str) -> Optional[dict]:
    """Get a single service entry by key."""
    return get_service_registry().get(service_key)


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def get_state() -> dict:
    """Load current health monitor state."""
    return _load_json(STATE_PATH)


def save_state(state: dict):
    """Persist health monitor state."""
    state["last_updated"] = datetime.now().isoformat()
    _save_json(STATE_PATH, state)


def _ensure_state() -> dict:
    """Ensure state has the expected structure."""
    state = get_state()
    if "target_status" not in state:
        state["target_status"] = {}
    if "last_alerts" not in state:
        state["last_alerts"] = {}
    if "last_checks" not in state:
        state["last_checks"] = {}
    if "paused" not in state:
        state["paused"] = False
    return state


# ---------------------------------------------------------------------------
# Health Checking
# ---------------------------------------------------------------------------

def check_health(url: str, timeout: float = 5.0) -> Tuple[bool, int, float]:
    """HTTP GET health check. Returns (ok, status_code, response_ms)."""
    import httpx
    start = time.monotonic()
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        elapsed = (time.monotonic() - start) * 1000
        return resp.status_code < 400, resp.status_code, round(elapsed, 1)
    except Exception:
        elapsed = (time.monotonic() - start) * 1000
        return False, 0, round(elapsed, 1)


def run_service_checks() -> dict:
    """Check all registered services and update state. Returns updated state."""
    registry = get_service_registry()
    state = _ensure_state()
    now = datetime.now().isoformat()

    # Prune stale entries for services no longer in registry
    valid_keys = {f"service:{k}" for k in registry}
    stale_keys = [k for k in state["target_status"] if k.startswith("service:") and k not in valid_keys]
    for sk in stale_keys:
        del state["target_status"][sk]
        state["last_alerts"].pop(sk, None)

    for key, entry in registry.items():
        if entry.get("snoozed"):
            state["target_status"][f"service:{key}"] = {
                "status": "snoozed",
                "ok": False,
                "last_checked": now,
            }
            continue

        port = entry.get("port")
        health_ep = entry.get("health_endpoint")
        host = entry.get("host", "127.0.0.1")
        if host == "0.0.0.0":
            host = "127.0.0.1"

        if not health_ep or not port:
            continue

        url = f"http://{host}:{port}{health_ep}"

        # Check with one retry on failure (transient protection)
        ok, status_code, response_ms = check_health(url)
        if not ok:
            time.sleep(1)
            ok, status_code, response_ms = check_health(url)

        status = "healthy" if ok else "down"

        entry_data = {
            "status": status,
            "ok": ok,
            "status_code": status_code,
            "response_ms": response_ms,
            "last_checked": now,
            "url": url,
            "description": entry.get("description", ""),
            "port": port,
            "start_command": entry.get("start_command"),
            "startup_mode": entry.get("startup_mode", "on_demand"),
            "self_hosted": entry.get("self_hosted", False),
            "snoozed": False,
        }

        # Enrich Ollama with model count
        if key == "ollama" and ok:
            try:
                import urllib.request
                req = urllib.request.Request(f"http://{host}:{port}/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    models = body.get("models", [])
                    entry_data["model_count"] = len(models)
                    entry_data["models"] = [m.get("name", "") for m in models[:10]]
            except Exception:
                pass

        state["target_status"][f"service:{key}"] = entry_data

        # Alert tracking
        alert_key = f"service:{key}"
        if not ok:
            if alert_key not in state["last_alerts"]:
                state["last_alerts"][alert_key] = {
                    "timestamp": now,
                    "severity": "error",
                    "message": f"Service DOWN: {key} at {url}",
                }
                _log_alert(f"Service DOWN: {key} at {url}")
        else:
            # Clear recovered alerts
            if alert_key in state["last_alerts"]:
                state["last_alerts"][alert_key]["recovered"] = True
                state["last_alerts"][alert_key]["recovered_at"] = now

    state["last_checks"]["services"] = now
    save_state(state)
    return state


def _log_alert(message: str):
    """Append alert to today's alert log."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = LOG_DIR / f"{today}_alerts.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] [!] ALERT: {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Windows Port Management
# ---------------------------------------------------------------------------

def _pid_exists(pid: int) -> bool:
    """Check if a PID corresponds to a running process (Windows)."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return "No tasks" not in result.stdout and str(pid) in result.stdout
    except Exception:
        return False


def _check_port_listening(port: int) -> bool:
    """Check if a port has a listening service using pure-Python socket."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        result = s.connect_ex(("127.0.0.1", int(port)))
        s.close()
        return result == 0
    except Exception:
        return False


def _find_pids_on_port(port: int) -> Tuple[List[int], List[int]]:
    """Find PIDs listening on a port. Returns (live_pids, phantom_pids)."""
    live = set()
    phantom = set()
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    pid_str = parts[-1]
                    if pid_str.isdigit() and int(pid_str) > 0:
                        pid = int(pid_str)
                        if _pid_exists(pid):
                            live.add(pid)
                        else:
                            phantom.add(pid)
    except Exception as e:
        logger.warning(f"[!] netstat failed: {e}")
    return list(live), list(phantom)


def _kill_pids(pids: List[int]) -> List[int]:
    """Force-kill PIDs and their children (Windows taskkill)."""
    killed = []
    for pid in pids:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                killed.append(pid)
                logger.info(f"[OK] Killed PID {pid}")
            else:
                logger.warning(f"[!] taskkill failed for PID {pid}: {result.stderr.strip()}")
        except Exception as e:
            logger.warning(f"[!] Failed to kill PID {pid}: {e}")
    return killed


def _port_is_bindable(port: int, host: str = "127.0.0.1") -> bool:
    """Test if a port can be bound (handles phantom sockets)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.close()
        return True
    except OSError:
        return False


def _clear_phantom_sockets(port: int) -> int:
    """Log phantom sockets and check bindability."""
    if sys.platform != "win32":
        return 0
    _, phantoms = _find_pids_on_port(port)
    if not phantoms:
        return 0
    count = len(phantoms)
    if _port_is_bindable(port, "0.0.0.0"):
        logger.info(f"[OK] Port {port} has {count} phantom socket(s) but is bindable")
    else:
        logger.warning(f"[!] Port {port} has {count} phantom socket(s) and is NOT bindable")
    return count


def _wait_for_port_free(port: int, max_wait: int = 15) -> bool:
    """Wait until port is free or bindable."""
    for _ in range(max_wait):
        live, phantom = _find_pids_on_port(port)
        if not live:
            if phantom:
                _clear_phantom_sockets(port)
            return True
        time.sleep(1)
    if _port_is_bindable(port, "0.0.0.0"):
        return True
    logger.warning(f"[!] Port {port} still in use after {max_wait}s")
    return False


# ---------------------------------------------------------------------------
# Service Lifecycle
# ---------------------------------------------------------------------------

def _launch_service(entry: dict, service_key: str) -> subprocess.Popen:
    """Launch a service subprocess. Tracked in _child_processes."""
    start_cwd = entry.get("start_cwd", ".")
    cwd = Path(start_cwd)
    if not cwd.is_absolute():
        cwd = COHORT_ROOT / cwd

    start_command = entry["start_command"]
    port = entry.get("port")
    logger.info(f"[>>] Starting {service_key}: {start_command} (cwd={cwd})")

    # Close any previously tracked Popen
    old_proc = _child_processes.pop(service_key, None)
    if old_proc is not None:
        try:
            old_proc.wait(timeout=0)
        except Exception:
            pass

    # Kill anything on the port first
    if port:
        live_pids, _ = _find_pids_on_port(port)
        if live_pids:
            logger.info(f"[*] Port {port} occupied by PIDs {live_pids} - killing")
            _kill_pids(live_pids)
            _wait_for_port_free(port)

    if sys.platform == "win32":
        proc = subprocess.Popen(
            start_command,
            cwd=str(cwd),
            shell=True,
            creationflags=(
                subprocess.CREATE_NEW_CONSOLE
                | subprocess.CREATE_NEW_PROCESS_GROUP
            ),
        )
    else:
        proc = subprocess.Popen(
            start_command,
            cwd=str(cwd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    _child_processes[service_key] = proc
    return proc


def stop_service(service_key: str) -> dict:
    """Stop a service by killing all processes on its port.

    Returns result dict with success/error info.
    """
    entry = get_service_entry(service_key)
    if not entry:
        return {"error": f"Unknown service: {service_key}", "success": False}

    if entry.get("self_hosted"):
        return {"error": f"{service_key} is self-hosted and cannot be stopped from within", "success": False}

    port = entry.get("port")
    if not port:
        return {"error": f"No port configured for {service_key}", "success": False}

    # Terminate tracked Popen first
    tracked = _child_processes.pop(service_key, None)
    if tracked is not None:
        try:
            if tracked.poll() is None:
                tracked.terminate()
                tracked.wait(timeout=5)
                logger.info(f"[OK] Terminated tracked process for {service_key}")
        except Exception as e:
            logger.warning(f"[!] Tracked process cleanup for {service_key}: {e}")

    # Kill anything on the port
    live_pids, phantom_pids = _find_pids_on_port(port)
    killed = _kill_pids(live_pids) if live_pids else []
    phantom_cleared = 0
    if phantom_pids:
        phantom_cleared = _clear_phantom_sockets(port)

    # Update state
    state = _ensure_state()
    state_key = f"service:{service_key}"
    if state_key in state["target_status"]:
        state["target_status"][state_key]["status"] = "down"
        state["target_status"][state_key]["ok"] = False
        state["target_status"][state_key]["last_checked"] = datetime.now().isoformat()
    save_state(state)

    result = {
        "success": True,
        "service": service_key,
        "port": port,
        "killed_pids": killed,
    }
    if phantom_pids:
        result["phantom_pids"] = phantom_pids
        result["phantom_cleared"] = phantom_cleared
    return result


def start_service(service_key: str) -> dict:
    """Start a service, killing any existing processes on the port first.

    Returns result dict with success/health info.
    """
    entry = get_service_entry(service_key)
    if not entry:
        return {"error": f"Unknown service: {service_key}", "success": False}

    if entry.get("self_hosted"):
        return {"error": f"{service_key} is self-hosted and cannot be started from within", "success": False}

    start_command = entry.get("start_command")
    if not start_command:
        return {"error": f"No start_command configured for {service_key}", "success": False}

    port = entry.get("port")

    # Kill existing processes on port
    live_pids, phantom_pids = _find_pids_on_port(port)
    killed = _kill_pids(live_pids) if live_pids else []
    if killed or phantom_pids:
        _wait_for_port_free(port)

    _launch_service(entry, service_key)

    # Wait and verify
    time.sleep(3)
    host = entry.get("host", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    health_ep = entry.get("health_endpoint")
    health_url = f"http://{host}:{port}{health_ep}" if health_ep else ""
    healthy = False
    if health_url:
        ok, _, _ = check_health(health_url)
        healthy = ok

    # Update state
    state = _ensure_state()
    now = datetime.now().isoformat()
    state["target_status"][f"service:{service_key}"] = {
        "status": "healthy" if healthy else "down",
        "ok": healthy,
        "last_checked": now,
        "url": health_url,
        "description": entry.get("description", ""),
        "port": port,
        "start_command": start_command,
        "startup_mode": entry.get("startup_mode", "on_demand"),
        "self_hosted": entry.get("self_hosted", False),
        "snoozed": False,
    }
    save_state(state)

    result = {
        "success": True,
        "service": service_key,
        "port": port,
        "killed_pids": killed,
        "started": True,
        "healthy": healthy,
    }
    if phantom_pids:
        result["phantom_pids"] = phantom_pids
    return result


def restart_service(service_key: str) -> dict:
    """Stop and restart a service. Returns result dict."""
    entry = get_service_entry(service_key)
    if not entry:
        return {"error": f"Unknown service: {service_key}", "success": False}

    if entry.get("self_hosted"):
        return {"error": f"{service_key} is self-hosted and cannot be restarted from within", "success": False}

    start_command = entry.get("start_command")
    if not start_command:
        return {"error": f"No start_command configured for {service_key}", "success": False}

    port = entry.get("port")

    # Kill phase
    tracked = _child_processes.pop(service_key, None)
    if tracked is not None:
        try:
            if tracked.poll() is None:
                tracked.terminate()
                tracked.wait(timeout=5)
        except Exception:
            pass

    live_pids, phantom_pids = _find_pids_on_port(port)
    killed = _kill_pids(live_pids) if live_pids else []
    if killed or phantom_pids:
        _wait_for_port_free(port)

    # Start phase
    _launch_service(entry, service_key)

    # Verify
    time.sleep(3)
    host = entry.get("host", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    health_ep = entry.get("health_endpoint")
    health_url = f"http://{host}:{port}{health_ep}" if health_ep else ""
    healthy = False
    if health_url:
        ok, _, _ = check_health(health_url)
        healthy = ok

    # Update state
    state = _ensure_state()
    now = datetime.now().isoformat()
    state["target_status"][f"service:{service_key}"] = {
        "status": "healthy" if healthy else "down",
        "ok": healthy,
        "last_checked": now,
        "url": health_url,
        "description": entry.get("description", ""),
        "port": port,
        "start_command": start_command,
        "startup_mode": entry.get("startup_mode", "on_demand"),
        "self_hosted": entry.get("self_hosted", False),
        "snoozed": False,
    }
    save_state(state)

    result = {
        "success": True,
        "service": service_key,
        "port": port,
        "killed_pids": killed,
        "started": True,
        "healthy": healthy,
    }
    if phantom_pids:
        result["phantom_pids"] = phantom_pids
    return result


def list_services() -> List[dict]:
    """List all registered services with their current status."""
    registry = get_service_registry()
    state = _ensure_state()

    services = []
    for key, entry in registry.items():
        state_key = f"service:{key}"
        status_info = state.get("target_status", {}).get(state_key, {})

        svc_data = {
            "key": key,
            "description": entry.get("description", ""),
            "port": entry.get("port"),
            "status": status_info.get("status", "unknown"),
            "ok": status_info.get("ok", False),
            "response_ms": status_info.get("response_ms"),
            "last_checked": status_info.get("last_checked"),
            "start_command": entry.get("start_command"),
            "startup_mode": entry.get("startup_mode", "on_demand"),
            "self_hosted": entry.get("self_hosted", False),
            "snoozed": entry.get("snoozed", False),
            "controllable": bool(entry.get("start_command")) and not entry.get("self_hosted"),
        }
        # Include Ollama model info if available
        if key == "ollama" and "model_count" in status_info:
            svc_data["model_count"] = status_info["model_count"]
            svc_data["models"] = status_info.get("models", [])
        services.append(svc_data)
    return services
