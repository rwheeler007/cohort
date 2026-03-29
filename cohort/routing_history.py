"""Routing feedback loop for cohort.

Records which agents were routed to and whether the outcome was
successful.  Uses this history to adjust routing scores over time,
replacing the static neutral-0.5 historical_success dimension.

Storage: JSON file (lightweight, append-oriented).  Falls back to
in-memory-only when the file is unwritable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RoutingOutcome:
    """Record of a single routing decision and its result."""

    task_keywords: list[str]
    agent_id: str
    score_at_routing: float
    outcome: str  # "success" | "partial" | "failed" | "reassigned"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    reassigned_to: str | None = None


class RoutingHistory:
    """Persistent routing feedback store.

    Parameters
    ----------
    path:
        Path to the JSON file for persistence.  If *None*, operates
        in memory only (no persistence across restarts).
    max_entries:
        Maximum entries to keep in memory / on disk.  Oldest entries
        are pruned when the limit is reached.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        max_entries: int = 500,
    ) -> None:
        self._path = Path(path) if path else None
        self._max_entries = max_entries
        self._entries: list[RoutingOutcome] = []
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data:
                self._entries.append(RoutingOutcome(**item))
            logger.debug("Loaded %d routing history entries", len(self._entries))
        except Exception:
            logger.warning("Could not load routing history from %s", self._path)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps([asdict(e) for e in self._entries], indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("Could not save routing history to %s", self._path)

    # -- recording ---------------------------------------------------------

    def record(self, outcome: RoutingOutcome) -> None:
        """Store a routing outcome."""
        self._entries.append(outcome)
        # Prune oldest if over limit
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]
        self._save()

    # -- querying ----------------------------------------------------------

    def get_outcomes_for_agent(
        self,
        agent_id: str,
        keywords: list[str] | None = None,
        lookback: int = 50,
    ) -> list[RoutingOutcome]:
        """Retrieve recent outcomes for an agent, optionally filtered by keyword overlap."""
        agent_entries = [e for e in self._entries if e.agent_id == agent_id]
        if keywords:
            kw_set = set(keywords)
            agent_entries = [
                e for e in agent_entries
                if kw_set & set(e.task_keywords)
            ]
        return agent_entries[-lookback:]

    def success_rate(
        self,
        agent_id: str,
        keywords: list[str] | None = None,
    ) -> float | None:
        """Compute success rate for an agent.  Returns None if no data."""
        entries = self.get_outcomes_for_agent(agent_id, keywords)
        if not entries:
            return None
        successes = sum(1 for e in entries if e.outcome == "success")
        return successes / len(entries)

    def adjusted_score(
        self,
        base_score: float,
        agent_id: str,
        keywords: list[str],
    ) -> float:
        """Apply historical adjustment to a routing score.

        Dampened to a maximum of +/-0.15 adjustment.  Returns
        *base_score* unchanged when no history data is available.
        """
        rate = self.success_rate(agent_id, keywords)
        if rate is None:
            return base_score
        # Dampen: max ±0.15 adjustment around 0.5 baseline
        adjustment = (rate - 0.5) * 0.3
        return max(0.0, min(1.0, base_score + adjustment))
