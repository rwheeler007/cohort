"""Cohort data layer -- feeds real data into Socket.IO events.

Provides :class:`CohortDataLayer` which reads agent status from
the Orchestrator and ChatManager, and provides the team snapshot
for the dashboard.

Task persistence is handled by :class:`cohort.task_store.TaskStore`.
This class retains agent registry and team snapshot functionality.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from cohort.api import ChatManager

logger = logging.getLogger(__name__)


class CohortDataLayer:
    """Backend data provider for the Cohort dashboard UI.

    Parameters
    ----------
    chat:
        ChatManager instance for message/channel access.
    agents:
        Agent registry mapping ``agent_id -> config dict``.
    task_store:
        Optional TaskStore instance for persistent task data.
        When set, ``get_team_snapshot`` reads tasks from the store.
    """

    def __init__(
        self,
        chat: ChatManager,
        agents: dict[str, dict[str, Any]] | None = None,
        task_store: Any = None,
    ) -> None:
        self.chat = chat
        self._agents: dict[str, dict[str, Any]] = agents or {}
        self._task_store = task_store

    def set_task_store(self, store: Any) -> None:
        """Wire the TaskStore for persistent task reads."""
        self._task_store = store

    # =====================================================================
    # Team snapshot
    # =====================================================================

    def get_team_snapshot(self) -> dict[str, Any]:
        """Return current state of all agents for the Team Dashboard panel."""
        # Reap stale briefings before reading, then fetch tasks
        if self._task_store is not None:
            self._task_store.reap_stale_briefings()
            all_tasks = self._task_store.list_tasks(limit=500)
        else:
            all_tasks = []

        team: list[dict[str, Any]] = []
        for agent_id, config in self._agents.items():
            agent_tasks = [
                t for t in all_tasks
                if t.get("agent_id") == agent_id and t.get("status") not in ("complete", "approved", "rejected", "failed")
            ]
            status = "busy" if agent_tasks else "idle"

            team.append({
                "agent_id": agent_id,
                "name": config.get("name", agent_id),
                "status": status,
                "skills": config.get("capabilities", []),
                "triggers": config.get("triggers", []),
                "current_task": agent_tasks[0] if agent_tasks else None,
                "active_task_count": len(agent_tasks),
                "tasks_completed": sum(
                    1 for t in all_tasks
                    if t.get("agent_id") == agent_id and t.get("status") in ("complete", "approved")
                ),
                # Display metadata
                "avatar": config.get("avatar", ""),
                "nickname": config.get("nickname", ""),
                "color": config.get("color", "#95A5A6"),
                "group": config.get("group", "Other"),
            })

        return {
            "agents": team,
            "total_agents": len(team),
            "busy_count": sum(1 for a in team if a["status"] == "busy"),
            "idle_count": sum(1 for a in team if a["status"] == "idle"),
            "timestamp": datetime.now().isoformat(),
        }

    # =====================================================================
    # Agent registry helpers
    # =====================================================================

    def register_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Add or update an agent in the registry."""
        self._agents[agent_id] = config

    def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent from the registry."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            return True
        return False
