"""
Rate limiter for the BOSS Communications Service.

Provides per-agent, global, and webhook rate limiting using
in-memory sliding windows. Thread-safe for async FastAPI usage.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import logging

logger = logging.getLogger(__name__)


# --- Configuration ---

@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limit thresholds."""
    # Per-agent limits
    agent_drafts_per_hour: int = 50
    agent_drafts_per_day: int = 200
    # Global limits
    global_drafts_per_hour: int = 200
    global_drafts_per_day: int = 1000
    # Webhook limits (per channel)
    webhook_per_hour: int = 100


DEFAULT_CONFIG = RateLimitConfig()

# Time windows in seconds
HOUR = 3600
DAY = 86400


# --- Rate Limiter ---

class RateLimiter:
    """
    Sliding-window rate limiter with per-agent, global, and webhook tracking.

    All public methods are async to play nicely with FastAPI,
    but the underlying data is protected by an asyncio.Lock.
    """

    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or DEFAULT_CONFIG
        self._lock = asyncio.Lock()

        # Timestamp lists keyed by bucket name.
        # Buckets: "agent:{agent_id}", "global", "webhook:{channel}"
        self._buckets: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_rate_limit(
        self, agent_id: str, action_type: str = "draft"
    ) -> Tuple[bool, Optional[int]]:
        """
        Check whether an action is allowed under current rate limits.

        Returns:
            (allowed, retry_after) - retry_after is seconds until the
            earliest slot opens, or None if allowed.
        """
        async with self._lock:
            now = time.time()

            if action_type == "webhook":
                # For webhooks, agent_id doubles as the channel identifier.
                return self._check_webhook(agent_id, now)

            # Per-agent checks
            agent_bucket = f"agent:{agent_id}"
            stamps = self._get_stamps(agent_bucket, now)

            hourly = self._count_in_window(stamps, now, HOUR)
            if hourly >= self.config.agent_drafts_per_hour:
                retry = self._earliest_expiry(stamps, now, HOUR)
                return False, retry

            daily = self._count_in_window(stamps, now, DAY)
            if daily >= self.config.agent_drafts_per_day:
                retry = self._earliest_expiry(stamps, now, DAY)
                return False, retry

            # Global checks
            global_stamps = self._get_stamps("global", now)

            g_hourly = self._count_in_window(global_stamps, now, HOUR)
            if g_hourly >= self.config.global_drafts_per_hour:
                retry = self._earliest_expiry(global_stamps, now, HOUR)
                return False, retry

            g_daily = self._count_in_window(global_stamps, now, DAY)
            if g_daily >= self.config.global_drafts_per_day:
                retry = self._earliest_expiry(global_stamps, now, DAY)
                return False, retry

            return True, None

    async def record_action(
        self, agent_id: str, action_type: str = "draft"
    ) -> None:
        """Record that an action was performed (call after check passes)."""
        async with self._lock:
            now = time.time()

            if action_type == "webhook":
                bucket = f"webhook:{agent_id}"
                self._get_stamps(bucket, now).append(now)
                return

            self._get_stamps(f"agent:{agent_id}", now).append(now)
            self._get_stamps("global", now).append(now)

    async def get_stats(
        self, agent_id: Optional[str] = None
    ) -> Dict:
        """
        Return current usage counts.

        If agent_id is provided, returns that agent's stats plus global.
        Otherwise returns global stats only.
        """
        async with self._lock:
            now = time.time()
            stats: Dict = {}

            # Global stats
            g_stamps = self._get_stamps("global", now)
            stats["global"] = {
                "drafts_last_hour": self._count_in_window(g_stamps, now, HOUR),
                "drafts_last_day": self._count_in_window(g_stamps, now, DAY),
                "limit_hour": self.config.global_drafts_per_hour,
                "limit_day": self.config.global_drafts_per_day,
            }

            if agent_id:
                a_stamps = self._get_stamps(f"agent:{agent_id}", now)
                stats["agent"] = {
                    "agent_id": agent_id,
                    "drafts_last_hour": self._count_in_window(a_stamps, now, HOUR),
                    "drafts_last_day": self._count_in_window(a_stamps, now, DAY),
                    "limit_hour": self.config.agent_drafts_per_hour,
                    "limit_day": self.config.agent_drafts_per_day,
                }

            # Webhook channel stats (only those with activity)
            webhook_stats = {}
            for key in list(self._buckets.keys()):
                if key.startswith("webhook:"):
                    channel = key[len("webhook:"):]
                    w_stamps = self._get_stamps(key, now)
                    count = self._count_in_window(w_stamps, now, HOUR)
                    if count > 0:
                        webhook_stats[channel] = {
                            "last_hour": count,
                            "limit_hour": self.config.webhook_per_hour,
                        }
            if webhook_stats:
                stats["webhooks"] = webhook_stats

            return stats

    # ------------------------------------------------------------------
    # Internal helpers (caller must hold self._lock)
    # ------------------------------------------------------------------

    def _get_stamps(self, bucket: str, now: float) -> List[float]:
        """Get the timestamp list for a bucket, pruning entries older than 24h."""
        if bucket not in self._buckets:
            self._buckets[bucket] = []
        stamps = self._buckets[bucket]
        cutoff = now - DAY
        # Prune old entries
        while stamps and stamps[0] < cutoff:
            stamps.pop(0)
        return stamps

    def _count_in_window(
        self, stamps: List[float], now: float, window: int
    ) -> int:
        """Count timestamps within the last `window` seconds."""
        cutoff = now - window
        # stamps are in chronological order; count from the right
        count = 0
        for ts in reversed(stamps):
            if ts >= cutoff:
                count += 1
            else:
                break
        return count

    def _earliest_expiry(
        self, stamps: List[float], now: float, window: int
    ) -> int:
        """Seconds until the oldest stamp in the window expires."""
        cutoff = now - window
        for ts in stamps:
            if ts >= cutoff:
                return max(1, int(ts - cutoff) + 1)
        return 1

    def _check_webhook(
        self, channel: str, now: float
    ) -> Tuple[bool, Optional[int]]:
        """Check webhook rate limit for a channel."""
        bucket = f"webhook:{channel}"
        stamps = self._get_stamps(bucket, now)
        hourly = self._count_in_window(stamps, now, HOUR)
        if hourly >= self.config.webhook_per_hour:
            retry = self._earliest_expiry(stamps, now, HOUR)
            return False, retry
        return True, None
