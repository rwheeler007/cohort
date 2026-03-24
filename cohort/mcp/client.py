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
        Base URL of the chat server (default ``http://127.0.0.1:5100``).
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

    async def delete_channel(self, channel_id: str) -> dict[str, Any] | None:
        """DELETE /api/channels/{channel_id} -> soft-delete a channel."""
        return await _request(
            "DELETE",
            f"{self.base_url}/api/channels/{channel_id}",
        )

    async def archive_channel(self, channel_id: str) -> dict[str, Any] | None:
        """POST /api/channels/{channel_id}/archive -> archive a channel."""
        return await _request(
            "POST",
            f"{self.base_url}/api/channels/{channel_id}/archive",
        )

    async def rename_channel(self, channel_id: str, new_name: str) -> dict[str, Any] | None:
        """PATCH /api/channels/{channel_id} -> rename a channel."""
        return await _request(
            "PATCH",
            f"{self.base_url}/api/channels/{channel_id}",
            json_body={"name": new_name},
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
        self, channel: str, sender: str, message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        body: dict[str, Any] = {"channel": channel, "sender": sender, "message": message}
        if metadata:
            body["metadata"] = metadata
        return await _request(
            "POST",
            f"{self.base_url}/api/send",
            json_body=body,
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

    # -- sessions ----------------------------------------------------------

    async def start_session(
        self,
        channel: str,
        agents: list[str],
        prompt: str,
        sender: str = "claude_code",
    ) -> dict[str, Any] | None:
        """POST /api/sessions/start -> start a discussion session."""
        return await _request(
            "POST",
            f"{self.base_url}/api/sessions/start",
            json_body={
                "channel": channel,
                "agents": agents,
                "prompt": prompt,
                "sender": sender,
            },
        )

    # Deprecated alias
    start_roundtable = start_session

    async def get_session_status(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """GET /api/sessions/{session_id}/status."""
        return await _request(
            "GET",
            f"{self.base_url}/api/sessions/{session_id}/status",
        )

    # Deprecated alias
    get_roundtable_status = get_session_status

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

    # -- work queue ------------------------------------------------------

    async def get_task_queue(
        self, status: str | None = None
    ) -> list[dict[str, Any]] | None:
        """GET /api/tasks -> task queue, optionally filtered by status."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        data = await _request(
            "GET", f"{self.base_url}/api/tasks", params=params,
        )
        if data and "tasks" in data:
            return data["tasks"]
        return None

    async def get_outputs_for_review(self) -> list[dict[str, Any]] | None:
        """GET /api/outputs -> completed tasks awaiting review."""
        data = await _request("GET", f"{self.base_url}/api/outputs")
        if data and "outputs" in data:
            return data["outputs"]
        return None

    async def create_task(
        self,
        agent_id: str,
        description: str,
        priority: str = "medium",
        trigger_type: str = "manual",
        trigger_source: str = "user",
        tool: str | None = None,
        success_criteria: str | None = None,
    ) -> dict[str, Any] | None:
        """POST /api/tasks -> create and assign a task."""
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "description": description,
            "priority": priority,
            "trigger_type": trigger_type,
            "trigger_source": trigger_source,
        }
        if tool:
            body["tool"] = tool
        if success_criteria:
            body["success_criteria"] = success_criteria
        return await _request(
            "POST",
            f"{self.base_url}/api/tasks",
            json_body=body,
        )

    # -- work queue (sequential execution) --------------------------------

    async def get_work_queue(
        self, status: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """GET /api/work-queue -> sequential work queue items."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        data = await _request(
            "GET", f"{self.base_url}/api/work-queue", params=params,
        )
        if data and "items" in data:
            return data["items"]
        return None

    async def enqueue_work_item(
        self,
        description: str,
        requester: str = "claude_code",
        priority: str = "medium",
        agent_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """POST /api/work-queue -> enqueue a new item."""
        body: dict[str, Any] = {
            "description": description,
            "requester": requester,
            "priority": priority,
        }
        if agent_id:
            body["agent_id"] = agent_id
        if depends_on:
            body["depends_on"] = depends_on
        return await _request(
            "POST", f"{self.base_url}/api/work-queue", json_body=body,
        )

    async def claim_work_item(self) -> dict[str, Any] | None:
        """POST /api/work-queue/claim -> claim next queued item."""
        return await _request(
            "POST", f"{self.base_url}/api/work-queue/claim",
        )

    async def update_work_item(
        self,
        item_id: str,
        status: str,
        result: str | None = None,
    ) -> dict[str, Any] | None:
        """PATCH /api/work-queue/{item_id} -> update item status."""
        body: dict[str, Any] = {"status": status}
        if result is not None:
            body["result"] = result
        return await _request(
            "PATCH", f"{self.base_url}/api/work-queue/{item_id}", json_body=body,
        )

    async def get_work_item(self, item_id: str) -> dict[str, Any] | None:
        """GET /api/work-queue/{item_id} -> single item."""
        data = await _request(
            "GET", f"{self.base_url}/api/work-queue/{item_id}",
        )
        if data and "item" in data:
            return data["item"]
        return None

    # -- briefing --------------------------------------------------------

    async def generate_briefing(
        self,
        hours: int = 24,
        post_to_channel: bool = True,
        channel: str = "daily-digest",
    ) -> dict[str, Any] | None:
        """POST /api/briefing/generate -> generate executive briefing."""
        return await _request(
            "POST",
            f"{self.base_url}/api/briefing/generate",
            json_body={
                "hours": hours,
                "post_to_channel": post_to_channel,
                "channel": channel,
            },
        )

    async def get_latest_briefing(self) -> dict[str, Any] | None:
        """GET /api/briefing/latest -> most recent briefing."""
        return await _request(
            "GET",
            f"{self.base_url}/api/briefing/latest",
        )

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
