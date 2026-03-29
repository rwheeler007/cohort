# Social Media Integration - Release Notes (2026-02-04)

## Major Features Added

### [OK] Reddit Platform Support
- Full OAuth integration for Reddit posting
- Support for text posts, link posts, and image posts
- Requires `subreddit` in metadata
- Title limit: 300 characters
- Setup: `python setup_social_auth.py --platform reddit`

### [OK] Automatic Platform Optimization
- New `social_media_optimizer.py` module
- Automatically adapts messages to platform-specific:
  - Tone (Twitter: concise, LinkedIn: professional, Reddit: community-first)
  - Format (character limits, line breaks, structure)
  - Best practices (hooks, engagement questions, CTAs)

### [OK] Intelligent Posting Time Suggestions
- Platform-specific optimal posting times based on engagement data
- Twitter: 8am, 12pm, 5pm (weekdays)
- LinkedIn: 7am, 12pm, 5pm (weekdays)
- Reddit: 6am, 12pm, 8pm (weekdays); 9am, 2pm (weekends)
- Facebook: 1pm, 3pm (weekdays); 12pm (weekends)
- Threads: 9am, 2pm (weekdays); 11am (weekends)

### [OK] Cross-Platform Campaign Creation
- **New client method**: `create_cross_platform_campaign()`
- One API call creates optimized drafts for all platforms
- Staggered scheduling (15 min intervals between platforms)
- Unified campaign tracking via `campaign_id`

### [OK] New API Endpoints

#### POST /api/social/posts/optimize
Optimize a message for multiple platforms with scheduling suggestions.

**Request:**
```json
{
  "agent_id": "marketing_agent",
  "base_message": "Your message here",
  "platforms": ["twitter", "linkedin", "reddit"],
  "link_url": "https://example.com",
  "campaign_id": "launch_q1",
  "auto_schedule": true
}
```

**Response:**
```json
{
  "variants": {
    "twitter": {
      "platform": "twitter",
      "text": "Optimized tweet...",
      "suggested_time": "2026-02-05T08:00:00Z",
      "reason": "Morning commute",
      "order": 1
    },
    "linkedin": { ... },
    "reddit": { ... }
  },
  "total_posts": 3
}
```

## Usage Examples

### Simple: Get Optimized Variants
```python
from tools.comms_service.comms_client import get_client

comms = get_client("marketing_agent")

# Get variants without creating drafts (for review)
result = comms.optimize_social_posts(
    base_message="We're launching AI Automation to save you 10+ hours/week",
    platforms=["twitter", "linkedin", "reddit"],
    link_url="https://example.com/launch",
    auto_schedule=True
)

# Review variants
for platform, variant in result['variants'].items():
    print(f"{platform}: {variant['text']}")
    print(f"Suggested time: {variant['suggested_time']}")
```

### Advanced: Create Full Campaign
```python
# One call creates all optimized drafts
drafts = comms.create_cross_platform_campaign(
    base_message="Product launch announcement",
    platforms=["twitter", "linkedin", "facebook", "reddit"],
    link_url="https://example.com/product",
    campaign_id="product_launch_q1",
    auto_schedule=True  # Uses platform best times
)

# All drafts created and ready for approval
print(f"Created {len(drafts)} posts")
# Approve at: http://localhost:5000/comms
```

### Reddit-Specific
```python
# Reddit requires subreddit
comms.draft_social_post(
    platform="reddit",
    text="Built a tool that saved me 10+ hours this week",
    link_url="https://example.com/blog",
    metadata={"subreddit": "productivity"}
)
```

## Platform-Specific Adaptations

### Twitter (280 chars)
- Concise, punchy
- Line breaks for readability (max 3 sentences)
- Hook in first line
- Link at end

### LinkedIn (3000 chars)
- Professional, detailed
- 150-300 words ideal
- Adds engagement question if missing
- "Learn more: [link]" format

### Facebook (63,206 chars)
- Conversational, friendly
- First 2-3 lines critical (before "See More")
- Community-focused tone

### Threads (500 chars)
- Authentic, conversational
- Behind-the-scenes feel
- Link in text (no rich preview)

### Reddit (Title: 300 chars)
- **Community-first, NOT promotional**
- Value-driven content
- Requires `subreddit` in metadata
- Supports text, link, or image posts

## Files Added/Modified

**New Files:**
- `social_media_optimizer.py` - Optimization engine
- `examples/cross_platform_campaign_example.py` - Working examples
- `OPTIMIZATION_FEATURES.md` - Complete feature documentation
- `RELEASE_NOTES_2026-02-04.md` - This file

**Modified Files:**
- `models.py` - Added Reddit + optimization models
- `social_media.py` - Reddit publishing + image/link support
- `service.py` - Optimization endpoint
- `comms_client.py` - New optimization methods
- `QUICK_REFERENCE.md` - Updated with examples
- `.env.example` - Reddit credentials

**Agent Memory Updates:**
- `agents/BOSS_agent/memory.json` - Learned facts about social media
- `agents/marketing_agent/memory.json` - Platform best practices and API usage

## Setup Required

### 1. Add Reddit Credentials to .env
```bash
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_secret
```

Get credentials at: https://www.reddit.com/prefs/apps
Create a "script" or "web app" application

### 2. Run OAuth Setup
```bash
cd tools/comms_service
python setup_social_auth.py --platform reddit
```

### 3. Test
```python
from tools.comms_service.comms_client import get_client

comms = get_client("test")
result = comms.optimize_social_posts(
    base_message="Test message",
    platforms=["twitter", "reddit"]
)
print(result)
```

## Documentation

- **Full Setup Guide**: `tools/comms_service/SOCIAL_MEDIA_SETUP.md`
- **Quick Reference**: `tools/comms_service/QUICK_REFERENCE.md`
- **Optimization Features**: `tools/comms_service/OPTIMIZATION_FEATURES.md`
- **API Docs**: `docs/tools/comms-service.md`
- **Examples**: `tools/comms_service/examples/`
- **Integration Guide**: `agents/marketing_agent/SOCIAL_MEDIA_INTEGRATION.md`

## Breaking Changes

None - all changes are additions. Existing social media posting continues to work as before.

## Next Steps

1. Configure Reddit API credentials in `.env`
2. Run OAuth setup for Reddit: `python setup_social_auth.py --platform reddit`
3. Test optimization: `python examples/cross_platform_campaign_example.py`
4. Update marketing workflows to use `create_cross_platform_campaign()`

## Summary

[OK] Reddit integration complete
[OK] Automatic platform optimization working
[OK] Intelligent scheduling implemented
[OK] Cross-platform campaigns ready
[OK] BOSS and marketing_agent informed
[OK] All documentation updated

Your agents can now create sophisticated multi-platform social media campaigns with automatic optimization and intelligent scheduling!
