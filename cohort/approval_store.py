"""File-backed Approval Store for Cohort.

Multi-stakeholder approval requests with rate limiting, timeout enforcement,
self-approval prevention, and append-only audit trail.

Extracted and generalized from BOSS/SMACK's approval pipeline.

Status lifecycle::

    pending --> approved
       |           |
       +--> denied
       |
       +--> expired  (timeout)
       |
       +--> cancelled (requester abort)

Storage::

    {data_dir}/approvals.json         -- approval requests (write-through)
    {data_dir}/approval_audit.jsonl   -- append-only audit trail
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# =====================================================================
# Constants
# =====================================================================

VALID_ACTION_TYPES = frozenset({
    "code_change", "deploy", "config_change", "api_call",
    "file_write", "git_push", "custom",
})

VALID_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})

VALID_STATUSES = frozenset({
    "pending", "approved", "denied", "expired", "cancelled",
})

# Timeout defaults per risk level (seconds)
TIMEOUT_DEFAULTS: Dict[str, int] = {
    "low": 600, "medium": 300, "high": 120, "critical": 60,
}
TIMEOUT_MIN: Dict[str, int] = {
    "low": 60, "medium": 60, "high": 30, "critical": 30,
}
TIMEOUT_MAX: Dict[str, int] = {
    "low": 900, "medium": 600, "high": 300, "critical": 120,
}

# Rate limits
MAX_PENDING_PER_REQUESTER = 10
MAX_PENDING_TOTAL = 100
MAX_REQUESTS_PER_REQUESTER_PER_MINUTE = 5

# Validation limits
MAX_DESCRIPTION_LENGTH = 500
MAX_DETAILS_SIZE_BYTES = 10240  # 10 KB
MAX_DETAILS_DEPTH = 3

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


# =====================================================================
# Data model
# =====================================================================

@dataclass
class ApprovalRequest:
    """A single approval request with full lifecycle tracking."""

    id: str
    item_id: str
    item_type: str  # "work_item" | "task"
    requester: str
    action_type: str
    risk_level: str
    description: str
    details: Dict[str, Any]
    status: str  # pending | approved | denied | expired | cancelled
    created_at: str
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    timeout_seconds: int = 300
    reviewer_role: Optional[str] = None
    audit_notes: str = ""

    def is_expired(self) -> bool:
        if self.status != "pending":
            return False
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - created).total_seconds()
        return elapsed > self.timeout_seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ApprovalRequest:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


# =====================================================================
# Validation helpers
# =====================================================================

def _check_json_depth(obj: Any, max_depth: int, current: int = 0) -> bool:
    """Return True if *obj* nesting exceeds *max_depth*."""
    if current > max_depth:
        return True
    if isinstance(obj, dict):
        return any(_check_json_depth(v, max_depth, current + 1) for v in obj.values())
    if isinstance(obj, list):
        return any(_check_json_depth(v, max_depth, current + 1) for v in obj)
    return False


def _sanitize_description(text: str) -> str:
    """Strip HTML tags and control characters."""
    text = _HTML_TAG_RE.sub("", text)
    text = _CONTROL_CHAR_RE.sub("", text)
    return text


def _clamp_timeout(risk_level: str, requested: Optional[int]) -> int:
    """Clamp timeout to min/max bounds for the given risk level."""
    default = TIMEOUT_DEFAULTS.get(risk_level, 300)
    if requested is None:
        return default
    lo = TIMEOUT_MIN.get(risk_level, 30)
    hi = TIMEOUT_MAX.get(risk_level, 900)
    return max(lo, min(hi, requested))


def validate_approval_input(
    action_type: str,
    risk_level: str,
    description: str,
    details: Dict[str, Any],
) -> Optional[str]:
    """Validate input fields.  Returns error message or ``None`` if valid."""
    if action_type not in VALID_ACTION_TYPES:
        return f"Invalid action_type '{action_type}'. Valid: {sorted(VALID_ACTION_TYPES)}"

    if risk_level not in VALID_RISK_LEVELS:
        return f"Invalid risk_level '{risk_level}'. Valid: {sorted(VALID_RISK_LEVELS)}"

    clean = _sanitize_description(description)
    if len(clean) > MAX_DESCRIPTION_LENGTH:
        return f"description exceeds {MAX_DESCRIPTION_LENGTH} chars (got {len(clean)})"

    details_json = json.dumps(details, default=str)
    if len(details_json.encode("utf-8")) > MAX_DETAILS_SIZE_BYTES:
        return f"details exceeds {MAX_DETAILS_SIZE_BYTES} bytes"

    if _check_json_depth(details, MAX_DETAILS_DEPTH):
        return f"details nesting exceeds max depth of {MAX_DETAILS_DEPTH}"

    return None


# =====================================================================
# ApprovalStore
# =====================================================================

class ApprovalStore:
    """Thread-safe, file-backed approval request store.

    Parameters
    ----------
    data_dir:
        Directory for persistence.  Approvals stored at
        ``{data_dir}/approvals.json``, audit trail at
        ``{data_dir}/approval_audit.jsonl``.
    """

    def __init__(self, data_dir: Path) -> None:
        self._approvals_path = data_dir / "approvals.json"
        self._audit_path = data_dir / "approval_audit.jsonl"
        self._lock = threading.Lock()
        self._approvals: Dict[str, ApprovalRequest] = {}
        self._loaded = False
        self._request_timestamps: Dict[str, List[float]] = {}

    # -- persistence ----------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        self._approvals = {}
        if self._approvals_path.exists():
            try:
                raw = json.loads(self._approvals_path.read_text(encoding="utf-8"))
                for entry in raw.get("approvals", {}).values():
                    req = ApprovalRequest.from_dict(entry)
                    self._approvals[req.id] = req
                logger.info(
                    "[OK] Loaded %d approvals from %s",
                    len(self._approvals), self._approvals_path,
                )
            except Exception as exc:
                logger.warning("[!] Approval store load error: %s", exc)
        self._loaded = True

    def _save_to_disk(self) -> None:
        try:
            self._approvals_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "approvals": {k: v.to_dict() for k, v in self._approvals.items()},
            }
            self._approvals_path.write_text(
                json.dumps(data, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[!] Approval store save error: %s", exc)

    def _append_audit(
        self,
        event_type: str,
        approval: ApprovalRequest,
        resolved_by: Optional[str] = None,
    ) -> None:
        """Append one event to the JSONL audit trail (never truncated)."""
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "approval_id": approval.id,
            "item_id": approval.item_id,
            "item_type": approval.item_type,
            "requester": approval.requester,
            "action_type": approval.action_type,
            "risk_level": approval.risk_level,
            "description": approval.description[:120],
            "resolved_by": resolved_by or "",
        }
        try:
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:
            logger.warning("[!] Audit log write error: %s", exc)

    # -- rate limiting --------------------------------------------------

    def _check_rate_limit(self, requester: str) -> Optional[str]:
        """Return error message if rate-limited, else ``None``."""
        # Per-requester pending count
        agent_pending = sum(
            1 for a in self._approvals.values()
            if a.requester == requester and a.status == "pending"
        )
        if agent_pending >= MAX_PENDING_PER_REQUESTER:
            return (
                f"Requester '{requester}' has {agent_pending} pending approvals "
                f"(max {MAX_PENDING_PER_REQUESTER})"
            )

        # System-wide pending count
        total_pending = sum(1 for a in self._approvals.values() if a.status == "pending")
        if total_pending >= MAX_PENDING_TOTAL:
            return f"System has {total_pending} pending approvals (max {MAX_PENDING_TOTAL})"

        # Burst rate: max N requests per minute per requester
        now = time.time()
        stamps = [t for t in self._request_timestamps.get(requester, []) if now - t < 60]
        self._request_timestamps[requester] = stamps

        if len(stamps) >= MAX_REQUESTS_PER_REQUESTER_PER_MINUTE:
            return (
                f"Requester '{requester}' exceeded "
                f"{MAX_REQUESTS_PER_REQUESTER_PER_MINUTE} requests/minute"
            )

        return None

    # -- public API -----------------------------------------------------

    def create(
        self,
        item_id: str,
        item_type: str,
        requester: str,
        action_type: str,
        risk_level: str,
        description: str,
        details: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        reviewer_role: Optional[str] = None,
    ) -> ApprovalRequest:
        """Create a new approval request.

        Raises ``ValueError`` on validation failure or rate limit exceeded.
        """
        if item_type not in ("work_item", "task"):
            raise ValueError(f"item_type must be 'work_item' or 'task', got '{item_type}'")

        details = details or {}
        error = validate_approval_input(action_type, risk_level, description, details)
        if error:
            raise ValueError(error)

        with self._lock:
            self._ensure_loaded()

            rate_error = self._check_rate_limit(requester)
            if rate_error:
                raise ValueError(rate_error)

            approval = ApprovalRequest(
                id=f"apr_{uuid.uuid4().hex[:8]}",
                item_id=item_id,
                item_type=item_type,
                requester=requester,
                action_type=action_type,
                risk_level=risk_level,
                description=_sanitize_description(description),
                details=details,
                status="pending",
                created_at=datetime.now(timezone.utc).isoformat(),
                timeout_seconds=_clamp_timeout(risk_level, timeout),
                reviewer_role=reviewer_role,
            )

            self._approvals[approval.id] = approval
            self._save_to_disk()
            self._request_timestamps.setdefault(requester, []).append(time.time())
            self._append_audit("approval_created", approval)

        logger.info(
            "[+] Approval %s created: %s/%s by %s for %s %s",
            approval.id, action_type, risk_level, requester, item_type, item_id,
        )
        return approval

    def resolve(
        self,
        approval_id: str,
        action: str,
        resolved_by: str,
        audit_notes: str = "",
    ) -> Dict[str, Any]:
        """Approve or deny a pending request.

        Args:
            action: ``'approve'`` or ``'deny'``
            resolved_by: who resolved (must not be the requester)

        Returns dict with ``'status'`` on success, ``'error'`` on failure.
        """
        if action not in ("approve", "deny"):
            return {"error": f"action must be 'approve' or 'deny', got '{action}'"}

        with self._lock:
            self._ensure_loaded()

            approval = self._approvals.get(approval_id)
            if not approval:
                return {"error": f"Approval '{approval_id}' not found"}

            if approval.status != "pending":
                return {
                    "error": "Approval already resolved",
                    "status": approval.status,
                    "resolved_by": approval.resolved_by,
                }

            # Self-approval prevention
            if resolved_by == approval.requester:
                return {"error": "Requester cannot approve their own request"}

            # Check expiry
            if approval.is_expired():
                approval.status = "expired"
                approval.resolved_at = datetime.now(timezone.utc).isoformat()
                self._save_to_disk()
                self._append_audit("approval_expired", approval)
                return {"error": "Approval has expired", "status": "expired"}

            # Resolve
            new_status = "approved" if action == "approve" else "denied"
            approval.status = new_status
            approval.resolved_by = resolved_by
            approval.resolved_at = datetime.now(timezone.utc).isoformat()
            approval.audit_notes = audit_notes
            self._save_to_disk()
            self._append_audit(f"approval_{new_status}", approval, resolved_by)

        logger.info("[*] Approval %s %s by %s", approval_id, new_status, resolved_by)
        return {
            "status": approval.status,
            "approval_id": approval.id,
            "resolved_by": approval.resolved_by,
            "resolved_at": approval.resolved_at,
        }

    def cancel(
        self,
        approval_id: str,
        cancelled_by: str,
    ) -> Dict[str, Any]:
        """Cancel a pending approval.  Only requester or ``'human'`` can cancel."""
        with self._lock:
            self._ensure_loaded()

            approval = self._approvals.get(approval_id)
            if not approval:
                return {"error": f"Approval '{approval_id}' not found"}

            if approval.status != "pending":
                return {"error": "Only pending approvals can be cancelled", "status": approval.status}

            if cancelled_by != approval.requester and cancelled_by != "human":
                return {"error": "Only the requester or a human can cancel"}

            approval.status = "cancelled"
            approval.resolved_at = datetime.now(timezone.utc).isoformat()
            approval.resolved_by = cancelled_by
            self._save_to_disk()
            self._append_audit("approval_cancelled", approval, cancelled_by)

        return {"status": "cancelled", "approval_id": approval.id}

    def get(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Get a single approval by ID."""
        with self._lock:
            self._ensure_loaded()
            return self._approvals.get(approval_id)

    def get_pending(self, reviewer_role: Optional[str] = None) -> List[ApprovalRequest]:
        """Get all pending approvals, sorted by creation time.

        Optionally filtered by *reviewer_role*.
        """
        with self._lock:
            self._ensure_loaded()
            pending = [a for a in self._approvals.values() if a.status == "pending"]
            if reviewer_role:
                pending = [a for a in pending if a.reviewer_role == reviewer_role]
            return sorted(pending, key=lambda a: a.created_at)

    def get_pending_count(self) -> int:
        """Count of pending approvals (for badges)."""
        with self._lock:
            self._ensure_loaded()
            return sum(1 for a in self._approvals.values() if a.status == "pending")

    def list_all(
        self,
        status: Optional[str] = None,
        item_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[ApprovalRequest]:
        """List approvals, most recent first.  Filter by status/item_type."""
        with self._lock:
            self._ensure_loaded()
            result = list(self._approvals.values())

        if status:
            result = [a for a in result if a.status == status]
        if item_type:
            result = [a for a in result if a.item_type == item_type]

        result.sort(key=lambda a: a.created_at, reverse=True)
        return result[:limit]

    def expire_stale(self) -> List[ApprovalRequest]:
        """Expire timed-out approvals.  Returns newly expired list."""
        with self._lock:
            self._ensure_loaded()
            newly_expired: List[ApprovalRequest] = []

            for approval in self._approvals.values():
                if approval.status == "pending" and approval.is_expired():
                    approval.status = "expired"
                    approval.resolved_at = datetime.now(timezone.utc).isoformat()
                    self._append_audit("approval_expired", approval)
                    newly_expired.append(approval)

            if newly_expired:
                self._save_to_disk()
                logger.info("[!] Expired %d stale approvals", len(newly_expired))

            return newly_expired
