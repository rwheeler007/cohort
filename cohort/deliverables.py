"""Deliverable Tracking for Cohort.

Acceptance criteria management for tasks and work items. Tracks deliverables
through their lifecycle (pending → pass/fail/skip) with structured reporting.

Extracted and generalized from BOSS's CodeQueueTask deliverables and
SelfReviewLoop. Language-agnostic (no Python AST checks — those can be
added as plugins via the ``test_fn`` callback on individual deliverables).

Usage::

    tracker = DeliverableTracker()
    tracker.set_deliverables("task_123", [
        {"id": "D1", "description": "API returns 200 on valid input", "category": "functional"},
        {"id": "D2", "description": "Input validation rejects XSS", "category": "security"},
    ])
    tracker.finalize("task_123")
    report = tracker.generate_report("task_123")
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# =====================================================================
# Constants
# =====================================================================

VALID_CATEGORIES = frozenset({"functional", "quality", "security", "testing"})
VALID_STATUSES = frozenset({"pending", "pass", "fail", "skip"})
MAX_DELIVERABLES_PER_ITEM = 50
MAX_DESCRIPTION_LENGTH = 500


# =====================================================================
# Data model
# =====================================================================

@dataclass
class Deliverable:
    """A single acceptance criterion."""

    id: str
    description: str
    category: str = "functional"       # functional | quality | security | testing
    source: str = ""                   # which agent/role defined this
    status: str = "pending"            # pending | pass | fail | skip
    notes: List[str] = field(default_factory=list)
    verified_at: Optional[str] = None
    verified_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Deliverable:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


# =====================================================================
# Validation
# =====================================================================

def validate_deliverables(deliverables: List[Dict[str, Any]]) -> List[str]:
    """Validate a list of deliverable dicts.  Returns list of error messages (empty = valid)."""
    errors: List[str] = []

    if not deliverables:
        errors.append("At least one deliverable is required")
        return errors

    if len(deliverables) > MAX_DELIVERABLES_PER_ITEM:
        errors.append(f"Too many deliverables: {len(deliverables)} (max {MAX_DELIVERABLES_PER_ITEM})")

    seen_ids: set = set()
    for i, d in enumerate(deliverables):
        prefix = f"Deliverable [{i}]"

        if not isinstance(d, dict):
            errors.append(f"{prefix}: must be a dict")
            continue

        # Required fields
        if not d.get("id"):
            errors.append(f"{prefix}: missing 'id'")
        elif d["id"] in seen_ids:
            errors.append(f"{prefix}: duplicate id '{d['id']}'")
        else:
            seen_ids.add(d["id"])

        if not d.get("description"):
            errors.append(f"{prefix}: missing 'description'")
        elif len(d["description"]) > MAX_DESCRIPTION_LENGTH:
            errors.append(
                f"{prefix}: description exceeds {MAX_DESCRIPTION_LENGTH} chars"
            )

        # Optional fields with enum validation
        cat = d.get("category", "functional")
        if cat not in VALID_CATEGORIES:
            errors.append(f"{prefix}: invalid category '{cat}'")

        status = d.get("status", "pending")
        if status not in VALID_STATUSES:
            errors.append(f"{prefix}: invalid status '{status}'")

    return errors


# =====================================================================
# DeliverableTracker
# =====================================================================

class DeliverableTracker:
    """Manages deliverables for multiple items (tasks or work queue items).

    Thread-safe via per-call dict operations (no shared mutable state
    beyond the items dict, which is only mutated through public methods).

    Parameters
    ----------
    data_dir:
        Optional directory for persistence.  If provided, deliverables
        are stored at ``{data_dir}/deliverables.json``.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._data_dir = data_dir
        self._path = data_dir / "deliverables.json" if data_dir else None
        # item_id -> {"deliverables": [Deliverable], "finalized": bool, "finalized_at": str|None}
        self._items: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # -- persistence ----------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        self._items = {}
        if self._path and self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for item_id, item_data in raw.get("items", {}).items():
                    self._items[item_id] = {
                        "deliverables": [
                            Deliverable.from_dict(d)
                            for d in item_data.get("deliverables", [])
                        ],
                        "finalized": item_data.get("finalized", False),
                        "finalized_at": item_data.get("finalized_at"),
                    }
            except Exception as exc:
                logger.warning("[!] Deliverables load error: %s", exc)
        self._loaded = True

    def _save_to_disk(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "items": {
                    item_id: {
                        "deliverables": [d.to_dict() for d in entry["deliverables"]],
                        "finalized": entry["finalized"],
                        "finalized_at": entry["finalized_at"],
                    }
                    for item_id, entry in self._items.items()
                },
            }
            self._path.write_text(
                json.dumps(data, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[!] Deliverables save error: %s", exc)

    # -- public API -----------------------------------------------------

    def set_deliverables(
        self,
        item_id: str,
        deliverables: List[Dict[str, Any]],
        source: str = "",
        append: bool = False,
    ) -> List[Deliverable]:
        """Set or append deliverables for an item.

        Parameters
        ----------
        item_id:
            The task or work item ID.
        deliverables:
            List of deliverable dicts (must have ``id`` and ``description``).
        source:
            Who defined these criteria (agent role or user).
        append:
            If True, add to existing deliverables.  If False, replace.

        Returns the full list of ``Deliverable`` objects for this item.

        Raises ``ValueError`` on validation failure.
        """
        self._ensure_loaded()

        errors = validate_deliverables(deliverables)
        if errors:
            raise ValueError(f"Invalid deliverables: {'; '.join(errors)}")

        new_deliverables = []
        for d in deliverables:
            new_deliverables.append(Deliverable(
                id=d["id"],
                description=d["description"],
                category=d.get("category", "functional"),
                source=source or d.get("source", ""),
                status=d.get("status", "pending"),
                notes=d.get("notes", []),
            ))

        if append and item_id in self._items:
            existing = self._items[item_id]["deliverables"]
            existing_ids = {d.id for d in existing}
            for nd in new_deliverables:
                if nd.id not in existing_ids:
                    existing.append(nd)
                    existing_ids.add(nd.id)
        else:
            self._items[item_id] = {
                "deliverables": new_deliverables,
                "finalized": False,
                "finalized_at": None,
            }

        self._save_to_disk()
        return self._items[item_id]["deliverables"]

    def finalize(self, item_id: str) -> bool:
        """Mark deliverables as finalized (no more changes allowed).

        Returns True if finalized, False if item not found or already finalized.
        """
        self._ensure_loaded()

        entry = self._items.get(item_id)
        if not entry:
            return False
        if entry["finalized"]:
            return False

        entry["finalized"] = True
        entry["finalized_at"] = datetime.now(timezone.utc).isoformat()
        self._save_to_disk()
        return True

    def is_finalized(self, item_id: str) -> bool:
        """Check if deliverables are finalized for an item."""
        self._ensure_loaded()
        entry = self._items.get(item_id)
        return entry["finalized"] if entry else False

    def get_deliverables(self, item_id: str) -> List[Deliverable]:
        """Get deliverables for an item (empty list if not found)."""
        self._ensure_loaded()
        entry = self._items.get(item_id)
        return list(entry["deliverables"]) if entry else []

    def update_status(
        self,
        item_id: str,
        deliverable_id: str,
        status: str,
        verified_by: str = "",
        notes: Optional[str] = None,
    ) -> Optional[Deliverable]:
        """Update the status of a specific deliverable.

        Returns the updated deliverable, or None if not found.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Valid: {sorted(VALID_STATUSES)}")

        self._ensure_loaded()
        entry = self._items.get(item_id)
        if not entry:
            return None

        for d in entry["deliverables"]:
            if d.id == deliverable_id:
                d.status = status
                d.verified_at = datetime.now(timezone.utc).isoformat()
                d.verified_by = verified_by
                if notes:
                    d.notes.append(notes)
                self._save_to_disk()
                return d

        return None

    def evaluate_against_output(
        self,
        item_id: str,
        output_text: str,
        evaluator_fn: Optional[Callable[[Deliverable, str], str]] = None,
    ) -> Dict[str, Any]:
        """Evaluate deliverables against task output.

        Parameters
        ----------
        item_id:
            The task/work item ID.
        output_text:
            The produced output (code, text, etc.) to evaluate against.
        evaluator_fn:
            Optional callback ``(deliverable, output) -> "pass"|"fail"|"skip"``.
            If not provided, deliverables stay in their current status.

        Returns a summary dict with pass/fail counts.
        """
        self._ensure_loaded()
        entry = self._items.get(item_id)
        if not entry:
            return {"error": f"No deliverables for item '{item_id}'"}

        results: Dict[str, str] = {}
        for d in entry["deliverables"]:
            if evaluator_fn:
                try:
                    new_status = evaluator_fn(d, output_text)
                    if new_status in VALID_STATUSES:
                        d.status = new_status
                        d.verified_at = datetime.now(timezone.utc).isoformat()
                except Exception as exc:
                    d.notes.append(f"Evaluation error: {exc}")
            results[d.id] = d.status

        self._save_to_disk()

        total = len(results)
        passed = sum(1 for s in results.values() if s == "pass")
        failed = sum(1 for s in results.values() if s == "fail")
        pending = sum(1 for s in results.values() if s == "pending")
        skipped = sum(1 for s in results.values() if s == "skip")

        return {
            "item_id": item_id,
            "total": total,
            "passed": passed,
            "failed": failed,
            "pending": pending,
            "skipped": skipped,
            "all_passed": failed == 0 and pending == 0,
            "results": results,
        }

    def generate_report(self, item_id: str) -> Dict[str, Any]:
        """Generate a structured report for review pipeline consumption.

        Returns a dict suitable for inclusion in review context or
        approval requests.
        """
        self._ensure_loaded()
        entry = self._items.get(item_id)
        if not entry:
            return {"error": f"No deliverables for item '{item_id}'"}

        deliverables = entry["deliverables"]
        total = len(deliverables)
        passed = sum(1 for d in deliverables if d.status == "pass")
        failed = sum(1 for d in deliverables if d.status == "fail")
        pending = sum(1 for d in deliverables if d.status == "pending")

        return {
            "item_id": item_id,
            "finalized": entry["finalized"],
            "finalized_at": entry["finalized_at"],
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "pending": pending,
            },
            "verdict": "PASS" if (failed == 0 and pending == 0) else "FAIL",
            "deliverables": [d.to_dict() for d in deliverables],
            "remaining_issues": [
                {"id": d.id, "description": d.description, "notes": d.notes}
                for d in deliverables
                if d.status in ("fail", "pending")
            ],
        }

    def remove(self, item_id: str) -> bool:
        """Remove all deliverables for an item.  Returns True if found."""
        self._ensure_loaded()
        if item_id in self._items:
            del self._items[item_id]
            self._save_to_disk()
            return True
        return False
