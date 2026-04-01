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
ABANDONED_REQUEST_TTL_S = 1800  # 30 minutes -- truly abandoned requests get reaped

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

# Context pressure thresholds (configurable via env vars)
_PRESSURE_WARN: float = float(os.environ.get("COHORT_PRESSURE_WARN", "0.35"))
_PRESSURE_ROTATE: float = float(os.environ.get("COHORT_PRESSURE_ROTATE", "0.50"))
_MAX_REQUESTS_PER_SESSION: int = int(os.environ.get("COHORT_MAX_REQUESTS", "30"))

# Context pressure estimation constants
_RETENTION_FACTOR: float = 0.6   # ~60% of cumulative I/O survives compression
_CONTEXT_BUDGET_CHARS: int = 800_000  # ~200K tokens * 4 chars/token

# Per-channel pressure tracking (channel_id -> {cumulative_prompt_chars, ...})
_channel_pressure: Dict[str, Dict[str, Any]] = {}

# Session handoffs for rotation continuity (channel_id -> handoff_text)
_session_handoffs: Dict[str, str] = {}

# Rotation event log
_rotation_log: deque = deque(maxlen=100)

# Reaper daemon state
_reaper_started: bool = False

_GLOBAL_KEY = "_global"  # Key for unscoped MCP work-queue sessions

# Session state persistence
_state_file: Optional[Path] = None
_resume_ids: Dict[str, str] = {}  # channel_id -> last known resume_id


def set_data_dir(data_dir: str) -> None:
    """Set the data directory for session state persistence."""
    global _state_file  # noqa: PLW0603
    _state_file = Path(data_dir) / "channel_sessions.json"


def _save_session_state() -> None:
    """Persist session registry to disk for server restart recovery."""
    if _state_file is None:
        return
    import json as _json
    entries = []
    with _sessions_lock:
        for ch_id, sessions in _channel_sessions.items():
            for sid, info in sessions.items():
                entries.append({
                    "channel_id": ch_id,
                    "session_id": sid,
                    "pid": info.get("pid"),
                    "resume_id": _resume_ids.get(ch_id),
                    "workspace_path": info.get("workspace_path"),
                    "spawned_at": info.get("registered_at"),
                    "last_heartbeat": info.get("last_heartbeat"),
                })
    try:
        _state_file.parent.mkdir(parents=True, exist_ok=True)
        _state_file.write_text(_json.dumps(entries, indent=2) + "\n", "utf-8")
    except Exception:
        logger.exception("[!] Failed to save session state to %s", _state_file)


def load_session_state() -> int:
    """Load session state from disk and re-adopt living sessions.

    Returns the number of sessions re-adopted.
    """
    if _state_file is None or not _state_file.exists():
        return 0
    import json as _json
    try:
        entries = _json.loads(_state_file.read_text("utf-8"))
    except Exception:
        logger.exception("[!] Failed to load session state from %s", _state_file)
        return 0

    adopted = 0
    for entry in entries:
        ch_id = entry.get("channel_id")
        sid = entry.get("session_id")
        pid = entry.get("pid")
        resume_id = entry.get("resume_id")

        if not ch_id or not pid:
            continue

        # Preserve resume_id for future spawns regardless of PID liveness
        if resume_id:
            _resume_ids[ch_id] = resume_id

        # Check if PID is still alive
        alive = False
        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if handle:
                    kernel32.CloseHandle(handle)
                    alive = True
            else:
                os.kill(pid, 0)
                alive = True
        except (OSError, ProcessLookupError):
            pass

        if alive:
            with _sessions_lock:
                if ch_id not in _channel_sessions:
                    _channel_sessions[ch_id] = {}
                _channel_sessions[ch_id][sid or f"recovered-{pid}"] = {
                    "session_id": sid,
                    "pid": pid,
                    "last_heartbeat": None,  # Will be refreshed on next heartbeat
                    "last_activity": time.time(),
                    "registered_at": entry.get("spawned_at"),
                    "workspace_path": entry.get("workspace_path"),
                    "process": None,  # We don't have the Popen handle
                }
            adopted += 1
            logger.info("[OK] Re-adopted session %s (PID %d) for #%s", sid, pid, ch_id)
        else:
            logger.info("[*] Session %s (PID %d) for #%s is dead — resume_id preserved", sid, pid, ch_id)

    return adopted


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
    pressure_warn: Optional[float] = None,
    pressure_rotate: Optional[float] = None,
    max_requests_per_session: Optional[int] = None,
) -> None:
    """Update session threshold settings.  Called from server.py post_settings()."""
    global _session_limit, _session_warn, _session_default, _idle_timeout_s, _auto_launch  # noqa: PLW0603
    global _PRESSURE_WARN, _PRESSURE_ROTATE, _MAX_REQUESTS_PER_SESSION  # noqa: PLW0603
    _session_limit = max(1, limit)
    _session_warn = max(1, warn)
    _session_default = max(0, default)
    _idle_timeout_s = max(60, idle_timeout)  # Minimum 1 minute
    _auto_launch = bool(auto_launch)
    if pressure_warn is not None:
        _PRESSURE_WARN = max(0.1, min(0.9, pressure_warn))
    if pressure_rotate is not None:
        _PRESSURE_ROTATE = max(0.2, min(1.0, pressure_rotate))
    if max_requests_per_session is not None:
        _MAX_REQUESTS_PER_SESSION = max(5, max_requests_per_session)
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

    # When the server manages sessions (set_data_dir was called),
    # skip the 30s VS Code wait and spawn directly.
    if _state_file is not None:
        logger.info("[>>] Server-managed mode — direct spawn for #%s", channel_id)
        if _spawn_channel_session(channel_id):
            return "direct"
        return ""

    # Add to launch queue for VS Code extension to pick up
    _add_to_launch_queue(channel_id)

    # Wait for the VS Code extension to spawn it (polls every ~3s,
    # heartbeat every 1s once alive).  30s window gives ~10 poll cycles.
    _vscode_wait = min(SPAWN_WAIT_TIMEOUT_S, 30)
    deadline = time.time() + _vscode_wait
    while time.time() < deadline:
        time.sleep(1.0)
        with _sessions_lock:
            info = _get_any_alive_session(channel_id)
            if info is not None:
                touch_channel_activity(channel_id)
                logger.info("[OK] Channel session for #%s came alive via VS Code", channel_id)
                return "vscode"

    # VS Code didn't pick it up — try direct spawn as fallback
    logger.info(
        "[*] VS Code didn't launch #%s in %ds — attempting direct spawn",
        channel_id, _vscode_wait,
    )
    if _spawn_channel_session(channel_id):
        return "direct"
    return ""


def _get_channel_workspace(channel_id: str) -> Optional[str]:
    """Look up workspace_path from channel metadata."""
    try:
        from cohort.agent_router import _chat
        if _chat:
            ch = _chat.get_channel(channel_id)
            if ch:
                return ch.metadata.get("workspace_path")
    except Exception:
        pass
    return None


def _add_to_launch_queue(channel_id: str, workspace_path: Optional[str] = None) -> None:
    """Add a channel to the launch queue if not already queued."""
    with _launch_lock:
        for item in _launch_queue:
            if item["channel_id"] == channel_id:
                return  # Already queued
        entry: Dict[str, Any] = {
            "channel_id": channel_id,
            "queued_at": time.time(),
        }
        # Include workspace so the extension knows where to launch
        wp = workspace_path or _get_channel_workspace(channel_id)
        if wp:
            entry["workspace_path"] = wp
        _launch_queue.append(entry)
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
    # Prepend handoff context if this is the first request after a rotation
    handoff = _session_handoffs.pop(channel_id, None)
    if handoff:
        prompt = handoff + "\n\n" + prompt
        logger.info("[*] Injected handoff for #%s (%d chars)", channel_id, len(handoff))

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

        # Deduplicate: both the extension and server's route_mentions enqueue
        # for the same message in channel mode.  Skip if a pending request for
        # the same agent+channel was created within the last 5 seconds.
        now = time.time()
        for existing in _channel_queues[channel_id]:
            if (existing["status"] == "pending"
                    and existing["agent_id"] == agent_id
                    and existing["channel_id"] == channel_id
                    and now - existing["created_at"] < 5.0):
                logger.info(
                    "[*] Dedup: skipping duplicate request for %s on #%s (existing: %s, age=%.1fs)",
                    agent_id, channel_id, existing["id"], now - existing["created_at"],
                )
                return existing["id"]

        _channel_queues[channel_id].append(request)
        _channel_cv.notify_all()

    touch_channel_activity(channel_id)

    # Demand-driven launch: if no session exists, queue one.
    # Uses force=True to bypass the auto_launch gate -- this is a real
    # request that needs a session, not a proactive startup launch.
    if not channel_mode_active(channel_id=channel_id):
        _ws = (metadata or {}).get("workspace_path")
        request_session(channel_id, force=True, workspace_path=_ws)

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
                # Caller timed out waiting, but leave request as pending
                # so the session can still claim it when it comes alive.
                # Mark caller_gone so deliver_response knows to self-post.
                # A separate reaper removes truly abandoned requests after
                # ABANDONED_REQUEST_TTL_S.
                req["caller_gone"] = True
                logger.warning(
                    "[X] Channel request %s: caller timed out after %.0fs, "
                    "request stays pending for late claim",
                    request_id, timeout,
                )
                return None, {"error": "channel_timeout", "request_id": request_id}

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
    _record_prompt(req["channel_id"], len(req.get("prompt", "")), req.get("agent_id"))
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
    """Deliver a response for a claimed request.

    If the caller already timed out (caller_gone=True), posts the response
    directly to the channel so late-arriving work doesn't get silently dropped.
    """
    caller_gone = False
    with _channel_cv:
        req = _find_request(request_id)
        if req is None or req["status"] != "claimed":
            return False

        caller_gone = req.get("caller_gone", False)
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

    channel_id = req.get("channel_id", "")
    agent_id = req.get("agent_id", "agent")
    thread_id = req.get("thread_id")
    _record_response(channel_id, len(content))

    pressure = _calculate_pressure(channel_id)
    if pressure >= _PRESSURE_WARN:
        logger.info(
            "[*] Channel #%s pressure: %.1f%% (%s tier)",
            channel_id, pressure * 100, get_pressure_tier(channel_id),
        )

    # If the caller timed out, nobody is reading the response from the queue.
    # Post directly to the channel so the user still gets the answer.
    if caller_gone:
        logger.info(
            "[OK] Late channel response for %s by %s in #%s (%.1fs) — self-posting",
            request_id, agent_id, channel_id, elapsed,
        )
        _self_post_response(channel_id, agent_id, content, thread_id, default_meta)
    else:
        logger.info("[OK] Channel response delivered: %s (%.1fs)", request_id, elapsed)

    # Check if session should be rotated due to context pressure
    _check_and_rotate(channel_id)

    # Clean up completed requests where caller is gone (nobody will read them)
    if caller_gone:
        with _channel_lock:
            _cleanup_request(request_id)

    return True


def _self_post_response(
    channel_id: str,
    agent_id: str,
    content: str,
    thread_id: Optional[str],
    metadata: Dict[str, Any],
) -> None:
    """Post a late-arriving response directly to the channel.

    This mirrors what _invoke_agent_sync does after await_channel_response
    returns, but handles the case where the caller already timed out.
    """
    try:
        from cohort.agent_router import _chat, _emit_sync
        if _chat is None:
            logger.warning("[!] Cannot self-post: _chat not initialized")
            return

        response_msg = _chat.post_message(
            channel_id=channel_id,
            sender=agent_id,
            content=content,
            thread_id=thread_id,
            metadata=metadata,
        )
        if response_msg:
            _emit_sync("new_message", response_msg.to_dict())
            logger.info("[OK] Self-posted late response to #%s by %s", channel_id, agent_id)
    except Exception:
        logger.exception("[X] Failed to self-post late response for %s in #%s", agent_id, channel_id)


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

    touch_channel_activity(channel_id)  # Seed idle reaper timestamp
    _save_session_state()
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
            _save_session_state()
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
            last_activity = _channel_last_activity.get(channel_id, 0)
            idle_secs = round(now - last_activity, 1) if last_activity else None

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
                "idle_seconds": idle_secs,
                "pressure": get_channel_pressure(channel_id),
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
            "pressure_warn": _PRESSURE_WARN,
            "pressure_rotate": _PRESSURE_ROTATE,
            "max_requests_per_session": _MAX_REQUESTS_PER_SESSION,
        },
        "recent_rotations": list(_rotation_log)[-10:],
        "launch_queue": get_launch_queue(),
    }


# =====================================================================
# Activity tracking
# =====================================================================

def touch_channel_activity(channel_id: str) -> None:
    """Record activity on a channel (for idle reaper scoring)."""
    _channel_last_activity[channel_id] = time.time()


# =====================================================================
# Context pressure tracking
# =====================================================================

def _get_pressure_info(channel_id: str) -> Dict[str, Any]:
    """Get or create pressure tracking dict for a channel."""
    if channel_id not in _channel_pressure:
        _channel_pressure[channel_id] = {
            "requests_served": 0,
            "cumulative_prompt_chars": 0,
            "cumulative_response_chars": 0,
            "last_agent_id": None,
        }
    return _channel_pressure[channel_id]


def _record_prompt(channel_id: str, prompt_chars: int, agent_id: Optional[str] = None) -> None:
    """Record a prompt injection into the pressure tracker."""
    info = _get_pressure_info(channel_id)
    info["requests_served"] += 1
    info["cumulative_prompt_chars"] += prompt_chars
    if agent_id:
        info["last_agent_id"] = agent_id


def _record_response(channel_id: str, response_chars: int) -> None:
    """Record a response from the session into the pressure tracker."""
    info = _get_pressure_info(channel_id)
    info["cumulative_response_chars"] += response_chars


def _calculate_pressure(channel_id: str) -> float:
    """Estimate 0.0-1.0 context utilization for a channel session."""
    info = _channel_pressure.get(channel_id)
    if not info:
        return 0.0
    total = info["cumulative_prompt_chars"] + info["cumulative_response_chars"]
    estimated_live = total * _RETENTION_FACTOR
    return min(1.0, estimated_live / _CONTEXT_BUDGET_CHARS)


def _reset_pressure(channel_id: str) -> None:
    """Reset pressure tracking for a channel (called on session rotation)."""
    _channel_pressure.pop(channel_id, None)


def get_pressure_tier(channel_id: str) -> str:
    """Return prompt tier based on current pressure: 'full', 'condensed', or 'minimal'."""
    pressure = _calculate_pressure(channel_id)
    if pressure >= _PRESSURE_ROTATE:
        return "minimal"
    if pressure >= _PRESSURE_WARN:
        return "condensed"
    return "full"


def get_channel_pressure(channel_id: str) -> Dict[str, Any]:
    """Return pressure metrics for a channel (for status API)."""
    info = _channel_pressure.get(channel_id, {})
    pressure = _calculate_pressure(channel_id)
    return {
        "pressure": round(pressure, 3),
        "tier": get_pressure_tier(channel_id),
        "requests_served": info.get("requests_served", 0),
        "cumulative_prompt_chars": info.get("cumulative_prompt_chars", 0),
        "cumulative_response_chars": info.get("cumulative_response_chars", 0),
        "last_agent_id": info.get("last_agent_id"),
    }


# =====================================================================
# Session rotation (context pressure-based)
# =====================================================================

def _should_rotate(channel_id: str) -> tuple:
    """Check whether a channel session should be rotated.

    Returns (should_rotate: bool, reason: str).
    Defers rotation if requests are still queued.
    """
    pressure = _calculate_pressure(channel_id)
    info = _channel_pressure.get(channel_id, {})
    requests = info.get("requests_served", 0)

    # Don't rotate while requests are queued
    with _channel_lock:
        queue = _channel_queues.get(channel_id, deque())
        has_pending = any(r["status"] in ("pending", "claimed") for r in queue)
    if has_pending:
        return (False, "")

    if pressure >= _PRESSURE_ROTATE:
        return (True, f"pressure_{pressure:.2f}")
    if requests >= _MAX_REQUESTS_PER_SESSION:
        return (True, f"max_requests_{requests}")
    return (False, "")


def _build_handoff(channel_id: str) -> str:
    """Build a condensed handoff for session continuity after rotation.

    Uses LocalRouter.distill() if available, otherwise falls back to
    last 5 messages verbatim.
    """
    # Collect recent channel messages for the handoff
    try:
        from cohort.agent_router import _chat
        messages = _chat.get_channel_messages(channel_id, limit=20) if _chat else []
    except Exception:
        messages = []

    if not messages:
        return ""

    # Build transcript for distillation (messages may be dicts or dataclasses)
    transcript_lines = []
    for msg in messages[-20:]:
        sender = getattr(msg, "sender", None) or (msg.get("sender", "?") if isinstance(msg, dict) else "?")
        content = getattr(msg, "content", None) or (msg.get("content", "") if isinstance(msg, dict) else "")
        transcript_lines.append(f"{sender}: {content[:500]}")
    transcript = "\n".join(transcript_lines)

    # Try LLM distillation
    try:
        from cohort.local.router import LocalRouter
        router = LocalRouter()
        handoff = router.distill(
            f"SESSION HANDOFF for #{channel_id}.\n"
            f"A new Claude Code session is taking over. Summarize the ESSENTIAL "
            f"state for continuity:\n"
            f"1. Active agents and their roles\n"
            f"2. Key decisions or agreements\n"
            f"3. Current topic being discussed\n"
            f"4. In-flight commitments or action items\n\n"
            f"Recent messages:\n{transcript}"
        )
        if handoff:
            return f"=== SESSION HANDOFF ===\n{handoff}\n=== END HANDOFF ==="
    except Exception:
        logger.debug("[!] Handoff distillation failed for #%s, using heuristic", channel_id)

    # Fallback: last 5 messages verbatim
    fallback_lines = transcript_lines[-5:]
    fallback = "\n".join(fallback_lines)[:2000]
    return f"=== SESSION HANDOFF ===\n{fallback}\n=== END HANDOFF ==="


def rotate_session(channel_id: str, reason: str) -> None:
    """Rotate a channel session: build handoff, kill old session, reset pressure.

    The next request to this channel will spawn a fresh session via
    ensure_channel_session(), which gets the handoff prepended.
    """
    logger.info("[*] Rotating session for #%s (reason: %s)", channel_id, reason)

    # Build handoff before killing the session
    handoff = _build_handoff(channel_id)
    if handoff:
        _session_handoffs[channel_id] = handoff

    # Log rotation event
    info = _channel_pressure.get(channel_id, {})
    _rotation_log.append({
        "channel_id": channel_id,
        "timestamp": time.time(),
        "reason": reason,
        "requests_served": info.get("requests_served", 0),
        "cumulative_chars": info.get("cumulative_prompt_chars", 0) + info.get("cumulative_response_chars", 0),
        "handoff_size_chars": len(handoff),
    })

    # Kill all sessions for this channel
    with _sessions_lock:
        ch_sessions = _channel_sessions.get(channel_id, {})
        for session_id, sess_info in list(ch_sessions.items()):
            _kill_session(sess_info)
            del ch_sessions[session_id]
        if not ch_sessions:
            _channel_sessions.pop(channel_id, None)

    # Reset pressure for fresh session
    _reset_pressure(channel_id)
    logger.info("[OK] Session rotated for #%s, handoff=%d chars", channel_id, len(handoff))


def _check_and_rotate(channel_id: str) -> None:
    """Check if rotation is needed and execute it. Called after deliver_response."""
    should, reason = _should_rotate(channel_id)
    if should:
        rotate_session(channel_id, reason)


# =====================================================================
# Auto-launch and priority eviction
# =====================================================================

def request_session(channel_id: str, *, force: bool = False, workspace_path: Optional[str] = None) -> Dict[str, Any]:
    """Request a session for a channel.

    Args:
        channel_id: Channel needing a session.
        force: If True, bypass the auto_launch gate.  Used for demand-driven
               launches (e.g. @mention triggers agent response and needs a
               session).  The auto_launch setting only gates proactive/startup
               launches.
        workspace_path: Explicit workspace for the session.  Passed through to
               the launch queue so the VS Code extension launches in the
               correct directory.

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

    _add_to_launch_queue(channel_id, workspace_path=workspace_path)
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
    """Background thread that kills idle sessions and abandoned requests."""
    while True:
        try:
            time.sleep(60)
            _reap_idle_sessions()
            _reap_abandoned_requests()
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
                # Fallback: use earliest session registration time
                earliest = min(
                    (info.get("registered_at", 0) for info in ch_sessions.values()),
                    default=0,
                )
                if earliest == 0 or (now - earliest) < _idle_timeout_s:
                    continue
                # Fall through — registered but never used, past timeout

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


def _reap_abandoned_requests() -> None:
    """Remove requests that have been pending for longer than the TTL.

    These are requests where the caller already timed out and no session
    ever claimed them.  Without this, the queue would grow unboundedly.
    """
    now = time.time()
    with _channel_lock:
        for channel_id, q in _channel_queues.items():
            to_remove = []
            for i, req in enumerate(q):
                if req["status"] == "pending":
                    age = now - req["created_at"]
                    if age > ABANDONED_REQUEST_TTL_S:
                        to_remove.append(i)
                        logger.info(
                            "[*] Reaping abandoned request %s in #%s (age %.0fs)",
                            req["id"], channel_id, age,
                        )
            # Remove in reverse order to preserve indices
            for i in reversed(to_remove):
                del q[i]


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

    if reaped:
        _save_session_state()
    return reaped


# Rate-limit respawns: max 3 per channel per 10 minutes
_respawn_history: Dict[str, List[float]] = {}  # channel_id -> [timestamps]
_RESPAWN_MAX = 3
_RESPAWN_WINDOW_S = 600  # 10 minutes


def recover_crashed_sessions() -> int:
    """Detect sessions with dead PIDs and auto-respawn.

    Called periodically by the reaper loop.  Rate-limited to prevent
    crash loops (max 3 respawns per channel per 10 minutes).

    Returns number of sessions respawned.
    """
    now = time.time()
    dead: list[tuple[str, str, Dict[str, Any]]] = []

    with _sessions_lock:
        for ch_id, ch_sessions in list(_channel_sessions.items()):
            if ch_id == _GLOBAL_KEY:
                continue
            for sid, info in list(ch_sessions.items()):
                pid = info.get("pid")
                if not pid:
                    continue
                proc = info.get("process")
                # Check if process is dead
                is_dead = False
                if proc is not None:
                    is_dead = proc.poll() is not None
                else:
                    try:
                        if sys.platform == "win32":
                            import ctypes
                            kernel32 = ctypes.windll.kernel32
                            handle = kernel32.OpenProcess(0x1000, False, pid)
                            if handle:
                                kernel32.CloseHandle(handle)
                            else:
                                is_dead = True
                        else:
                            os.kill(pid, 0)
                    except (OSError, ProcessLookupError):
                        is_dead = True

                if is_dead:
                    dead.append((ch_id, sid, info))

    respawned = 0
    for ch_id, sid, info in dead:
        # Remove dead session
        with _sessions_lock:
            ch = _channel_sessions.get(ch_id, {})
            ch.pop(sid, None)
            if not ch:
                _channel_sessions.pop(ch_id, None)

        logger.warning("[!] Session %s (PID %s) for #%s is dead", sid, info.get("pid"), ch_id)

        # Rate-limit check
        history = _respawn_history.setdefault(ch_id, [])
        history[:] = [t for t in history if now - t < _RESPAWN_WINDOW_S]
        if len(history) >= _RESPAWN_MAX:
            logger.warning(
                "[X] #%s hit respawn limit (%d in %ds) — skipping auto-respawn",
                ch_id, _RESPAWN_MAX, _RESPAWN_WINDOW_S,
            )
            continue

        # Auto-respawn
        logger.info("[>>] Auto-respawning session for #%s", ch_id)
        if _spawn_channel_session(ch_id):
            history.append(now)
            respawned += 1

    if dead:
        _save_session_state()
    return respawned


# =====================================================================
# MCP config helper
# =====================================================================

def _ensure_mcp_entry(cohort_root: Path, server_key: str, channel_id: str) -> None:
    """Ensure .mcp.json in *cohort_root* contains a server entry for this channel.

    Creates the file if missing; adds the entry if absent; leaves existing
    entries untouched.
    """
    import json as _json
    import shutil

    mcp_path = cohort_root / ".mcp.json"
    plugin_entry = str(cohort_root / "plugins" / "cohort-channel" / "src" / "index.ts")
    # Forward slashes for cross-platform compat
    plugin_entry = plugin_entry.replace("\\", "/")

    try:
        data = _json.loads(mcp_path.read_text("utf-8")) if mcp_path.exists() else {}
    except Exception:
        data = {}

    servers = data.setdefault("mcpServers", {})
    if server_key in servers:
        return  # Already configured

    bun_cmd = shutil.which("bun") or "bun"
    project_id = cohort_root.name.lower().replace(" ", "-")

    servers[server_key] = {
        "command": bun_cmd,
        "args": [plugin_entry],
        "env": {
            "COHORT_BASE_URL": COHORT_BASE_URL,
            "CHANNEL_ID": channel_id,
            "PROJECT_ID": project_id,
            "CHANNEL_NAME": server_key,
            "POLL_INTERVAL": "5000",
        },
    }
    mcp_path.write_text(_json.dumps(data, indent=2) + "\n", "utf-8")
    logger.info("[*] Added MCP entry '%s' to %s", server_key, mcp_path)


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
        "--dangerously-skip-permissions",
        "--allowedTools",
        "mcp__cohort-wq__cohort_respond,mcp__cohort-wq__cohort_error,mcp__cohort-wq__cohort_post,mcp__cohort-wq__cohort_ready",
        "--model", CHANNEL_MODEL,
        "--system-prompt", system_prompt,
    ]

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
    env["CHANNEL_ID"] = channel_id
    env["COHORT_BASE_URL"] = COHORT_BASE_URL
    env["CHANNEL_NAME"] = f"cohort-ch-{channel_id}"

    # Resolve workspace: channel metadata > AGENTS_ROOT > fallback
    workspace_root = _get_channel_workspace(channel_id)
    if not workspace_root:
        try:
            from cohort.agent_router import AGENTS_ROOT
            workspace_root = str(AGENTS_ROOT) if AGENTS_ROOT else None
        except ImportError:
            pass
    if not workspace_root:
        workspace_root = str(Path(__file__).parent.parent)

    popen_kwargs: dict = dict(
        cwd=str(workspace_root),
        env=env,
    )
    if sys.platform == "win32":
        cmd = ["cmd", "/c"] + cmd
        _visible = os.environ.get("COHORT_SESSION_VISIBLE", "").lower() in ("1", "true", "yes")
        flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        popen_kwargs["creationflags"] = flags
        if not _visible:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            popen_kwargs["startupinfo"] = si
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
                _save_session_state()
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
