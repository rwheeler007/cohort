"""Cohort HTTP server -- Starlette ASGI app wrapping ChatManager.

Provides the REST API that :class:`cohort.mcp.client.CohortClient` talks to.

Usage::

    python -m cohort serve                  # default 0.0.0.0:5000
    python -m cohort serve --port 8080      # custom port

Endpoints::

    GET  /health                              -> {"status": "ok"}
    GET  /api/channels                        -> [channel, ...]
    GET  /api/messages?channel=X&limit=50     -> {"messages": [...]}
    POST /api/send  {channel, sender, message} -> {"success": true, "message_id": "..."}
    POST /api/channels/{channel_id}/condense  -> {"success": true, "archived_count": N, ...}
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from cohort.chat import ChatManager
from cohort.registry import JsonFileStorage

logger = logging.getLogger(__name__)

# =====================================================================
# Shared state -- populated by create_app()
# =====================================================================

_chat: ChatManager | None = None


def _get_chat() -> ChatManager:
    """Return the global ChatManager instance (set during app startup)."""
    assert _chat is not None, "ChatManager not initialised -- call create_app() first"
    return _chat


# =====================================================================
# Route handlers
# =====================================================================

async def health(request: Request) -> JSONResponse:
    """GET /health -- liveness probe."""
    return JSONResponse({"status": "ok"})


async def list_channels(request: Request) -> JSONResponse:
    """GET /api/channels -- return all non-archived channels."""
    try:
        chat = _get_chat()
        channels = chat.list_channels(include_archived=False)
        return JSONResponse([ch.to_dict() for ch in channels])
    except Exception as exc:
        logger.exception("Error listing channels")
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


async def get_messages(request: Request) -> JSONResponse:
    """GET /api/messages?channel=X&limit=50 -- fetch channel messages."""
    channel = request.query_params.get("channel")
    if not channel:
        return JSONResponse(
            {"error": "Missing required query parameter: channel"},
            status_code=400,
        )

    try:
        limit = int(request.query_params.get("limit", "50"))
    except (TypeError, ValueError):
        limit = 50

    try:
        chat = _get_chat()
        messages = chat.get_channel_messages(channel, limit=limit)
        return JSONResponse({"messages": [m.to_dict() for m in messages]})
    except Exception as exc:
        logger.exception("Error fetching messages for channel %s", channel)
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


async def send_message(request: Request) -> JSONResponse:
    """POST /api/send -- post a message to a channel.

    Expects JSON body: ``{"channel": "...", "sender": "...", "message": "..."}``.
    Auto-creates the channel if it does not exist yet.
    """
    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        return JSONResponse(
            {"error": "Invalid JSON body"}, status_code=400,
        )

    channel = body.get("channel")
    sender = body.get("sender")
    message = body.get("message")

    if not channel or not sender or not message:
        missing = [
            name for name, val in [("channel", channel), ("sender", sender), ("message", message)]
            if not val
        ]
        return JSONResponse(
            {"error": f"Missing required fields: {', '.join(missing)}"},
            status_code=400,
        )

    try:
        chat = _get_chat()

        # Auto-create channel if it doesn't exist
        if chat.get_channel(channel) is None:
            chat.create_channel(
                name=channel,
                description=f"Auto-created channel: {channel}",
            )
            logger.info("Auto-created channel: %s", channel)

        msg = chat.post_message(
            channel_id=channel,
            sender=sender,
            content=message,
        )
        return JSONResponse({"success": True, "message_id": msg.id})
    except Exception as exc:
        logger.exception("Error posting message to %s", channel)
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


async def condense_channel(request: Request) -> JSONResponse:
    """POST /api/channels/{channel_id}/condense -- trim old messages.

    Keeps the last *keep_last* messages and deletes the rest.
    """
    channel_id = request.path_params["channel_id"]

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}

    keep_last = body.get("keep_last", 5)
    if not isinstance(keep_last, int) or keep_last < 1:
        keep_last = 5

    try:
        chat = _get_chat()
        storage = chat._storage  # noqa: SLF001 -- internal access for condense

        if chat.get_channel(channel_id) is None:
            return JSONResponse(
                {"error": f"Channel not found: {channel_id}"},
                status_code=404,
            )

        # Fetch all messages for this channel (high limit to get everything)
        all_messages = chat.get_channel_messages(channel_id, limit=10000)
        total = len(all_messages)

        if total <= keep_last:
            return JSONResponse({
                "success": True,
                "archived_count": 0,
                "message": "Nothing to condense",
            })

        # Determine which messages to keep (the last N)
        keep_ids = {m.id for m in all_messages[-keep_last:]}
        archived_count = total - keep_last

        # Rewrite the messages file, removing the old messages for this channel
        raw_messages: list[dict] = storage._read_json(  # noqa: SLF001
            storage._messages_path, [],  # noqa: SLF001
        )
        new_messages = [
            m for m in raw_messages
            if m.get("channel_id") != channel_id or m.get("id") in keep_ids
        ]
        storage._write_json(storage._messages_path, new_messages)  # noqa: SLF001

        logger.info(
            "Condensed channel %s: archived %d messages, kept %d",
            channel_id, archived_count, keep_last,
        )
        return JSONResponse({
            "success": True,
            "archived_count": archived_count,
            "message": f"Kept last {keep_last} messages",
        })
    except Exception as exc:
        logger.exception("Error condensing channel %s", channel_id)
        return JSONResponse(
            {"error": str(exc)}, status_code=500,
        )


# =====================================================================
# App factory
# =====================================================================

def create_app(data_dir: str = "data") -> Starlette:
    """Create and return the Starlette ASGI application.

    Parameters
    ----------
    data_dir:
        Directory for JSON file storage.  Defaults to the
        ``COHORT_DATA_DIR`` environment variable, or ``./data``.
    """
    global _chat  # noqa: PLW0603

    resolved_dir = os.environ.get("COHORT_DATA_DIR", data_dir)
    storage = JsonFileStorage(resolved_dir)
    _chat = ChatManager(storage)

    logger.info("[OK] ChatManager initialised (data_dir=%s)", resolved_dir)

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/api/channels", list_channels, methods=["GET"]),
        Route("/api/messages", get_messages, methods=["GET"]),
        Route("/api/send", send_message, methods=["POST"]),
        Route("/api/channels/{channel_id}/condense", condense_channel, methods=["POST"]),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    app = Starlette(routes=routes, middleware=middleware)
    return app


# =====================================================================
# Convenience runner
# =====================================================================

def serve(host: str = "0.0.0.0", port: int = 5000, data_dir: str = "data") -> None:
    """Start the cohort HTTP server with uvicorn.

    Parameters
    ----------
    host:
        Bind address (default ``0.0.0.0``).
    port:
        Port number (default ``5000``).
    data_dir:
        Directory for JSON file storage.
    """
    import uvicorn

    app = create_app(data_dir=data_dir)
    logger.info("[*] Starting cohort server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
