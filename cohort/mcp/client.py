"""Async HTTP client for a Cohort chat server.

Wraps the chat API with fail-safe async httpx calls.
Returns *None* on connection/timeout errors so the MCP server can
return actionable error messages instead of crashing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:5100"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


# =====================================================================
# Internal helper
# =====================================================================

async def _request(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any | None:
    """Send an async HTTP request. Returns parsed JSON or *None* on error."""
    if params:
        params = {k: v for k, v in params.items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(
                method, url, json=json_body, params=params or None,
            )
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        logger.debug("[!] cohort client: cannot connect to %s", url)
        return None
    except httpx.TimeoutException:
        logger.debug("[!] cohort client: timeout for %s", url)
        return None
    except httpx.HTTPStatusError as exc:
        logger.debug(
            "[!] cohort client: HTTP %s from %s",
            exc.response.status_code, url,
        )
        return None
    except Exception as exc:
        logger.debug("[!] cohort client: error for %s - %s", url, exc)
        return None


# =====================================================================
# CohortClient
# =====================================================================

class CohortClient:
    """Async HTTP client for a cohort chat server.

    Parameters
    ----------
    base_url:
        Base URL of the chat server (default ``http://127.0.0.1:5000``).
    checklist_path:
        Optional filesystem path to a to-do checklist JSON file.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        checklist_path: Path | str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._checklist_path = Path(checklist_path) if checklist_path else None

    # -- channels -------------------------------------------------------

    async def get_channels(self) -> list[dict[str, Any]] | None:
        return await _request("GET", f"{self.base_url}/api/channels")

    async def create_channel(
        self,
        name: str,
        description: str = "",
        members: list[str] | None = None,
        is_private: bool = False,
        topic: str = "",
    ) -> dict[str, Any] | None:
        """POST /api/channels -> create a new channel."""
        return await _request(
            "POST",
            f"{self.base_url}/api/channels",
            json_body={
                "name": name,
                "description": description,
                "members": members or [],
                "is_private": is_private,
                "topic": topic,
            },
        )

    async def get_messages(
        self, channel: str, limit: int = 50
    ) -> list[dict[str, Any]] | None:
        data = await _request(
            "GET",
            f"{self.base_url}/api/messages",
            params={"channel": channel, "limit": limit},
        )
        if data and "messages" in data:
            return data["messages"]
        return None

    async def post_message(
        self, channel: str, sender: str, message: str
    ) -> dict[str, Any] | None:
        return await _request(
            "POST",
            f"{self.base_url}/api/send",
            json_body={"channel": channel, "sender": sender, "message": message},
        )

    async def condense_channel(
        self, channel: str, keep_last: int = 5
    ) -> dict[str, Any] | None:
        return await _request(
            "POST",
            f"{self.base_url}/api/channels/{channel}/condense",
            json_body={"keep_last": keep_last},
        )

    # -- agents ----------------------------------------------------------

    async def list_agents(self) -> list[dict[str, Any]] | None:
        """GET /api/agents -> list of agent config dicts."""
        return await _request("GET", f"{self.base_url}/api/agents")

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """GET /api/agents/{agent_id} -> full agent config."""
        return await _request("GET", f"{self.base_url}/api/agents/{agent_id}")

    async def get_agent_memory(self, agent_id: str) -> dict[str, Any] | None:
        """GET /api/agents/{agent_id}/memory -> agent memory."""
        return await _request("GET", f"{self.base_url}/api/agents/{agent_id}/memory")

    async def create_agent(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        """POST /api/agents/create -> create a new agent."""
        return await _request(
            "POST",
            f"{self.base_url}/api/agents/create",
            json_body=spec,
        )

    async def clean_agent_memory(
        self, agent_id: str, keep_last: int = 10, dry_run: bool = False
    ) -> dict[str, Any] | None:
        """POST /api/agents/{agent_id}/memory/clean -> trim working memory."""
        return await _request(
            "POST",
            f"{self.base_url}/api/agents/{agent_id}/memory/clean",
            json_body={"keep_last": keep_last, "dry_run": dry_run},
        )

    async def add_agent_fact(
        self, agent_id: str, fact: dict[str, Any]
    ) -> dict[str, Any] | None:
        """POST /api/agents/{agent_id}/memory/facts -> add a learned fact."""
        return await _request(
            "POST",
            f"{self.base_url}/api/agents/{agent_id}/memory/facts",
            json_body=fact,
        )

    # -- search & mentions -----------------------------------------------

    async def search_messages(
        self, query: str, channel: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]] | None:
        """Search messages across channels by keyword (case-insensitive).

        Fetches messages from the server and filters client-side since the
        Cohort server has no dedicated search endpoint.
        """
        if channel:
            messages = await self.get_messages(channel, limit=500)
            if messages is None:
                return None
        else:
            channels = await self.get_channels()
            if channels is None:
                return None
            messages = []
            for ch in channels:
                ch_id = ch.get("id", "")
                ch_msgs = await self.get_messages(ch_id, limit=200)
                if ch_msgs:
                    for m in ch_msgs:
                        m["_channel"] = ch_id
                    messages.extend(ch_msgs)

        query_lower = query.lower()
        matches = [
            m for m in messages
            if query_lower in (m.get("content") or "").lower()
            or query_lower in (m.get("sender") or "").lower()
        ]
        return matches[-limit:]

    async def get_mentions(
        self, agent_id: str, limit: int = 50
    ) -> list[dict[str, Any]] | None:
        """Find messages that @mention a specific agent."""
        channels = await self.get_channels()
        if channels is None:
            return None
        mention_pattern = f"@{agent_id}"
        matches: list[dict[str, Any]] = []
        for ch in channels:
            ch_id = ch.get("id", "")
            ch_msgs = await self.get_messages(ch_id, limit=200)
            if ch_msgs:
                for m in ch_msgs:
                    if mention_pattern in (m.get("content") or ""):
                        m["_channel"] = ch_id
                        matches.append(m)
        return matches[-limit:]

    # -- roundtable ------------------------------------------------------

    async def start_roundtable(
        self,
        channel: str,
        agents: list[str],
        prompt: str,
        sender: str = "claude_code",
    ) -> dict[str, Any] | None:
        """POST /api/roundtable/start -> start a roundtable session."""
        return await _request(
            "POST",
            f"{self.base_url}/api/roundtable/start",
            json_body={
                "channel": channel,
                "agents": agents,
                "prompt": prompt,
                "sender": sender,
            },
        )

    async def get_roundtable_status(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """GET /api/roundtable/{session_id}/status."""
        return await _request(
            "GET",
            f"{self.base_url}/api/roundtable/{session_id}/status",
        )

    # -- agent persona ---------------------------------------------------

    async def get_agent_persona(self, agent_id: str) -> str | None:
        """GET /api/agents/{agent_id}/prompt -> agent prompt text.

        Falls back to the config name/role if no prompt is available.
        """
        data = await _request(
            "GET",
            f"{self.base_url}/api/agents/{agent_id}/prompt",
        )
        if data is None:
            return None
        if isinstance(data, dict):
            return data.get("prompt") or data.get("content") or None
        if isinstance(data, str):
            return data
        return None

    # -- channel checklists (file-based) ----------------------------------

    def _channel_checklist_path(self, channel: str) -> Path | None:
        """Resolve the checklist file path for a channel."""
        if not self._checklist_path:
            return None
        base_dir = self._checklist_path.parent / "channel_checklists"
        # Sanitize channel name
        safe_ch = "".join(c for c in channel if c.isalnum() or c in "-_")
        if not safe_ch:
            return None
        return base_dir / f"{safe_ch}.json"

    async def read_channel_checklist(self, channel: str) -> dict[str, Any] | None:
        """Read a channel-specific checklist from disk."""
        path = self._channel_checklist_path(channel)
        if not path:
            return {"items": []}
        try:
            if not path.exists():
                return {"items": []}
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("[!] cohort client: channel checklist read error - %s", exc)
            return None

    async def write_channel_checklist(
        self, channel: str, data: dict[str, Any]
    ) -> bool:
        """Write a channel-specific checklist to disk."""
        path = self._channel_checklist_path(channel)
        if not path:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return True
        except Exception as exc:
            logger.debug("[!] cohort client: channel checklist write error - %s", exc)
            return False

    # -- checklist (file-based) -----------------------------------------

    async def read_checklist(self) -> dict[str, Any] | None:
        if not self._checklist_path:
            return {"items": []}
        try:
            if not self._checklist_path.exists():
                return {"items": []}
            return json.loads(self._checklist_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("[!] cohort client: checklist read error - %s", exc)
            return None

    async def write_checklist(self, data: dict[str, Any]) -> bool:
        if not self._checklist_path:
            return False
        try:
            self._checklist_path.parent.mkdir(parents=True, exist_ok=True)
            self._checklist_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return True
        except Exception as exc:
            logger.debug("[!] cohort client: checklist write error - %s", exc)
            return False
