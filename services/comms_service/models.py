"""
Pydantic models for the BOSS Communications Service.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field

# --- Enums ---

class DraftStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"
    FAILED = "failed"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationPriority(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class CalendarEventStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CREATED = "created"
    REJECTED = "rejected"
    FAILED = "failed"


class SocialPlatform(str, Enum):
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    THREADS = "threads"
    REDDIT = "reddit"


class SocialPostStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    POSTED = "posted"
    REJECTED = "rejected"
    FAILED = "failed"
    SCHEDULED = "scheduled"


class ReceivedEmailStatus(str, Enum):
    UNPROCESSED = "unprocessed"
    ROUTED = "routed"
    RESPONDED = "responded"
    ARCHIVED = "archived"
    SPAM = "spam"
    ERROR = "error"


class EmailIntent(str, Enum):
    SUPPORT = "support"
    SALES = "sales"
    SCHEDULING = "scheduling"
    FEEDBACK = "feedback"
    MARKETING = "marketing"
    GENERAL = "general"
    SPAM = "spam"
    UNKNOWN = "unknown"


# --- Email Models ---

class EmailDraftCreate(BaseModel):
    agent_id: str
    to: List[EmailStr]
    cc: Optional[List[EmailStr]] = None
    subject: str
    body_text: str
    body_html: Optional[str] = None
    priority: Priority = Priority.NORMAL
    campaign_id: Optional[str] = None
    template_ref: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Agent confidence in draft quality (0.0-1.0). Used by trust engine for auto-approval.")
    metadata: Dict = Field(default_factory=dict)


class EmailDraft(BaseModel):
    draft_id: str
    agent_id: str
    to: List[EmailStr]
    cc: Optional[List[EmailStr]] = None
    subject: str
    body_text: str
    body_html: Optional[str] = None
    status: DraftStatus = DraftStatus.PENDING
    priority: Priority = Priority.NORMAL
    campaign_id: Optional[str] = None
    template_ref: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Agent confidence in draft quality (0.0-1.0). Used by trust engine for auto-approval.")
    metadata: Dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    sent_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    send_error: Optional[str] = None


class EmailDraftUpdate(BaseModel):
    to: Optional[List[EmailStr]] = None
    cc: Optional[List[EmailStr]] = None
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    priority: Optional[Priority] = None


class EmailDraftApproval(BaseModel):
    approved_by: str = "human"


class EmailDraftRejection(BaseModel):
    reason: Optional[str] = None


# --- Calendar Models ---

class CalendarEventCreate(BaseModel):
    agent_id: str
    summary: str
    start: datetime
    end: datetime
    description: Optional[str] = None
    attendees: Optional[List[EmailStr]] = None
    location: Optional[str] = None
    metadata: Dict = Field(default_factory=dict)


class CalendarEventDraft(BaseModel):
    event_id: str
    agent_id: str
    summary: str
    start: datetime
    end: datetime
    description: Optional[str] = None
    attendees: Optional[List[EmailStr]] = None
    location: Optional[str] = None
    status: CalendarEventStatus = CalendarEventStatus.PENDING
    metadata: Dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = None
    google_event_id: Optional[str] = None


# --- Social Media Models ---

class SocialPostCreate(BaseModel):
    agent_id: str
    platform: SocialPlatform
    text: str
    media_urls: Optional[List[str]] = None
    link_url: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    campaign_id: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Agent confidence in post quality (0.0-1.0). Used by trust engine for auto-approval.")
    metadata: Dict = Field(default_factory=dict)


class SocialPostDraft(BaseModel):
    post_id: str
    agent_id: str
    platform: SocialPlatform
    text: str
    media_urls: Optional[List[str]] = None
    link_url: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    status: SocialPostStatus = SocialPostStatus.PENDING
    campaign_id: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Agent confidence in post quality (0.0-1.0). Used by trust engine for auto-approval.")
    metadata: Dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    posted_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    post_error: Optional[str] = None
    platform_post_id: Optional[str] = None
    platform_url: Optional[str] = None


class SocialPostUpdate(BaseModel):
    text: Optional[str] = None
    media_urls: Optional[List[str]] = None
    link_url: Optional[str] = None
    scheduled_for: Optional[datetime] = None


class SocialPostApproval(BaseModel):
    approved_by: str = "human"


class SocialPostRejection(BaseModel):
    reason: Optional[str] = None


# --- Notification Models ---

class NotificationRequest(BaseModel):
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.INFO
    agent_id: str
    channels: List[str] = Field(default_factory=lambda: ["smack:general"])


class WebhookConfig(BaseModel):
    name: str
    url: str
    platform: str = "custom"
    enabled: bool = True
    headers: Dict = Field(default_factory=dict)


# --- Response Models ---

class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "comms_service"
    version: str = "1.0.0"
    uptime_seconds: float = 0.0
    pending_drafts: int = 0
    pending_events: int = 0
    pending_posts: int = 0
    smack_queue: int = 0
    schedulers: Dict[str, Any] = {}


class DraftListResponse(BaseModel):
    drafts: List[EmailDraft]
    total: int
    status_filter: Optional[str] = None
    agent_filter: Optional[str] = None


class DraftStatsResponse(BaseModel):
    pending: int = 0
    approved: int = 0
    sent: int = 0
    rejected: int = 0
    failed: int = 0
    sent_today: int = 0


class NotificationResponse(BaseModel):
    success: bool
    channels_sent: List[str] = Field(default_factory=list)
    channels_failed: List[str] = Field(default_factory=list)


class SocialPostListResponse(BaseModel):
    posts: List[SocialPostDraft]
    total: int
    status_filter: Optional[str] = None
    platform_filter: Optional[str] = None
    agent_filter: Optional[str] = None


class SocialPostStatsResponse(BaseModel):
    pending: int = 0
    approved: int = 0
    posted: int = 0
    rejected: int = 0
    failed: int = 0
    scheduled: int = 0
    posted_today: int = 0


class SocialPostOptimizeRequest(BaseModel):
    """Request to optimize a message for multiple platforms."""
    agent_id: str
    base_message: str
    platforms: List[str]
    link_url: Optional[str] = None
    campaign_id: Optional[str] = None
    auto_schedule: bool = True
    metadata: Optional[Dict[str, Any]] = None


class SocialPostOptimizedVariant(BaseModel):
    """Single optimized post variant for a platform."""
    platform: str
    text: str
    link_url: Optional[str] = None
    campaign_id: Optional[str] = None
    suggested_time: Optional[str] = None  # ISO datetime
    reason: Optional[str] = None  # Why this time was suggested
    order: Optional[int] = None  # Order in campaign


class SocialPostOptimizeResponse(BaseModel):
    """Response containing optimized variants for multiple platforms."""
    variants: Dict[str, SocialPostOptimizedVariant]
    total_posts: int


# --- Received Email Models ---

class EmailAttachment(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    url: Optional[str] = None  # URL to download attachment
    content: Optional[str] = None  # Base64 encoded content


class ReceivedEmail(BaseModel):
    email_id: str
    from_address: EmailStr
    from_name: Optional[str] = None
    to: List[EmailStr]
    cc: Optional[List[EmailStr]] = None
    subject: str
    body_text: str
    body_html: Optional[str] = None
    attachments: List[EmailAttachment] = Field(default_factory=list)
    headers: Dict = Field(default_factory=dict)

    # Processing metadata
    status: ReceivedEmailStatus = ReceivedEmailStatus.UNPROCESSED
    intent: Optional[EmailIntent] = None
    routed_to_agent: Optional[str] = None
    priority: Priority = Priority.NORMAL

    # Threading
    in_reply_to: Optional[str] = None  # Message-ID being replied to
    thread_id: Optional[str] = None

    # Timestamps
    received_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None

    # AI classification results
    classification: Dict = Field(default_factory=dict)  # Full AI analysis
    entities: Dict = Field(default_factory=dict)  # Extracted entities
    sentiment: Optional[str] = None  # positive/negative/neutral

    # Response tracking
    response_draft_id: Optional[str] = None

    # Metadata
    metadata: Dict = Field(default_factory=dict)


class EmailClassification(BaseModel):
    """AI classification result for incoming email."""
    intent: EmailIntent
    priority: Priority
    suggested_agent: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    entities: Dict = Field(default_factory=dict)
    sentiment: str  # positive/negative/neutral
    requires_urgent_response: bool = False
    is_spam: bool = False
    spam_indicators: List[str] = Field(default_factory=list)


class EmailRoutingDecision(BaseModel):
    """Routing decision for an incoming email."""
    email_id: str
    agent_id: str
    priority: Priority
    reasoning: str
    auto_response: bool = False  # Whether to auto-draft response
    suggested_actions: List[str] = Field(default_factory=list)


class ReceivedEmailListResponse(BaseModel):
    emails: List[ReceivedEmail]
    total: int
    status_filter: Optional[str] = None
    intent_filter: Optional[str] = None


class ReceivedEmailStatsResponse(BaseModel):
    unprocessed: int = 0
    routed: int = 0
    responded: int = 0
    archived: int = 0
    spam: int = 0
    error: int = 0
    received_today: int = 0
