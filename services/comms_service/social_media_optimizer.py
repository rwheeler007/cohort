"""
Social Media Post Optimizer - Automatic Platform-Specific Enhancement

Automatically routes draft posts to platform-specific agents for tone/format optimization,
suggests optimal posting times, and handles cross-platform campaigns.

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
Use [OK], [!], [X], [*], [>>] for status indicators.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from models import SocialPostDraft

logger = logging.getLogger(__name__)


class SocialMediaOptimizer:
    """Optimizes social media posts using platform-specific agents and scheduling intelligence."""

    def __init__(self):
        """Initialize the optimizer."""
        self.platform_best_times = self._load_best_times()

    def _load_best_times(self) -> Dict[str, List[Dict]]:
        """Load optimal posting times for each platform.

        Returns:
            Dict mapping platform to list of optimal time windows
        """
        # Based on industry research and can be customized per account
        return {
            "twitter": [
                {"day": "weekday", "hour": 8, "label": "Morning commute"},
                {"day": "weekday", "hour": 12, "label": "Lunch break"},
                {"day": "weekday", "hour": 17, "label": "Evening commute"},
                {"day": "weekend", "hour": 10, "label": "Weekend morning"},
            ],
            "linkedin": [
                {"day": "weekday", "hour": 7, "label": "Early morning"},
                {"day": "weekday", "hour": 12, "label": "Lunch hour"},
                {"day": "weekday", "hour": 17, "label": "End of workday"},
            ],
            "facebook": [
                {"day": "weekday", "hour": 13, "label": "Early afternoon"},
                {"day": "weekday", "hour": 15, "label": "Mid-afternoon"},
                {"day": "weekend", "hour": 12, "label": "Weekend noon"},
            ],
            "threads": [
                {"day": "weekday", "hour": 9, "label": "Morning"},
                {"day": "weekday", "hour": 14, "label": "Afternoon"},
                {"day": "weekend", "hour": 11, "label": "Weekend late morning"},
            ],
            "reddit": [
                {"day": "weekday", "hour": 6, "label": "Early morning (US)"},
                {"day": "weekday", "hour": 12, "label": "Lunch hour"},
                {"day": "weekday", "hour": 20, "label": "Evening prime time"},
                {"day": "weekend", "hour": 9, "label": "Weekend morning"},
                {"day": "weekend", "hour": 14, "label": "Weekend afternoon"},
            ],
        }

    def suggest_post_time(
        self,
        platform: str,
        from_time: Optional[datetime] = None
    ) -> Tuple[datetime, str]:
        """Suggest optimal posting time for a platform.

        Args:
            platform: Social media platform
            from_time: Earliest time to schedule (default: now)

        Returns:
            Tuple of (suggested_datetime, reason)
        """
        if from_time is None:
            from_time = datetime.utcnow()

        platform_times = self.platform_best_times.get(platform, [])
        if not platform_times:
            # Default: 2 hours from now
            suggested = from_time + timedelta(hours=2)
            return suggested, "Default 2-hour buffer"

        # Find next optimal time window
        for days_ahead in range(7):  # Look up to 7 days ahead
            check_date = from_time + timedelta(days=days_ahead)
            is_weekday = check_date.weekday() < 5

            for time_window in platform_times:
                # Check if this time window applies to this day
                if time_window["day"] == "weekday" and not is_weekday:
                    continue
                if time_window["day"] == "weekend" and is_weekday:
                    continue

                suggested = check_date.replace(
                    hour=time_window["hour"],
                    minute=0,
                    second=0,
                    microsecond=0
                )

                # Must be at least 30 minutes in the future
                if suggested > from_time + timedelta(minutes=30):
                    return suggested, time_window["label"]

        # Fallback: next business day at 9am
        days_to_add = 1
        while (from_time + timedelta(days=days_to_add)).weekday() >= 5:
            days_to_add += 1

        suggested = (from_time + timedelta(days=days_to_add)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        return suggested, "Next business day morning"

    def create_cross_platform_variants(
        self,
        base_message: str,
        platforms: List[str],
        link_url: Optional[str] = None,
        campaign_id: Optional[str] = None
    ) -> Dict[str, str]:
        """Create platform-optimized variants of a message.

        Args:
            base_message: Core message to adapt
            platforms: List of platforms to create variants for
            link_url: Optional link to include
            campaign_id: Optional campaign identifier

        Returns:
            Dict mapping platform to optimized text
        """
        variants = {}

        for platform in platforms:
            if platform == "twitter":
                variants[platform] = self._optimize_for_twitter(
                    base_message, link_url
                )
            elif platform == "linkedin":
                variants[platform] = self._optimize_for_linkedin(
                    base_message, link_url
                )
            elif platform == "facebook":
                variants[platform] = self._optimize_for_facebook(
                    base_message, link_url
                )
            elif platform == "threads":
                variants[platform] = self._optimize_for_threads(
                    base_message, link_url
                )
            elif platform == "reddit":
                variants[platform] = self._optimize_for_reddit(
                    base_message, link_url
                )
            else:
                variants[platform] = base_message

        return variants

    def _optimize_for_twitter(self, message: str, link: Optional[str]) -> str:
        """Optimize message for Twitter.

        Args:
            message: Base message
            link: Optional link

        Returns:
            Twitter-optimized text
        """
        # Twitter best practices:
        # - 280 char limit
        # - Line breaks for readability
        # - Hook in first line
        # - CTA or link at end

        lines = message.split(". ")
        if len(lines) > 1:
            # Add line breaks between sentences for readability
            optimized = "\n\n".join(lines[:3])  # Max 3 sentences
        else:
            optimized = message

        # Ensure under 280 chars (leaving room for link)
        max_len = 240 if link else 280
        if len(optimized) > max_len:
            optimized = optimized[:max_len-3] + "..."

        if link:
            optimized += f"\n\n{link}"

        return optimized

    def _optimize_for_linkedin(self, message: str, link: Optional[str]) -> str:
        """Optimize message for LinkedIn.

        Args:
            message: Base message
            link: Optional link

        Returns:
            LinkedIn-optimized text
        """
        # LinkedIn best practices:
        # - 150-300 words ideal
        # - Professional tone
        # - Add context and insights
        # - Question at end for engagement

        # If message is too short, suggest adding context
        if len(message) < 100:
            logger.info("[*] LinkedIn post may benefit from more context")

        # Add professional framing
        optimized = message

        # Add engagement question if not present
        if "?" not in optimized:
            optimized += "\n\nWhat's your experience with this?"

        if link:
            optimized += f"\n\nLearn more: {link}"

        return optimized

    def _optimize_for_facebook(self, message: str, link: Optional[str]) -> str:
        """Optimize message for Facebook.

        Args:
            message: Base message
            link: Optional link

        Returns:
            Facebook-optimized text
        """
        # Facebook best practices:
        # - Friendly, conversational tone
        # - First 2-3 lines visible before "See More"
        # - Strong hook at start

        optimized = message

        if link:
            # Facebook prefers native content, but link is ok
            optimized += f"\n\n{link}"

        return optimized

    def _optimize_for_threads(self, message: str, link: Optional[str]) -> str:
        """Optimize message for Threads.

        Args:
            message: Base message
            link: Optional link

        Returns:
            Threads-optimized text
        """
        # Threads best practices:
        # - 500 char limit
        # - Authentic, conversational
        # - Behind-the-scenes feel

        # Ensure under 500 chars
        max_len = 450 if link else 500
        optimized = message
        if len(optimized) > max_len:
            optimized = optimized[:max_len-3] + "..."

        if link:
            optimized += f"\n\n{link}"

        return optimized

    def _optimize_for_reddit(self, message: str, link: Optional[str]) -> str:
        """Optimize message for Reddit.

        Args:
            message: Base message
            link: Optional link

        Returns:
            Reddit-optimized text
        """
        # Reddit best practices:
        # - Title-focused (300 char limit)
        # - Authentic, community-first tone
        # - Provide value, not promotion
        # - Follow subreddit rules
        # - Use text for context when posting links

        # Reddit is unique - the title is most important
        # Extract key hook for title
        optimized = message

        # If message is promotional, soften it
        promotional_words = ["buy", "purchase", "sale", "discount", "offer"]
        if any(word in message.lower() for word in promotional_words):
            logger.info("[*] Reddit post may be too promotional - consider value-first approach")

        if link:
            # For link posts, the link is primary, text becomes title
            optimized = message[:300]  # Reddit title limit
        else:
            # For text posts, message is the body
            optimized = message

        return optimized

    def schedule_cross_platform_campaign(
        self,
        variants: Dict[str, str],
        link_url: Optional[str] = None,
        campaign_id: Optional[str] = None,
        stagger_minutes: int = 15
    ) -> Dict[str, Dict]:
        """Create scheduling plan for cross-platform campaign.

        Args:
            variants: Platform-specific message variants
            link_url: Optional link to include
            campaign_id: Optional campaign identifier
            stagger_minutes: Minutes between platform posts

        Returns:
            Dict mapping platform to post details with suggested times
        """
        schedule = {}
        base_time = datetime.utcnow()

        for i, (platform, text) in enumerate(variants.items()):
            # Stagger posts to avoid simultaneous posting
            from_time = base_time + timedelta(minutes=i * stagger_minutes)

            suggested_time, reason = self.suggest_post_time(platform, from_time)

            schedule[platform] = {
                "text": text,
                "platform": platform,
                "link_url": link_url,
                "campaign_id": campaign_id,
                "suggested_time": suggested_time.isoformat(),
                "reason": reason,
                "order": i + 1,
            }

        return schedule

    def get_optimization_prompt(self, platform: str, draft: SocialPostDraft) -> str:
        """Generate prompt for platform-specific agent to optimize post.

        Args:
            platform: Social media platform
            draft: Draft post to optimize

        Returns:
            Prompt string for agent
        """
        prompt = f"""Optimize this {platform} post for maximum engagement.

Original text:
{draft.text}

Campaign: {draft.campaign_id or 'N/A'}
Link: {draft.link_url or 'None'}

Please provide:
1. Optimized text following {platform} best practices
2. Why you made these changes
3. Expected engagement improvement

Focus on:
- Platform-appropriate tone and format
- Hook that grabs attention
- Clear value proposition
- Call-to-action or engagement driver
"""
        return prompt

    def apply_agent_optimization(
        self,
        draft: SocialPostDraft,
        agent_response: str
    ) -> Tuple[str, str]:
        """Extract optimized text and reasoning from agent response.

        Args:
            draft: Original draft
            agent_response: Response from platform agent

        Returns:
            Tuple of (optimized_text, reasoning)
        """
        # Parse agent response to extract optimized text
        # This is a simple implementation - could be enhanced with structured output

        lines = agent_response.strip().split("\n")
        optimized_text = []
        reasoning = []
        in_text = False
        in_reasoning = False

        for line in lines:
            if "optimized" in line.lower() and "text" in line.lower():
                in_text = True
                in_reasoning = False
                continue
            elif any(word in line.lower() for word in ["why", "reasoning", "changes"]):
                in_text = False
                in_reasoning = True
                continue
            elif "expected" in line.lower() and "engagement" in line.lower():
                in_reasoning = True
                in_text = False
                continue

            if in_text and line.strip():
                optimized_text.append(line.strip())
            elif in_reasoning and line.strip():
                reasoning.append(line.strip())

        if optimized_text:
            return "\n".join(optimized_text), "\n".join(reasoning)
        else:
            # Couldn't parse - return original
            logger.warning("[!] Could not parse agent response, using original text")
            return draft.text, "Agent optimization could not be parsed"


# Module-level convenience function
def optimize_post(
    base_message: str,
    platforms: List[str],
    link_url: Optional[str] = None,
    campaign_id: Optional[str] = None,
    auto_schedule: bool = True
) -> Dict[str, Dict]:
    """Optimize a message for multiple platforms with scheduling.

    Args:
        base_message: Core message to optimize
        platforms: List of platforms to target
        link_url: Optional link to include
        campaign_id: Optional campaign identifier
        auto_schedule: Whether to suggest optimal posting times

    Returns:
        Dict mapping platform to post details
    """
    optimizer = SocialMediaOptimizer()

    # Create platform-specific variants
    variants = optimizer.create_cross_platform_variants(
        base_message, platforms, link_url, campaign_id
    )

    if auto_schedule:
        # Add scheduling recommendations
        schedule = optimizer.schedule_cross_platform_campaign(
            variants, link_url, campaign_id
        )
        return schedule
    else:
        # Return variants without scheduling
        return {
            platform: {
                "text": text,
                "platform": platform,
                "link_url": link_url,
                "campaign_id": campaign_id,
            }
            for platform, text in variants.items()
        }
