"""Channel Bridge -- per-channel Claude Code session management.

Each Cohort chat channel gets its own Claude Code session, spawned on demand
and reaped after idle timeout.  The agent router enqueues prompts; the channel
plugin (running inside the spawned Claude session) polls, claims, and responds.

Thread-safe.  All prompt construction stays in the existing codebase.

Features:
  - Per-channel request queues (no cross-channel starvation)
  - Multi-session per channel (nested session registry)
  - Configurable session limits, idle timeout, auto-launch
  - Built-in idle reaper daemon
  - Priority eviction of least-active idle sessions
  - Direct spawn fallback when VS Code extension is unavailable

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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# Configuration
# =====================================================================

HEARTBEAT_TIMEOUT_S = 30
CLAIMED_STALE_TIMEOUT_S = 60
SPAWN_WAIT_TIMEOUT_S = 30

CLAUDE_CMD = os.environ.get("COHORT_CLAUDE_CMD", "claude")
COHORT_BASE_URL = os.environ.get("COHORT_BASE_URL", "http://localhost:5100")
CHANNEL_MODEL = os.environ.get("COHORT_CHANNEL_MODEL", "sonnet")

# System prompt loaded once from plugin directory
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "plugins" / "cohort-channel" / "system_prompt.md"
_system_prompt_cache: Optional[str] = None

# User-configurable thresholds (set via apply_channel_settings)
_session_limit: int = 5       # Hard cap -- refuse new sessions beyond this
_session_warn: int = 3        # Warning threshold
_session_default: int = 1     # Sessions to launch on startup (0 = on-demand)
_idle_timeout_s: int = 600    # 10 minutes -- kill idle sessions
_auto_launch: bool = False    # Auto-launch sessions on demand (opt-in)

# Reaper daemon state
_reaper_started: bool = False

_GLOBAL_KEY = "_global"  # Key for unscoped MCP work-queue sessions


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
# Per-channel request queues (in-memory, thread-safe)
# =====================================================================

_channel_queues: Dict[str, deque] = {}  # channel_id -> deque(maxlen=100)
_channel_lock = threading.Lock()
_channel_cv = threading.Condition(_channel_lock)

# =====================================================================
# Per-channel session registry (nested: channel_id -> session_id -> info)
# =====================================================================

_channel_sessions: Dict[str, Dict[str, Dict[str, Any]]] = {}
# channel_id -> { session_id -> {pid, registered_at, last_heartbeat, process, ...} }
_sessions_lock = threading.Lock()

# Track last activity per channel (for idle reaper)
_channel_last_activity: Dict[str, float] = {}

# =====================================================================
# Launch queue -- extension polls this to spawn VS Code terminals
# =====================================================================

_launch_queue: deque[Dict[str, Any]] = deque(maxlen=20)
_launch_lock = threading.Lock()


# =====================================================================
# Settings API
# =====================================================================

def apply_channel_settings(
    limit: int = 5,
    warn: int = 3,
    default: int = 1,
    idle_timeout: int = 600,
    auto_launch: bool = False,
) -> None:
    """Update session threshold settings.  Called from server.py post_settings()."""
    global _session_limit, _session_warn, _session_default, _idle_timeout_s, _auto_launch  # noqa: PLW0603
    _session_limit = max(1, limit)
    _session_warn = max(1, warn)
    _session_default = max(0, default)
    _idle_timeout_s = max(60, idle_timeout)  # Minimum 1 minute
    _auto_launch = bool(auto_launch)
    logger.info(
        "[OK] Channel session thresholds: limit=%d, warn=%d, default=%d, "
        "idle_timeout=%ds, auto_launch=%s",
        _session_limit, _session_warn, _session_default,
        _idle_timeout_s, _auto_launch,
    )
    _start_reaper()


# =====================================================================
# Session helpers (must be called under _sessions_lock)
# =====================================================================

def _is_session_alive(info: Dict[str, Any]) -> bool:
    """Check if a single session info dict has a recent heartbeat."""
    last_hb = info.get("last_heartbeat")
    if last_hb is None:
        return False
    return (time.time() - last_hb) < HEARTBEAT_TIMEOUT_S


def _count_healthy_sessions() -> int:
    """Count healthy per-channel sessions (excludes _global).  Under lock."""
    count = 0
    for channel_id, ch_sessions in _channel_sessions.items():
        if channel_id == _GLOBAL_KEY:
            continue
        for info in ch_sessions.values():
            if _is_session_alive(info):
                count += 1
    return count


def _prune_stale_sessions() -> None:
    """Remove sessions whose heartbeat is older than 2x timeout.  Under lock."""
    cutoff = HEARTBEAT_TIMEOUT_S * 2
    now = time.time()
    for channel_id in list(_channel_sessions):
        ch_sessions = _channel_sessions[channel_id]
        stale = [
            sid for sid, info in ch_sessions.items()
            if (now - info.get("last_heartbeat", 0)) > cutoff
            and info.get("last_heartbeat") is not None
        ]
        for sid in stale:
            del ch_sessions[sid]
            logger.info("[*] Pruned stale session %s from #%s", sid, channel_id)
        if not ch_sessions:
            del _channel_sessions[channel_id]


def _get_any_alive_session(channel_id: str) -> Optional[Dict[str, Any]]:
    """Return the first alive session info for a channel, or None.  Under lock."""
    ch_sessions = _channel_sessions.get(channel_id, {})
    for info in ch_sessions.values():
        if _is_session_alive(info):
            return info
    return None


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
        info = _get_any_alive_session(channel_id)
        if info is not None:
            touch_channel_activity(channel_id)
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
            info = _get_any_alive_session(channel_id)
            if info is not None:
                touch_channel_activity(channel_id)
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
    """Peek the next channel needing a session (called by extension poll)."""
    with _launch_lock:
        if _launch_queue:
            return _launch_queue[0]
    return None


def get_launch_queue() -> list:
    """Return the current launch queue (for status endpoints)."""
    with _launch_lock:
        return list(_launch_queue)


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
    """Add an agent prompt to the per-channel queue.

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
        if channel_id not in _channel_queues:
            _channel_queues[channel_id] = deque(maxlen=100)
        _channel_queues[channel_id].append(request)
        _channel_cv.notify_all()

    touch_channel_activity(channel_id)

    # Demand-driven launch: if no session exists, queue one.
    # Uses force=True to bypass the auto_launch gate -- this is a real
    # request that needs a session, not a proactive startup launch.
    if not channel_mode_active(channel_id=channel_id):
        request_session(channel_id, force=True)

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
                            "[X] Channel request %s auto-failed: session lost, "
                            "claimed %.0fs ago with no heartbeat",
                            request_id, claimed_age,
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
            return _get_any_alive_session(channel_id) is not None
        for ch_id in _channel_sessions:
            if _get_any_alive_session(ch_id) is not None:
                return True
        return False


# =====================================================================
# Public API -- called by server.py channel endpoints
# =====================================================================

def poll_next_request(channel_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the next pending request metadata.

    If channel_id is provided, only search that channel's queue.
    If None, search all queues (backward compat for global poll).
    """
    with _channel_lock:
        queues: List[deque] = []
        if channel_id is not None:
            q = _channel_queues.get(channel_id)
            if q:
                queues.append(q)
        else:
            queues.extend(_channel_queues.values())

        for q in queues:
            for req in q:
                if req["status"] == "pending":
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

    touch_channel_activity(req["channel_id"])
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
        tokens_in = len(req.get("prompt", "")) // 4
        tokens_out = len(content) // 4
        default_meta = {
            "tier": 5,
            "model": "claude_code_channel",
            "confidence": "high",
            "pipeline": "channel",
            "elapsed_seconds": round(elapsed, 2),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
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

    For per-channel sessions, only updates existing entries -- sessions
    must be created via register_channel_session() or _spawn_channel_session().
    Global sessions auto-register on first heartbeat for backward compat.
    """
    key = channel_id or _GLOBAL_KEY
    now = time.time()
    with _sessions_lock:
        if key not in _channel_sessions:
            if key == _GLOBAL_KEY:
                # Auto-register global MCP sessions (backward compat)
                _channel_sessions[key] = {}
                _channel_sessions[key][session_id] = {
                    "pid": pid,
                    "registered_at": now,
                    "last_heartbeat": now,
                    "last_activity": now,
                    "process": None,
                }
            return  # Ignore heartbeats from unregistered per-channel sessions

        ch_sessions = _channel_sessions[key]

        # Find session by session_id, or by matching PID for spawned sessions
        if session_id in ch_sessions:
            ch_sessions[session_id]["last_heartbeat"] = now
            if pid is not None:
                ch_sessions[session_id]["pid"] = pid
        elif key == _GLOBAL_KEY:
            # Auto-register new global sessions
            ch_sessions[session_id] = {
                "pid": pid,
                "registered_at": now,
                "last_heartbeat": now,
                "last_activity": now,
                "process": None,
            }
        else:
            # Per-channel: find by PID match (spawned sessions don't know their session_id)
            for sid, info in ch_sessions.items():
                if info.get("pid") == pid and pid is not None:
                    info["last_heartbeat"] = now
                    info["session_id"] = session_id  # Update session_id from heartbeat
                    return
            # Also accept if there's exactly one session for this channel (spawned)
            if len(ch_sessions) == 1:
                only_info = next(iter(ch_sessions.values()))
                if only_info.get("last_heartbeat") is None:
                    # First heartbeat for a spawned session
                    only_info["last_heartbeat"] = now
                    only_info["session_id"] = session_id
                    if pid is not None:
                        only_info["pid"] = pid


def register_channel_session(
    channel_id: str,
    session_id: str,
    pid: Optional[int] = None,
) -> Dict[str, Any]:
    """Register a Claude Code session for a specific channel.

    Enforces the hard session limit.  Returns registration result with
    warning flag if at or above warning threshold.
    """
    with _sessions_lock:
        _prune_stale_sessions()
        active_count = _count_healthy_sessions()

        if active_count >= _session_limit:
            # Try priority eviction before rejecting
            evicted = _try_evict_idle_session()
            if evicted:
                logger.info("[*] Evicted idle session from #%s to make room for #%s", evicted, channel_id)
                active_count -= 1
            else:
                logger.warning(
                    "[X] Session limit reached (%d/%d), rejecting %s for #%s",
                    active_count, _session_limit, session_id, channel_id,
                )
                return {
                    "ok": False,
                    "error": "session_limit_reached",
                    "limit": _session_limit,
                    "active": active_count,
                }

        if channel_id not in _channel_sessions:
            _channel_sessions[channel_id] = {}

        now = time.time()
        _channel_sessions[channel_id][session_id] = {
            "pid": pid,
            "registered_at": now,
            "last_heartbeat": now,
            "last_activity": now,
            "process": None,
        }
        active_count += 1

    logger.info(
        "[OK] Registered session %s for #%s (pid=%s, %d/%d active)",
        session_id, channel_id, pid, active_count, _session_limit,
    )
    return {
        "ok": True,
        "channel_id": channel_id,
        "session_id": session_id,
        "warn": active_count >= _session_warn,
        "active": active_count,
        "limit": _session_limit,
    }


def unregister_channel_session(channel_id: str, session_id: str) -> bool:
    """Remove a session registration.  Returns True if found."""
    with _sessions_lock:
        ch_sessions = _channel_sessions.get(channel_id)
        if ch_sessions and session_id in ch_sessions:
            del ch_sessions[session_id]
            if not ch_sessions:
                del _channel_sessions[channel_id]
            logger.info("[OK] Unregistered session %s from #%s", session_id, channel_id)
            return True
    return False


def purge_all_sessions() -> int:
    """Remove ALL sessions from the registry.  Returns count purged."""
    with _sessions_lock:
        total = sum(len(s) for s in _channel_sessions.values())
        _channel_sessions.clear()
        logger.info("[*] Purged all %d sessions from registry", total)
        return total


def get_session_status(channel_id: Optional[str] = None) -> Dict[str, Any]:
    """Return channel session health for status endpoints."""
    now = time.time()
    with _sessions_lock:
        if channel_id:
            ch_sessions = _channel_sessions.get(channel_id, {})
            healthy = any(_is_session_alive(info) for info in ch_sessions.values())
            # Find most recent session for backward-compat response shape
            latest = max(
                ch_sessions.values(),
                key=lambda s: s.get("last_heartbeat", 0),
                default={},
            )
            last_hb = latest.get("last_heartbeat")
            queue = _channel_queues.get(channel_id, deque())
            return {
                "healthy": healthy,
                "session_id": latest.get("session_id"),
                "last_heartbeat": last_hb,
                "stale_seconds": round(now - last_hb, 1) if last_hb else None,
                "queue_depth": sum(1 for r in queue if r["status"] == "pending"),
            }

        # Aggregate across all sessions
        any_healthy = channel_mode_active()
        all_info = [
            info
            for ch_sessions in _channel_sessions.values()
            for info in ch_sessions.values()
        ]
        latest = max(
            all_info,
            key=lambda s: s.get("last_heartbeat", 0),
            default={},
        )
        last_hb = latest.get("last_heartbeat")
        total_pending = sum(
            1
            for q in _channel_queues.values()
            for r in q
            if r["status"] == "pending"
        )
        return {
            "healthy": any_healthy,
            "session_id": None,
            "last_heartbeat": last_hb,
            "stale_seconds": round(now - last_hb, 1) if last_hb else None,
            "queue_depth": total_pending,
        }


def get_all_sessions_status() -> Dict[str, Any]:
    """Return detailed status of all channel sessions.

    Used by /api/channel/sessions for the Channel Sessions panel.
    """
    now = time.time()
    channels: Dict[str, Any] = {}

    with _sessions_lock:
        _prune_stale_sessions()

        for channel_id, ch_sessions in _channel_sessions.items():
            sessions_list = []
            for session_id, info in ch_sessions.items():
                last_hb = info.get("last_heartbeat", 0)
                sessions_list.append({
                    "session_id": info.get("session_id", session_id),
                    "pid": info.get("pid"),
                    "registered_at": info.get("registered_at"),
                    "last_heartbeat": last_hb,
                    "healthy": _is_session_alive(info),
                    "stale_seconds": round(now - last_hb, 1) if last_hb else None,
                })

            queue = _channel_queues.get(channel_id, deque())
            channels[channel_id] = {
                "sessions": sessions_list,
                "queue_depth": sum(1 for r in queue if r["status"] == "pending"),
            }

        total_healthy = _count_healthy_sessions()

    return {
        "channels": channels,
        "total_sessions": sum(len(ch["sessions"]) for ch in channels.values()),
        "total_healthy": total_healthy,
        "total_queue_depth": sum(ch["queue_depth"] for ch in channels.values()),
        "thresholds": {
            "limit": _session_limit,
            "warn": _session_warn,
            "default": _session_default,
            "idle_timeout": _idle_timeout_s,
            "auto_launch": _auto_launch,
        },
        "launch_queue": get_launch_queue(),
    }


# =====================================================================
# Activity tracking
# =====================================================================

def touch_channel_activity(channel_id: str) -> None:
    """Record activity on a channel (for idle reaper scoring)."""
    _channel_last_activity[channel_id] = time.time()


# =====================================================================
# Auto-launch and priority eviction
# =====================================================================

def request_session(channel_id: str, *, force: bool = False) -> Dict[str, Any]:
    """Request a session for a channel.

    Args:
        channel_id: Channel needing a session.
        force: If True, bypass the auto_launch gate.  Used for demand-driven
               launches (e.g. @mention triggers agent response and needs a
               session).  The auto_launch setting only gates proactive/startup
               launches.

    If auto-launch is enabled (or force=True) and we're under the session
    limit, adds the channel to the launch queue.  If at the limit, attempts
    priority eviction of the least-active idle session.
    """
    if not force and not _auto_launch:
        return {"queued": False, "reason": "auto_launch_disabled"}

    if channel_mode_active(channel_id=channel_id):
        return {"queued": False, "reason": "session_exists"}

    with _launch_lock:
        if any(item["channel_id"] == channel_id for item in _launch_queue):
            return {"queued": False, "reason": "already_queued"}

    with _sessions_lock:
        _prune_stale_sessions()
        active = _count_healthy_sessions()

        if active >= _session_limit:
            evicted = _try_evict_idle_session()
            if not evicted:
                return {
                    "queued": False,
                    "reason": "at_limit_no_idle",
                    "limit": _session_limit,
                    "active": active,
                }
            logger.info(
                "[*] Evicted idle session from #%s to make room for #%s",
                evicted, channel_id,
            )

    _add_to_launch_queue(channel_id)
    logger.info("[>>] Session launch queued for #%s", channel_id)
    return {"queued": True, "channel_id": channel_id}


def _try_evict_idle_session() -> Optional[str]:
    """Evict the least-active idle session.  Must be called under _sessions_lock.

    Returns the evicted channel_id, or None if no session is idle.
    """
    now = time.time()
    candidates: list[tuple[str, float]] = []

    for channel_id, ch_sessions in _channel_sessions.items():
        if channel_id == _GLOBAL_KEY:
            continue

        # Don't evict channels with pending requests
        queue = _channel_queues.get(channel_id, deque())
        has_pending = any(r["status"] == "pending" for r in queue)
        if has_pending:
            continue

        has_healthy = any(_is_session_alive(info) for info in ch_sessions.values())
        if not has_healthy:
            continue

        last_activity = _channel_last_activity.get(channel_id, 0)
        idle_seconds = now - last_activity if last_activity else now
        candidates.append((channel_id, idle_seconds))

    if not candidates:
        return None

    # Evict the channel idle the longest
    candidates.sort(key=lambda x: x[1], reverse=True)
    evict_channel = candidates[0][0]

    ch_sessions = _channel_sessions.get(evict_channel, {})
    for session_id, info in list(ch_sessions.items()):
        _kill_session(info)
        del ch_sessions[session_id]

    if not ch_sessions:
        _channel_sessions.pop(evict_channel, None)

    return evict_channel


# =====================================================================
# Idle session reaper daemon
# =====================================================================

def _start_reaper() -> None:
    """Start the idle session reaper thread (once)."""
    global _reaper_started  # noqa: PLW0603
    if _reaper_started:
        return
    _reaper_started = True
    t = threading.Thread(
        target=_reaper_loop,
        daemon=True,
        name="session-reaper",
    )
    t.start()
    logger.info("[OK] Session idle reaper started (timeout=%ds)", _idle_timeout_s)


def _reaper_loop() -> None:
    """Background thread that kills idle sessions."""
    while True:
        try:
            time.sleep(60)
            _reap_idle_sessions()
        except Exception:
            logger.exception("[X] Reaper error")


def _reap_idle_sessions() -> None:
    """Kill sessions that have been idle beyond the timeout."""
    now = time.time()
    reaped: list[str] = []

    with _sessions_lock:
        for channel_id in list(_channel_sessions):
            if channel_id == _GLOBAL_KEY:
                continue

            ch_sessions = _channel_sessions[channel_id]
            if not ch_sessions:
                continue

            last_activity = _channel_last_activity.get(channel_id, 0)
            if last_activity == 0:
                continue  # Never had activity, skip

            idle_seconds = now - last_activity
            if idle_seconds < _idle_timeout_s:
                continue

            # Don't reap channels with pending/claimed requests
            queue = _channel_queues.get(channel_id, deque())
            has_active = any(r["status"] in ("pending", "claimed") for r in queue)
            if has_active:
                continue

            for session_id, info in list(ch_sessions.items()):
                _kill_session(info)
                del ch_sessions[session_id]

            if not ch_sessions:
                _channel_sessions.pop(channel_id, None)

            reaped.append(channel_id)

    for ch_id in reaped:
        logger.info(
            "[*] Reaped idle session for #%s (idle %.0fs, timeout %ds)",
            ch_id, now - _channel_last_activity.get(ch_id, 0), _idle_timeout_s,
        )


def reap_idle_sessions(max_idle_seconds: Optional[float] = None) -> int:
    """Kill channel sessions idle too long.  External API (called by server.py).

    Returns number of sessions reaped.
    """
    threshold = max_idle_seconds if max_idle_seconds is not None else _idle_timeout_s
    now = time.time()
    to_reap: list[tuple[str, Dict[str, Dict[str, Any]]]] = []

    with _sessions_lock:
        for ch_id, ch_sessions in list(_channel_sessions.items()):
            if ch_id == _GLOBAL_KEY:
                continue
            last_activity = _channel_last_activity.get(ch_id, 0)
            idle = now - last_activity if last_activity else now
            if idle > threshold:
                to_reap.append((ch_id, dict(ch_sessions)))

    reaped = 0
    for ch_id, sessions_copy in to_reap:
        for sid, info in sessions_copy.items():
            logger.info(
                "[*] Reaping idle channel session #%s/%s (PID %s)",
                ch_id, sid, info.get("pid"),
            )
            _kill_session(info)
        with _sessions_lock:
            _channel_sessions.pop(ch_id, None)
        reaped += 1

    return reaped


# =====================================================================
# Session lifecycle -- spawn and kill
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
    spawn_session_id = f"cohort-ch-{channel_id}-{int(now)}"
    with _sessions_lock:
        if channel_id not in _channel_sessions:
            _channel_sessions[channel_id] = {}
        _channel_sessions[channel_id][spawn_session_id] = {
            "session_id": spawn_session_id,
            "last_heartbeat": None,
            "last_activity": now,
            "pid": process.pid,
            "process": process,
        }

    logger.info("[*] Waiting for channel session #%s to connect (PID %d)...", channel_id, process.pid)

    deadline = time.time() + SPAWN_WAIT_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(1.0)

        if process.poll() is not None:
            logger.error("[X] Channel session for #%s exited with code %d", channel_id, process.returncode)
            with _sessions_lock:
                ch = _channel_sessions.get(channel_id, {})
                ch.pop(spawn_session_id, None)
                if not ch:
                    _channel_sessions.pop(channel_id, None)
            return False

        with _sessions_lock:
            info = (_channel_sessions.get(channel_id, {}).get(spawn_session_id))
            if info and info.get("last_heartbeat") is not None:
                logger.info("[OK] Channel session for #%s connected (PID %d)", channel_id, process.pid)
                return True

    logger.error("[X] Channel session for #%s timed out waiting for heartbeat", channel_id)
    process.terminate()
    with _sessions_lock:
        ch = _channel_sessions.get(channel_id, {})
        ch.pop(spawn_session_id, None)
        if not ch:
            _channel_sessions.pop(channel_id, None)
    return False


def _kill_session(info: Dict[str, Any]) -> None:
    """Terminate a session process gracefully, then force-kill."""
    proc = info.get("process")
    if proc is None:
        pid = info.get("pid")
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


# =====================================================================
# Internal helpers
# =====================================================================

def _find_request(request_id: str) -> Optional[Dict[str, Any]]:
    """Find a request by ID across all queues.  Must be called under lock."""
    for q in _channel_queues.values():
        for req in q:
            if req["id"] == request_id:
                return req
    return None


def _cleanup_request(request_id: str) -> None:
    """Remove a terminal request from its queue.  Must be called under lock."""
    for q in _channel_queues.values():
        for i, req in enumerate(q):
            if req["id"] == request_id:
                del q[i]
                return
