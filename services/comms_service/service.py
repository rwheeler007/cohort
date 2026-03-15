"""
BOSS Communications Service - FastAPI Application.

Main entry point for the communications service running on port 8001.
Provides email draft management, calendar event drafting, notification
routing, and rate limiting.

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
Use [OK], [!], [X], [*], [>>] for status indicators.
"""

import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Path setup - ensure comms_service directory is importable
# ---------------------------------------------------------------------------

COMMS_DIR = Path(__file__).resolve().parent
if str(COMMS_DIR) not in sys.path:
    sys.path.insert(0, str(COMMS_DIR))

BOSS_ROOT = COMMS_DIR.parent.parent  # d:\Projects\...\BOSS

# Add agents/BOSS_agent to path for scheduler imports
_AGENTS_DIR = str(BOSS_ROOT / "agents" / "BOSS_agent")
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

# ---------------------------------------------------------------------------
# Local module imports (all live in the same directory)
# ---------------------------------------------------------------------------

from calendar_integration import CalendarManager
from email_drafts import EmailDraftManager
from rate_limiter import RateLimiter
from email_receiver import EmailReceiver
from email_router import EmailRouter
from models import (
    CalendarEventCreate,
    CalendarEventStatus,
    DraftListResponse,
    DraftStatsResponse,
    DraftStatus,
    EmailDraftApproval,
    EmailDraftCreate,
    EmailDraftRejection,
    EmailDraftUpdate,
    HealthResponse,
    NotificationRequest,
    NotificationResponse,
    ReceivedEmailListResponse,
    ReceivedEmailStatsResponse,
    ReceivedEmailStatus,
    SocialPlatform,
    SocialPostApproval,
    SocialPostCreate,
    SocialPostListResponse,
    SocialPostOptimizeRequest,
    SocialPostRejection,
    SocialPostStatsResponse,
    SocialPostStatus,
    SocialPostUpdate,
    WebhookConfig,
)
from social_media import SocialMediaManager
from webhook_manager import WebhookManager

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("comms_service")

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

EMAIL_DRAFTS_PATH = BOSS_ROOT / "data" / "comms_service" / "email_drafts"
WEBHOOK_CONFIG_PATH = BOSS_ROOT / "data" / "comms_service" / "config" / "webhook_config.json"
WEBHOOK_LOG_PATH = BOSS_ROOT / "data" / "comms_service" / "webhook_logs"

# ---------------------------------------------------------------------------
# Service instances (initialised during lifespan startup)
# ---------------------------------------------------------------------------

draft_manager: EmailDraftManager
calendar_manager: CalendarManager
social_manager: SocialMediaManager
rate_limiter: RateLimiter
webhook_manager: WebhookManager
email_receiver: EmailReceiver
email_router: EmailRouter

STARTUP_TIME: float = 0.0

# Scheduler daemon state (started in lifespan)
_intel_scheduler = None
_intel_thread: Optional[threading.Thread] = None
_content_monitor = None
_content_monitor_thread: Optional[threading.Thread] = None

# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated on_event("startup") / on_event("shutdown"))
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup and shutdown lifecycle for the comms service."""
    global draft_manager, calendar_manager, social_manager, rate_limiter
    global webhook_manager, email_receiver, email_router, STARTUP_TIME

    load_dotenv()
    STARTUP_TIME = time.time()

    draft_manager = EmailDraftManager(base_path=EMAIL_DRAFTS_PATH)
    calendar_manager = CalendarManager(base_path=BOSS_ROOT)
    social_manager = SocialMediaManager(base_path=BOSS_ROOT)
    rate_limiter = RateLimiter()
    webhook_manager = WebhookManager(
        config_path=WEBHOOK_CONFIG_PATH,
        log_path=WEBHOOK_LOG_PATH,
    )
    email_receiver = EmailReceiver(data_dir=str(BOSS_ROOT / "data" / "comms_service"))
    email_router = EmailRouter(config_path=str(BOSS_ROOT / "config" / "email_routing_rules.yaml"))

    # ------------------------------------------------------------------
    # Start scheduler daemon threads
    # ------------------------------------------------------------------
    global _intel_scheduler, _intel_thread, _content_monitor, _content_monitor_thread

    try:
        from intel_scheduler import IntelScheduler
        _intel_scheduler = IntelScheduler()
        _intel_thread = threading.Thread(
            target=_intel_scheduler.run_daemon,
            daemon=True,
            name="intel-scheduler-daemon",
        )
        _intel_thread.start()
        logger.info("[OK] Intel Scheduler daemon started")
    except Exception as exc:
        logger.warning("[!] Intel Scheduler failed to start: %s", exc)

    try:
        from content_monitor_scheduler import ContentMonitorScheduler
        _content_monitor = ContentMonitorScheduler()
        _content_monitor_thread = threading.Thread(
            target=_content_monitor.run_daemon,
            daemon=True,
            name="content-monitor-daemon",
        )
        _content_monitor_thread.start()
        logger.info("[OK] Content Monitor daemon started")
    except Exception as exc:
        logger.warning("[!] Content Monitor failed to start: %s", exc)

    logger.info("[OK] BOSS Communications Service started on port 8001")

    yield  # --- server is running ---

    # ------------------------------------------------------------------
    # Shutdown scheduler daemons
    # ------------------------------------------------------------------
    for name, inst, thread in [
        ("Intel Scheduler", _intel_scheduler, _intel_thread),
        ("Content Monitor", _content_monitor, _content_monitor_thread),
    ]:
        if inst and hasattr(inst, "_shutdown_event"):
            inst._shutdown_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=5)
            logger.info("[OK] %s daemon stopped", name)

    webhook_manager.disconnect_smack()
    logger.info("[OK] BOSS Communications Service shut down")


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="BOSS Communications Service",
    description="Email draft management, calendar integration, and notification routing.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware - configurable origins (security hardening)
ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5000').split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ===================================================================
# HEALTH
# ===================================================================


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service health with uptime and pending counts."""
    uptime = time.time() - STARTUP_TIME

    # Count pending drafts
    pending_drafts = len(draft_manager.list_drafts(status=DraftStatus.PENDING, limit=9999))

    # Count pending calendar events
    pending_events = len(
        calendar_manager.list_event_drafts(status=CalendarEventStatus.PENDING)
    )

    # Count pending social posts
    pending_posts = len(
        social_manager.list_drafts(status=SocialPostStatus.PENDING, limit=9999)
    )

    queue_status = webhook_manager.get_queue_status()

    # Scheduler daemon status
    scheduler_status = {}
    for name, inst, thread in [
        ("intel_scheduler", _intel_scheduler, _intel_thread),
        ("content_monitor", _content_monitor, _content_monitor_thread),
    ]:
        if inst is None:
            scheduler_status[name] = {"status": "not_loaded"}
        else:
            last_runs = inst.state.get("last_runs", {})
            scheduler_status[name] = {
                "status": "running" if thread and thread.is_alive() else "stopped",
                "paused": inst.state.get("paused", False),
                "last_runs": last_runs,
            }

    return HealthResponse(
        status="ok",
        service="comms_service",
        version="1.0.0",
        uptime_seconds=round(uptime, 2),
        pending_drafts=pending_drafts,
        pending_events=pending_events,
        pending_posts=pending_posts,
        smack_queue=queue_status["queued_count"],
        schedulers=scheduler_status,
    )


# ===================================================================
# EMAIL DRAFTS
# ===================================================================


@app.post("/api/drafts/email", status_code=201)
async def create_email_draft(draft: EmailDraftCreate):
    """Create a new email draft (rate-limited per agent)."""
    allowed, retry_after = await rate_limiter.check_rate_limit(
        draft.agent_id, "draft"
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded for agent: " + draft.agent_id},
            headers={"Retry-After": str(retry_after)},
        )

    result = draft_manager.create_draft(draft)
    await rate_limiter.record_action(draft.agent_id, "draft")
    return result


@app.get("/api/drafts/email", response_model=DraftListResponse)
async def list_email_drafts(
    status: Optional[DraftStatus] = Query(None),
    agent_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> DraftListResponse:
    """List email drafts with optional filters."""
    drafts = draft_manager.list_drafts(
        status=status,
        agent_id=agent_id,
        limit=limit,
    )
    return DraftListResponse(
        drafts=drafts,
        total=len(drafts),
        status_filter=status.value if status else None,
        agent_filter=agent_id,
    )


@app.get("/api/drafts/email/stats", response_model=DraftStatsResponse)
async def get_draft_stats() -> DraftStatsResponse:
    """Get email draft statistics."""
    return draft_manager.get_stats()


@app.get("/api/drafts/email/{draft_id}")
async def get_email_draft(draft_id: str):
    """Get a single email draft by ID."""
    draft = draft_manager.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")
    return draft


@app.post("/api/drafts/email/{draft_id}/approve")
async def approve_email_draft(draft_id: str, approval: EmailDraftApproval):
    """Approve a pending email draft."""
    # Check the draft exists first
    existing = draft_manager.get_draft(draft_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    if existing.status != DraftStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Draft {draft_id} is not pending (status={existing.status.value})",
        )

    result = draft_manager.approve_draft(draft_id, approved_by=approval.approved_by)
    return result


@app.post("/api/drafts/email/{draft_id}/reject")
async def reject_email_draft(draft_id: str, rejection: EmailDraftRejection):
    """Reject a pending email draft."""
    existing = draft_manager.get_draft(draft_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    if existing.status != DraftStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Draft {draft_id} is not pending (status={existing.status.value})",
        )

    result = draft_manager.reject_draft(draft_id, reason=rejection.reason)
    return result


@app.patch("/api/drafts/email/{draft_id}")
async def update_email_draft(draft_id: str, update: EmailDraftUpdate):
    """Update a pending email draft."""
    existing = draft_manager.get_draft(draft_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    if existing.status != DraftStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Draft {draft_id} is not pending (status={existing.status.value})",
        )

    result = draft_manager.update_draft(draft_id, update)
    if result is None:
        raise HTTPException(status_code=400, detail="Update failed")
    return result


@app.delete("/api/drafts/email/{draft_id}")
async def delete_email_draft(draft_id: str):
    """Delete an email draft."""
    success = draft_manager.delete_draft(draft_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")
    return {"detail": f"Draft {draft_id} deleted"}


# ===================================================================
# CALENDAR
# ===================================================================


@app.get("/api/calendar/status")
async def get_calendar_status(project_id: Optional[str] = Query(None)):
    """Check Google Calendar connection status for a project.

    Args:
        project_id: Optional project ID. If None, returns status for all projects.
    """
    if project_id:
        status = calendar_manager.check_connection_status(project_id)
        return status
    else:
        # Return status for all projects
        statuses = calendar_manager.check_all_projects_status()
        return statuses


@app.get("/api/calendar/events")
async def query_calendar_events(
    start: datetime = Query(..., description="Start datetime (ISO format)"),
    end: datetime = Query(..., description="End datetime (ISO format)"),
    project_id: Optional[str] = Query(None, description="Project ID to query"),
):
    """Query Google Calendar events within a time range for a project."""
    if start >= end:
        raise HTTPException(status_code=400, detail="start must be before end")

    events = calendar_manager.query_events(start=start, end=end, project_id=project_id)
    return {"events": events, "count": len(events), "project_id": project_id or "general"}


@app.post("/api/calendar/events", status_code=201)
async def create_calendar_event(event: CalendarEventCreate):
    """Create a calendar event draft (rate-limited per agent)."""
    allowed, retry_after = await rate_limiter.check_rate_limit(
        event.agent_id, "draft"
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded for agent: " + event.agent_id},
            headers={"Retry-After": str(retry_after)},
        )

    result = calendar_manager.create_event_draft(event)
    await rate_limiter.record_action(event.agent_id, "draft")
    return result


@app.get("/api/calendar/drafts")
async def list_calendar_drafts(
    status: Optional[CalendarEventStatus] = Query(None),
):
    """List calendar event drafts with optional status filter."""
    drafts = calendar_manager.list_event_drafts(status=status)
    return {
        "events": drafts,
        "total": len(drafts),
        "status_filter": status.value if status else None,
    }


@app.get("/api/calendar/events/{event_id}")
async def get_calendar_event(event_id: str):
    """Get a calendar event draft by ID."""
    draft = calendar_manager.get_event_draft(event_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")
    return draft


@app.post("/api/calendar/events/{event_id}/approve")
async def approve_calendar_event(event_id: str):
    """Approve a pending calendar event draft."""
    existing = calendar_manager.get_event_draft(event_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

    if existing.status != CalendarEventStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Event {event_id} is not pending (status={existing.status.value})",
        )

    result = calendar_manager.approve_event(event_id)
    return result


@app.post("/api/calendar/events/{event_id}/reject")
async def reject_calendar_event(event_id: str):
    """Reject a pending calendar event draft."""
    existing = calendar_manager.get_event_draft(event_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

    if existing.status != CalendarEventStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Event {event_id} is not pending (status={existing.status.value})",
        )

    result = calendar_manager.reject_event(event_id)
    return result


# ===================================================================
# SOCIAL MEDIA
# ===================================================================


@app.get("/api/social/status")
async def get_social_status(project_id: Optional[str] = Query(None)):
    """Get connection status for all social media platforms for a project.

    Args:
        project_id: Optional project ID. If None, returns status for all projects.
    """
    if project_id:
        statuses = social_manager.get_connection_status(project_id)
        return statuses
    else:
        # Return status for all projects
        all_statuses = social_manager.get_all_projects_status()
        return all_statuses


@app.post("/api/social/posts", status_code=201)
async def create_social_post(post: SocialPostCreate):
    """Create a social media post draft (rate-limited per agent)."""
    allowed, retry_after = await rate_limiter.check_rate_limit(
        post.agent_id, "draft"
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded for agent: " + post.agent_id},
            headers={"Retry-After": str(retry_after)},
        )

    result = social_manager.create_draft(post)
    await rate_limiter.record_action(post.agent_id, "draft")
    return result


@app.get("/api/social/posts", response_model=SocialPostListResponse)
async def list_social_posts(
    status: Optional[SocialPostStatus] = Query(None),
    platform: Optional[SocialPlatform] = Query(None),
    agent_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> SocialPostListResponse:
    """List social media post drafts with optional filters."""
    posts = social_manager.list_drafts(
        status=status,
        platform=platform,
        agent_id=agent_id,
        limit=limit,
    )
    return SocialPostListResponse(
        posts=posts,
        total=len(posts),
        status_filter=status.value if status else None,
        platform_filter=platform.value if platform else None,
        agent_filter=agent_id,
    )


@app.get("/api/social/posts/stats", response_model=SocialPostStatsResponse)
async def get_social_post_stats() -> SocialPostStatsResponse:
    """Get social media post statistics."""
    return social_manager.get_stats()


@app.get("/api/social/posts/{post_id}")
async def get_social_post(post_id: str):
    """Get a single social media post draft by ID."""
    post = social_manager.get_draft(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"Post not found: {post_id}")
    return post


@app.post("/api/social/posts/{post_id}/approve")
async def approve_social_post(post_id: str, approval: SocialPostApproval):
    """Approve a pending social media post and publish it."""
    existing = social_manager.get_draft(post_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Post not found: {post_id}")

    if existing.status != SocialPostStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Post {post_id} is not pending (status={existing.status.value})",
        )

    result = social_manager.approve_draft(post_id, approved_by=approval.approved_by)
    return result


@app.post("/api/social/posts/{post_id}/reject")
async def reject_social_post(post_id: str, rejection: SocialPostRejection):
    """Reject a pending social media post draft."""
    existing = social_manager.get_draft(post_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Post not found: {post_id}")

    if existing.status != SocialPostStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Post {post_id} is not pending (status={existing.status.value})",
        )

    result = social_manager.reject_draft(post_id, reason=rejection.reason)
    return result


@app.patch("/api/social/posts/{post_id}")
async def update_social_post(post_id: str, update: SocialPostUpdate):
    """Update a pending social media post draft."""
    existing = social_manager.get_draft(post_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Post not found: {post_id}")

    if existing.status != SocialPostStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Post {post_id} is not pending (status={existing.status.value})",
        )

    result = social_manager.update_draft(post_id, update)
    if result is None:
        raise HTTPException(status_code=400, detail="Update failed")
    return result


@app.delete("/api/social/posts/{post_id}")
async def delete_social_post(post_id: str):
    """Delete a social media post draft."""
    success = social_manager.delete_draft(post_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Post not found: {post_id}")
    return {"detail": f"Post {post_id} deleted"}


@app.post("/api/social/posts/optimize")
async def optimize_social_posts(request: SocialPostOptimizeRequest):
    """Optimize a message for multiple platforms with suggested posting times.

    Returns platform-specific variants with optimal scheduling suggestions.
    Does NOT create drafts - use POST /api/social/posts for each variant to create drafts.
    """
    from social_media_optimizer import optimize_post

    # Validate platforms
    valid_platforms = ["twitter", "linkedin", "facebook", "threads", "reddit"]
    invalid = [p for p in request.platforms if p not in valid_platforms]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platforms: {invalid}. Valid: {valid_platforms}"
        )

    # Generate optimized variants
    result = optimize_post(
        base_message=request.base_message,
        platforms=request.platforms,
        link_url=request.link_url,
        campaign_id=request.campaign_id,
        auto_schedule=request.auto_schedule
    )

    # Format response
    variants = {}
    for platform, details in result.items():
        variants[platform] = SocialPostOptimizedVariant(
            platform=details["platform"],
            text=details["text"],
            link_url=details.get("link_url"),
            campaign_id=details.get("campaign_id"),
            suggested_time=details.get("suggested_time"),
            reason=details.get("reason"),
            order=details.get("order")
        )

    return SocialPostOptimizeResponse(
        variants=variants,
        total_posts=len(variants)
    )


# ===================================================================
# NOTIFICATIONS
# ===================================================================


@app.post("/api/notifications/send", response_model=NotificationResponse)
async def send_notification(request: NotificationRequest):
    """Send a notification through configured channels (rate-limited)."""
    # Check webhook rate limit for each channel
    for channel in request.channels:
        allowed, retry_after = await rate_limiter.check_rate_limit(
            channel, "webhook"
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded for channel: {channel}"},
                headers={"Retry-After": str(retry_after)},
            )

    result = webhook_manager.send_notification(request)

    # Record webhook actions for sent channels
    for channel in result.channels_sent:
        await rate_limiter.record_action(channel, "webhook")

    return result


@app.get("/api/notifications/config")
async def get_notification_config():
    """Get current webhook configurations."""
    configs = webhook_manager.load_config()
    return {
        name: cfg.model_dump() for name, cfg in configs.items()
    }


@app.put("/api/notifications/config")
async def update_notification_config(configs: Dict[str, WebhookConfig]):
    """Update webhook configurations."""
    webhook_manager.save_config(configs)
    return {"detail": f"Saved {len(configs)} webhook configuration(s)"}


@app.post("/api/notifications/test/{config_name}")
async def test_webhook(config_name: str):
    """Test a named webhook configuration."""
    success = webhook_manager.test_webhook(config_name)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Webhook test failed for: {config_name}",
        )
    return {"detail": f"Webhook test successful: {config_name}"}


# ===================================================================
# INCOMING EMAIL (Received)
# ===================================================================


@app.post("/api/email/webhook")
async def receive_email_webhook(webhook_data: Dict):
    """
    Receive incoming email webhook from Resend.

    This endpoint receives webhook notifications when emails are sent to
    the configured incoming email address (e.g., boss@partspec.ai).

    The email is parsed, stored, classified by AI, and routed to the
    appropriate agent.
    """
    try:
        # Get webhook signature from headers if available
        # Note: FastAPI Request object needed for header access
        # For now, signature validation is optional in EmailReceiver

        # Receive and store email
        email = email_receiver.receive_webhook(webhook_data)
        if not email:
            raise HTTPException(
                status_code=400,
                detail="Failed to parse email webhook"
            )

        # Route email to appropriate agent
        routing_decision = email_router.route_email(email, use_ai=True)

        # Update email with routing decision
        email_receiver.update_email(email)

        # Send notification to SMACK
        if routing_decision.agent_id and email.status != ReceivedEmailStatus.SPAM:
            webhook_manager.send_notification(
                title=f"[>>] New email routed to {routing_decision.agent_id}",
                message=f"From: {email.from_address}\nSubject: {email.subject}\nPriority: {routing_decision.priority.value}\n\nReasoning: {routing_decision.reasoning}",
                priority="warning" if routing_decision.priority.value in ["high", "urgent"] else "info",
                agent_id="email_receiver",
                channels=[f"smack:general"]
            )

        return {
            "email_id": email.email_id,
            "status": email.status.value,
            "routed_to": routing_decision.agent_id,
            "priority": routing_decision.priority.value,
            "intent": email.intent.value if email.intent else "unknown",
            "auto_response": routing_decision.auto_response,
            "suggested_actions": routing_decision.suggested_actions
        }

    except Exception as e:
        logger.error(f"[X] Email webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/email/inbox", response_model=ReceivedEmailListResponse)
async def list_received_emails(
    status: Optional[ReceivedEmailStatus] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List received emails with optional status filter."""
    emails = email_receiver.list_emails(status=status, limit=limit, offset=offset)

    return ReceivedEmailListResponse(
        emails=emails,
        total=len(emails),
        status_filter=status.value if status else None,
        intent_filter=None
    )


@app.get("/api/email/inbox/stats", response_model=ReceivedEmailStatsResponse)
async def get_inbox_stats():
    """Get inbox statistics by status."""
    stats = email_receiver.get_stats()

    return ReceivedEmailStatsResponse(
        unprocessed=stats.get("unprocessed", 0),
        routed=stats.get("routed", 0),
        responded=stats.get("responded", 0),
        archived=stats.get("archived", 0),
        spam=stats.get("spam", 0),
        error=stats.get("error", 0),
        received_today=stats.get("received_today", 0)
    )


@app.get("/api/email/inbox/{email_id}")
async def get_received_email(email_id: str):
    """Get a single received email by ID."""
    email = email_receiver.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail=f"Email not found: {email_id}")
    return email


@app.post("/api/email/inbox/{email_id}/reroute")
async def reroute_email(email_id: str, agent_id: str):
    """Manually reroute an email to a different agent."""
    email = email_receiver.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail=f"Email not found: {email_id}")

    # Update routing
    email.routed_to_agent = agent_id
    email.status = ReceivedEmailStatus.ROUTED

    if email_receiver.update_email(email):
        # Send notification
        webhook_manager.send_notification(
            title=f"[*] Email rerouted to {agent_id}",
            message=f"Email {email_id} manually rerouted\nFrom: {email.from_address}\nSubject: {email.subject}",
            priority="info",
            agent_id="email_receiver",
            channels=["smack:general"]
        )
        return {"detail": f"Email rerouted to {agent_id}"}
    else:
        raise HTTPException(status_code=500, detail="Failed to update email")


@app.post("/api/email/inbox/{email_id}/archive")
async def archive_email(email_id: str):
    """Archive an email."""
    email = email_receiver.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail=f"Email not found: {email_id}")

    email.status = ReceivedEmailStatus.ARCHIVED

    if email_receiver.update_email(email):
        return {"detail": f"Email {email_id} archived"}
    else:
        raise HTTPException(status_code=500, detail="Failed to archive email")


@app.post("/api/email/inbox/{email_id}/mark-spam")
async def mark_as_spam(email_id: str):
    """Mark an email as spam."""
    email = email_receiver.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail=f"Email not found: {email_id}")

    email.status = ReceivedEmailStatus.SPAM

    if email_receiver.update_email(email):
        return {"detail": f"Email {email_id} marked as spam"}
    else:
        raise HTTPException(status_code=500, detail="Failed to mark as spam")


# ===================================================================
# PROJECT SETTINGS
# ===================================================================


@app.get("/api/projects")
async def list_projects():
    """List all configured projects."""
    projects = calendar_manager.project_settings.list_projects()
    return {
        "projects": [p.model_dump() for p in projects],
        "total": len(projects)
    }


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    """Get configuration for a specific project."""
    project = calendar_manager.project_settings.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return project.model_dump()


@app.put("/api/projects/{project_id}/calendar/enable")
async def enable_project_calendar(project_id: str, enabled: bool = Query(...)):
    """Enable or disable calendar integration for a project."""
    success = calendar_manager.project_settings.set_calendar_enabled(project_id, enabled)
    if not success:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {"detail": f"Calendar {'enabled' if enabled else 'disabled'} for project: {project_id}"}


@app.put("/api/projects/{project_id}/social/enable")
async def enable_project_social(project_id: str, enabled: bool = Query(...)):
    """Enable or disable social media integration for a project."""
    success = social_manager.project_settings.set_social_enabled(project_id, enabled)
    if not success:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {"detail": f"Social media {'enabled' if enabled else 'disabled'} for project: {project_id}"}


@app.post("/api/projects")
async def create_project(
    project_id: str = Body(...),
    display_name: str = Body(...),
    color: str = Body(...)
):
    """Create a new project."""
    from project_settings import ProjectConfig, ProjectCalendarConfig, ProjectSocialConfig

    # Validate project_id format
    import re
    if not re.match(r'^[a-z0-9_]+$', project_id):
        raise HTTPException(
            status_code=400,
            detail="Project ID can only contain lowercase letters, numbers, and underscores"
        )

    # Check if project already exists
    existing = calendar_manager.project_settings.get_project(project_id)
    if existing:
        raise HTTPException(status_code=400, detail=f"Project already exists: {project_id}")

    # Create new project
    new_project = ProjectConfig(
        project_id=project_id,
        project_name=project_id,
        display_name=display_name,
        color=color,
        calendar=ProjectCalendarConfig(
            project_id=project_id,
            project_name=display_name,
            google_credentials_file=f"google_credentials_{project_id}.json",
            google_tokens_file=f"google_tokens_{project_id}.json",
            enabled=False
        ),
        social=ProjectSocialConfig(
            project_id=project_id,
            project_name=display_name,
            platforms={},
            enabled=False
        )
    )

    success = calendar_manager.project_settings.add_project(new_project)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to add project")

    return {"detail": "Project created successfully", "project_id": project_id}


@app.put("/api/projects/{project_id}")
async def update_project(
    project_id: str,
    display_name: str = Body(...),
    color: str = Body(...)
):
    """Update a project's display name and color."""
    project = calendar_manager.project_settings.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    # Update project
    project.display_name = display_name
    project.color = color
    if project.calendar:
        project.calendar.project_name = display_name
    if project.social:
        project.social.project_name = display_name

    # Save changes
    success = calendar_manager.project_settings.update_project(project)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update project")

    return {"detail": "Project updated successfully", "project_id": project_id}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and all its credentials."""
    project = calendar_manager.project_settings.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    # Remove project
    success = calendar_manager.project_settings.remove_project(project_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to remove project")

    return {"detail": "Project removed successfully", "project_id": project_id}


# ===================================================================
# RATE LIMITS
# ===================================================================


@app.get("/api/rate-limits")
async def get_rate_limits(agent_id: Optional[str] = Query(None)):
    """Get current rate limit usage statistics."""
    stats = await rate_limiter.get_stats(agent_id=agent_id)
    return stats


# ===================================================================
# Main entry point
# ===================================================================

if __name__ == "__main__":
    import uvicorn

    load_dotenv()
    logger.info("[>>] Starting BOSS Communications Service...")
    uvicorn.run(
        "service:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info",
    )
