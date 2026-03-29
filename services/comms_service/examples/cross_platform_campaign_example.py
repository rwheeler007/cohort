"""
Cross-Platform Campaign Example - Social Media Optimizer

Demonstrates automatic platform optimization, scheduling, and cross-posting.
"""

from tools.comms_service.comms_client import get_client


def example_manual_optimization():
    """Example 1: Get optimized variants without creating drafts (for review)."""
    print("[*] Example 1: Manual Optimization - Review Variants First\n")

    comms = get_client("marketing_agent")

    # Get optimized variants for review
    result = comms.optimize_social_posts(
        base_message="We're launching AI Automation to save you 10+ hours every week. Check it out!",
        platforms=["twitter", "linkedin", "reddit"],
        link_url="https://example.com/ai-automation",
        campaign_id="product_launch_q1",
        auto_schedule=True
    )

    if not result:
        print("[X] Optimization failed")
        return

    # Review all variants
    print(f"[OK] Generated {result['total_posts']} optimized variants:\n")

    for platform, variant in result['variants'].items():
        print(f"--- {platform.upper()} ---")
        print(f"Text: {variant['text']}")
        print(f"Suggested time: {variant.get('suggested_time', 'immediate')}")
        print(f"Reason: {variant.get('reason', 'N/A')}")
        print(f"Campaign order: #{variant.get('order', 'N/A')}")
        print()


def example_auto_campaign():
    """Example 2: Automatic campaign creation with optimization and scheduling."""
    print("\n[*] Example 2: Automatic Cross-Platform Campaign\n")

    comms = get_client("marketing_agent")

    # Create optimized drafts automatically
    drafts = comms.create_cross_platform_campaign(
        base_message="Product managers lose 10+ hours per week to manual tasks. We're changing that.",
        platforms=["twitter", "linkedin", "reddit"],
        link_url="https://example.com/automation",
        campaign_id="automation_launch",
        auto_schedule=True  # Suggests optimal times
    )

    if not drafts:
        print("[X] Campaign creation failed")
        return

    print(f"[OK] Created {len(drafts)} draft posts:\n")

    for draft in drafts:
        platform = draft['platform']
        text_preview = draft['text'][:60] + "..." if len(draft['text']) > 60 else draft['text']
        scheduled = draft.get('scheduled_for', 'immediate')

        print(f"[{platform.upper()}] {draft['post_id'][:8]}")
        print(f"  Text: {text_preview}")
        print(f"  Scheduled: {scheduled}")
        print(f"  Status: {draft['status']}")
        print()


def example_reddit_post():
    """Example 3: Reddit-specific post with subreddit and image."""
    print("\n[*] Example 3: Reddit Post with Image\n")

    comms = get_client("marketing_agent")

    # Reddit requires subreddit in metadata
    draft = comms.draft_social_post(
        platform="reddit",
        text="I built a tool to automate repetitive tasks across my tools. Saved 10+ hours this week!",
        link_url="https://example.com/automation",  # Can also use media_urls for image
        campaign_id="reddit_community_launch",
        metadata={
            "subreddit": "productivity",
            "title": "Built an automation tool that saved me 10+ hours this week"
        }
    )

    if draft:
        print(f"[OK] Reddit draft created: {draft['post_id']}")
        print(f"  Subreddit: r/{draft['metadata']['subreddit']}")
        print(f"  Title: {draft['metadata']['title']}")
        print(f"  Link: {draft.get('link_url', 'N/A')}")
    else:
        print("[X] Failed to create Reddit draft")


def example_staggered_campaign():
    """Example 4: Create campaign with custom staggering between platforms."""
    print("\n[*] Example 4: Staggered Multi-Platform Launch\n")

    comms = get_client("marketing_agent")

    base_message = "Excited to announce our new feature launch!"

    # Optimize first to see suggested times
    result = comms.optimize_social_posts(
        base_message=base_message,
        platforms=["twitter", "linkedin", "facebook", "reddit"],
        link_url="https://example.com/launch",
        campaign_id="big_launch",
        auto_schedule=True
    )

    if not result:
        print("[X] Optimization failed")
        return

    print("[OK] Optimization complete. Suggested schedule:\n")

    for platform, variant in result['variants'].items():
        suggested_time = variant.get('suggested_time', 'immediate')
        reason = variant.get('reason', 'N/A')

        print(f"{platform:10} | {suggested_time:25} | {reason}")

    # Now create drafts with the optimized variants
    print("\n[*] Creating drafts with suggested times...")

    drafts = comms.create_cross_platform_campaign(
        base_message=base_message,
        platforms=["twitter", "linkedin", "facebook", "reddit"],
        link_url="https://example.com/launch",
        campaign_id="big_launch",
        auto_schedule=True
    )

    if drafts:
        print(f"\n[OK] Created {len(drafts)} drafts ready for approval")
        print("    View at: http://localhost:5000/comms")


def main():
    """Run all examples."""
    print("=== Social Media Optimizer Examples ===\n")

    # Run examples
    example_manual_optimization()
    example_auto_campaign()
    example_reddit_post()
    example_staggered_campaign()

    print("\n=== Examples Complete ===")
    print("[*] Check Communications Dashboard: http://localhost:5000/comms")
    print("[*] Approve posts to publish them to social platforms")


if __name__ == "__main__":
    main()
