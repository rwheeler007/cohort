"""Channel Bridge -- in-memory request queue for Claude Code Channels integration.

Replaces ephemeral ``subprocess.run()`` calls to Claude CLI with a persistent
two-way bridge.  The agent router enqueues prompts; a Claude Code Channel
plugin polls, claims, and responds via HTTP.

Thread-safe.  All prompt construction, context enrichment, and response posting
stay in the existing codebase.  This module only manages request lifecycle.

Supports per-channel session isolation: each Cohort channel can have its own
Claude Code session with independent context.  Backward compatible -- when no
channel_id is specified, behaves as a global single-pipe.

Status lifecycle::

    pending --> claimed --> completed
                  |
                  +--> failed
                  |
                  +--> timeout

Usage::

    from cohort.channel_bridge import (
        enqueue_channel_request,
        await_channel_response,
        channel_mode_active,
    )
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# Per-channel request queues (in-memory, thread-safe)
# =====================================================================

_channel_queues: Dict[str, deque] = {}  # channel_id -> deque(maxlen=100)
_channel_lock = threading.Lock()

# Condition variable for blocking waits (avoids busy-polling in await)
_channel_cv = threading.Condition(_channel_lock)


# =====================================================================
# Per-channel session state (heartbeat tracking)
# =====================================================================

# channel_id -> {session_id -> {pid, registered_at, last_heartbeat}}
_channel_sessions: Dict[str, Dict[str, Dict[str, Any]]] = {}

HEARTBEAT_TIMEOUT_S = 30
CLAIMED_STALE_TIMEOUT_S = 60  # Auto-fail claimed requests if session dies

# User-configurable thresholds (set via apply_channel_settings)
_session_limit: int = 5    # Hard cap -- refuse new sessions beyond this
_session_warn: int = 3     # Warning threshold
_session_default: int = 1  # Sessions to launch on startup (0 = on-demand)
_idle_timeout_s: int = 600  # 10 minutes -- kill sessions with no activity
_auto_launch: bool = False  # Auto-launch sessions on demand (opt-in)

_GLOBAL_KEY = "__global__"  # Backward compat key for unscoped sessions

# Chat manager reference for hydration (set via set_chat_ref)
_chat_ref: Any = None

# Launch queue: channels waiting for a session to be spawned
# (VS Code extension or CLI polls this to know what to launch)
_launch_queue: deque = deque(maxlen=20)
_launch_queue_lock = threading.Lock()

# Track last activity per channel (for idle reaper)
_channel_last_activity: Dict[str, float] = {}

# Reaper thread state
_reaper_started: bool = False


def set_chat_ref(chat: Any) -> None:
    """Set the ChatManager reference for context hydration.

    Called from agent_router.setup_agent_router().
    """
    global _chat_ref  # noqa: PLW0603
    _chat_ref = chat


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
    """Update session threshold settings.  Called from agent_router.apply_settings()."""
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

    # Start the reaper thread on first settings load
    _start_reaper()


# =====================================================================
# Session registration
# =====================================================================

def _count_healthy_sessions() -> int:
    """Count all healthy sessions across all channels.  Must be called under lock."""
    now = time.time()
    count = 0
    for ch_sessions in _channel_sessions.values():
        for info in ch_sessions.values():
            if (now - info.get("last_heartbeat", 0)) < HEARTBEAT_TIMEOUT_S:
                count += 1
    return count


def _prune_stale_sessions() -> None:
    """Remove sessions whose heartbeat is older than 2x timeout.  Under lock."""
    now = time.time()
    cutoff = HEARTBEAT_TIMEOUT_S * 2
    for channel_id in list(_channel_sessions):
        ch_sessions = _channel_sessions[channel_id]
        stale = [
            sid for sid, info in ch_sessions.items()
            if (now - info.get("last_heartbeat", 0)) > cutoff
        ]
        for sid in stale:
            del ch_sessions[sid]
            logger.info("[*] Pruned stale session %s from #%s", sid, channel_id)
        if not ch_sessions:
            del _channel_sessions[channel_id]


def register_channel_session(
    channel_id: str,
    session_id: str,
    pid: Optional[int] = None,
) -> Dict[str, Any]:
    """Register a Claude Code session for a specific channel.

    Enforces the hard session limit.  Returns registration result with
    warning flag if at or above warning threshold.
    """
    with _channel_lock:
        _prune_stale_sessions()
        active_count = _count_healthy_sessions()

        if active_count >= _session_limit:
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
        if channel_id not in _channel_queues:
            _channel_queues[channel_id] = deque(maxlen=100)

        now = time.time()
        _channel_sessions[channel_id][session_id] = {
            "pid": pid,
            "registered_at": now,
            "last_heartbeat": now,
        }
        active_count += 1

    logger.info(
        "[OK] Registered session %s for #%s (active: %d/%d)",
        session_id, channel_id, active_count, _session_limit,
    )

    # Trigger context hydration in background (non-blocking)
    _trigger_hydration(channel_id)

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
    with _channel_lock:
        ch_sessions = _channel_sessions.get(channel_id)
        if ch_sessions and session_id in ch_sessions:
            del ch_sessions[session_id]
            if not ch_sessions:
                del _channel_sessions[channel_id]
            logger.info("[OK] Unregistered session %s from #%s", session_id, channel_id)
            return True
    return False


# =====================================================================
# Public API -- called by agent_router.py
# =====================================================================

def enqueue_channel_request(
    prompt: str,
    agent_id: str,
    channel_id: str,
    thread_id: Optional[str] = None,
    response_mode: str = "smarter",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Add an agent prompt to the channel queue.

    Returns the request_id.  The caller then calls
    :func:`await_channel_response` to block until the channel plugin
    delivers a response.
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
        # Populated by respond/error:
        "response_content": None,
        "response_metadata": None,
        "error": None,
    }

    with _channel_cv:
        if channel_id not in _channel_queues:
            _channel_queues[channel_id] = deque(maxlen=100)
        _channel_queues[channel_id].append(request)
        _channel_cv.notify_all()

    # Track activity for idle reaper
    touch_channel_activity(channel_id)

    # Auto-launch: if no session exists for this channel, queue a launch
    if _auto_launch and not channel_mode_active(channel_id=channel_id):
        request_session(channel_id)

    logger.info(
        "[>>] Channel request enqueued: %s for %s in #%s",
        request_id, agent_id, channel_id,
    )
    return request_id


def await_channel_response(
    request_id: str,
    timeout: float = 300.0,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Block until the channel plugin delivers a response or timeout.

    Returns (response_content, response_metadata).
    Content is None on failure/timeout.
    """
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

            # Auto-fail if claimed but session died (heartbeat stale)
            if req["status"] == "claimed" and not channel_mode_active(
                channel_id=req["channel_id"]
            ):
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

            # Wait for notification (respond/error will notify)
            _channel_cv.wait(timeout=min(remaining, 2.0))


def channel_mode_active(channel_id: Optional[str] = None) -> bool:
    """Check if a healthy channel session is connected.

    If *channel_id* is given, checks only that channel's sessions.
    If None, checks if ANY session across all channels is healthy
    (backward compatible).
    """
    now = time.time()
    if channel_id is not None:
        ch_sessions = _channel_sessions.get(channel_id, {})
        return any(
            (now - info.get("last_heartbeat", 0)) < HEARTBEAT_TIMEOUT_S
            for info in ch_sessions.values()
        )
    # No channel_id -- check all sessions (backward compat)
    for ch_sessions in _channel_sessions.values():
        for info in ch_sessions.values():
            if (now - info.get("last_heartbeat", 0)) < HEARTBEAT_TIMEOUT_S:
                return True
    return False


# =====================================================================
# Public API -- called by server.py channel endpoints
# =====================================================================

def poll_next_request(
    channel_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the next pending request metadata (no side effects).

    If *channel_id* is given, only searches that channel's queue.
    If None, searches all queues (backward compat for global poll).

    Returns None if queue is empty or all requests are claimed.
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
    """Claim a pending request.  Returns the full prompt and metadata.

    Returns None if request not found or already claimed.
    """
    with _channel_lock:
        req = _find_request(request_id)
        if req is None or req["status"] != "pending":
            return None

        req["status"] = "claimed"
        req["claimed_at"] = time.time()
        req["claimed_by"] = session_id

    # Track activity for idle reaper
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
    """Deliver a response for a claimed request.

    Returns True on success, False if request not found or not claimed.
    """
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

    logger.info(
        "[OK] Channel response delivered: %s (%.1fs)",
        request_id, elapsed,
    )
    return True


def deliver_error(request_id: str, error: str) -> bool:
    """Report an error for a claimed request.

    Returns True on success, False if request not found or not claimed.
    """
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
) -> None:
    """Update channel session heartbeat.

    If *channel_id* is given, updates the specific channel's session.
    If None, updates under the ``__global__`` key (backward compat).
    """
    key = channel_id or _GLOBAL_KEY
    with _channel_lock:
        if key not in _channel_sessions:
            _channel_sessions[key] = {}
        _channel_sessions[key][session_id] = {
            "pid": pid,
            "registered_at": _channel_sessions[key].get(
                session_id, {}
            ).get("registered_at", time.time()),
            "last_heartbeat": time.time(),
        }


def get_session_status(
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return channel session health for status endpoints.

    If *channel_id* is given, returns status for that channel.
    If None, returns aggregate status across all sessions (backward compat).
    """
    now = time.time()

    if channel_id is not None:
        ch_sessions = _channel_sessions.get(channel_id, {})
        healthy = any(
            (now - info.get("last_heartbeat", 0)) < HEARTBEAT_TIMEOUT_S
            for info in ch_sessions.values()
        )
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
            "session_id": next(
                (sid for sid, info in ch_sessions.items()
                 if info is latest),
                None,
            ),
            "last_heartbeat": last_hb,
            "stale_seconds": round(now - last_hb, 1) if last_hb else None,
            "queue_depth": sum(1 for r in queue if r["status"] == "pending"),
        }

    # Aggregate across all sessions
    healthy = channel_mode_active()
    all_sessions = [
        info
        for ch_sessions in _channel_sessions.values()
        for info in ch_sessions.values()
    ]
    latest = max(
        all_sessions,
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
        "healthy": healthy,
        "session_id": None,  # Aggregate -- no single session
        "last_heartbeat": last_hb,
        "stale_seconds": round(now - last_hb, 1) if last_hb else None,
        "queue_depth": total_pending,
    }


def get_all_sessions_status() -> Dict[str, Any]:
    """Return detailed status of all channel sessions.

    Used by the /api/channel/sessions endpoint for the session manager UI.
    """
    now = time.time()
    channels: Dict[str, Any] = {}

    with _channel_lock:
        _prune_stale_sessions()

        for channel_id, ch_sessions in _channel_sessions.items():
            sessions_list = []
            for session_id, info in ch_sessions.items():
                last_hb = info.get("last_heartbeat", 0)
                sessions_list.append({
                    "session_id": session_id,
                    "pid": info.get("pid"),
                    "registered_at": info.get("registered_at"),
                    "last_heartbeat": last_hb,
                    "healthy": (now - last_hb) < HEARTBEAT_TIMEOUT_S,
                    "stale_seconds": round(now - last_hb, 1),
                })

            queue = _channel_queues.get(channel_id, deque())
            channels[channel_id] = {
                "sessions": sessions_list,
                "queue_depth": sum(1 for r in queue if r["status"] == "pending"),
            }

        total_healthy = _count_healthy_sessions()

    return {
        "channels": channels,
        "total_sessions": sum(
            len(ch["sessions"]) for ch in channels.values()
        ),
        "total_healthy": total_healthy,
        "total_queue_depth": sum(
            ch["queue_depth"] for ch in channels.values()
        ),
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


# =====================================================================
# Context hydration helpers
# =====================================================================

def _trigger_hydration(channel_id: str) -> None:
    """Kick off context hydration in a daemon thread.  Best-effort."""
    from cohort.context_hydration import get_cached_hydration

    if get_cached_hydration(channel_id) is not None:
        return  # Already cached

    if _chat_ref is None:
        return  # No chat manager yet

    t = threading.Thread(
        target=_run_hydration,
        args=(channel_id,),
        daemon=True,
        name=f"hydrate-{channel_id}",
    )
    t.start()


def _run_hydration(channel_id: str) -> None:
    """Thread target for context hydration."""
    try:
        from cohort.context_hydration import hydrate_channel_context

        hydrate_channel_context(_chat_ref, channel_id)
    except Exception:
        logger.exception("[X] Hydration failed for #%s", channel_id)


# =====================================================================
# Phase 3: Smart session scheduling
# =====================================================================

def request_session(channel_id: str) -> Dict[str, Any]:
    """Request a session for a channel.  Used by auto-launch.

    If auto-launch is enabled and we're under the session limit, adds the
    channel to the launch queue so the VS Code extension or CLI can pick it
    up and spawn a ``claude`` process.

    If at the session limit, attempts priority eviction of the least-active
    idle session.

    Returns ``{queued: True, ...}`` if a launch was queued, or
    ``{queued: False, reason: ...}`` if not.
    """
    if not _auto_launch:
        return {"queued": False, "reason": "auto_launch_disabled"}

    # Already have a healthy session for this channel?
    if channel_mode_active(channel_id=channel_id):
        return {"queued": False, "reason": "session_exists"}

    # Already in the launch queue?
    with _launch_queue_lock:
        if any(item["channel_id"] == channel_id for item in _launch_queue):
            return {"queued": False, "reason": "already_queued"}

    with _channel_lock:
        _prune_stale_sessions()
        active = _count_healthy_sessions()

        if active >= _session_limit:
            # Try priority eviction
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

    # Add to launch queue
    with _launch_queue_lock:
        _launch_queue.append({
            "channel_id": channel_id,
            "requested_at": time.time(),
        })

    logger.info("[>>] Session launch queued for #%s", channel_id)
    return {"queued": True, "channel_id": channel_id}


def poll_launch_queue() -> Optional[Dict[str, Any]]:
    """Return the next channel needing a session, or None.

    Called by the VS Code extension or CLI to discover channels that need
    sessions launched.  Non-destructive peek -- use :func:`ack_launch` to
    remove from queue after spawning.
    """
    with _launch_queue_lock:
        for item in _launch_queue:
            # Skip if a session appeared since queuing (e.g., manual launch)
            if not channel_mode_active(channel_id=item["channel_id"]):
                return item
    return None


def ack_launch(channel_id: str) -> bool:
    """Acknowledge that a session was launched for a channel.

    Removes the channel from the launch queue.  Returns True if found.
    """
    with _launch_queue_lock:
        for i, item in enumerate(_launch_queue):
            if item["channel_id"] == channel_id:
                del _launch_queue[i]
                logger.info("[OK] Launch acknowledged for #%s", channel_id)
                return True
    return False


def get_launch_queue() -> list:
    """Return the current launch queue (for debugging/status)."""
    with _launch_queue_lock:
        return list(_launch_queue)


def touch_channel_activity(channel_id: str) -> None:
    """Record activity on a channel (for idle reaper scoring)."""
    _channel_last_activity[channel_id] = time.time()


def _try_evict_idle_session() -> Optional[str]:
    """Evict the least-active idle session.  Must be called under _channel_lock.

    Returns the evicted channel_id, or None if no session is idle.

    Scoring: channels with the oldest last_activity AND no pending requests
    are evicted first.
    """
    now = time.time()
    candidates: list[tuple[str, float]] = []  # (channel_id, score)

    for channel_id, ch_sessions in _channel_sessions.items():
        if channel_id == _GLOBAL_KEY:
            continue  # Never evict global session

        # Check if this channel has pending requests (busy = don't evict)
        queue = _channel_queues.get(channel_id, deque())
        has_pending = any(r["status"] == "pending" for r in queue)
        if has_pending:
            continue

        # Check if any session is healthy
        has_healthy = any(
            (now - info.get("last_heartbeat", 0)) < HEARTBEAT_TIMEOUT_S
            for info in ch_sessions.values()
        )
        if not has_healthy:
            continue  # Already dead, will be pruned

        # Score: lower = better candidate for eviction
        last_activity = _channel_last_activity.get(channel_id, 0)
        idle_seconds = now - last_activity if last_activity else now
        candidates.append((channel_id, idle_seconds))

    if not candidates:
        return None

    # Evict the channel idle the longest
    candidates.sort(key=lambda x: x[1], reverse=True)
    evict_channel = candidates[0][0]

    # Kill sessions for the evicted channel
    ch_sessions = _channel_sessions.get(evict_channel, {})
    for session_id, info in list(ch_sessions.items()):
        pid = info.get("pid")
        if pid:
            try:
                import os
                os.kill(pid, 15)  # SIGTERM
            except (ProcessLookupError, PermissionError, OSError):
                pass
        del ch_sessions[session_id]

    if not ch_sessions:
        _channel_sessions.pop(evict_channel, None)

    return evict_channel


# =====================================================================
# Idle session reaper
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
            time.sleep(60)  # Check every minute
            _reap_idle_sessions()
        except Exception:
            logger.exception("[X] Reaper error")


def _reap_idle_sessions() -> None:
    """Kill sessions that have been idle beyond the timeout."""
    now = time.time()
    reaped: list[str] = []

    with _channel_lock:
        for channel_id in list(_channel_sessions):
            if channel_id == _GLOBAL_KEY:
                continue

            ch_sessions = _channel_sessions[channel_id]
            if not ch_sessions:
                continue

            # Check last activity
            last_activity = _channel_last_activity.get(channel_id, 0)
            if last_activity == 0:
                continue  # Never had activity, skip (just registered)

            idle_seconds = now - last_activity
            if idle_seconds < _idle_timeout_s:
                continue

            # Check if there are pending requests (don't reap busy channels)
            queue = _channel_queues.get(channel_id, deque())
            has_pending = any(r["status"] in ("pending", "claimed") for r in queue)
            if has_pending:
                continue

            # Reap: kill all sessions for this channel
            for session_id, info in list(ch_sessions.items()):
                pid = info.get("pid")
                if pid:
                    try:
                        import os
                        os.kill(pid, 15)  # SIGTERM
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                del ch_sessions[session_id]

            if not ch_sessions:
                _channel_sessions.pop(channel_id, None)

            reaped.append(channel_id)

    for ch_id in reaped:
        logger.info(
            "[*] Reaped idle session for #%s (idle %.0fs, timeout %ds)",
            ch_id, now - _channel_last_activity.get(ch_id, 0), _idle_timeout_s,
        )
