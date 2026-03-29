"""
BOSS Communications Service - Agent Client Library

Agent-facing client for the BOSS Comms Service. Agents import this module
to draft emails, schedule calendar events, and send notifications without
knowing about Resend, Google APIs, or webhook URLs.

Usage:
    from tools.comms_service.comms_client import get_client

    comms = get_client("marketing_agent")
    comms.draft_email(
        to=["partner@example.com"],
        subject="Partnership Follow-up",
        body_text="Hello, ..."
    )
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import httpx

logger = logging.getLogger(__name__)


class CommsClient:
    """HTTP client for the BOSS Communications Service (FastAPI at port 8001)."""

    def __init__(self, agent_id: str, base_url: str = "http://localhost:8001"):
        self.agent_id = agent_id
        self.base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(30.0, connect=10.0)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Send an HTTP request and return the parsed JSON response.

        Returns None on connection / timeout errors instead of raising,
        so agents never crash from a comms-service outage.
        """
        url = f"{self.base_url}{path}"
        # Strip None values from params so httpx doesn't send "param=None"
        if params:
            params = {k: v for k, v in params.items() if v is not None}

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(
                    method,
                    url,
                    json=json,
                    params=params or None,
                )
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError:
            logger.warning(
                "[!] CommsClient: cannot connect to %s - is the service running?",
                self.base_url,
            )
            return None
        except httpx.TimeoutException:
            logger.warning(
                "[!] CommsClient: request to %s timed out", url,
            )
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "[!] CommsClient: HTTP %s from %s - %s",
                exc.response.status_code,
                url,
                exc.response.text[:500],
            )
            return None
        except Exception as exc:
            logger.warning(
                "[!] CommsClient: unexpected error for %s %s - %s",
                method,
                url,
                exc,
            )
            return None

    @staticmethod
    def _to_iso(value: Union[datetime, str, None]) -> Optional[str]:
        """Convert a datetime or ISO string to an ISO-format string."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    # ------------------------------------------------------------------ #
    #  Email Drafts                                                        #
    # ------------------------------------------------------------------ #

    def draft_email(
        self,
        to: Union[str, List[str]],
        subject: str,
        body_text: str,
        cc: Optional[Union[str, List[str]]] = None,
        body_html: Optional[str] = None,
        priority: str = "normal",
        campaign_id: Optional[str] = None,
        template_ref: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create an email draft in the comms service.

        Args:
            to: Recipient email address(es).
            subject: Email subject line.
            body_text: Plain-text body.
            cc: CC recipients (optional).
            body_html: HTML body (optional).
            priority: One of low, normal, high, urgent.
            campaign_id: Optional campaign identifier for tracking.
            template_ref: Optional template reference.
            metadata: Arbitrary key-value metadata dict.

        Returns:
            The created draft as a dict, or None on failure.
        """
        # Normalise single addresses to lists
        if isinstance(to, str):
            to = [to]
        if isinstance(cc, str):
            cc = [cc]

        payload: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "to": to,
            "subject": subject,
            "body_text": body_text,
            "priority": priority,
        }
        if cc is not None:
            payload["cc"] = cc
        if body_html is not None:
            payload["body_html"] = body_html
        if campaign_id is not None:
            payload["campaign_id"] = campaign_id
        if template_ref is not None:
            payload["template_ref"] = template_ref
        if metadata is not None:
            payload["metadata"] = metadata

        return self._request("POST", "/api/drafts/email", json=payload)

    def list_drafts(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List email drafts, optionally filtered by status.

        Returns:
            A list of draft dicts, or an empty list on failure.
        """
        params: Dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status

        result = self._request("GET", "/api/drafts/email", params=params)
        if result is None:
            return []
        # The API may wrap drafts in a container object
        if isinstance(result, dict) and "drafts" in result:
            return result["drafts"]
        if isinstance(result, list):
            return result
        return []

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single email draft by ID.

        Returns:
            The draft dict, or None if not found / on failure.
        """
        return self._request("GET", f"/api/drafts/email/{draft_id}")

    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Get email draft statistics (pending, approved, sent, etc.).

        Returns:
            Stats dict, or None on failure.
        """
        return self._request("GET", "/api/drafts/email/stats")

    # ------------------------------------------------------------------ #
    #  Calendar Events                                                     #
    # ------------------------------------------------------------------ #

    def draft_calendar_event(
        self,
        summary: str,
        start: Union[datetime, str],
        end: Union[datetime, str],
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        location: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a calendar event draft for human approval.

        Args:
            summary: Event title.
            start: Start time (datetime or ISO string).
            end: End time (datetime or ISO string).
            description: Event description.
            attendees: List of attendee email addresses.
            location: Event location.
            metadata: Arbitrary key-value metadata.

        Returns:
            The created event draft dict, or None on failure.
        """
        payload: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "summary": summary,
            "start": self._to_iso(start),
            "end": self._to_iso(end),
        }
        if description is not None:
            payload["description"] = description
        if attendees is not None:
            payload["attendees"] = attendees
        if location is not None:
            payload["location"] = location
        if metadata is not None:
            payload["metadata"] = metadata

        return self._request("POST", "/api/calendar/events", json=payload)

    def list_calendar_events(
        self,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List calendar event drafts, optionally filtered by status.

        For querying actual Google Calendar events, use query_calendar_events().

        Returns:
            A list of event draft dicts, or an empty list on failure.
        """
        params: Dict[str, Any] = {}
        if status is not None:
            params["status"] = status

        result = self._request(
            "GET", "/api/calendar/events", params=params or None
        )
        if result is None:
            return []
        if isinstance(result, dict) and "events" in result:
            return result["events"]
        if isinstance(result, list):
            return result
        return []

    def query_calendar_events(
        self,
        start: Union[datetime, str],
        end: Union[datetime, str],
    ) -> List[Dict[str, Any]]:
        """Query Google Calendar for events in a time range.

        Args:
            start: Range start (datetime or ISO string).
            end: Range end (datetime or ISO string).

        Returns:
            A list of calendar event dicts, or an empty list on failure.
        """
        params = {
            "start": self._to_iso(start),
            "end": self._to_iso(end),
        }
        result = self._request("GET", "/api/calendar/events", params=params)
        if result is None:
            return []
        if isinstance(result, dict) and "events" in result:
            return result["events"]
        if isinstance(result, list):
            return result
        return []

    # ------------------------------------------------------------------ #
    #  Social Media                                                        #
    # ------------------------------------------------------------------ #

    def draft_social_post(
        self,
        platform: str,
        text: str,
        media_urls: Optional[List[str]] = None,
        link_url: Optional[str] = None,
        scheduled_for: Optional[Union[datetime, str]] = None,
        campaign_id: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a social media post draft for human approval.

        Args:
            platform: Social platform (twitter, linkedin, facebook, threads).
            text: Post text content.
            media_urls: Optional list of media URLs to attach.
            link_url: Optional link to include.
            scheduled_for: Optional future publish time.
            campaign_id: Optional campaign identifier.
            confidence: Agent confidence in post quality (0.0-1.0).
                Used by trust engine for auto-approval decisions.
            metadata: Arbitrary key-value metadata.

        Returns:
            The created post draft dict, or None on failure.
        """
        payload: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "platform": platform,
            "text": text,
        }
        if media_urls is not None:
            payload["media_urls"] = media_urls
        if link_url is not None:
            payload["link_url"] = link_url
        if scheduled_for is not None:
            payload["scheduled_for"] = self._to_iso(scheduled_for)
        if campaign_id is not None:
            payload["campaign_id"] = campaign_id
        if confidence is not None:
            payload["confidence"] = confidence
        if metadata is not None:
            payload["metadata"] = metadata

        return self._request("POST", "/api/social/posts", json=payload)

    def list_social_posts(
        self,
        status: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List social media post drafts with filters.

        Args:
            status: Filter by status (pending, approved, posted, etc.).
            platform: Filter by platform (twitter, linkedin, facebook, threads).
            limit: Maximum posts to return.

        Returns:
            A list of post draft dicts, or an empty list on failure.
        """
        params: Dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if platform is not None:
            params["platform"] = platform

        result = self._request("GET", "/api/social/posts", params=params)
        if result is None:
            return []
        if isinstance(result, dict) and "posts" in result:
            return result["posts"]
        if isinstance(result, list):
            return result
        return []

    def get_social_post(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Get a single social media post draft by ID.

        Args:
            post_id: Post draft ID.

        Returns:
            The post draft dict, or None if not found.
        """
        return self._request("GET", f"/api/social/posts/{post_id}")

    def get_social_stats(self) -> Optional[Dict[str, Any]]:
        """Get social media post statistics.

        Returns:
            Stats dict with counts by status, or None on failure.
        """
        return self._request("GET", "/api/social/posts/stats")

    def optimize_social_posts(
        self,
        base_message: str,
        platforms: List[str],
        link_url: Optional[str] = None,
        campaign_id: Optional[str] = None,
        auto_schedule: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Optimize a message for multiple platforms with suggested posting times.

        This generates platform-specific variants with optimal scheduling suggestions.
        Does NOT create drafts - use draft_social_post() for each variant to create drafts.

        Args:
            base_message: Core message to adapt for each platform.
            platforms: List of platforms ("twitter", "linkedin", "facebook", "threads", "reddit").
            link_url: Optional link to include.
            campaign_id: Optional campaign identifier.
            auto_schedule: Whether to suggest optimal posting times.
            metadata: Optional metadata dict.

        Returns:
            Dict with "variants" (platform -> optimized variant) and "total_posts",
            or None on failure.
        """
        payload: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "base_message": base_message,
            "platforms": platforms,
            "link_url": link_url,
            "campaign_id": campaign_id,
            "auto_schedule": auto_schedule,
            "metadata": metadata or {},
        }
        return self._request("POST", "/api/social/posts/optimize", json=payload)

    def create_cross_platform_campaign(
        self,
        base_message: str,
        platforms: List[str],
        link_url: Optional[str] = None,
        campaign_id: Optional[str] = None,
        auto_schedule: bool = True,
    ) -> Optional[List[Dict[str, Any]]]:
        """Create optimized drafts for a cross-platform campaign.

        This is a convenience method that:
        1. Optimizes the message for each platform
        2. Creates drafts for each optimized variant
        3. Returns all created drafts

        Args:
            base_message: Core message to adapt.
            platforms: List of platforms.
            link_url: Optional link.
            campaign_id: Optional campaign identifier.
            auto_schedule: Whether to use suggested times.

        Returns:
            List of created draft dicts, or None on failure.
        """
        # Step 1: Optimize
        result = self.optimize_social_posts(
            base_message=base_message,
            platforms=platforms,
            link_url=link_url,
            campaign_id=campaign_id,
            auto_schedule=auto_schedule,
        )

        if not result:
            return None

        # Step 2: Create drafts for each variant
        drafts = []
        variants = result.get("variants", {})

        for platform, variant in variants.items():
            # Parse scheduled time if provided
            scheduled_for = None
            if auto_schedule and variant.get("suggested_time"):
                try:
                    from datetime import datetime
                    scheduled_for = datetime.fromisoformat(variant["suggested_time"].replace("Z", "+00:00"))
                except Exception:
                    pass  # Use None if parsing fails

            draft = self.draft_social_post(
                platform=platform,
                text=variant["text"],
                link_url=variant.get("link_url"),
                campaign_id=variant.get("campaign_id"),
                scheduled_for=scheduled_for,
                metadata={
                    "optimization_reason": variant.get("reason"),
                    "campaign_order": variant.get("order"),
                }
            )

            if draft:
                drafts.append(draft)

        return drafts if drafts else None

    # ------------------------------------------------------------------ #
    #  Inbox (Received Emails)                                             #
    # ------------------------------------------------------------------ #

    def get_inbox(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Optional[List[Dict[str, Any]]]:
        """Get received emails from inbox.

        Args:
            status: Filter by status (unprocessed, routed, responded, archived, spam).
            limit: Maximum number of emails to return (1-200).
            offset: Skip first N emails.

        Returns:
            List of received email dicts, or None on failure.
        """
        params = {
            "status": status,
            "limit": min(limit, 200),
            "offset": offset
        }
        result = self._request("GET", "/api/email/inbox", params=params)
        if result:
            return result.get("emails", [])
        return None

    def get_received_email(self, email_id: str) -> Optional[Dict[str, Any]]:
        """Get a single received email by ID.

        Args:
            email_id: Email ID (e.g., "rcv_abc123").

        Returns:
            Email dict, or None on failure.
        """
        return self._request("GET", f"/api/email/inbox/{email_id}")

    def get_inbox_stats(self) -> Optional[Dict[str, int]]:
        """Get inbox statistics by status.

        Returns:
            Dict with counts for each status, or None on failure.
        """
        return self._request("GET", "/api/email/inbox/stats")

    def reroute_email(self, email_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
        """Manually reroute a received email to a different agent.

        Args:
            email_id: Email ID to reroute.
            agent_id: Target agent ID.

        Returns:
            Success message dict, or None on failure.
        """
        params = {"agent_id": agent_id}
        return self._request("POST", f"/api/email/inbox/{email_id}/reroute", params=params)

    def archive_email(self, email_id: str) -> Optional[Dict[str, Any]]:
        """Archive a received email.

        Args:
            email_id: Email ID to archive.

        Returns:
            Success message dict, or None on failure.
        """
        return self._request("POST", f"/api/email/inbox/{email_id}/archive")

    def mark_email_spam(self, email_id: str) -> Optional[Dict[str, Any]]:
        """Mark a received email as spam.

        Args:
            email_id: Email ID to mark as spam.

        Returns:
            Success message dict, or None on failure.
        """
        return self._request("POST", f"/api/email/inbox/{email_id}/mark-spam")

    # ------------------------------------------------------------------ #
    #  Notifications                                                       #
    # ------------------------------------------------------------------ #

    def notify(
        self,
        title: str,
        message: str,
        priority: str = "info",
        channels: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Send a notification through configured channels.

        Args:
            title: Notification title.
            message: Notification body.
            priority: One of info, success, warning, error.
            channels: Target channels (default: ["smack:general"]).

        Returns:
            Notification result dict, or None on failure.
        """
        payload: Dict[str, Any] = {
            "title": title,
            "message": message,
            "priority": priority,
            "agent_id": self.agent_id,
            "channels": channels or ["smack:general"],
        }
        return self._request("POST", "/api/notifications/send", json=payload)

    # ------------------------------------------------------------------ #
    #  Health                                                              #
    # ------------------------------------------------------------------ #

    def health(self) -> Optional[Dict[str, Any]]:
        """Check if the comms service is running and healthy.

        Returns:
            Health status dict, or None if the service is unreachable.
        """
        return self._request("GET", "/health")

    # ------------------------------------------------------------------ #
    #  Representation                                                      #
    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:
        return f"CommsClient(agent_id={self.agent_id!r}, base_url={self.base_url!r})"


# ------------------------------------------------------------------ #
#  Module-level convenience                                            #
# ------------------------------------------------------------------ #

def get_client(agent_id: str) -> CommsClient:
    """Create and return a CommsClient for the given agent.

    Args:
        agent_id: The identifier of the calling agent (e.g. "marketing_agent").

    Returns:
        A configured CommsClient instance.
    """
    return CommsClient(agent_id=agent_id)
