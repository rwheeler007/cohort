"""
BOSS Communications Service - Social Media Manager.

Manages social media post drafts with OAuth2 for Twitter, LinkedIn, Facebook, and Threads.
All posts require human approval before publishing.

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
Use [OK], [!], [X], [*], [>>] for status indicators.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from models import (
    SocialPlatform,
    SocialPostCreate,
    SocialPostDraft,
    SocialPostStatus,
    SocialPostStatsResponse,
    SocialPostUpdate,
)
from project_settings import ProjectSettingsManager

logger = logging.getLogger(__name__)


class SocialMediaManager:
    """Manages social media post drafts with OAuth2 for multiple platforms.

    Supports multi-project configurations with separate OAuth tokens per project.
    """

    def __init__(self, base_path: Path):
        """Initialize the social media manager.

        Args:
            base_path: Path to BOSS root directory
        """
        self.base_path = base_path
        self.drafts_path = base_path / "data" / "comms_service" / "social_posts"
        self.config_path = base_path / "data" / "comms_service" / "config"
        self.tokens_path = self.config_path / "social_tokens.json"

        self.drafts_path.mkdir(parents=True, exist_ok=True)
        self.config_path.mkdir(parents=True, exist_ok=True)

        # Initialize project settings manager
        self.project_settings = ProjectSettingsManager(base_path)

        # Load OAuth tokens (legacy/backward compatibility)
        self.tokens = self._load_tokens()

    # ------------------------------------------------------------------ #
    #  Token Management                                                    #
    # ------------------------------------------------------------------ #

    def _load_tokens(self) -> Dict[str, Dict]:
        """Load OAuth tokens from disk.

        Returns:
            Dict mapping platform name to token data
        """
        if not self.tokens_path.exists():
            return {}

        try:
            with open(self.tokens_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("[!] Failed to load social tokens: %s", exc)
            return {}

    def _save_tokens(self) -> None:
        """Save OAuth tokens to disk."""
        try:
            with open(self.tokens_path, "w", encoding="utf-8") as f:
                json.dump(self.tokens, f, indent=2)
            logger.info("[OK] Saved social media tokens")
        except Exception as exc:
            logger.error("[X] Failed to save social tokens: %s", exc)

    def set_platform_token(self, platform: SocialPlatform, token_data: Dict) -> None:
        """Set OAuth token for a platform.

        Args:
            platform: Social media platform
            token_data: Dict with access_token, refresh_token, expires_at, etc.
        """
        self.tokens[platform.value] = token_data
        self._save_tokens()
        logger.info("[OK] Set token for %s", platform.value)

    def get_platform_token(self, platform: SocialPlatform, project_id: Optional[str] = None) -> Optional[Dict]:
        """Get OAuth token for a platform and project.

        Args:
            platform: Social media platform
            project_id: Optional project ID. If None, uses legacy tokens or general project.

        Returns:
            Token data dict or None if not configured
        """
        # Try project-specific token first
        if project_id:
            token = self.project_settings.get_social_platform_token(project_id, platform.value)
            if token:
                return token

        # Fallback to legacy tokens for backward compatibility
        return self.tokens.get(platform.value)

    def is_platform_configured(self, platform: SocialPlatform, project_id: Optional[str] = None) -> bool:
        """Check if a platform has valid OAuth credentials for a project.

        Args:
            platform: Social media platform
            project_id: Optional project ID

        Returns:
            True if platform is configured
        """
        token_data = self.get_platform_token(platform, project_id)
        if not token_data:
            return False

        # Check if token exists and hasn't expired
        if "access_token" not in token_data:
            return False

        # Check expiration if present
        if "expires_at" in token_data:
            expires_at = datetime.fromisoformat(token_data["expires_at"])
            if datetime.utcnow() >= expires_at:
                logger.warning("[!] Token expired for %s (project: %s)", platform.value, project_id or "legacy")
                return False

        return True

    def get_connection_status(self, project_id: Optional[str] = None) -> Dict[str, Dict]:
        """Get connection status for all social media platforms for a project.

        Args:
            project_id: Optional project ID. If None, uses general project.

        Returns:
            Dict mapping platform names to connection status dicts
        """
        if project_id is None:
            project_id = "general"

        platforms = [
            SocialPlatform.TWITTER,
            SocialPlatform.LINKEDIN,
            SocialPlatform.FACEBOOK,
            SocialPlatform.THREADS,
        ]

        statuses = {}
        for platform in platforms:
            token_data = self.get_platform_token(platform, project_id)

            if not token_data:
                statuses[platform.value] = {
                    "connected": False,
                    "status": "not_configured",
                    "message": f"Not connected - run setup_social_auth.py --project {project_id} --platform {platform.value}",
                    "username": None,
                    "project_id": project_id,
                }
            elif "access_token" not in token_data:
                statuses[platform.value] = {
                    "connected": False,
                    "status": "invalid_token",
                    "message": "Token missing access_token",
                    "username": None,
                    "project_id": project_id,
                }
            elif "expires_at" in token_data:
                expires_at = datetime.fromisoformat(token_data["expires_at"])
                if datetime.utcnow() >= expires_at:
                    statuses[platform.value] = {
                        "connected": False,
                        "status": "expired",
                        "message": "Token expired - reconnect required",
                        "username": token_data.get("username"),
                        "project_id": project_id,
                    }
                else:
                    statuses[platform.value] = {
                        "connected": True,
                        "status": "connected",
                        "message": f"Connected as {token_data.get('username', 'Unknown')}",
                        "username": token_data.get("username"),
                        "project_id": project_id,
                    }
            else:
                # No expiration info, assume valid
                statuses[platform.value] = {
                    "connected": True,
                    "status": "connected",
                    "message": f"Connected as {token_data.get('username', 'Unknown')}",
                    "username": token_data.get("username"),
                    "project_id": project_id,
                }

        return statuses

    def get_all_projects_status(self) -> Dict[str, Dict[str, Dict]]:
        """Get connection status for all platforms across all projects.

        Returns:
            Dict mapping project_id to platform status dicts
        """
        all_statuses = {}
        for project in self.project_settings.list_projects():
            if project.social and project.social.enabled:
                all_statuses[project.project_id] = self.get_connection_status(project.project_id)
        return all_statuses

    # ------------------------------------------------------------------ #
    #  Draft Management                                                    #
    # ------------------------------------------------------------------ #

    def create_draft(self, post: SocialPostCreate) -> SocialPostDraft:
        """Create a new social post draft.

        Args:
            post: Post creation request

        Returns:
            Created draft with assigned post_id
        """
        post_id = str(uuid.uuid4())

        # Detect project from metadata
        project_id = self.project_settings.detect_project_from_metadata(post.metadata)

        # Ensure project is in metadata for later retrieval
        metadata = post.metadata.copy()
        if "project" not in metadata:
            metadata["project"] = project_id

        # Carry confidence through to draft for trust engine evaluation
        confidence = getattr(post, "confidence", None)

        draft = SocialPostDraft(
            post_id=post_id,
            agent_id=post.agent_id,
            platform=post.platform,
            text=post.text,
            media_urls=post.media_urls,
            link_url=post.link_url,
            scheduled_for=post.scheduled_for,
            status=SocialPostStatus.PENDING,
            campaign_id=post.campaign_id,
            confidence=confidence,
            metadata=metadata,
            created_at=datetime.utcnow(),
        )

        self._save_draft(draft)
        logger.info("[OK] Created social post draft %s for %s (project: %s)", post_id, post.platform.value, project_id)
        return draft

    def get_draft(self, post_id: str) -> Optional[SocialPostDraft]:
        """Get a social post draft by ID.

        Args:
            post_id: Draft ID

        Returns:
            Draft or None if not found
        """
        draft_file = self.drafts_path / f"{post_id}.json"
        if not draft_file.exists():
            return None

        try:
            with open(draft_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SocialPostDraft(**data)
        except Exception as exc:
            logger.error("[X] Failed to load draft %s: %s", post_id, exc)
            return None

    def list_drafts(
        self,
        status: Optional[SocialPostStatus] = None,
        platform: Optional[SocialPlatform] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[SocialPostDraft]:
        """List social post drafts with filters.

        Args:
            status: Filter by status
            platform: Filter by platform
            agent_id: Filter by agent
            limit: Maximum drafts to return

        Returns:
            List of matching drafts
        """
        drafts = []

        for draft_file in sorted(self.drafts_path.glob("*.json"), reverse=True):
            if len(drafts) >= limit:
                break

            try:
                with open(draft_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                draft = SocialPostDraft(**data)

                # Apply filters
                if status and draft.status != status:
                    continue
                if platform and draft.platform != platform:
                    continue
                if agent_id and draft.agent_id != agent_id:
                    continue

                drafts.append(draft)
            except Exception as exc:
                logger.warning("[!] Failed to load draft %s: %s", draft_file.name, exc)

        return drafts

    def update_draft(self, post_id: str, update: SocialPostUpdate) -> Optional[SocialPostDraft]:
        """Update a pending social post draft.

        Args:
            post_id: Draft ID
            update: Fields to update

        Returns:
            Updated draft or None if not found
        """
        draft = self.get_draft(post_id)
        if not draft:
            return None

        if draft.status != SocialPostStatus.PENDING:
            logger.warning("[!] Cannot update draft %s - status is %s", post_id, draft.status.value)
            return None

        # Update fields
        if update.text is not None:
            draft.text = update.text
        if update.media_urls is not None:
            draft.media_urls = update.media_urls
        if update.link_url is not None:
            draft.link_url = update.link_url
        if update.scheduled_for is not None:
            draft.scheduled_for = update.scheduled_for

        self._save_draft(draft)
        logger.info("[OK] Updated draft %s", post_id)
        return draft

    def approve_draft(self, post_id: str, approved_by: str = "human") -> SocialPostDraft:
        """Approve a social post draft and publish it.

        Args:
            post_id: Draft ID
            approved_by: Who approved the post

        Returns:
            Updated draft with posted status
        """
        draft = self.get_draft(post_id)
        if not draft:
            raise ValueError(f"Draft not found: {post_id}")

        if draft.status != SocialPostStatus.PENDING:
            raise ValueError(f"Draft {post_id} is not pending (status={draft.status.value})")

        # Extract project from draft metadata
        project_id = draft.metadata.get("project", "general")

        # Check if platform is configured for this project
        if not self.is_platform_configured(draft.platform, project_id):
            draft.status = SocialPostStatus.FAILED
            draft.post_error = f"Platform {draft.platform.value} not configured for project {project_id}"
            self._save_draft(draft)
            logger.error("[X] Platform %s not configured for draft %s (project: %s)", draft.platform.value, post_id, project_id)
            return draft

        # Check if scheduled for future
        if draft.scheduled_for and draft.scheduled_for > datetime.utcnow():
            draft.status = SocialPostStatus.SCHEDULED
            draft.approved_at = datetime.utcnow()
            draft.approved_by = approved_by
            self._save_draft(draft)
            logger.info("[OK] Scheduled post %s for %s", post_id, draft.scheduled_for)
            return draft

        # Post immediately
        draft.approved_at = datetime.utcnow()
        draft.approved_by = approved_by

        try:
            platform_post_id, platform_url = self._publish_post(draft)
            draft.status = SocialPostStatus.POSTED
            draft.posted_at = datetime.utcnow()
            draft.platform_post_id = platform_post_id
            draft.platform_url = platform_url
            logger.info("[OK] Posted to %s: %s", draft.platform.value, platform_url)
        except Exception as exc:
            draft.status = SocialPostStatus.FAILED
            draft.post_error = str(exc)
            logger.error("[X] Failed to post to %s: %s", draft.platform.value, exc)

        self._save_draft(draft)
        return draft

    def reject_draft(self, post_id: str, reason: Optional[str] = None) -> SocialPostDraft:
        """Reject a social post draft.

        Args:
            post_id: Draft ID
            reason: Optional rejection reason

        Returns:
            Updated draft with rejected status
        """
        draft = self.get_draft(post_id)
        if not draft:
            raise ValueError(f"Draft not found: {post_id}")

        if draft.status != SocialPostStatus.PENDING:
            raise ValueError(f"Draft {post_id} is not pending (status={draft.status.value})")

        draft.status = SocialPostStatus.REJECTED
        draft.rejected_at = datetime.utcnow()
        draft.reject_reason = reason

        self._save_draft(draft)
        logger.info("[OK] Rejected draft %s", post_id)
        return draft

    def delete_draft(self, post_id: str) -> bool:
        """Delete a social post draft.

        Args:
            post_id: Draft ID

        Returns:
            True if deleted, False if not found
        """
        draft_file = self.drafts_path / f"{post_id}.json"
        if not draft_file.exists():
            return False

        draft_file.unlink()
        logger.info("[OK] Deleted draft %s", post_id)
        return True

    def get_stats(self) -> SocialPostStatsResponse:
        """Get statistics about social post drafts.

        Returns:
            Stats response with counts
        """
        all_drafts = list(self.drafts_path.glob("*.json"))

        stats = SocialPostStatsResponse()
        today = datetime.utcnow().date()

        for draft_file in all_drafts:
            try:
                with open(draft_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                draft = SocialPostDraft(**data)

                if draft.status == SocialPostStatus.PENDING:
                    stats.pending += 1
                elif draft.status == SocialPostStatus.APPROVED:
                    stats.approved += 1
                elif draft.status == SocialPostStatus.POSTED:
                    stats.posted += 1
                    if draft.posted_at and draft.posted_at.date() == today:
                        stats.posted_today += 1
                elif draft.status == SocialPostStatus.REJECTED:
                    stats.rejected += 1
                elif draft.status == SocialPostStatus.FAILED:
                    stats.failed += 1
                elif draft.status == SocialPostStatus.SCHEDULED:
                    stats.scheduled += 1
            except Exception as exc:
                logger.warning("[!] Failed to process draft %s: %s", draft_file.name, exc)

        return stats

    def _save_draft(self, draft: SocialPostDraft) -> None:
        """Save a draft to disk.

        Args:
            draft: Draft to save
        """
        draft_file = self.drafts_path / f"{draft.post_id}.json"

        try:
            with open(draft_file, "w", encoding="utf-8") as f:
                json.dump(draft.model_dump(mode="json"), f, indent=2, default=str)
        except Exception as exc:
            logger.error("[X] Failed to save draft %s: %s", draft.post_id, exc)
            raise

    # ------------------------------------------------------------------ #
    #  Platform Publishing                                                 #
    # ------------------------------------------------------------------ #

    def _publish_post(self, draft: SocialPostDraft) -> tuple[str, str]:
        """Publish a post to a social media platform.

        Args:
            draft: Draft to publish

        Returns:
            Tuple of (platform_post_id, platform_url)

        Raises:
            Exception if posting fails
        """
        if draft.platform == SocialPlatform.TWITTER:
            return self._publish_twitter(draft)
        elif draft.platform == SocialPlatform.LINKEDIN:
            return self._publish_linkedin(draft)
        elif draft.platform == SocialPlatform.FACEBOOK:
            return self._publish_facebook(draft)
        elif draft.platform == SocialPlatform.THREADS:
            return self._publish_threads(draft)
        elif draft.platform == SocialPlatform.REDDIT:
            return self._publish_reddit(draft)
        else:
            raise ValueError(f"Unsupported platform: {draft.platform.value}")

    def _publish_twitter(self, draft: SocialPostDraft) -> tuple[str, str]:
        """Publish to Twitter/X.

        Args:
            draft: Draft to publish

        Returns:
            Tuple of (tweet_id, tweet_url)
        """
        project_id = draft.metadata.get("project", "general")
        token_data = self.get_platform_token(SocialPlatform.TWITTER, project_id)
        if not token_data:
            raise ValueError(f"Twitter not configured for project {project_id}")

        # Twitter API v2 endpoint
        url = "https://api.twitter.com/2/tweets"
        headers = {
            "Authorization": f"Bearer {token_data['access_token']}",
            "Content-Type": "application/json",
        }

        payload = {"text": draft.text}

        # Add media if present (requires media upload first)
        # TODO: Implement media upload for Twitter

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                tweet_id = data["data"]["id"]
                # Get username from token data or API
                username = token_data.get("username", "unknown")
                tweet_url = f"https://twitter.com/{username}/status/{tweet_id}"

                return tweet_id, tweet_url
        except httpx.HTTPStatusError as exc:
            logger.error("[X] Twitter API error: %s - %s", exc.response.status_code, exc.response.text)
            raise Exception(f"Twitter API error: {exc.response.status_code}")
        except Exception as exc:
            logger.error("[X] Twitter publish failed: %s", exc)
            raise

    def _publish_linkedin(self, draft: SocialPostDraft) -> tuple[str, str]:
        """Publish to LinkedIn.

        Args:
            draft: Draft to publish

        Returns:
            Tuple of (post_id, post_url)
        """
        project_id = draft.metadata.get("project", "general")
        token_data = self.get_platform_token(SocialPlatform.LINKEDIN, project_id)
        if not token_data:
            raise ValueError(f"LinkedIn not configured for project {project_id}")

        # LinkedIn API endpoint
        url = "https://api.linkedin.com/v2/ugcPosts"
        headers = {
            "Authorization": f"Bearer {token_data['access_token']}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

        # Get person URN from token data
        person_urn = token_data.get("person_urn", "")
        if not person_urn:
            raise ValueError("LinkedIn person URN not found in token data")

        payload = {
            "author": person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {
                        "text": draft.text
                    },
                    "shareMediaCategory": "NONE"
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            }
        }

        # Add link if present
        if draft.link_url:
            payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "ARTICLE"
            payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
                {
                    "status": "READY",
                    "originalUrl": draft.link_url
                }
            ]

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()

                # LinkedIn returns post ID in the response headers
                post_id = response.headers.get("X-RestLi-Id", "unknown")
                post_url = f"https://www.linkedin.com/feed/update/{post_id}"

                return post_id, post_url
        except httpx.HTTPStatusError as exc:
            logger.error("[X] LinkedIn API error: %s - %s", exc.response.status_code, exc.response.text)
            raise Exception(f"LinkedIn API error: {exc.response.status_code}")
        except Exception as exc:
            logger.error("[X] LinkedIn publish failed: %s", exc)
            raise

    def _publish_facebook(self, draft: SocialPostDraft) -> tuple[str, str]:
        """Publish to Facebook.

        Args:
            draft: Draft to publish

        Returns:
            Tuple of (post_id, post_url)
        """
        project_id = draft.metadata.get("project", "general")
        token_data = self.get_platform_token(SocialPlatform.FACEBOOK, project_id)
        if not token_data:
            raise ValueError(f"Facebook not configured for project {project_id}")

        # Get page ID from token data
        page_id = token_data.get("page_id", "")
        if not page_id:
            raise ValueError("Facebook page ID not found in token data")

        # Facebook Graph API endpoint
        url = f"https://graph.facebook.com/v18.0/{page_id}/feed"

        payload = {
            "message": draft.text,
            "access_token": token_data["access_token"]
        }

        # Add link if present
        if draft.link_url:
            payload["link"] = draft.link_url

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, data=payload)
                response.raise_for_status()
                data = response.json()

                post_id = data["id"]
                post_url = f"https://www.facebook.com/{post_id}"

                return post_id, post_url
        except httpx.HTTPStatusError as exc:
            logger.error("[X] Facebook API error: %s - %s", exc.response.status_code, exc.response.text)
            raise Exception(f"Facebook API error: {exc.response.status_code}")
        except Exception as exc:
            logger.error("[X] Facebook publish failed: %s", exc)
            raise

    def _publish_threads(self, draft: SocialPostDraft) -> tuple[str, str]:
        """Publish to Threads.

        Args:
            draft: Draft to publish

        Returns:
            Tuple of (post_id, post_url)
        """
        project_id = draft.metadata.get("project", "general")
        token_data = self.get_platform_token(SocialPlatform.THREADS, project_id)
        if not token_data:
            raise ValueError(f"Threads not configured for project {project_id}")

        # Threads uses Instagram Graph API
        user_id = token_data.get("user_id", "")
        if not user_id:
            raise ValueError("Threads user ID not found in token data")

        # Step 1: Create media container
        create_url = f"https://graph.threads.net/v1.0/{user_id}/threads"

        payload = {
            "media_type": "TEXT",
            "text": draft.text,
            "access_token": token_data["access_token"]
        }

        # Add link if present
        if draft.link_url:
            payload["link_attachment"] = draft.link_url

        try:
            with httpx.Client(timeout=30.0) as client:
                # Create container
                response = client.post(create_url, data=payload)
                response.raise_for_status()
                container_data = response.json()
                container_id = container_data["id"]

                # Step 2: Publish container
                publish_url = f"https://graph.threads.net/v1.0/{user_id}/threads_publish"
                publish_payload = {
                    "creation_id": container_id,
                    "access_token": token_data["access_token"]
                }

                response = client.post(publish_url, data=publish_payload)
                response.raise_for_status()
                publish_data = response.json()

                post_id = publish_data["id"]
                post_url = f"https://www.threads.net/@{token_data.get('username', 'user')}/post/{post_id}"

                return post_id, post_url
        except httpx.HTTPStatusError as exc:
            logger.error("[X] Threads API error: %s - %s", exc.response.status_code, exc.response.text)
            raise Exception(f"Threads API error: {exc.response.status_code}")
        except Exception as exc:
            logger.error("[X] Threads publish failed: %s", exc)
            raise

    def _publish_reddit(self, draft: SocialPostDraft) -> tuple[str, str]:
        """Publish to Reddit.

        Args:
            draft: Draft to publish

        Returns:
            Tuple of (post_id, post_url)
        """
        project_id = draft.metadata.get("project", "general")
        token_data = self.get_platform_token(SocialPlatform.REDDIT, project_id)
        if not token_data:
            raise ValueError(f"Reddit not configured for project {project_id}")

        # Get subreddit from metadata
        subreddit = draft.metadata.get("subreddit") if draft.metadata else None
        if not subreddit:
            raise ValueError("Reddit posts require 'subreddit' in metadata")

        # Reddit API endpoint
        url = "https://oauth.reddit.com/api/submit"
        headers = {
            "Authorization": f"Bearer {token_data['access_token']}",
            "User-Agent": "BOSS-CommsService/1.0"
        }

        # Reddit supports text posts, link posts, and image posts
        # Determine post type based on what's provided
        payload = {
            "sr": subreddit,
            "title": draft.metadata.get("title", draft.text[:100]),  # Title required
            "kind": "self",  # Default to text post
            "api_type": "json"
        }

        # If link_url provided, make it a link post
        if draft.link_url:
            payload["kind"] = "link"
            payload["url"] = draft.link_url
            if draft.text:
                # Can't have both link and text in Reddit, use text as title
                payload["title"] = draft.text[:300]  # Reddit title limit
        # If media_urls provided, use first image
        elif draft.media_urls and len(draft.media_urls) > 0:
            payload["kind"] = "image"
            payload["url"] = draft.media_urls[0]
            if draft.text:
                payload["title"] = draft.text[:300]
        # Otherwise text post
        else:
            payload["text"] = draft.text

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, data=payload)
                response.raise_for_status()
                data = response.json()

                # Reddit response format
                if data.get("json", {}).get("errors"):
                    errors = data["json"]["errors"]
                    raise Exception(f"Reddit API errors: {errors}")

                post_data = data["json"]["data"]
                post_id = post_data["id"]
                post_url = post_data["url"]

                return post_id, post_url
        except httpx.HTTPStatusError as exc:
            logger.error("[X] Reddit API error: %s - %s", exc.response.status_code, exc.response.text)
            raise Exception(f"Reddit API error: {exc.response.status_code}")
        except Exception as exc:
            logger.error("[X] Reddit publish failed: %s", exc)
            raise
