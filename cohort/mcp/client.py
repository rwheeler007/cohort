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
