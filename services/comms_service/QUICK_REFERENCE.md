# Social Media Quick Reference

## For Agents: How to Post to Social Media

### 1. Import the Client

```python
from tools.comms_service.comms_client import get_client

comms = get_client("your_agent_name")
```

### 2. Draft a Post (Simple)

Create individual posts manually:

```python
# Simple post
post = comms.draft_social_post(
    platform="twitter",  # twitter, linkedin, facebook, threads
    text="Your post text here"
)

# With link
post = comms.draft_social_post(
    platform="linkedin",
    text="Check out our latest article!",
    link_url="https://example.com/article"
)

# Scheduled post
from datetime import datetime, timedelta
scheduled = datetime.utcnow() + timedelta(hours=24)

post = comms.draft_social_post(
    platform="facebook",
    text="Tomorrow's announcement",
    scheduled_for=scheduled
)
```

### 3. List Drafts

```python
# All pending posts
pending = comms.list_social_posts(status="pending")

# By platform
twitter_posts = comms.list_social_posts(platform="twitter")

# Get stats
stats = comms.get_social_stats()
print(f"Posted today: {stats['posted_today']}")
```

### 4. Cross-Platform Campaigns (Automatic Optimization)

Let the system optimize and schedule posts automatically:

```python
# Optimize for multiple platforms with automatic scheduling
drafts = comms.create_cross_platform_campaign(
    base_message="We're launching AI Automation to save you 10+ hours/week",
    platforms=["twitter", "linkedin", "reddit"],
    link_url="https://example.com/launch",
    campaign_id="product_launch_q1",
    auto_schedule=True  # Uses platform-specific best times
)

# Returns list of created drafts with optimized text and scheduled times
for draft in drafts:
    print(f"[OK] {draft['platform']}: {draft['text'][:50]}...")
    print(f"    Scheduled for: {draft.get('scheduled_for', 'immediate')}")
```

### 5. Manual Optimization (Get Variants Without Creating Drafts)

Get optimized variants without creating drafts (for review first):

```python
result = comms.optimize_social_posts(
    base_message="Your message here",
    platforms=["twitter", "linkedin", "reddit"],
    link_url="https://example.com",
    auto_schedule=True
)

# Review variants before creating drafts
for platform, variant in result['variants'].items():
    print(f"\n{platform.upper()}:")
    print(f"  Text: {variant['text']}")
    print(f"  Suggested time: {variant['suggested_time']}")
    print(f"  Reason: {variant['reason']}")
```

## Platform Limits

| Platform | Character Limit | Media | Links | Notes |
|----------|----------------|-------|-------|-------|
| Twitter  | 280            | Yes   | Yes   | Images/videos supported |
| LinkedIn | 3,000          | Yes   | Yes   | Images/videos supported |
| Facebook | 63,206         | Yes   | Yes   | Images/videos supported |
| Threads  | 500            | Yes   | Yes   | Images/videos supported |
| Reddit   | Title: 300     | Yes   | Yes   | Requires subreddit in metadata |

**Reddit Special Requirements:**
```python
comms.draft_social_post(
    platform="reddit",
    text="Your post title (max 300 chars)",
    link_url="https://example.com",  # OR media_urls for image post
    metadata={"subreddit": "programming", "title": "Optional custom title"}
)
```

## Response Format

```python
{
    "post_id": "uuid-here",
    "agent_id": "your_agent",
    "platform": "twitter",
    "text": "Your post text",
    "status": "pending",  # pending, approved, posted, rejected, failed
    "created_at": "2026-02-04T12:00:00",
    "campaign_id": "optional_campaign_id",
    "scheduled_for": null,  # or ISO datetime
    "platform_url": null  # filled after posting
}
```

## Human Approval

All posts require human approval. Status flow:

1. **pending** - Awaiting human review
2. **approved** → **posted** - Published to platform
3. **rejected** - Not published

## Error Handling

The client never throws exceptions - it returns `None` on failure:

```python
post = comms.draft_social_post(platform="twitter", text="...")

if post:
    print(f"[OK] Draft created: {post['post_id']}")
else:
    print("[X] Failed to create draft")
    # Service may be down, rate limited, or platform not configured
```

## Rate Limits

- Per-agent rate limiting enforced
- 429 response if exceeded
- Check `Retry-After` header

## Examples

See `tools/comms_service/examples/social_media_example.py` for complete working examples.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Platform not configured" | Run `setup_social_auth.py --platform <name>` |
| Service not responding | Check if running: `http://localhost:8001/health` |
| Rate limited | Wait for rate limit window to reset |
| Token expired | Re-run `setup_social_auth.py` |

## Setup (One-Time)

```bash
# 1. Add credentials to .env
cd tools/comms_service
# Edit .env with your API credentials

# 2. Run OAuth setup
python setup_social_auth.py --platform twitter
python setup_social_auth.py --platform linkedin
python setup_social_auth.py --platform facebook

# 3. Test the service
python examples/social_media_example.py
```

## Full Documentation

See `SOCIAL_MEDIA_SETUP.md` for complete setup guide and API reference.
