"""
Google Calendar integration for the BOSS Communications Service.

Provides a gated workflow for calendar event management:
- Agents create event drafts (stored as pending)
- Human approves/rejects drafts
- Approved events are created in Google Calendar
- Query operations bypass the gate (read-only)

Credentials are optional. If not configured, methods return
meaningful errors instead of crashing.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from models import CalendarEventCreate, CalendarEventDraft, CalendarEventStatus
from project_settings import ProjectSettingsManager

logger = logging.getLogger(__name__)

# Google API imports - optional, may not be installed
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError  # noqa: F401

    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False
    logger.warning(
        "[!] Google API libraries not installed. "
        "Calendar integration will operate in draft-only mode."
    )

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarManager:
    """Manages calendar event drafts and Google Calendar integration.

    Supports multi-project configurations with separate credentials per project.
    """

    def __init__(
        self,
        base_path: Path,
        credentials_path: Optional[Path] = None,
    ):
        self.base_path = Path(base_path)
        self.pending_dir = self.base_path / "data" / "comms_service" / "calendar_events" / "pending"
        self.created_dir = self.base_path / "data" / "comms_service" / "calendar_events" / "created"
        self.token_path = self.base_path / "data" / "comms_service" / "config" / "google_tokens.json"

        # Resolve credentials path: explicit arg > env var > None
        # This is the default/fallback credentials for backward compatibility
        if credentials_path is not None:
            self.credentials_path = Path(credentials_path)
        else:
            env_path = os.environ.get("GOOGLE_CREDENTIALS_PATH")
            self.credentials_path = Path(env_path) if env_path else None

        # Initialize project settings manager
        self.project_settings = ProjectSettingsManager(base_path)

        # Ensure storage directories exist
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.created_dir.mkdir(parents=True, exist_ok=True)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("[OK] CalendarManager initialized (base=%s)", self.base_path)

    # ------------------------------------------------------------------
    # Connection Status
    # ------------------------------------------------------------------

    def check_connection_status(self, project_id: Optional[str] = None) -> Dict:
        """Check Google Calendar connection status.

        Args:
            project_id: Optional project ID to check specific project connection.
                       If None, checks default/general project.

        Returns:
            Dict with connection status details
        """
        if project_id is None:
            project_id = "general"

        calendar_config = self.project_settings.get_calendar_config(project_id)
        if not calendar_config or not calendar_config.enabled:
            return {
                "connected": False,
                "status": "not_enabled",
                "message": f"Calendar not enabled for project: {project_id}",
                "calendar_name": None,
                "project_id": project_id,
            }

        if not GOOGLE_API_AVAILABLE:
            return {
                "connected": False,
                "status": "missing_libraries",
                "message": "Google API libraries not installed",
                "calendar_name": None,
                "project_id": project_id,
            }

        paths = self.project_settings.get_calendar_paths(project_id)
        if not paths or not paths["tokens"].exists():
            return {
                "connected": False,
                "status": "not_configured",
                "message": f"No token found - run setup_google_auth.py --project {project_id}",
                "calendar_name": None,
                "project_id": project_id,
            }

        try:
            creds = Credentials.from_authorized_user_file(str(paths["tokens"]), SCOPES)

            # Check if token is valid or needs refresh
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Save refreshed token
                paths["tokens"].write_text(creds.to_json(), encoding="utf-8")
                logger.info("[OK] Token refreshed automatically for project: %s", project_id)

            if creds and creds.valid:
                # Test connection by fetching calendar info
                service = build("calendar", "v3", credentials=creds)
                calendar = service.calendars().get(calendarId="primary").execute()
                calendar_name = calendar.get("summary", "Primary Calendar")

                return {
                    "connected": True,
                    "status": "connected",
                    "message": f"Connected to {calendar_name}",
                    "calendar_name": calendar_name,
                    "project_id": project_id,
                }
            else:
                return {
                    "connected": False,
                    "status": "invalid_token",
                    "message": f"Token is invalid - run setup_google_auth.py --project {project_id}",
                    "calendar_name": None,
                    "project_id": project_id,
                }

        except Exception as e:
            logger.warning("[!] Calendar connection check failed for project %s: %s", project_id, e)
            return {
                "connected": False,
                "status": "error",
                "message": f"Connection error: {str(e)}",
                "calendar_name": None,
                "project_id": project_id,
            }

    def check_all_projects_status(self) -> Dict[str, Dict]:
        """Check calendar connection status for all configured projects.

        Returns:
            Dict mapping project_id to status dict
        """
        statuses = {}
        for project in self.project_settings.list_projects():
            if project.calendar and project.calendar.enabled:
                statuses[project.project_id] = self.check_connection_status(project.project_id)
        return statuses

    # ------------------------------------------------------------------
    # Draft Management
    # ------------------------------------------------------------------

    def create_event_draft(self, event: CalendarEventCreate) -> CalendarEventDraft:
        """Create a new calendar event draft and save it to the pending directory."""
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow()

        # Detect project from metadata
        project_id = self.project_settings.detect_project_from_metadata(event.metadata)

        # Ensure project is in metadata for later retrieval
        metadata = event.metadata.copy()
        if "project" not in metadata:
            metadata["project"] = project_id

        draft = CalendarEventDraft(
            event_id=event_id,
            agent_id=event.agent_id,
            summary=event.summary,
            start=event.start,
            end=event.end,
            description=event.description,
            attendees=event.attendees,
            location=event.location,
            status=CalendarEventStatus.PENDING,
            metadata=metadata,
            created_at=now,
        )

        self._save_draft(draft, self.pending_dir)
        logger.info(
            "[OK] Event draft created: %s (%s) by agent '%s' for project '%s'",
            event_id, draft.summary, draft.agent_id, project_id,
        )
        return draft

    def list_event_drafts(
        self, status: Optional[CalendarEventStatus] = None
    ) -> List[CalendarEventDraft]:
        """List event drafts, optionally filtered by status."""
        drafts: List[CalendarEventDraft] = []

        # Determine which directories to scan
        if status is None:
            dirs = [self.pending_dir, self.created_dir]
        elif status in (CalendarEventStatus.PENDING, CalendarEventStatus.REJECTED):
            dirs = [self.pending_dir]
        elif status in (CalendarEventStatus.CREATED, CalendarEventStatus.APPROVED):
            dirs = [self.created_dir]
        else:
            dirs = [self.pending_dir, self.created_dir]

        for directory in dirs:
            if not directory.exists():
                continue
            for file_path in directory.glob("*.json"):
                try:
                    draft = self._load_draft(file_path)
                    if status is None or draft.status == status:
                        drafts.append(draft)
                except Exception as exc:
                    logger.warning(
                        "[!] Failed to load draft from %s: %s", file_path, exc
                    )

        # Sort by creation time, newest first
        drafts.sort(key=lambda d: d.created_at, reverse=True)
        return drafts

    def get_event_draft(self, event_id: str) -> Optional[CalendarEventDraft]:
        """Retrieve a specific event draft by ID."""
        for directory in [self.pending_dir, self.created_dir]:
            file_path = directory / f"{event_id}.json"
            if file_path.exists():
                try:
                    return self._load_draft(file_path)
                except Exception as exc:
                    logger.error(
                        "[X] Failed to load draft %s: %s", event_id, exc
                    )
                    return None
        return None

    def approve_event(self, event_id: str) -> Optional[CalendarEventDraft]:
        """
        Approve a pending event draft.

        If Google Calendar credentials are available, the event is created
        in Google Calendar. The draft is then moved to the created directory.

        Returns the updated draft, or None if the event was not found.
        """
        pending_file = self.pending_dir / f"{event_id}.json"
        if not pending_file.exists():
            logger.warning("[!] Event draft not found for approval: %s", event_id)
            return None

        draft = self._load_draft(pending_file)
        if draft.status != CalendarEventStatus.PENDING:
            logger.warning(
                "[!] Event %s is not pending (status=%s), cannot approve",
                event_id, draft.status,
            )
            return draft

        draft.approved_at = datetime.utcnow()

        # Attempt to create in Google Calendar
        google_event_id = self._create_google_event(draft)
        if google_event_id is not None:
            draft.google_event_id = google_event_id
            draft.status = CalendarEventStatus.CREATED
            logger.info(
                "[OK] Event %s created in Google Calendar (google_id=%s)",
                event_id, google_event_id,
            )
        else:
            # Mark as approved even if Google creation failed/unavailable
            draft.status = CalendarEventStatus.APPROVED
            logger.warning(
                "[!] Event %s approved but Google Calendar creation "
                "was skipped or failed. Draft marked as APPROVED.",
                event_id,
            )

        # Move from pending to created
        self._save_draft(draft, self.created_dir)
        self._delete_file(pending_file)

        return draft

    def reject_event(self, event_id: str) -> Optional[CalendarEventDraft]:
        """
        Reject a pending event draft and remove it from the pending directory.

        Returns the rejected draft, or None if the event was not found.
        """
        pending_file = self.pending_dir / f"{event_id}.json"
        if not pending_file.exists():
            logger.warning("[!] Event draft not found for rejection: %s", event_id)
            return None

        draft = self._load_draft(pending_file)
        draft.status = CalendarEventStatus.REJECTED
        self._delete_file(pending_file)

        logger.info("[OK] Event draft rejected and removed: %s", event_id)
        return draft

    def query_events(
        self, start: datetime, end: datetime, project_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Query events directly from Google Calendar (read-only, no gate).

        Args:
            start: Start datetime for query range
            end: End datetime for query range
            project_id: Optional project ID to query. If None, uses general project.

        Returns a list of event dicts from the Google Calendar API.
        If credentials are not configured, returns an empty list with
        a logged warning.
        """
        if project_id is None:
            project_id = "general"

        service = self._get_calendar_service(project_id)
        if service is None:
            logger.warning(
                "[!] Cannot query Google Calendar for project %s - service unavailable. "
                "Check credentials configuration.",
                project_id
            )
            return []

        try:
            time_min = start.isoformat() + "Z" if start.tzinfo is None else start.isoformat()
            time_max = end.isoformat() + "Z" if end.tzinfo is None else end.isoformat()

            events_result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = events_result.get("items", [])
            logger.info(
                "[OK] Queried %d events from Google Calendar for project %s (%s to %s)",
                len(events), project_id, time_min, time_max,
            )
            return events

        except Exception as exc:
            logger.error("[X] Failed to query Google Calendar for project %s: %s", project_id, exc)
            return []

    # ------------------------------------------------------------------
    # Google Calendar API
    # ------------------------------------------------------------------

    def _get_calendar_service(self, project_id: Optional[str] = None):
        """
        Build and return a Google Calendar API service object for a project.

        Args:
            project_id: Project ID for which to get calendar service.
                       If None, uses default/general project.

        Returns None if:
        - Google API libraries are not installed
        - No credentials path is configured
        - Authentication fails
        - Project not found or disabled
        """
        if not GOOGLE_API_AVAILABLE:
            logger.warning(
                "[!] Google API libraries not available. "
                "Install google-api-python-client, google-auth, "
                "google-auth-oauthlib to enable calendar integration."
            )
            return None

        if project_id is None:
            project_id = "general"

        # Get project-specific paths
        paths = self.project_settings.get_calendar_paths(project_id)
        if not paths:
            logger.warning(
                "[!] Calendar not configured for project: %s",
                project_id
            )
            return None

        credentials_path = paths["credentials"]
        token_path = paths["tokens"]

        if not credentials_path.exists():
            logger.warning(
                "[!] Google credentials not found for project %s. "
                "Path: %s. Run setup_google_auth.py --project %s",
                project_id, credentials_path, project_id
            )
            return None

        try:
            creds = None

            # Load existing token if available
            if token_path.exists():
                creds = Credentials.from_authorized_user_file(
                    str(token_path), SCOPES
                )

            # Refresh or create new credentials
            if creds is None or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(credentials_path), SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                # Save the token for future use
                with open(token_path, "w", encoding="utf-8") as token_file:
                    token_file.write(creds.to_json())
                logger.info("[OK] Google Calendar token saved for project %s: %s", project_id, token_path)

            service = build("calendar", "v3", credentials=creds)
            return service

        except Exception as exc:
            logger.error("[X] Failed to build Google Calendar service for project %s: %s", project_id, exc)
            return None

    def _create_google_event(self, draft: CalendarEventDraft) -> Optional[str]:
        """
        Create an event in Google Calendar from an approved draft.

        Uses project-specific credentials based on draft metadata.

        Returns the Google event ID on success, None on failure.
        """
        # Extract project from draft metadata
        project_id = draft.metadata.get("project", "general")

        service = self._get_calendar_service(project_id)
        if service is None:
            logger.warning(
                "[!] Cannot create calendar event for project %s - service unavailable",
                project_id
            )
            return None

        try:
            event_body = {
                "summary": draft.summary,
                "start": self._format_datetime(draft.start),
                "end": self._format_datetime(draft.end),
            }

            if draft.description:
                event_body["description"] = draft.description

            if draft.location:
                event_body["location"] = draft.location

            if draft.attendees:
                event_body["attendees"] = [
                    {"email": email} for email in draft.attendees
                ]

            created_event = (
                service.events()
                .insert(calendarId="primary", body=event_body)
                .execute()
            )

            google_event_id = created_event.get("id")
            logger.info(
                "[OK] Google Calendar event created for project %s: %s",
                project_id, google_event_id
            )
            return google_event_id

        except Exception as exc:
            logger.error(
                "[X] Failed to create Google Calendar event for draft %s (project %s): %s",
                draft.event_id, project_id, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_datetime(dt: datetime) -> Dict:
        """Format a datetime for the Google Calendar API."""
        if dt.tzinfo is None:
            # Naive datetime - treat as UTC
            return {"dateTime": dt.isoformat() + "Z", "timeZone": "UTC"}
        else:
            return {"dateTime": dt.isoformat()}

    @staticmethod
    def _save_draft(draft: CalendarEventDraft, directory: Path) -> None:
        """Save a draft to a JSON file in the given directory."""
        file_path = directory / f"{draft.event_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(
                draft.model_dump(mode="json"),
                f,
                indent=2,
                default=str,
            )

    @staticmethod
    def _load_draft(file_path: Path) -> CalendarEventDraft:
        """Load a draft from a JSON file."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CalendarEventDraft(**data)

    @staticmethod
    def _delete_file(file_path: Path) -> None:
        """Safely delete a file."""
        try:
            file_path.unlink()
        except OSError as exc:
            logger.error("[X] Failed to delete %s: %s", file_path, exc)
