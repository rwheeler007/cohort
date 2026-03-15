"""
Example: Using BOSS Social Media Integration

This script demonstrates how agents can draft social media posts
for human approval before publishing.

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add comms_service to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from comms_client import get_client


def example_simple_post():
    """Example: Simple tweet draft."""
    print("\n[>>] Example 1: Simple Twitter Post")
    print("=" * 60)

    comms = get_client("marketing_agent")

    post = comms.draft_social_post(
        platform="twitter",
        text="Excited to announce BOSS v2.3 with social media integration! Now agents can draft posts for human approval. [OK]",
        campaign_id="boss_v2_3_launch"
    )

    if post:
        print(f"[OK] Draft created: {post['post_id']}")
        print(f"[*]  Platform: {post['platform']}")
        print(f"[*]  Status: {post['status']}")
        print(f"[*]  Text: {post['text']}")
        print()
        print("[*]  Post is pending human approval")
        print(f"[*]  Approve at: http://localhost:8001/api/social/posts/{post['post_id']}/approve")
    else:
        print("[X] Failed to create draft")


def example_multi_platform():
    """Example: Draft posts for multiple platforms."""
    print("\n[>>] Example 2: Multi-Platform Campaign")
    print("=" * 60)

    comms = get_client("marketing_agent")

    platforms = ["twitter", "linkedin", "facebook"]
    texts = {
        "twitter": "Just shipped: BOSS agents can now post to Twitter, LinkedIn, Facebook & Threads! [>>]",
        "linkedin": "Excited to share that BOSS v2.3 now includes social media integration. Agents can draft posts across multiple platforms with human approval gates. Learn more at partspec.ai/boss",
        "facebook": "Big update: BOSS agents now support social media posting! All posts require human approval for safety. Check out the new features.",
    }

    posts = []
    for platform in platforms:
        post = comms.draft_social_post(
            platform=platform,
            text=texts[platform],
            campaign_id="multi_platform_launch"
        )

        if post:
            posts.append(post)
            print(f"[OK] {platform.capitalize()}: {post['post_id']}")
        else:
            print(f"[X] {platform.capitalize()}: Failed")

    print()
    print(f"[OK] Created {len(posts)} draft posts across {len(platforms)} platforms")
    print("[*]  Review and approve each post individually")


def example_scheduled_post():
    """Example: Schedule a post for future publication."""
    print("\n[>>] Example 3: Scheduled Post")
    print("=" * 60)

    comms = get_client("marketing_agent")

    # Schedule for tomorrow at 9am UTC
    tomorrow = datetime.utcnow() + timedelta(days=1)
    scheduled_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)

    post = comms.draft_social_post(
        platform="twitter",
        text="Good morning! Join us for today's webinar on AI orchestration at 2pm ET.",
        scheduled_for=scheduled_time,
        campaign_id="webinar_series"
    )

    if post:
        print(f"[OK] Draft created: {post['post_id']}")
        print(f"[*]  Scheduled for: {post['scheduled_for']}")
        print(f"[*]  Status: {post['status']}")
        print()
        print("[*]  Post will be published after human approval at scheduled time")
    else:
        print("[X] Failed to create scheduled post")


def example_with_link():
    """Example: Post with link attachment."""
    print("\n[>>] Example 4: Post with Link")
    print("=" * 60)

    comms = get_client("marketing_agent")

    post = comms.draft_social_post(
        platform="linkedin",
        text="Check out our latest blog post on multi-agent orchestration patterns. We dive deep into coordination strategies and human approval workflows.",
        link_url="https://partspec.ai/blog/agent-orchestration",
        campaign_id="blog_promotion"
    )

    if post:
        print(f"[OK] Draft created: {post['post_id']}")
        print(f"[*]  Platform: {post['platform']}")
        print(f"[*]  Link: {post['link_url']}")
        print(f"[*]  Text: {post['text'][:100]}...")
    else:
        print("[X] Failed to create post with link")


def example_list_drafts():
    """Example: List and review pending drafts."""
    print("\n[>>] Example 5: List Pending Drafts")
    print("=" * 60)

    comms = get_client("marketing_agent")

    # List all pending posts
    pending = comms.list_social_posts(status="pending")

    if pending:
        print(f"[*]  Found {len(pending)} pending post(s):")
        print()

        for post in pending:
            print(f"     ID: {post['post_id']}")
            print(f"     Platform: {post['platform']}")
            print(f"     Agent: {post['agent_id']}")
            print(f"     Text: {post['text'][:80]}...")
            print(f"     Created: {post['created_at']}")
            print()
    else:
        print("[*]  No pending posts")

    # Get stats
    stats = comms.get_social_stats()
    if stats:
        print("[*]  Statistics:")
        print(f"     Pending: {stats['pending']}")
        print(f"     Approved: {stats['approved']}")
        print(f"     Posted: {stats['posted']}")
        print(f"     Rejected: {stats['rejected']}")
        print(f"     Posted today: {stats['posted_today']}")


def example_check_health():
    """Example: Check service health."""
    print("\n[>>] Example 6: Service Health Check")
    print("=" * 60)

    comms = get_client("marketing_agent")

    health = comms.health()
    if health:
        print(f"[OK] Service: {health['service']}")
        print(f"[OK] Status: {health['status']}")
        print(f"[*]  Uptime: {health['uptime_seconds']}s")
        print(f"[*]  Pending email drafts: {health['pending_drafts']}")
        print(f"[*]  Pending calendar events: {health['pending_events']}")
        print(f"[*]  Pending social posts: {health['pending_posts']}")
        print(f"[*]  SMACK queue: {health['smack_queue']}")
    else:
        print("[X] Service not responding")
        print("[!] Is comms_service running on port 8001?")


def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("BOSS Social Media Integration Examples")
    print("=" * 60)

    try:
        # Check if service is running
        comms = get_client("example_agent")
        health = comms.health()

        if not health:
            print("\n[X] Comms service not responding")
            print("[!] Start the service: cd tools/comms_service && python service.py")
            return

        print(f"\n[OK] Connected to {health['service']} v{health['version']}")

        # Run examples
        example_simple_post()
        example_multi_platform()
        example_scheduled_post()
        example_with_link()
        example_list_drafts()
        example_check_health()

        print("\n" + "=" * 60)
        print("[OK] Examples complete!")
        print("=" * 60)
        print()
        print("[*]  Next steps:")
        print("     1. Review pending drafts at http://localhost:8001/api/social/posts?status=pending")
        print("     2. Approve a post: POST /api/social/posts/{post_id}/approve")
        print("     3. Check post stats: http://localhost:8001/api/social/posts/stats")
        print()

    except KeyboardInterrupt:
        print("\n\n[!] Interrupted by user")
    except Exception as exc:
        print(f"\n[X] Error: {exc}")


if __name__ == "__main__":
    main()
