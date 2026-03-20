"""Channel Bridge -- in-memory request queue for Claude Code Channels integration.

Replaces ephemeral ``subprocess.run()`` calls to Claude CLI with a persistent
two-way bridge.  The agent router enqueues prompts; a Claude Code Channel
plugin polls, claims, and responds via HTTP.

Thread-safe.  All prompt construction, context enrichment, and response posting
stay in the existing codebase.  This module only manages request lifecycle.

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
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# Channel request queue (in-memory, thread-safe)
# =====================================================================

_channel_queue: deque[Dict[str, Any]] = deque(maxlen=100)
_channel_lock = threading.Lock()

# Condition variable for blocking waits (avoids busy-polling in await)
_channel_cv = threading.Condition(_channel_lock)


# =====================================================================
# Channel session state (heartbeat tracking)
# =====================================================================

_channel_session: Dict[str, Any] = {
    "session_id": None,
    "last_heartbeat": None,
    "pid": None,
}

HEARTBEAT_TIMEOUT_S = 30
CLAIMED_STALE_TIMEOUT_S = 60  # Auto-fail claimed requests if session dies


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
        _channel_queue.append(request)
        _channel_cv.notify_all()

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
            if req["status"] == "claimed" and not channel_mode_active():
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


def channel_mode_active() -> bool:
    """Check if a healthy channel session is connected.

    Returns True if a heartbeat was received within the timeout window.
    """
    last_hb = _channel_session.get("last_heartbeat")
    if last_hb is None:
        return False
    return (time.time() - last_hb) < HEARTBEAT_TIMEOUT_S


# =====================================================================
# Public API -- called by server.py channel endpoints
# =====================================================================

def poll_next_request() -> Optional[Dict[str, Any]]:
    """Return the next pending request metadata (no side effects).

    Returns None if queue is empty or all requests are claimed.
    """
    with _channel_lock:
        for req in _channel_queue:
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


def update_heartbeat(session_id: str, pid: Optional[int] = None) -> None:
    """Update channel session heartbeat."""
    _channel_session["session_id"] = session_id
    _channel_session["last_heartbeat"] = time.time()
    _channel_session["pid"] = pid


def get_session_status() -> Dict[str, Any]:
    """Return channel session health for status endpoints."""
    last_hb = _channel_session.get("last_heartbeat")
    healthy = channel_mode_active()
    return {
        "healthy": healthy,
        "session_id": _channel_session.get("session_id"),
        "last_heartbeat": last_hb,
        "stale_seconds": round(time.time() - last_hb, 1) if last_hb else None,
        "queue_depth": sum(
            1 for req in _channel_queue if req["status"] == "pending"
        ),
    }


# =====================================================================
# Internal helpers
# =====================================================================

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
