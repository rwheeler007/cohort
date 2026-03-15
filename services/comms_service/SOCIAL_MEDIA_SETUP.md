# Social Media Integration Setup Guide

The BOSS Comms Service now supports posting to **Twitter/X, LinkedIn, Facebook, and Threads** with human approval gates.

## Overview

All social media posts follow the same approval workflow as emails and calendar events:
1. Agent creates a **draft post**
2. Human **reviews and approves/rejects**
3. Service **publishes to social media** (only if approved)

**Safety features:**
- [OK] Human approval required for all posts
- [OK] Rate limiting per agent and platform
- [OK] Audit logging to JSON files
- [OK] Post scheduling support
- [OK] Draft editing before approval

---

## Quick Start

### 1. Install Dependencies

```bash
cd tools/comms_service
pip install httpx
```

### 2. Get API Credentials

#### Twitter/X
1. Visit [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)
2. Create a new app or use existing
3. Generate OAuth 2.0 Client ID and Secret
4. Set redirect URI: `http://localhost:8888/callback`
5. Enable scopes: `tweet.read`, `tweet.write`, `users.read`, `offline.access`

#### LinkedIn
1. Visit [LinkedIn Developers](https://www.linkedin.com/developers/apps)
2. Create a new app
3. Get Client ID and Client Secret
4. Set redirect URI: `http://localhost:8888/callback`
5. Request access to LinkedIn Share API
6. Enable scopes: `w_member_social`, `r_liteprofile`, `r_emailaddress`

#### Facebook
1. Visit [Facebook Developers](https://developers.facebook.com/apps)
2. Create a new app (Business type)
3. Add Facebook Login product
4. Get App ID and App Secret
5. Set redirect URI: `http://localhost:8888/callback`
6. Request permissions: `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`
7. You must manage at least one Facebook Page

#### Threads
1. Visit [Facebook Developers](https://developers.facebook.com/apps)
2. Add Threads API to your app
3. Follow [Threads Getting Started Guide](https://developers.facebook.com/docs/threads/get-started)
4. Connect your Instagram Professional/Creator account
5. Get User ID and access token (manual setup required)

### 3. Configure Environment

Edit `tools/comms_service/.env`:

```bash
# Twitter
TWITTER_CLIENT_ID=your_twitter_client_id
TWITTER_CLIENT_SECRET=your_twitter_client_secret

# LinkedIn
LINKEDIN_CLIENT_ID=your_linkedin_client_id
LINKEDIN_CLIENT_SECRET=your_linkedin_client_secret

# Facebook
FACEBOOK_APP_ID=your_facebook_app_id
FACEBOOK_APP_SECRET=your_facebook_app_secret

# Threads
THREADS_APP_ID=your_threads_app_id
THREADS_APP_SECRET=your_threads_app_secret
```

### 4. Authorize Platforms

Run the OAuth setup script for each platform:

```bash
# Twitter
python setup_social_auth.py --platform twitter

# LinkedIn
python setup_social_auth.py --platform linkedin

# Facebook (will prompt you to select a page)
python setup_social_auth.py --platform facebook

# Setup all at once (except Threads)
python setup_social_auth.py --all
```

For **Threads**, follow the manual setup guide (requires Instagram Graph API configuration).

### 5. Verify Setup

Check that tokens were saved:

```bash
ls data/comms_service/config/social_tokens.json
```

---

## Usage from Agents

### Draft a Social Post

```python
from tools.comms_service.comms_client import get_client

comms = get_client("marketing_agent")

# Create a Twitter post draft
post = comms.draft_social_post(
    platform="twitter",
    text="Excited to announce our new feature! Check it out at https://example.com",
    campaign_id="product_launch_2026"
)

print(f"Post draft created: {post['post_id']}")
print(f"Status: {post['status']}")  # 'pending'
```

### Post to Multiple Platforms

```python
# LinkedIn post
linkedin_post = comms.draft_social_post(
    platform="linkedin",
    text="We're hiring! Join our team to build the future of AI orchestration.",
    link_url="https://example.com/careers"
)

# Facebook post
facebook_post = comms.draft_social_post(
    platform="facebook",
    text="Join us for our webinar this Friday at 2pm ET!",
    link_url="https://example.com/webinar"
)

# Threads post
threads_post = comms.draft_social_post(
    platform="threads",
    text="Quick update: just shipped v2.0 with social media integration!"
)
```

### Schedule a Post

```python
from datetime import datetime, timedelta

# Schedule for tomorrow at 9am
scheduled_time = datetime.utcnow() + timedelta(days=1)
scheduled_time = scheduled_time.replace(hour=9, minute=0, second=0)

post = comms.draft_social_post(
    platform="twitter",
    text="Good morning! Today we're launching our new product.",
    scheduled_for=scheduled_time,
    campaign_id="launch_week"
)

print(f"Post scheduled for: {post['scheduled_for']}")
```

### List Drafts

```python
# List all pending posts
pending = comms.list_social_posts(status="pending")
print(f"Pending posts: {len(pending)}")

# Filter by platform
twitter_posts = comms.list_social_posts(platform="twitter")

# Get stats
stats = comms.get_social_stats()
print(f"Posted today: {stats['posted_today']}")
```

---

## Human Approval Workflow

### Via API (for web UI)

```bash
# List pending posts
curl http://localhost:8001/api/social/posts?status=pending

# Get a specific draft
curl http://localhost:8001/api/social/posts/{post_id}

# Approve and publish
curl -X POST http://localhost:8001/api/social/posts/{post_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "ryan"}'

# Reject
curl -X POST http://localhost:8001/api/social/posts/{post_id}/reject \
  -H "Content-Type: application/json" \
  -d '{"reason": "Off-brand messaging"}'

# Update draft before approving
curl -X PATCH http://localhost:8001/api/social/posts/{post_id} \
  -H "Content-Type: application/json" \
  -d '{"text": "Updated post text"}'
```

### Via Python

```python
import httpx

# Approve a post
response = httpx.post(
    f"http://localhost:8001/api/social/posts/{post_id}/approve",
    json={"approved_by": "ryan"}
)

result = response.json()
print(f"Posted to {result['platform']}: {result['platform_url']}")
```

---

## API Endpoints

### POST `/api/social/posts`
Create a social media post draft (rate-limited)

**Request:**
```json
{
  "agent_id": "marketing_agent",
  "platform": "twitter",
  "text": "Post text here",
  "media_urls": ["https://example.com/image.jpg"],
  "link_url": "https://example.com",
  "scheduled_for": "2026-02-10T09:00:00Z",
  "campaign_id": "launch_week",
  "metadata": {}
}
```

**Response:** `SocialPostDraft` object with `post_id` and `status: "pending"`

### GET `/api/social/posts`
List social media post drafts with filters

**Query params:**
- `status` - Filter by status (pending, approved, posted, rejected, failed, scheduled)
- `platform` - Filter by platform (twitter, linkedin, facebook, threads)
- `agent_id` - Filter by agent
- `limit` - Max results (default 50, max 500)

### GET `/api/social/posts/{post_id}`
Get a single post draft

### POST `/api/social/posts/{post_id}/approve`
Approve and publish a post

**Request:**
```json
{
  "approved_by": "human"
}
```

### POST `/api/social/posts/{post_id}/reject`
Reject a post draft

**Request:**
```json
{
  "reason": "Optional reason"
}
```

### PATCH `/api/social/posts/{post_id}`
Update a pending post

**Request:**
```json
{
  "text": "Updated text",
  "media_urls": ["https://example.com/new-image.jpg"],
  "link_url": "https://example.com/updated",
  "scheduled_for": "2026-02-11T09:00:00Z"
}
```

### DELETE `/api/social/posts/{post_id}`
Delete a post draft

### GET `/api/social/posts/stats`
Get post statistics (pending, approved, posted, rejected, failed, scheduled, posted_today)

---

## Platform-Specific Notes

### Twitter/X
- **Character limit:** 280 characters
- **Media support:** Yes (requires separate media upload - TODO)
- **Link handling:** URLs auto-shortened
- **Rate limits:** 50 tweets per 24 hours (free tier)

### LinkedIn
- **Character limit:** 3,000 characters
- **Media support:** Yes (images, videos, articles)
- **Link handling:** Rich previews for article links
- **Post visibility:** Public by default

### Facebook
- **Character limit:** 63,206 characters (but shorter is better)
- **Media support:** Yes (photos, videos, links)
- **Link handling:** Rich previews
- **Posting to:** Facebook Page (not personal profile)

### Threads
- **Character limit:** 500 characters
- **Media support:** Images, videos
- **Link handling:** Links in text (no rich previews yet)
- **Account requirement:** Instagram Professional or Creator account

---

## Security & Privacy

[OK] **OAuth tokens stored securely:**
- Location: `data/comms_service/config/social_tokens.json`
- **DO NOT commit this file to git**
- Already added to `.gitignore`

[OK] **Human approval required:**
- No agent can post directly
- All posts require explicit approval
- Rejected posts are logged with reason

[OK] **Rate limiting:**
- Per-agent limits enforced
- Platform-specific rate limits respected
- 429 responses with Retry-After headers

[OK] **Audit logging:**
- All posts logged to `data/comms_service/social_posts/`
- Includes timestamps, approver, platform response

---

## Troubleshooting

### "Platform not configured"
- Run `setup_social_auth.py --platform <platform>`
- Check `.env` file has correct credentials
- Verify tokens exist in `social_tokens.json`

### "Token expired"
- Re-run setup script to refresh token
- Twitter/LinkedIn tokens auto-refresh (if refresh_token present)
- Facebook page tokens don't expire (unless revoked)

### "Rate limit exceeded"
- Wait for rate limit window to reset
- Check `Retry-After` header in 429 response
- Reduce posting frequency

### "Permission denied"
- Check app has required OAuth scopes
- For Facebook: ensure you manage the selected page
- For LinkedIn: ensure app has Share API access approved

### "Media upload failed"
- Media upload for Twitter not yet implemented (TODO)
- Use direct image URLs where supported
- Check file size limits for each platform

---

## Future Enhancements (TODO)

- [ ] Twitter media upload support
- [ ] Image/video attachment for all platforms
- [ ] Post analytics tracking
- [ ] Thread/carousel post support
- [ ] Hashtag suggestions
- [ ] Post preview generation
- [ ] Scheduled post execution (cron job)
- [ ] Post performance metrics
- [ ] Cross-posting to multiple platforms at once
- [ ] Instagram support (separate from Threads)

---

## Support

For issues or questions:
- Check `data/service_logs/comms_service.log`
- Check `data/comms_service/social_posts/` for draft files
- Verify platform API status pages
- Test OAuth flow with `setup_social_auth.py`

**Platform API docs:**
- [Twitter API](https://developer.twitter.com/en/docs/twitter-api)
- [LinkedIn API](https://docs.microsoft.com/en-us/linkedin/marketing/)
- [Facebook Graph API](https://developers.facebook.com/docs/graph-api/)
- [Threads API](https://developers.facebook.com/docs/threads/)
