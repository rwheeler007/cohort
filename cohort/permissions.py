"""Cohort Permission Tier System.

Three-tier model controlling what agents can access:

* **sandbox** (default) -- fully contained, no host access, no outbound net.
* **local** -- project directory access, outbound network, no desktop.
* **unrestricted** -- current behavior, everything enabled.

The tier is read from the ``COHORT_TIER`` environment variable.  When unset,
defaults to ``unrestricted`` on bare metal (preserving existing behavior) or
``sandbox`` inside Docker (detected via ``/.dockerenv``).
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path


class PermissionTier(Enum):
    SANDBOX = "sandbox"
    LOCAL = "local"
    UNRESTRICTED = "unrestricted"


_HIERARCHY = [PermissionTier.SANDBOX, PermissionTier.LOCAL, PermissionTier.UNRESTRICTED]

_cached_tier: PermissionTier | None = None


def get_tier() -> PermissionTier:
    """Read tier from env var.  Auto-detects Docker if unset."""
    global _cached_tier
    if _cached_tier is not None:
        return _cached_tier

    raw = os.environ.get("COHORT_TIER", "").lower().strip()
    if raw:
        try:
            _cached_tier = PermissionTier(raw)
        except ValueError:
            _cached_tier = PermissionTier.SANDBOX
    else:
        # No env var -- detect environment
        in_docker = (
            Path("/.dockerenv").exists()
            or os.environ.get("COHORT_DOCKER") == "1"
        )
        _cached_tier = PermissionTier.SANDBOX if in_docker else PermissionTier.UNRESTRICTED

    return _cached_tier


def require_tier(minimum: PermissionTier) -> bool:
    """Check if current tier meets or exceeds *minimum*."""
    current = get_tier()
    return _HIERARCHY.index(current) >= _HIERARCHY.index(minimum)


def reset_tier_cache() -> None:
    """Clear cached tier (for testing)."""
    global _cached_tier
    _cached_tier = None


def browser_allowlist() -> list[str] | None:
    """Return the URL allowlist for the current tier.

    * sandbox -- localhost only
    * local / unrestricted -- None (no allowlist, use default rules)
    """
    tier = get_tier()
    if tier == PermissionTier.SANDBOX:
        return ["localhost", "127.0.0.1"]
    return None


def browser_allow_local() -> bool:
    """Whether the browser may navigate to RFC1918 / localhost addresses."""
    tier = get_tier()
    # Sandbox needs localhost for in-container pages
    # Local blocks RFC1918 by default (use allowlist for exceptions)
    # Unrestricted allows everything
    return tier in (PermissionTier.SANDBOX, PermissionTier.UNRESTRICTED)
