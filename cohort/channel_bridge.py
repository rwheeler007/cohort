"""Channel Bridge -- per-channel Claude Code session management.

Each Cohort chat channel gets its own Claude Code session, spawned on demand
and reaped after idle timeout.  The agent router enqueues prompts; the channel
plugin (running inside the spawned Claude session) polls, claims, and responds.

Thread-safe.  All prompt construction stays in the existing codebase.

Status lifecycle::

    pending --> claimed --> completed
                  |
                  +--> failed
                  |
                  +--> timeout

Usage::

    from cohort.channel_bridge import (
        ensure_channel_session,
        enqueue_channel_request,
        await_channel_response,
        channel_mode_active,
    )
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# Configuration
# =====================================================================

HEARTBEAT_TIMEOUT_S = 30
CLAIMED_STALE_TIMEOUT_S = 60
SPAWN_WAIT_TIMEOUT_S = 30
IDLE_REAP_SECONDS = 1800  # 30 minutes

CLAUDE_CMD = os.environ.get("COHORT_CLAUDE_CMD", "claude")
COHORT_BASE_URL = os.environ.get("COHORT_BASE_URL", "http://localhost:5100")
CHANNEL_MODEL = os.environ.get("COHORT_CHANNEL_MODEL", "sonnet")

# System prompt loaded once from plugin directory
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "plugins" / "cohort-channel" / "system_prompt.md"
_system_prompt_cache: Optional[str] = None


def _get_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        try:
            _system_prompt_cache = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            _system_prompt_cache = "You are an agent in the Cohort team chat system."
            logger.warning("[!] system_prompt.md not found at %s, using fallback", _SYSTEM_PROMPT_PATH)
    return _system_prompt_cache


# =====================================================================
# Channel request queue (in-memory, thread-safe)
# =====================================================================

_channel_queue: deque[Dict[str, Any]] = deque(maxlen=100)
_channel_lock = threading.Lock()
_channel_cv = threading.Condition(_channel_lock)

# =====================================================================
# Per-channel session registry
# =====================================================================

_channel_sessions: Dict[str, Dict[str, Any]] = {}
# Each entry: {
#     "session_id": str,
#     "last_heartbeat": float,
#     "last_activity": float,
#     "pid": int,
# }
_sessions_lock = threading.Lock()

# =====================================================================
# Launch queue -- extension polls this to spawn VS Code terminals
# =====================================================================

_launch_queue: deque[Dict[str, Any]] = deque(maxlen=20)
_launch_lock = threading.Lock()


# =====================================================================
# Public API -- called by agent_router.py
# =====================================================================

def ensure_channel_session(channel_id: str) -> str:
    """Ensure a Claude Code channel session exists and is alive.

    Returns a string indicating outcome:
      "existing"  -- healthy session already running
      "vscode"    -- VS Code extension spawned it
      "direct"    -- fell back to direct spawn (no VS Code)
      ""          -- failed to create a session (falsy)
    """
    with _sessions_lock:
        session = _channel_sessions.get(channel_id)
        if session and _is_session_alive(session):
            session["last_activity"] = time.time()
            return "existing"

    # Add to launch queue for VS Code extension to pick up
    _add_to_launch_queue(channel_id)

    # Wait for the VS Code extension to spawn it (polls every ~10s,
    # heartbeat every 1s once alive)
    _vscode_wait = min(SPAWN_WAIT_TIMEOUT_S, 15)
    deadline = time.time() + _vscode_wait
    while time.time() < deadline:
        time.sleep(1.0)
        with _sessions_lock:
            session = _channel_sessions.get(channel_id)
            if session and _is_session_alive(session):
                session["last_activity"] = time.time()
                logger.info("[OK] Channel session for #%s came alive via VS Code", channel_id)
                return "vscode"

    # VS Code didn't spawn it in time -- try direct spawn as fallback
    logger.info("[*] VS Code didn't launch #%s in %ds, spawning directly", channel_id, _vscode_wait)
    if _spawn_channel_session(channel_id):
        return "direct"
    return ""


def _add_to_launch_queue(channel_id: str) -> None:
    """Add a channel to the launch queue if not already queued."""
    with _launch_lock:
        for item in _launch_queue:
            if item["channel_id"] == channel_id:
                return  # Already queued
        _launch_queue.append({
            "channel_id": channel_id,
            "queued_at": time.time(),
        })
    logger.info("[>>] Added #%s to launch queue for VS Code extension", channel_id)


def pop_launch_queue() -> Optional[Dict[str, Any]]:
    """Pop the next channel needing a session (called by extension poll)."""
    with _launch_lock:
        if _launch_queue:
            return _launch_queue[0]  # Peek, don't pop -- wait for ACK
    return None


def ack_launch(channel_id: str) -> bool:
    """Acknowledge a launch (extension spawned the terminal)."""
    with _launch_lock:
        for i, item in enumerate(_launch_queue):
            if item["channel_id"] == channel_id:
                del _launch_queue[i]
                logger.info("[OK] Launch ACK for #%s", channel_id)
                return True
    return False


def enqueue_channel_request(
    prompt: str,
    agent_id: str,
    channel_id: str,
    thread_id: Optional[str] = None,
    response_mode: str = "channel",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Add an agent prompt to the channel queue.

    Returns the request_id.
    """
    request_id = f"ch_{uuid.uuid4().hex[:8]}"
    request = {
        "id": request_id,
        "status": "pending",
        "created_at": time.time(),
        "prompt": prompt,
        "agent_id": agent_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "response_mode": response_mode,
        "metadata": metadata or {},
        "response_content": None,
        "response_metadata": None,
        "error": None,
    }

    with _channel_cv:
        _channel_queue.append(request)
        _channel_cv.notify_all()

    # Update activity timestamp for this channel's session
    with _sessions_lock:
        session = _channel_sessions.get(channel_id)
        if session:
            session["last_activity"] = time.time()

    logger.info(
        "[>>] Channel request enqueued: %s for %s in #%s",
        request_id, agent_id, channel_id,
    )
    return request_id


def await_channel_response(
    request_id: str,
    timeout: float = 300.0,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Block until the channel plugin delivers a response or timeout."""
    deadline = time.time() + timeout

    with _channel_cv:
        while True:
            req = _find_request(request_id)
            if req is None:
                logger.warning("[!] Channel request %s not found", request_id)
                return None, {"error": "request_not_found"}

            if req["status"] == "completed":
                _cleanup_request(request_id)
                return req["response_content"], req.get("response_metadata", {})

            if req["status"] == "failed":
                _cleanup_request(request_id)
                return None, {"error": req.get("error", "channel_error")}

            # Auto-fail if claimed but session died
            if req["status"] == "claimed":
                ch_id = req.get("channel_id")
                if ch_id and not channel_mode_active(ch_id):
                    claimed_age = time.time() - req.get("claimed_at", req["created_at"])
                    if claimed_age > CLAIMED_STALE_TIMEOUT_S:
                        req["status"] = "failed"
                        req["error"] = "channel_session_lost"
                        logger.warning(
                            "[X] Channel request %s auto-failed: session lost",
                            request_id,
                        )
                        _cleanup_request(request_id)
                        return None, {"error": "channel_session_lost"}

            remaining = deadline - time.time()
            if remaining <= 0:
                req["status"] = "timeout"
                logger.warning("[X] Channel request %s timed out", request_id)
                _cleanup_request(request_id)
                return None, {"error": "channel_timeout"}

            _channel_cv.wait(timeout=min(remaining, 2.0))


def channel_mode_active(channel_id: Optional[str] = None) -> bool:
    """Check if a healthy channel session exists.

    If channel_id is provided, check that specific channel.
    If None, check if ANY session is healthy.
    """
    with _sessions_lock:
        if channel_id:
            session = _channel_sessions.get(channel_id)
            return session is not None and _is_session_alive(session)
        return any(_is_session_alive(s) for s in _channel_sessions.values())


# =====================================================================
# Public API -- called by server.py channel endpoints
# =====================================================================

def poll_next_request(channel_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the next pending request metadata.

    If channel_id is provided, only return requests for that channel.
    """
    with _channel_lock:
        for req in _channel_queue:
            if req["status"] == "pending":
                if channel_id and req["channel_id"] != channel_id:
                    continue
                return {
                    "id": req["id"],
                    "agent_id": req["agent_id"],
                    "channel_id": req["channel_id"],
                    "response_mode": req["response_mode"],
                }
    return None


def claim_request(request_id: str, session_id: str = "unknown") -> Optional[Dict[str, Any]]:
    """Claim a pending request.  Returns the full prompt and metadata."""
    with _channel_lock:
        req = _find_request(request_id)
        if req is None or req["status"] != "pending":
            return None

        req["status"] = "claimed"
        req["claimed_at"] = time.time()
        req["claimed_by"] = session_id

    logger.info("[>>] Channel request claimed: %s by %s", request_id, session_id)
    return {
        "id": req["id"],
        "prompt": req["prompt"],
        "agent_id": req["agent_id"],
        "channel_id": req["channel_id"],
        "thread_id": req["thread_id"],
        "response_mode": req["response_mode"],
        "metadata": req["metadata"],
    }


def deliver_response(
    request_id: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Deliver a response for a claimed request."""
    with _channel_cv:
        req = _find_request(request_id)
        if req is None or req["status"] != "claimed":
            return False

        req["status"] = "completed"
        req["response_content"] = content
        req["completed_at"] = time.time()

        elapsed = req["completed_at"] - req.get("claimed_at", req["created_at"])
        default_meta = {
            "tier": 5,
            "model": "claude_code_channel",
            "confidence": "high",
            "pipeline": "channel",
            "elapsed_seconds": round(elapsed, 2),
            "tokens_out_estimate": len(content) // 4,
        }
        if metadata:
            default_meta.update(metadata)
        req["response_metadata"] = default_meta

        _channel_cv.notify_all()

    logger.info("[OK] Channel response delivered: %s (%.1fs)", request_id, elapsed)
    return True


def deliver_error(request_id: str, error: str) -> bool:
    """Report an error for a claimed request."""
    with _channel_cv:
        req = _find_request(request_id)
        if req is None or req["status"] != "claimed":
            return False

        req["status"] = "failed"
        req["error"] = error
        _channel_cv.notify_all()

    logger.warning("[X] Channel error for %s: %s", request_id, error)
    return True


def update_heartbeat(
    session_id: str,
    pid: Optional[int] = None,
    channel_id: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Update channel session heartbeat.

    If channel_id provided, updates that channel's session.
    Otherwise updates/creates a '_global' entry (backward compat).
    """
    key = channel_id or "_global"
    now = time.time()
    with _sessions_lock:
        if key not in _channel_sessions:
            _channel_sessions[key] = {
                "session_id": session_id,
                "last_heartbeat": now,
                "last_activity": now,
                "pid": pid,
                "process": None,
            }
        else:
            _channel_sessions[key]["session_id"] = session_id
            _channel_sessions[key]["last_heartbeat"] = now
            _channel_sessions[key]["pid"] = pid


def get_session_status(channel_id: Optional[str] = None) -> Dict[str, Any]:
    """Return channel session health for status endpoints."""
    with _sessions_lock:
        if channel_id:
            session = _channel_sessions.get(channel_id)
            if session is None:
                return {"healthy": False, "session_id": None, "last_heartbeat": None,
                        "stale_seconds": None, "queue_depth": 0}
            return _session_to_status(session, channel_id)

        # Summary of all sessions
        sessions = {}
        for ch_id, session in _channel_sessions.items():
            sessions[ch_id] = _session_to_status(session, ch_id)

        any_healthy = any(s["healthy"] for s in sessions.values())
        total_queue = sum(1 for req in _channel_queue if req["status"] == "pending")
        return {
            "healthy": any_healthy,
            "sessions": sessions,
            "session_count": len(sessions),
            "queue_depth": total_queue,
        }


def get_all_sessions_status() -> Dict[str, Any]:
    """Return detailed session status in the format expected by the VS Code extension.

    Used by ``/api/channel/sessions`` for the Channel Sessions panel.
    """
    now = time.time()
    channels: Dict[str, Any] = {}

    with _sessions_lock:
        for channel_id, session in _channel_sessions.items():
            last_hb = session.get("last_heartbeat", 0)
            channels[channel_id] = {
                "sessions": [{
                    "session_id": session.get("session_id"),
                    "pid": session.get("pid"),
                    "registered_at": session.get("registered_at"),
                    "last_heartbeat": last_hb,
                    "healthy": (now - last_hb) < HEARTBEAT_TIMEOUT_S if last_hb else False,
                    "stale_seconds": round(now - last_hb, 1) if last_hb else None,
                }],
                "queue_depth": 0,
            }

        total_queue = sum(1 for req in _channel_queue if req["status"] == "pending")
        total_healthy = sum(
            1 for s in _channel_sessions.values()
            if _is_session_alive(s)
        )

    # Distribute queue depth to channels (best-effort: assign to first channel)
    for req in _channel_queue:
        if req["status"] == "pending":
            ch = req.get("channel_id")
            if ch and ch in channels:
                channels[ch]["queue_depth"] += 1

    return {
        "channels": channels,
        "total_sessions": len(channels),
        "total_healthy": total_healthy,
        "total_queue_depth": total_queue,
        "thresholds": {
            "limit": 5,
            "warn": 3,
            "default": 1,
            "idle_timeout": 600,
            "auto_launch": False,
        },
        "launch_queue": [],
    }


def register_channel_session(
    channel_id: str,
    session_id: str,
    pid: Optional[int] = None,
) -> Dict[str, Any]:
    """Register a Claude Code session for a specific channel.

    Enforces a hard session limit.  Returns registration result.
    Called by ``/api/channel/register``.
    """
    SESSION_LIMIT = 5
    now = time.time()

    with _sessions_lock:
        active_count = sum(1 for s in _channel_sessions.values() if _is_session_alive(s))

        if active_count >= SESSION_LIMIT:
            logger.warning(
                "[X] Session limit reached (%d/%d), rejecting %s for #%s",
                active_count, SESSION_LIMIT, session_id, channel_id,
            )
            return {
                "ok": False,
                "error": "session_limit_reached",
                "limit": SESSION_LIMIT,
                "active": active_count,
            }

        _channel_sessions[channel_id] = {
            "session_id": session_id,
            "pid": pid,
            "registered_at": now,
            "last_heartbeat": now,
            "last_activity": now,
        }

    logger.info(
        "[OK] Registered session %s for #%s (pid=%s, %d/%d active)",
        session_id, channel_id, pid, active_count + 1, SESSION_LIMIT,
    )
    return {
        "ok": True,
        "session_id": session_id,
        "active": active_count + 1,
        "limit": SESSION_LIMIT,
    }


# =====================================================================
# Session lifecycle -- spawn and reap
# =====================================================================

def _spawn_channel_session(channel_id: str) -> bool:
    """Spawn a Claude Code channel session for a specific channel.

    Blocks until the session heartbeats or times out.
    """
    logger.info("[>>] Spawning channel session for #%s", channel_id)

    system_prompt = _get_system_prompt()

    cmd = [
        CLAUDE_CMD,
        "--dangerously-load-development-channels", "server:cohort-wq",
        "--permission-mode", "acceptEdits",
        "--allowedTools",
        "mcp__cohort-wq__cohort_respond,mcp__cohort-wq__cohort_error,mcp__cohort-wq__cohort_post",
        "--model", CHANNEL_MODEL,
        "--system-prompt", system_prompt,
    ]

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
    env["CHANNEL_ID"] = channel_id
    env["COHORT_BASE_URL"] = COHORT_BASE_URL
    env["CHANNEL_NAME"] = f"cohort-ch-{channel_id}"

    cohort_root = Path(__file__).parent.parent

    # On Windows, give Claude Code its own console so it stays in
    # interactive mode.  The channel plugin feeds it work via MCP
    # notifications; stdin is not used.
    popen_kwargs: dict = dict(
        cwd=str(cohort_root),
        env=env,
    )
    if sys.platform == "win32":
        cmd = ["cmd", "/c"] + cmd
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        popen_kwargs["stdin"] = subprocess.PIPE
        popen_kwargs["stdout"] = subprocess.DEVNULL
        popen_kwargs["stderr"] = subprocess.DEVNULL

    try:
        process = subprocess.Popen(cmd, **popen_kwargs)
    except Exception:
        logger.exception("[X] Failed to spawn channel session for #%s", channel_id)
        return False

    now = time.time()
    with _sessions_lock:
        _channel_sessions[channel_id] = {
            "session_id": f"cohort-ch-{channel_id}-{int(now)}",
            "last_heartbeat": None,
            "last_activity": now,
            "pid": process.pid,
            "process": process,
        }

    logger.info("[*] Waiting for channel session #%s to connect (PID %d)...", channel_id, process.pid)

    # Poll for heartbeat
    deadline = time.time() + SPAWN_WAIT_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(1.0)

        # Check if process died
        if process.poll() is not None:
            logger.error("[X] Channel session for #%s exited with code %d", channel_id, process.returncode)
            with _sessions_lock:
                _channel_sessions.pop(channel_id, None)
            return False

        # Check for heartbeat
        with _sessions_lock:
            session = _channel_sessions.get(channel_id)
            if session and session.get("last_heartbeat") is not None:
                logger.info("[OK] Channel session for #%s connected (PID %d)", channel_id, process.pid)
                return True

    # Timed out waiting for heartbeat
    logger.error("[X] Channel session for #%s timed out waiting for heartbeat", channel_id)
    process.terminate()
    with _sessions_lock:
        _channel_sessions.pop(channel_id, None)
    return False


def reap_idle_sessions(max_idle_seconds: Optional[float] = None) -> int:
    """Kill channel sessions that have been idle too long.

    Returns number of sessions reaped.
    """
    threshold = max_idle_seconds if max_idle_seconds is not None else IDLE_REAP_SECONDS
    now = time.time()
    to_reap = []

    with _sessions_lock:
        for ch_id, session in list(_channel_sessions.items()):
            if ch_id == "_global":
                continue
            idle = now - session.get("last_activity", now)
            if idle > threshold:
                to_reap.append((ch_id, session))

    reaped = 0
    for ch_id, session in to_reap:
        logger.info(
            "[*] Reaping idle channel session #%s (idle %.0fs, PID %s)",
            ch_id, now - session.get("last_activity", now), session.get("pid"),
        )
        _kill_session(session)
        with _sessions_lock:
            _channel_sessions.pop(ch_id, None)
        reaped += 1

    return reaped


# =====================================================================
# Internal helpers
# =====================================================================

def _is_session_alive(session: Dict[str, Any]) -> bool:
    """Check if a session has a recent heartbeat."""
    last_hb = session.get("last_heartbeat")
    if last_hb is None:
        return False
    return (time.time() - last_hb) < HEARTBEAT_TIMEOUT_S


def _session_to_status(session: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
    """Convert a session dict to a status response."""
    last_hb = session.get("last_heartbeat")
    healthy = _is_session_alive(session)
    return {
        "healthy": healthy,
        "channel_id": channel_id,
        "session_id": session.get("session_id"),
        "last_heartbeat": last_hb,
        "stale_seconds": round(time.time() - last_hb, 1) if last_hb else None,
        "pid": session.get("pid"),
        "last_activity": session.get("last_activity"),
    }


def _kill_session(session: Dict[str, Any]) -> None:
    """Terminate a session process gracefully, then force-kill."""
    proc = session.get("process")
    if proc is None:
        # External session (not spawned by us), try OS kill
        pid = session.get("pid")
        if pid:
            try:
                os.kill(pid, 15)  # SIGTERM
            except (OSError, ProcessLookupError):
                pass
        return

    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except (OSError, ProcessLookupError):
        pass


def _find_request(request_id: str) -> Optional[Dict[str, Any]]:
    """Find a request by ID.  Must be called under lock."""
    for req in _channel_queue:
        if req["id"] == request_id:
            return req
    return None


def _cleanup_request(request_id: str) -> None:
    """Remove a terminal request from the queue.  Must be called under lock."""
    for i, req in enumerate(_channel_queue):
        if req["id"] == request_id:
            del _channel_queue[i]
            return
