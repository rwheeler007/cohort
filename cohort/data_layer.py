"""Cohort data layer -- feeds real data into Socket.IO events.

Provides :class:`CohortDataLayer` which reads agent status from
the Orchestrator and ChatManager, manages a task queue, and records
human reviews.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from cohort.chat import ChatManager

logger = logging.getLogger(__name__)


class CohortDataLayer:
    """Backend data provider for the Cohort dashboard UI.

    Parameters
    ----------
    chat:
        ChatManager instance for message/channel access.
    agents:
        Agent registry mapping ``agent_id -> config dict``.
    """

    def __init__(
        self,
        chat: ChatManager,
        agents: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.chat = chat
        self._agents: dict[str, dict[str, Any]] = agents or {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._reviews: dict[str, dict[str, Any]] = {}

    # =====================================================================
    # Team snapshot
    # =====================================================================

    def get_team_snapshot(self) -> dict[str, Any]:
        """Return current state of all agents for the Team Dashboard panel."""
        team: list[dict[str, Any]] = []
        for agent_id, config in self._agents.items():
            agent_tasks = [
                t for t in self._tasks.values()
                if t.get("agent_id") == agent_id and t.get("status") != "complete"
            ]
            status = "busy" if agent_tasks else "idle"

            team.append({
                "agent_id": agent_id,
                "name": config.get("name", agent_id),
                "status": status,
                "skills": config.get("capabilities", []),
                "triggers": config.get("triggers", []),
                "current_task": agent_tasks[0] if agent_tasks else None,
                "tasks_completed": sum(
                    1 for t in self._tasks.values()
                    if t.get("agent_id") == agent_id and t.get("status") == "complete"
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
    # Task management
    # =====================================================================

    def assign_task(
        self,
        agent_id: str,
        description: str,
        priority: str = "medium",
    ) -> dict[str, Any]:
        """Create and assign a task to an agent."""
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = {
            "task_id": task_id,
            "agent_id": agent_id,
            "description": description,
            "priority": priority,
            "status": "briefing",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "output": None,
            "review": None,
        }
        self._tasks[task_id] = task
        logger.info("[+] Task %s assigned to %s (briefing)", task_id, agent_id)
        return task

    def confirm_task(
        self,
        task_id: str,
        confirmed_brief: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Transition a task from briefing to assigned (ready for execution).

        Parameters
        ----------
        task_id:
            The task to confirm.
        confirmed_brief:
            Structured data extracted from the agent's confirmation block
            (goal, approach, scope, acceptance).
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task["status"] = "assigned"
        task["brief"] = confirmed_brief
        task["updated_at"] = datetime.now().isoformat()
        logger.info("[OK] Task %s confirmed, ready for execution", task_id)
        return task

    def update_task_progress(
        self,
        task_id: str,
        status: str,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a task's status and optional progress data."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task["status"] = status
        task["updated_at"] = datetime.now().isoformat()
        if progress:
            task.setdefault("progress", {}).update(progress)
        return task

    def complete_task(
        self,
        task_id: str,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Mark a task as complete with optional output payload."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task["status"] = "complete"
        task["updated_at"] = datetime.now().isoformat()
        task["completed_at"] = datetime.now().isoformat()
        if output:
            task["output"] = output
        logger.info("[OK] Task %s completed", task_id)
        return task

    def get_task_queue(
        self,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all tasks, optionally filtered by status."""
        tasks = list(self._tasks.values())
        if status_filter:
            tasks = [t for t in tasks if t.get("status") == status_filter]
        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return tasks

    # =====================================================================
    # Reviews
    # =====================================================================

    def record_review(
        self,
        task_id: str,
        verdict: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Record a human review for a task output."""
        review = {
            "task_id": task_id,
            "verdict": verdict,
            "notes": notes,
            "reviewed_at": datetime.now().isoformat(),
        }
        self._reviews[task_id] = review

        # Update the task's review field
        task = self._tasks.get(task_id)
        if task:
            task["review"] = review

        logger.info("[*] Review for %s: %s", task_id, verdict)
        return review

    def get_outputs_for_review(self) -> list[dict[str, Any]]:
        """Return completed tasks that haven't been reviewed yet."""
        return [
            t for t in self._tasks.values()
            if t.get("status") == "complete" and t.get("review") is None
        ]

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
