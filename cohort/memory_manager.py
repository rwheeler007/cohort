"""Agent memory lifecycle management for cohort.

Handles adding working memory entries, learned facts, trimming old
entries, archiving to text logs, and computing stats.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cohort.agent import AgentMemory, LearnedFact, WorkingMemoryEntry
from cohort.agent_store import AgentStore

logger = logging.getLogger(__name__)


@dataclass
class CleaningResult:
    """Outcome of a memory cleaning operation."""

    agent_id: str
    success: bool
    working_memory_removed: int = 0
    working_memory_kept: int = 0
    archive_path: str | None = None
    error: str | None = None


class MemoryManager:
    """Manages agent memory lifecycle: add, trim, archive, export.

    Parameters
    ----------
    store:
        :class:`~cohort.agent_store.AgentStore` for loading/saving memory.
    archive_dir:
        Directory for text-format memory archives.  If *None*, archiving
        is skipped.
    keep_last:
        Number of working memory entries to keep during cleaning.
    """

    def __init__(
        self,
        store: AgentStore,
        archive_dir: Path | None = None,
        keep_last: int = 10,
    ) -> None:
        self._store = store
        self._archive_dir = archive_dir
        self._keep_last = keep_last

    # =====================================================================
    # Working memory
    # =====================================================================

    def add_working_memory(
        self, agent_id: str, entry: WorkingMemoryEntry
    ) -> None:
        """Append a working memory entry to an agent's memory."""
        memory = self._store.load_memory(agent_id)
        if memory is None:
            memory = AgentMemory.create_empty(agent_id)
        memory.working_memory.append(entry)
        self._store.save_memory(agent_id, memory)

    # =====================================================================
    # Learned facts
    # =====================================================================

    def add_learned_fact(self, agent_id: str, fact: LearnedFact) -> None:
        """Add a learned fact to an agent's memory."""
        memory = self._store.load_memory(agent_id)
        if memory is None:
            memory = AgentMemory.create_empty(agent_id)
        memory.learned_facts.append(fact)
        self._store.save_memory(agent_id, memory)

    # =====================================================================
    # Collaborator tracking
    # =====================================================================

    def record_collaboration(
        self, agent_id: str, other_agent: str
    ) -> None:
        """Update collaborator tracking for an agent."""
        memory = self._store.load_memory(agent_id)
        if memory is None:
            memory = AgentMemory.create_empty(agent_id)
        memory.collaborators[other_agent] = {
            "last_interaction": datetime.now().isoformat(),
            "relationship": "mentioned",
        }
        self._store.save_memory(agent_id, memory)

    # =====================================================================
    # Cleaning
    # =====================================================================

    def clean_agent(
        self,
        agent_id: str,
        keep_last: int | None = None,
        dry_run: bool = False,
    ) -> CleaningResult:
        """Trim working memory for a single agent.

        Keeps the last *keep_last* entries, archives the rest to a
        text log (if ``archive_dir`` is set), and preserves
        ``learned_facts`` and ``learning_history``.
        """
        keep = keep_last if keep_last is not None else self._keep_last

        memory = self._store.load_memory(agent_id)
        if memory is None:
            return CleaningResult(
                agent_id=agent_id, success=False,
                error="Agent not found or no memory file",
            )

        total = len(memory.working_memory)
        if total <= keep:
            return CleaningResult(
                agent_id=agent_id, success=True,
                working_memory_removed=0,
                working_memory_kept=total,
            )

        to_remove = memory.working_memory[:-keep] if keep > 0 else memory.working_memory[:]
        to_keep = memory.working_memory[-keep:] if keep > 0 else []
        archive_path: str | None = None

        if not dry_run:
            # Archive removed entries to text log
            if self._archive_dir and to_remove:
                archive_path = self._archive_to_text(agent_id, to_remove)

            # Update memory
            memory.working_memory = to_keep
            memory.archive_history.append({
                "date": datetime.now().isoformat(),
                "working_memory_archived": len(to_remove),
                "txt_log": archive_path or "",
            })
            self._store.save_memory(agent_id, memory)

            logger.info(
                "[OK] Cleaned %s: removed %d, kept %d working_memory entries",
                agent_id, len(to_remove), len(to_keep),
            )

        return CleaningResult(
            agent_id=agent_id,
            success=True,
            working_memory_removed=len(to_remove),
            working_memory_kept=len(to_keep),
            archive_path=archive_path,
        )

    def clean_all(
        self,
        keep_last: int | None = None,
        dry_run: bool = False,
    ) -> list[CleaningResult]:
        """Trim working memory for all agents."""
        results: list[CleaningResult] = []
        for agent in self._store.list_agents(include_hidden=True):
            result = self.clean_agent(
                agent.agent_id, keep_last=keep_last, dry_run=dry_run,
            )
            results.append(result)
        return results

    def _archive_to_text(
        self, agent_id: str, entries: list[WorkingMemoryEntry]
    ) -> str:
        """Write working memory entries to a human-readable text file."""
        if self._archive_dir is None:
            return ""
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{agent_id}_archive_{timestamp}.txt"
        path = self._archive_dir / filename

        lines = [
            f"Memory Archive: {agent_id}",
            f"Archived: {datetime.now().isoformat()}",
            f"Entries: {len(entries)}",
            "=" * 60,
            "",
        ]
        for entry in entries:
            lines.append(f"[{entry.timestamp}] Channel: {entry.channel}")
            lines.append(f"  Input: {entry.input[:500]}")
            lines.append(f"  Response: {entry.response[:500]}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    # =====================================================================
    # Stats
    # =====================================================================

    def get_stats(self, agent_id: str) -> dict[str, Any]:
        """Return memory statistics for a single agent."""
        memory = self._store.load_memory(agent_id)
        if memory is None:
            return {"agent_id": agent_id, "error": "not_found"}

        return {
            "agent_id": agent_id,
            "working_memory_count": len(memory.working_memory),
            "learned_facts_count": len(memory.learned_facts),
            "learning_history_count": len(memory.learning_history),
            "collaborators_count": len(memory.collaborators),
            "archive_count": len(memory.archive_history),
            "known_paths_count": len(memory.known_paths),
            "active_tasks_count": len(memory.active_tasks),
        }

    def get_all_stats(self) -> list[dict[str, Any]]:
        """Return memory stats for all agents."""
        stats: list[dict[str, Any]] = []
        for agent in self._store.list_agents(include_hidden=True):
            stats.append(self.get_stats(agent.agent_id))
        return stats
