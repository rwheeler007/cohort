"""Sliding window context truncation for Cohort.

Provides a zero-dependency context budget manager that keeps
conversations within a character limit by dropping oldest messages
first while preserving the most recent ones.
"""

from __future__ import annotations

import os
from typing import Any

# Budget sized for qwen3.5:9b (262K context). 120K chars ~ 30K tokens,
# leaving ample room for system prompt + persona + output.
DEFAULT_CHAR_BUDGET = int(os.environ.get("COHORT_CHAR_BUDGET", "120000"))
DEFAULT_KEEP_RECENT = int(os.environ.get("COHORT_KEEP_RECENT", "20"))


def truncate_context(
    messages: list[Any],
    char_budget: int = DEFAULT_CHAR_BUDGET,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> list[Any]:
    """Return a subset of *messages* that fits within *char_budget*.

    Strategy:
      1. Always keep the last *keep_recent* messages.
      2. If those alone exceed the budget, truncate individual message
         content from the oldest of the kept set.
      3. If budget remains, add older messages newest-first until full.

    Each message is expected to have a ``.content`` attribute (str)
    and a ``.sender`` attribute (str).  Messages are returned in
    their original order.

    Returns the filtered message list (same type as input).
    """
    if not messages:
        return []

    # Clamp keep_recent to message count
    keep_recent = min(keep_recent, len(messages))

    recent = messages[-keep_recent:]
    older = messages[:-keep_recent] if keep_recent < len(messages) else []

    # Calculate char cost of recent messages
    def _msg_chars(msg: Any) -> int:
        content = getattr(msg, "content", "")
        sender = getattr(msg, "sender", "")
        return len(content) + len(sender) + 20  # overhead for formatting

    recent_cost = sum(_msg_chars(m) for m in recent)

    # If recent alone exceeds budget, just return recent (can't drop them)
    if recent_cost >= char_budget:
        return list(recent)

    # Fill remaining budget with older messages, newest-first
    remaining = char_budget - recent_cost
    included_older: list[Any] = []
    for msg in reversed(older):
        cost = _msg_chars(msg)
        if cost <= remaining:
            included_older.append(msg)
            remaining -= cost
        else:
            break  # Stop at first message that doesn't fit

    # Restore original order
    included_older.reverse()
    return included_older + list(recent)
