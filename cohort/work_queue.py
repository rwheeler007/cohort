"""Sequential Work Queue for Cohort.

FIFO execution queue with priority ordering and single-active constraint.
File-backed JSON persistence. Thread-safe.

Status lifecycle::

    queued --> active --> reviewing --> approved --> completed
      |          |          |
      |          +--> failed |
      |                      +--> rejected
      +--> cancelled                  |
                                      +--> requeued (new item)

    stale_bounced: git staleness detected, bounced for re-evaluation

Storage: ``{data_dir}/work_queue.json``
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({
    "queued", "active", "reviewing", "approved", "rejected",
    "completed", "failed", "cancelled", "stale_bounced",
})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
MAX_REQUEUE_COUNT = 3
VALID_PRIORITIES = frozenset({"critical", "high", "medium", "low"})
PRIORITY_RANK: Dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
# WorkItem dataclass
# =====================================================================

@dataclass
class WorkItem:
    """A single item in the sequential work queue."""

    id: str
    description: str
    requester: str
    priority: str
    status: str
    created_at: str
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None
    agent_id: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    result: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Approval pipeline fields
    deliverables: List[Dict[str, Any]] = field(default_factory=list)
    review_results: Optional[List[Dict[str, Any]]] = None
    approval_id: Optional[str] = None
    requeue_count: int = 0
    requeued_from: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkItem:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


# =====================================================================
# WorkQueue
# =====================================================================

class WorkQueue:
    """Thread-safe, file-backed sequential work queue.

    Parameters
    ----------
    data_dir:
        Directory for persistence.  Queue is stored at
        ``{data_dir}/work_queue.json``.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "work_queue.json"
        self._lock = threading.Lock()
        self._items: Dict[str, WorkItem] = {}
        self._loaded = False

    # -- persistence ----------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        self._items = {}
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for item_data in raw.get("items", []):
                    item = WorkItem.from_dict(item_data)
                    self._items[item.id] = item
                logger.info(
                    "[OK] Work queue loaded: %d items from %s",
                    len(self._items), self._path,
                )
            except Exception as exc:
                logger.warning("[!] Work queue load error: %s", exc)
        self._loaded = True

    def _save_to_disk(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "last_updated": _now_iso(),
                "items": [item.to_dict() for item in self._items.values()],
            }
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[!] Work queue save error: %s", exc)

    # -- public API -----------------------------------------------------

    def enqueue(
        self,
        description: str,
        requester: str,
        priority: str = "medium",
        agent_id: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkItem:
        """Add an item to the queue.  Returns the created WorkItem."""
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority: {priority}")

        item = WorkItem(
            id=f"wq_{uuid.uuid4().hex[:8]}",
            description=description,
            requester=requester,
            priority=priority,
            status="queued",
            created_at=_now_iso(),
            agent_id=agent_id,
            depends_on=depends_on or [],
            metadata=metadata or {},
        )

        with self._lock:
            self._ensure_loaded()
            self._items[item.id] = item
            self._save_to_disk()

        logger.info("[+] Enqueued %s (%s) from %s", item.id, priority, requester)
        return item

    def claim_next(self) -> Dict[str, Any]:
        """Atomically claim the next queued item.

        Returns
        -------
        dict
            ``{"item": dict}`` on success,
            ``{"item": None}`` if queue is empty,
            ``{"error": str, "active_item": dict}`` if an item is already active.
        """
        with self._lock:
            self._ensure_loaded()

            # Single-active constraint
            active = self.get_active()
            if active is not None:
                return {
                    "error": "An item is already active",
                    "active_item": active.to_dict(),
                }

            # Find eligible items: queued + deps satisfied
            eligible = [
                item for item in self._items.values()
                if item.status == "queued" and self._deps_satisfied(item)
            ]
            if not eligible:
                return {"item": None}

            # Sort by priority rank then created_at (FIFO within priority)
            eligible.sort(
                key=lambda i: (PRIORITY_RANK.get(i.priority, 3), i.created_at),
            )
            item = eligible[0]

            # Claim it
            item.status = "active"
            item.claimed_at = _now_iso()
            self._save_to_disk()

        logger.info("[>>] Claimed %s (%s)", item.id, item.priority)
        return {"item": item.to_dict()}

    def complete(
        self, item_id: str, result: Optional[str] = None,
    ) -> Optional[WorkItem]:
        """Mark an active item as completed."""
        return self._transition(item_id, "completed", result=result)

    def fail(
        self, item_id: str, reason: Optional[str] = None,
    ) -> Optional[WorkItem]:
        """Mark an active item as failed."""
        return self._transition(item_id, "failed", result=reason)

    def cancel(self, item_id: str) -> Optional[WorkItem]:
        """Cancel a queued or active item."""
        return self._transition(item_id, "cancelled")

    def get_item(self, item_id: str) -> Optional[WorkItem]:
        """Get a single item by ID."""
        with self._lock:
            self._ensure_loaded()
            return self._items.get(item_id)

    def list_items(self, status: Optional[str] = None) -> List[WorkItem]:
        """Return items sorted by (priority_rank, created_at).

        Optionally filtered by status.
        """
        with self._lock:
            self._ensure_loaded()
            items = list(self._items.values())

        if status:
            items = [i for i in items if i.status == status]

        items.sort(
            key=lambda i: (PRIORITY_RANK.get(i.priority, 3), i.created_at),
        )
        return items

    def get_active(self) -> Optional[WorkItem]:
        """Return the currently active item, or None."""
        # Called under lock from claim_next, but also callable standalone
        if not self._loaded:
            self._ensure_loaded()
        for item in self._items.values():
            if item.status == "active":
                return item
        return None

    # -- approval pipeline ----------------------------------------------

    def submit_for_review(self, item_id: str) -> Optional[WorkItem]:
        """Transition an active item to reviewing status."""
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(item_id)
            if item is None or item.status != "active":
                return None
            item.status = "reviewing"
            self._save_to_disk()
        logger.info("[*] %s -> reviewing", item_id)
        return item

    def approve(
        self,
        item_id: str,
        approved_by: str = "",
        notes: str = "",
    ) -> Optional[WorkItem]:
        """Approve a reviewing item."""
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(item_id)
            if item is None or item.status != "reviewing":
                return None
            item.status = "approved"
            item.completed_at = _now_iso()
            if notes:
                item.metadata.setdefault("review_notes", []).append({
                    "by": approved_by, "notes": notes, "verdict": "approved",
                    "at": _now_iso(),
                })
            self._save_to_disk()
        logger.info("[OK] %s approved by %s", item_id, approved_by)
        return item

    def reject(
        self,
        item_id: str,
        rejected_by: str = "",
        reason: str = "",
    ) -> Optional[WorkItem]:
        """Reject a reviewing item."""
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(item_id)
            if item is None or item.status != "reviewing":
                return None
            item.status = "rejected"
            item.completed_at = _now_iso()
            if reason:
                item.metadata.setdefault("review_notes", []).append({
                    "by": rejected_by, "notes": reason, "verdict": "rejected",
                    "at": _now_iso(),
                })
            self._save_to_disk()
        logger.info("[X] %s rejected by %s", item_id, rejected_by)
        return item

    def attach_reviews(
        self, item_id: str, reviews: List[Dict[str, Any]],
    ) -> Optional[WorkItem]:
        """Attach review pipeline results to an item."""
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(item_id)
            if item is None:
                return None
            item.review_results = reviews
            self._save_to_disk()
        return item

    def requeue(
        self,
        item_id: str,
        feedback: str = "",
    ) -> Optional[WorkItem]:
        """Requeue a rejected or stale_bounced item as a new queued item.

        Returns the *new* queued item, or ``None`` if requeue limit hit
        or item not in a requeuable state.
        """
        with self._lock:
            self._ensure_loaded()
            old = self._items.get(item_id)
            if old is None or old.status not in ("rejected", "stale_bounced", "failed"):
                return None

            if old.requeue_count >= MAX_REQUEUE_COUNT:
                logger.warning(
                    "[!] %s already requeued %d times (max %d)",
                    item_id, old.requeue_count, MAX_REQUEUE_COUNT,
                )
                return None

            new_item = WorkItem(
                id=f"wq_{uuid.uuid4().hex[:8]}",
                description=old.description,
                requester=old.requester,
                priority=old.priority,
                status="queued",
                created_at=_now_iso(),
                agent_id=old.agent_id,
                depends_on=old.depends_on,
                metadata={
                    **old.metadata,
                    "requeue_feedback": feedback,
                },
                deliverables=old.deliverables,
                requeue_count=old.requeue_count + 1,
                requeued_from=old.id,
            )
            self._items[new_item.id] = new_item
            self._save_to_disk()

        logger.info(
            "[+] Requeued %s -> %s (count %d)",
            item_id, new_item.id, new_item.requeue_count,
        )
        return new_item

    def stale_bounce(self, item_id: str, reason: str = "") -> Optional[WorkItem]:
        """Mark an item as stale_bounced (git staleness detected)."""
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(item_id)
            if item is None or item.status in TERMINAL_STATUSES:
                return None
            item.status = "stale_bounced"
            item.completed_at = _now_iso()
            if reason:
                item.metadata["stale_reason"] = reason
            self._save_to_disk()
        logger.info("[!] %s stale-bounced: %s", item_id, reason)
        return item

    # -- internal -------------------------------------------------------

    def _deps_satisfied(self, item: WorkItem) -> bool:
        """Check whether all depends_on items are in a terminal state."""
        if not item.depends_on:
            return True
        for dep_id in item.depends_on:
            dep = self._items.get(dep_id)
            if dep is None:
                # Unknown dep -- treat as satisfied (don't deadlock)
                continue
            if dep.status not in TERMINAL_STATUSES:
                return False
        return True

    def _transition(
        self,
        item_id: str,
        target_status: str,
        result: Optional[str] = None,
    ) -> Optional[WorkItem]:
        """Transition an item to a new status."""
        with self._lock:
            self._ensure_loaded()
            item = self._items.get(item_id)
            if item is None:
                return None

            # Validate transition
            if target_status == "cancelled":
                if item.status in TERMINAL_STATUSES:
                    return None  # Already terminal
            elif target_status == "completed" and item.status == "approved":
                pass  # approved -> completed is valid
            elif item.status != "active":
                return None  # Can only complete/fail active items

            item.status = target_status
            if target_status in TERMINAL_STATUSES:
                item.completed_at = _now_iso()
            if result is not None:
                item.result = result
            self._save_to_disk()

        logger.info("[*] %s -> %s", item_id, target_status)
        return item
