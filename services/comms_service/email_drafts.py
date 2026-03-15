"""
Email Draft Queue Manager for the BOSS Communications Service.

Manages the lifecycle of email drafts: create, approve/reject, send via Resend API.
Each draft is stored as a JSON file in a status-based subdirectory structure.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

try:
    import resend
except ImportError:
    resend = None

from models import (
    DraftStatsResponse,
    DraftStatus,
    EmailDraft,
    EmailDraftCreate,
    EmailDraftUpdate,
)

logger = logging.getLogger(__name__)

# Status directories that correspond to DraftStatus values
STATUS_DIRS = [
    DraftStatus.PENDING,
    DraftStatus.APPROVED,
    DraftStatus.REJECTED,
    DraftStatus.FAILED,
]

# Sent drafts live inside the approved/ directory with status=SENT in their JSON.
# There is no separate "sent/" directory on disk -- sent files stay in approved/.


class EmailDraftManager:
    """Manages email draft storage, approval workflow, and sending via Resend."""

    def __init__(self, base_path: Path) -> None:
        self.base_path = Path(base_path)
        self._ensure_dirs()

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        """Create status subdirectories if they do not exist."""
        for status in STATUS_DIRS:
            (self.base_path / status.value).mkdir(parents=True, exist_ok=True)

    def _status_dir(self, status: DraftStatus) -> Path:
        """Return the directory path for a given status.

        SENT drafts are stored inside the approved/ directory.
        """
        if status == DraftStatus.SENT:
            return self.base_path / DraftStatus.APPROVED.value
        return self.base_path / status.value

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _draft_path(self, status: DraftStatus, draft_id: str) -> Path:
        return self._status_dir(status) / f"{draft_id}.json"

    def _save_draft(self, draft: EmailDraft) -> None:
        path = self._draft_path(draft.status, draft.draft_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            draft.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _load_draft(self, path: Path) -> Optional[EmailDraft]:
        try:
            data = path.read_text(encoding="utf-8")
            return EmailDraft.model_validate_json(data)
        except Exception as exc:
            logger.warning("[!] Failed to load draft %s: %s", path, exc)
            return None

    def _find_draft_path(self, draft_id: str) -> Optional[Path]:
        """Search all status directories for a draft by ID."""
        filename = f"{draft_id}.json"
        for status in STATUS_DIRS:
            candidate = self._status_dir(status) / filename
            if candidate.exists():
                return candidate
        # Also check approved/ for sent drafts (same dir, but be explicit)
        candidate = self._status_dir(DraftStatus.SENT) / filename
        if candidate.exists():
            return candidate
        return None

    def _move_draft(self, draft: EmailDraft, old_status: DraftStatus) -> None:
        """Move a draft file from the old status dir to the new one."""
        old_path = self._draft_path(old_status, draft.draft_id)
        new_path = self._draft_path(draft.status, draft.draft_id)
        if old_path == new_path:
            # Same directory -- just overwrite in place
            self._save_draft(draft)
            return
        # Write to new location first, then remove old
        self._save_draft(draft)
        if old_path.exists():
            old_path.unlink()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_draft(self, draft: EmailDraftCreate) -> EmailDraft:
        """Create a new email draft and save it to pending/."""
        draft_id = str(uuid.uuid4())
        email_draft = EmailDraft(
            draft_id=draft_id,
            agent_id=draft.agent_id,
            to=draft.to,
            cc=draft.cc,
            subject=draft.subject,
            body_text=draft.body_text,
            body_html=draft.body_html,
            status=DraftStatus.PENDING,
            priority=draft.priority,
            campaign_id=draft.campaign_id,
            template_ref=draft.template_ref,
            metadata=draft.metadata,
            created_at=datetime.now(timezone.utc),
        )
        self._save_draft(email_draft)
        logger.info("[OK] Draft created: %s (agent=%s)", draft_id, draft.agent_id)
        return email_draft

    def list_drafts(
        self,
        status: Optional[DraftStatus] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[EmailDraft]:
        """List drafts, optionally filtered by status and/or agent_id."""
        drafts: List[EmailDraft] = []

        # Determine which directories to scan
        if status is not None:
            dirs_to_scan = [self._status_dir(status)]
        else:
            # Scan all status directories (approved/ covers both approved + sent)
            dirs_to_scan = list({self._status_dir(s) for s in STATUS_DIRS})

        for dir_path in dirs_to_scan:
            if not dir_path.is_dir():
                continue
            for file_path in dir_path.glob("*.json"):
                d = self._load_draft(file_path)
                if d is None:
                    continue
                # Filter by requested status if set
                if status is not None and d.status != status:
                    continue
                if agent_id is not None and d.agent_id != agent_id:
                    continue
                drafts.append(d)

        # Sort newest first
        drafts.sort(key=lambda d: d.created_at, reverse=True)
        return drafts[:limit]

    def get_draft(self, draft_id: str) -> Optional[EmailDraft]:
        """Find and return a draft by ID across all status directories."""
        path = self._find_draft_path(draft_id)
        if path is None:
            logger.warning("[!] Draft not found: %s", draft_id)
            return None
        return self._load_draft(path)

    def update_draft(
        self, draft_id: str, update: EmailDraftUpdate
    ) -> Optional[EmailDraft]:
        """Update a pending draft's editable fields.

        Only drafts in PENDING status can be updated.
        """
        draft = self.get_draft(draft_id)
        if draft is None:
            return None

        if draft.status != DraftStatus.PENDING:
            logger.warning(
                "[!] Cannot update draft %s -- status is %s (must be pending)",
                draft_id,
                draft.status.value,
            )
            return None

        update_data = update.model_dump(exclude_unset=True)
        if not update_data:
            return draft

        for field, value in update_data.items():
            setattr(draft, field, value)

        self._save_draft(draft)
        logger.info("[OK] Draft updated: %s", draft_id)
        return draft

    def approve_draft(
        self, draft_id: str, approved_by: str = "human"
    ) -> Optional[EmailDraft]:
        """Approve a pending draft, move it to approved/, and trigger send."""
        draft = self.get_draft(draft_id)
        if draft is None:
            return None

        if draft.status != DraftStatus.PENDING:
            logger.warning(
                "[!] Cannot approve draft %s -- status is %s",
                draft_id,
                draft.status.value,
            )
            return None

        old_status = draft.status
        draft.status = DraftStatus.APPROVED
        draft.approved_at = datetime.now(timezone.utc)
        draft.approved_by = approved_by

        self._move_draft(draft, old_status)
        logger.info("[OK] Draft approved: %s by %s", draft_id, approved_by)

        # Trigger send automatically after approval
        self.send_approved(draft_id)

        return self.get_draft(draft_id)

    def reject_draft(
        self, draft_id: str, reason: Optional[str] = None
    ) -> Optional[EmailDraft]:
        """Reject a pending draft and move it to rejected/."""
        draft = self.get_draft(draft_id)
        if draft is None:
            return None

        if draft.status != DraftStatus.PENDING:
            logger.warning(
                "[!] Cannot reject draft %s -- status is %s",
                draft_id,
                draft.status.value,
            )
            return None

        old_status = draft.status
        draft.status = DraftStatus.REJECTED
        draft.rejected_at = datetime.now(timezone.utc)
        draft.reject_reason = reason

        self._move_draft(draft, old_status)
        logger.info("[OK] Draft rejected: %s (reason: %s)", draft_id, reason)
        return draft

    def send_approved(self, draft_id: str) -> bool:
        """Send an approved draft via the Resend API.

        On success the draft status becomes SENT (file stays in approved/).
        On failure it is moved to failed/.
        """
        draft = self.get_draft(draft_id)
        if draft is None:
            return False

        if draft.status != DraftStatus.APPROVED:
            logger.warning(
                "[!] Cannot send draft %s -- status is %s (must be approved)",
                draft_id,
                draft.status.value,
            )
            return False

        api_key = os.environ.get("RESEND_API_KEY")
        if not api_key:
            logger.error("[X] RESEND_API_KEY environment variable not set")
            self._mark_failed(draft, "RESEND_API_KEY not configured")
            return False

        if resend is None:
            logger.error("[X] resend package is not installed")
            self._mark_failed(draft, "resend package not installed")
            return False

        from_address = os.environ.get(
            "RESEND_FROM_ADDRESS", "BOSS <boss@yourdomain.com>"
        )

        try:
            resend.api_key = api_key

            params = {
                "from_": from_address,
                "to": list(draft.to),
                "subject": draft.subject,
                "text": draft.body_text,
            }

            if draft.cc:
                params["cc"] = list(draft.cc)
            if draft.body_html:
                params["html"] = draft.body_html

            resend.Emails.send(params)

            # Mark as sent -- stays in approved/ directory
            old_status = draft.status
            draft.status = DraftStatus.SENT
            draft.sent_at = datetime.now(timezone.utc)
            self._move_draft(draft, old_status)
            logger.info("[OK] Email sent for draft: %s", draft_id)
            return True

        except Exception as exc:
            error_msg = str(exc)
            logger.error("[X] Failed to send draft %s: %s", draft_id, error_msg)
            self._mark_failed(draft, error_msg)
            return False

    def delete_draft(self, draft_id: str) -> bool:
        """Delete a draft file from any status directory."""
        path = self._find_draft_path(draft_id)
        if path is None:
            logger.warning("[!] Draft not found for deletion: %s", draft_id)
            return False

        path.unlink()
        logger.info("[OK] Draft deleted: %s", draft_id)
        return True

    def get_stats(self) -> DraftStatsResponse:
        """Return counts of drafts in each status, plus sent-today count."""
        counts = {
            "pending": 0,
            "approved": 0,
            "sent": 0,
            "rejected": 0,
            "failed": 0,
            "sent_today": 0,
        }

        today = datetime.now(timezone.utc).date()

        for status in STATUS_DIRS:
            dir_path = self._status_dir(status)
            if not dir_path.is_dir():
                continue
            for file_path in dir_path.glob("*.json"):
                draft = self._load_draft(file_path)
                if draft is None:
                    continue
                counts[draft.status.value] = counts.get(draft.status.value, 0) + 1
                # Count sent today
                if draft.status == DraftStatus.SENT and draft.sent_at is not None:
                    sent_date = draft.sent_at
                    if hasattr(sent_date, "date"):
                        sent_date = sent_date.date()
                    if sent_date == today:
                        counts["sent_today"] += 1

        return DraftStatsResponse(**counts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mark_failed(self, draft: EmailDraft, error_msg: str) -> None:
        """Move a draft to failed/ with the given error message."""
        old_status = draft.status
        draft.status = DraftStatus.FAILED
        draft.send_error = error_msg
        self._move_draft(draft, old_status)
