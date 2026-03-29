# Social Media Optimization Features

## Overview

The BOSS Comms Service now includes **automatic platform-specific optimization** for social media posts. This system:

1. Adapts messages to each platform's tone, format, and character limits
2. Suggests optimal posting times based on platform-specific engagement patterns
3. Creates cross-platform campaigns with intelligent scheduling
4. Supports images, videos, and links across all platforms

## Supported Platforms

- **Twitter/X** - 280 characters, concise and engaging
- **LinkedIn** - Professional tone, detailed content
- **Facebook** - Community-focused, conversational
- **Threads** - Authentic, behind-the-scenes, 500 characters
- **Reddit** - Community-first, value-driven, requires subreddit

## Key Features

### 1. Automatic Platform Optimization

Takes a single base message and creates platform-appropriate variants:

```python
from tools.comms_service.comms_client import get_client

comms = get_client("marketing_agent")

# Get optimized variants
result = comms.optimize_social_posts(
    base_message="We're launching AI Automation to save you 10+ hours/week",
    platforms=["twitter", "linkedin", "reddit"],
    link_url="https://example.com",
    auto_schedule=True
)

# Returns platform-specific variants with suggested times
for platform, variant in result['variants'].items():
    print(f"{platform}: {variant['text']}")
    print(f"Suggested time: {variant['suggested_time']}")
```

**Platform adaptations:**
- **Twitter**: Concise (280 chars), line breaks for readability, hook-first
- **LinkedIn**: Professional tone, 150-300 words, engagement question
- **Facebook**: Friendly tone, strong hook, community-focused
- **Threads**: Authentic, conversational, under 500 chars
- **Reddit**: Value-first (not promotional), title-focused, community rules

### 2. Intelligent Posting Time Suggestions

Based on platform-specific engagement patterns:

| Platform | Best Times (UTC) | Reason |
|----------|------------------|--------|
| Twitter | 8am, 12pm, 5pm weekdays | Commute and lunch breaks |
| LinkedIn | 7am, 12pm, 5pm weekdays | Work hours |
| Facebook | 1pm, 3pm weekdays; 12pm weekends | Afternoon browsing |
| Threads | 9am, 2pm weekdays | Morning and afternoon |
| Reddit | 6am, 12pm, 8pm; 9am/2pm weekends | Prime engagement times |

**How it works:**
```python
result = comms.optimize_social_posts(
    base_message="...",
    platforms=["twitter", "linkedin"],
    auto_schedule=True  # Suggests next optimal time
)

# Each variant includes:
# - suggested_time: ISO datetime
# - reason: Why this time was chosen
# - order: Stagger order for campaigns
```

### 3. Cross-Platform Campaign Creation

One-click campaign creation with automatic optimization and scheduling:

```python
# Automatically creates optimized drafts for all platforms
drafts = comms.create_cross_platform_campaign(
    base_message="Product launch announcement",
    platforms=["twitter", "linkedin", "facebook", "reddit"],
    link_url="https://example.com/launch",
    campaign_id="product_launch_q1",
    auto_schedule=True
)

# Returns list of created drafts, ready for human approval
for draft in drafts:
    print(f"{draft['platform']}: {draft['post_id']}")
    print(f"Scheduled for: {draft['scheduled_for']}")
```

**Campaign features:**
- Staggered posting (15 min intervals by default)
- Platform-specific optimization
- Unified campaign tracking
- Human approval gate for all posts

### 4. Media Support

All platforms support images, videos, and links:

```python
# Image post
comms.draft_social_post(
    platform="twitter",
    text="Check out our new design!",
    media_urls=["https://example.com/image.jpg"],
    campaign_id="design_reveal"
)

# Link post
comms.draft_social_post(
    platform="linkedin",
    text="Read our latest blog post",
    link_url="https://example.com/blog/post",
    campaign_id="content_marketing"
)

# Reddit image post (with subreddit)
comms.draft_social_post(
    platform="reddit",
    text="Built this cool project over the weekend",
    media_urls=["https://example.com/project.jpg"],
    metadata={"subreddit": "programming"}
)
```

## Usage Patterns

### Pattern 1: Review Before Posting

Get variants, review, then create drafts manually:

```python
# Step 1: Get optimized variants
result = comms.optimize_social_posts(
    base_message="Your message",
    platforms=["twitter", "linkedin"],
    auto_schedule=True
)

# Step 2: Review variants
for platform, variant in result['variants'].items():
    print(f"{platform}: {variant['text']}")

# Step 3: Manually create drafts (optional edits)
for platform, variant in result['variants'].items():
    comms.draft_social_post(
        platform=platform,
        text=variant['text'],  # Can edit here
        link_url=variant.get('link_url'),
        scheduled_for=variant.get('suggested_time')
    )
```

### Pattern 2: Automatic Campaign

Let the system handle everything:

```python
# One call creates all optimized drafts
drafts = comms.create_cross_platform_campaign(
    base_message="Launching our new product!",
    platforms=["twitter", "linkedin", "facebook", "reddit"],
    link_url="https://example.com/product",
    campaign_id="product_launch",
    auto_schedule=True
)

# All drafts created and ready for approval
print(f"Created {len(drafts)} posts")
print("Approve at: http://localhost:5000/comms")
```

### Pattern 3: Reddit-Specific

Reddit requires subreddit in metadata:

```python
comms.draft_social_post(
    platform="reddit",
    text="Your post title (max 300 chars)",
    link_url="https://example.com",  # For link posts
    # OR media_urls=["url"] for image posts
    # OR just text for text posts
    metadata={
        "subreddit": "programming",
        "title": "Optional custom title override"
    }
)
```

## Platform-Specific Optimization Details

### Twitter
- Character limit: 280
- Adds line breaks between sentences (max 3 sentences)
- Truncates with "..." if too long
- Link at end (counted in character limit)

### LinkedIn
- Ideal length: 150-300 words
- Adds engagement question if not present
- Professional, insightful tone
- "Learn more: [link]" format

### Facebook
- Conversational, friendly tone
- First 2-3 lines critical (before "See More")
- Strong hook at start
- Native content preferred

### Threads
- Character limit: 500
- Authentic, conversational
- Behind-the-scenes feel
- Link in text (no rich preview)

### Reddit
- **Title limit: 300 characters**
- Value-first (not promotional)
- Respect community rules
- Post types: text, link, or image
- **REQUIRED**: `subreddit` in metadata

## Best Practices

### 1. Use Campaign IDs

Track related posts across platforms:

```python
campaign_id = "product_launch_2026_q1"

# All posts use same campaign_id
drafts = comms.create_cross_platform_campaign(
    base_message="...",
    platforms=["twitter", "linkedin"],
    campaign_id=campaign_id
)

# Later: analyze campaign performance
posts = comms.list_social_posts(limit=100)
campaign_posts = [p for p in posts if p.get('campaign_id') == campaign_id]
```

### 2. Schedule for Optimal Times

Let the system suggest times, or override:

```python
# Use suggested times
result = comms.optimize_social_posts(..., auto_schedule=True)

# OR manually schedule
from datetime import datetime, timedelta
scheduled = datetime.utcnow() + timedelta(hours=24)

comms.draft_social_post(
    platform="twitter",
    text="...",
    scheduled_for=scheduled
)
```

### 3. Platform-Appropriate Content

Different messages for different platforms:

```python
base = "We're launching AI Automation"

# Let optimizer handle platform differences
drafts = comms.create_cross_platform_campaign(
    base_message=base,
    platforms=["twitter", "linkedin"],  # Twitter: concise, LinkedIn: detailed
    link_url="https://example.com"
)
```

### 4. Reddit Community Guidelines

Reddit is community-first, not promotional:

```python
# GOOD: Value-driven
comms.draft_social_post(
    platform="reddit",
    text="Built a tool that saved me 10+ hours this week. Here's what I learned.",
    link_url="https://blog.example.com/automation-lessons",
    metadata={"subreddit": "productivity"}
)

# BAD: Too promotional (optimizer will warn)
comms.draft_social_post(
    platform="reddit",
    text="Buy our new product! 50% off sale this week!",  # Will warn
    metadata={"subreddit": "productivity"}
)
```

## API Reference

### optimize_social_posts()

Generate platform-optimized variants without creating drafts.

```python
result = comms.optimize_social_posts(
    base_message: str,          # Core message to adapt
    platforms: List[str],        # ["twitter", "linkedin", "reddit", ...]
    link_url: Optional[str] = None,
    campaign_id: Optional[str] = None,
    auto_schedule: bool = True,  # Suggest posting times
    metadata: Optional[Dict] = None
) -> Optional[Dict]

# Returns:
{
    "variants": {
        "twitter": {
            "platform": "twitter",
            "text": "Optimized tweet text...",
            "link_url": "https://...",
            "suggested_time": "2026-02-05T08:00:00Z",
            "reason": "Morning commute",
            "order": 1
        },
        "linkedin": { ... }
    },
    "total_posts": 2
}
```

### create_cross_platform_campaign()

Create optimized drafts for multiple platforms automatically.

```python
drafts = comms.create_cross_platform_campaign(
    base_message: str,
    platforms: List[str],
    link_url: Optional[str] = None,
    campaign_id: Optional[str] = None,
    auto_schedule: bool = True
) -> Optional[List[Dict]]

# Returns list of created draft dicts
[
    {
        "post_id": "uuid",
        "platform": "twitter",
        "text": "Optimized text...",
        "status": "pending",
        "scheduled_for": "2026-02-05T08:00:00Z",
        "campaign_id": "launch_q1",
        "metadata": {
            "optimization_reason": "Morning commute",
            "campaign_order": 1
        }
    },
    ...
]
```

## Files Added/Modified

### New Files
- `tools/comms_service/social_media_optimizer.py` - Optimization engine
- `tools/comms_service/examples/cross_platform_campaign_example.py` - Usage examples
- `tools/comms_service/OPTIMIZATION_FEATURES.md` - This document

### Modified Files
- `tools/comms_service/models.py` - Added Reddit platform, optimization request/response models
- `tools/comms_service/social_media.py` - Added Reddit publishing, image/link support
- `tools/comms_service/service.py` - Added `/api/social/posts/optimize` endpoint
- `tools/comms_service/comms_client.py` - Added `optimize_social_posts()` and `create_cross_platform_campaign()`
- `tools/comms_service/QUICK_REFERENCE.md` - Added optimization examples and Reddit docs
- `tools/comms_service/.env.example` - Added Reddit OAuth credentials

### Updated Memory
- `MEMORY.md` - Documented new optimization features

## Examples

See working examples:
- `tools/comms_service/examples/cross_platform_campaign_example.py`
- `tools/comms_service/examples/social_media_example.py`

Run examples:
```bash
cd tools/comms_service
python examples/cross_platform_campaign_example.py
```

## Setup

1. Add platform credentials to `.env`:
```bash
# Reddit
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_secret
```

2. Run OAuth setup:
```bash
python setup_social_auth.py --platform reddit
```

3. Test optimization:
```python
from tools.comms_service.comms_client import get_client

comms = get_client("test_agent")
result = comms.optimize_social_posts(
    base_message="Test message",
    platforms=["twitter", "reddit"]
)
print(result)
```

## Summary

[OK] **Reddit support added** - Full posting with subreddit, image, and link support
[OK] **Automatic optimization** - Platform-specific tone, format, and character limits
[OK] **Intelligent scheduling** - Suggests optimal times based on engagement patterns
[OK] **Cross-platform campaigns** - One-click campaign creation with staggered scheduling
[OK] **Media support** - Images, videos, and links across all platforms
[OK] **Human approval required** - All posts go through approval gate before publishing

Your agents can now create sophisticated multi-platform campaigns with a single API call!
