"""Unified inventory entry schema for cross-project capability discovery.

Every tool, golden pattern, project, and CLAUDE.md export in the ecosystem
is normalized to this shape before storage, querying, or prompt injection.

Consumers:
    - inventory_loader.py   (produces entries)
    - inventory_query.py    (scores entries against a query)
    - agent_router.py       (injects top matches into chat prompts)
    - /api/inventory        (serves merged inventory as JSON)

The shape is intentionally aligned with BOSS's data/project_inventory.yaml
and data/tool_inventory.yaml so entries from both systems can coexist in a
single merged list without transformation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class InventoryEntry:
    """A single capability in the unified ecosystem inventory.

    Fields:
        id:             Unique slug (e.g. "llm-router", "cohort", "cli-first-development")
        source_project: Which project this came from (e.g. "BOSS", "cohort", "3DWorkshop")
        entry_point:    File or directory path relative to the source project root
        keywords:       Terms for LLM-scored relevance matching
        description:    One-line human summary of what this provides
        type:           One of: tool, pattern, project, export
        status:         active or deprecated (deprecated entries are kept but down-ranked)
        last_verified:  ISO date when this entry was last confirmed to exist
    """

    id: str
    source_project: str
    entry_point: str = ""
    keywords: list[str] = field(default_factory=list)
    description: str = ""
    type: str = "tool"          # tool | pattern | project | export
    status: str = "active"      # active | deprecated
    last_verified: str = ""     # ISO date, empty = never verified

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InventoryEntry:
        return cls(
            id=data.get("id", ""),
            source_project=data.get("source_project", ""),
            entry_point=data.get("entry_point", data.get("path", "")),
            keywords=data.get("keywords", []),
            description=data.get("description", ""),
            type=data.get("type", "tool"),
            status=data.get("status", "active"),
            last_verified=data.get("last_verified", ""),
        )

    def to_inventory_line(self) -> str:
        """Format for LLM prompt injection (one line per entry)."""
        kw = ", ".join(self.keywords[:8]) if self.keywords else ""
        return f"[{self.type}: {self.id}] {self.entry_point} -- {self.description} (keywords: {kw})"


def today_iso() -> str:
    """Return today's date as ISO string for last_verified."""
    return datetime.now().strftime("%Y-%m-%d")
