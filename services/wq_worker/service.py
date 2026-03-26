"""Cohort Work-Queue Dispatcher Service.

Standalone worker that polls the Cohort work queue, claims items,
and dispatches them as messages to the appropriate channel where a
persistent Claude Code session handles execution with full context.

Runs as a managed service on port 8102, registered with Cohort's
health monitor for start/stop/restart from the dashboard.

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
Use [OK], [!], [X], [*], [>>] for status indicators.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = 8102
COHORT_URL = os.environ.get("COHORT_URL", "http://127.0.0.1:5100")
POLL_INTERVAL = int(os.environ.get("WQ_POLL_INTERVAL", "1"))  # seconds
DEFAULT_CHANNEL = os.environ.get("WQ_DEFAULT_CHANNEL", "general")
WORKER_SENDER = os.environ.get("WQ_SENDER", "wq-dispatcher")

logger = logging.getLogger("wq_worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------

_worker_task: Optional[asyncio.Task] = None
_stats: Dict[str, Any] = {
    "started_at": None,
    "items_dispatched": 0,
    "items_failed": 0,
    "last_poll": None,
    "last_item_id": None,
    "current_item": None,
    "paused": False,
}


# ---------------------------------------------------------------------------
# Cohort API client
# ---------------------------------------------------------------------------

async def cohort_post(path: str, json: Optional[Dict] = None) -> Optional[Dict]:
    """POST to the Cohort server."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{COHORT_URL}{path}", json=json or {})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("[!] POST %s failed: %s", path, exc)
        return None


async def cohort_patch(path: str, json: Optional[Dict] = None) -> Optional[Dict]:
    """PATCH to the Cohort server."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(f"{COHORT_URL}{path}", json=json or {})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("[!] PATCH %s failed: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def build_channel_message(item: Dict) -> str:
    """Format a work item as a channel message."""
    item_id = item.get("id", "unknown")
    desc = item.get("description", "No description")
    priority = item.get("priority", "medium")
    requester = item.get("requester", "unknown")
    metadata = item.get("metadata", {})

    parts = [
        f"[Work Queue] {item_id} (priority: {priority}, from: {requester})",
        "",
        desc,
    ]

    if metadata:
        # Include relevant metadata but skip routing keys
        extra = {k: v for k, v in metadata.items() if k != "channel"}
        if extra:
            parts.append("")
            for k, v in extra.items():
                parts.append(f"  {k}: {v}")

    return "\n".join(parts)


def resolve_channel(item: Dict) -> str:
    """Determine which channel a work item should be dispatched to."""
    # Check metadata for explicit channel routing
    meta = item.get("metadata", {})
    if meta.get("channel"):
        return meta["channel"]

    # Could add agent-based routing here later
    # (e.g. item.agent_id -> agent's preferred channel)

    return DEFAULT_CHANNEL


# ---------------------------------------------------------------------------
# Main poll/dispatch loop
# ---------------------------------------------------------------------------

async def poll_loop():
    """Main worker loop: poll queue, claim, dispatch to channel."""
    logger.info("[OK] Dispatcher started (poll every %ds, default channel: %s)",
                POLL_INTERVAL, DEFAULT_CHANNEL)

    while True:
        try:
            if _stats["paused"]:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            _stats["last_poll"] = datetime.now(timezone.utc).isoformat()

            # Claim next item
            claim = await cohort_post("/api/work-queue/claim")
            if claim is None or "error" in (claim or {}):
                await asyncio.sleep(POLL_INTERVAL)
                continue

            item = claim.get("item")
            if item is None:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            item_id = item.get("id", "unknown")
            channel = resolve_channel(item)
            logger.info("[>>] Claimed %s -> #%s: %s",
                        item_id, channel, item.get("description", "")[:80])

            _stats["current_item"] = item_id
            _stats["last_item_id"] = item_id

            # Dispatch to channel as a message
            message = build_channel_message(item)
            result = await cohort_post("/api/send", json={
                "channel": channel,
                "sender": WORKER_SENDER,
                "message": message,
                "metadata": {
                    "wq_item_id": item_id,
                    "source": "wq_dispatcher",
                },
            })

            _stats["current_item"] = None

            if result and result.get("success"):
                _stats["items_dispatched"] += 1
                logger.info("[OK] %s dispatched to #%s", item_id, channel)
            else:
                _stats["items_failed"] += 1
                error = (result or {}).get("error", "unknown error")
                logger.warning("[X] %s dispatch failed: %s", item_id, error)

                # Mark failed since we couldn't deliver
                await cohort_patch(
                    f"/api/work-queue/{item_id}",
                    json={"status": "failed", "result": f"Dispatch failed: {error}"},
                )

        except asyncio.CancelledError:
            logger.info("[!] Dispatcher loop cancelled")
            break
        except Exception as exc:
            logger.error("[X] Unexpected error in dispatch loop: %s", exc, exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start dispatch loop on startup, cancel on shutdown."""
    global _worker_task
    _stats["started_at"] = datetime.now(timezone.utc).isoformat()
    _worker_task = asyncio.create_task(poll_loop())
    logger.info("[OK] WQ Dispatcher service started on port %d", PORT)
    yield
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    logger.info("[OK] WQ Dispatcher service stopped")


app = FastAPI(
    title="Cohort WQ Dispatcher",
    description="Work-queue dispatcher that routes items to channels for execution",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health check endpoint for Cohort health monitor."""
    return {
        "status": "ok",
        "service": "wq_dispatcher",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stats": _stats,
    }


@app.get("/status")
async def status():
    """Detailed dispatcher status."""
    return {
        "service": "wq_dispatcher",
        "cohort_url": COHORT_URL,
        "poll_interval_s": POLL_INTERVAL,
        "default_channel": DEFAULT_CHANNEL,
        "sender": WORKER_SENDER,
        "stats": _stats,
    }


@app.post("/pause")
async def pause():
    """Pause the dispatch loop (stops claiming new items)."""
    _stats["paused"] = True
    logger.info("[!] Dispatcher paused")
    return {"paused": True}


@app.post("/resume")
async def resume():
    """Resume the dispatch loop."""
    _stats["paused"] = False
    logger.info("[OK] Dispatcher resumed")
    return {"paused": False}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="info",
    )
