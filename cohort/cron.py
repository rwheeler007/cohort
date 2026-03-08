"""Minimal cron expression parser.

Supports the standard 5-field cron format::

    minute hour day-of-month month day-of-week
    *      *    *            *     *

Field syntax:
    - ``*``       any value
    - ``5``       exact value
    - ``1-5``     range (inclusive)
    - ``1,3,5``   list
    - ``*/15``    step (every 15th)
    - ``1-5/2``   range with step

Day-of-week: 0=Sunday, 1=Monday, ..., 6=Saturday (also 7=Sunday).

No external dependencies -- stdlib only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Set


def _parse_field(expr: str, min_val: int, max_val: int) -> Set[int]:
    """Parse a single cron field into a set of valid integer values."""
    values: Set[int] = set()

    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue

        # Handle step: */N or range/N
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            try:
                step = int(step_str)
            except ValueError:
                raise ValueError(f"Invalid step: {part}")
            if step < 1:
                raise ValueError(f"Step must be >= 1: {part}")
        else:
            base = part

        # Handle base: *, N, or N-M
        if base == "*":
            start, end = min_val, max_val
        elif "-" in base:
            lo_str, hi_str = base.split("-", 1)
            try:
                start, end = int(lo_str), int(hi_str)
            except ValueError:
                raise ValueError(f"Invalid range: {part}")
        else:
            try:
                val = int(base)
                if step > 1 and "/" in part:
                    # e.g. "5/10" means starting at 5, every 10th
                    start, end = val, max_val
                else:
                    values.add(val)
                    continue
            except ValueError:
                raise ValueError(f"Invalid cron field: {part}")

        # Clamp to valid range
        start = max(start, min_val)
        end = min(end, max_val)

        for v in range(start, end + 1, step):
            values.add(v)

    return values


def parse_cron(expr: str) -> dict:
    """Parse a 5-field cron expression into sets of matching values.

    Returns
    -------
    dict
        Keys: ``minute``, ``hour``, ``dom`` (day of month),
        ``month``, ``dow`` (day of week).  Each value is a
        ``set[int]`` of matching values.

    Raises
    ------
    ValueError
        If the expression is malformed.
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"Cron expression must have exactly 5 fields "
            f"(minute hour dom month dow), got {len(fields)}: {expr!r}"
        )

    minute_str, hour_str, dom_str, month_str, dow_str = fields

    result = {
        "minute": _parse_field(minute_str, 0, 59),
        "hour": _parse_field(hour_str, 0, 23),
        "dom": _parse_field(dom_str, 1, 31),
        "month": _parse_field(month_str, 1, 12),
        "dow": _parse_field(dow_str, 0, 7),  # 0 and 7 both = Sunday
    }

    # Normalize: treat 7 as 0 (both mean Sunday)
    if 7 in result["dow"]:
        result["dow"].add(0)
        result["dow"].discard(7)

    return result


def cron_matches(parsed: dict, dt: datetime) -> bool:
    """Check if a datetime matches a parsed cron expression."""
    return (
        dt.minute in parsed["minute"]
        and dt.hour in parsed["hour"]
        and dt.day in parsed["dom"]
        and dt.month in parsed["month"]
        and dt.weekday() in _iso_to_cron_dow(dt)
    )


def _iso_to_cron_dow(dt: datetime) -> Set[int]:
    """Convert Python weekday (0=Mon) to cron dow set for matching.

    Python: 0=Monday ... 6=Sunday
    Cron:   0=Sunday ... 6=Saturday
    """
    # Convert Python weekday to cron weekday
    cron_dow = (dt.weekday() + 1) % 7  # Mon=1, Tue=2, ..., Sun=0
    return {cron_dow}


def next_cron_time(
    parsed: dict,
    after: datetime,
    max_iterations: int = 527040,  # ~1 year of minutes
) -> Optional[datetime]:
    """Find the next datetime matching the cron expression after ``after``.

    Returns None if no match found within max_iterations minutes.
    """
    # Start from the next whole minute
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    for _ in range(max_iterations):
        # Check month first (skip entire months if no match)
        if candidate.month not in parsed["month"]:
            # Jump to first day of next month
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                candidate = candidate.replace(month=candidate.month + 1, day=1, hour=0, minute=0)
            continue

        if candidate.day not in parsed["dom"]:
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue

        # Check day-of-week
        cron_dow = (candidate.weekday() + 1) % 7
        if cron_dow not in parsed["dow"]:
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue

        if candidate.hour not in parsed["hour"]:
            candidate = candidate.replace(minute=0) + timedelta(hours=1)
            continue

        if candidate.minute not in parsed["minute"]:
            candidate += timedelta(minutes=1)
            continue

        return candidate

    return None


def compute_next_run(schedule_type: str, schedule_expr: str, now: Optional[datetime] = None) -> Optional[str]:
    """Compute the next run time for a schedule.

    Parameters
    ----------
    schedule_type:
        "once", "interval", or "cron"
    schedule_expr:
        Integer seconds for interval, cron string for cron,
        ISO timestamp for once.
    now:
        Current time (defaults to UTC now).

    Returns
    -------
    str or None
        ISO timestamp of next run, or None if schedule is exhausted (one-shot already ran).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if schedule_type == "interval":
        interval_secs = int(schedule_expr)
        return (now + timedelta(seconds=interval_secs)).isoformat()

    elif schedule_type == "cron":
        parsed = parse_cron(schedule_expr)
        nxt = next_cron_time(parsed, now)
        return nxt.isoformat() if nxt else None

    elif schedule_type == "once":
        # One-shot: no next run after execution
        return None

    return None


# -- Human-readable preset helpers -------------------------------------------

PRESETS = {
    "every_5_min": ("interval", "300"),
    "every_15_min": ("interval", "900"),
    "every_30_min": ("interval", "1800"),
    "every_hour": ("interval", "3600"),
    "every_2_hours": ("interval", "7200"),
    "every_6_hours": ("interval", "21600"),
    "every_12_hours": ("interval", "43200"),
    "daily_9am": ("cron", "0 9 * * *"),
    "daily_midnight": ("cron", "0 0 * * *"),
    "weekdays_9am": ("cron", "0 9 * * 1-5"),
    "weekly_monday": ("cron", "0 9 * * 1"),
    "weekly_friday": ("cron", "0 9 * * 5"),
    "monthly_first": ("cron", "0 9 1 * *"),
}


def resolve_preset(preset_name: str) -> tuple[str, str]:
    """Resolve a preset name to (schedule_type, schedule_expr).

    Raises ValueError if preset is unknown.
    """
    if preset_name not in PRESETS:
        raise ValueError(
            f"Unknown preset: {preset_name}. "
            f"Available: {', '.join(sorted(PRESETS))}"
        )
    return PRESETS[preset_name]


def preset_label(preset_name: str) -> str:
    """Return a human-readable label for a preset."""
    labels = {
        "every_5_min": "Every 5 minutes",
        "every_15_min": "Every 15 minutes",
        "every_30_min": "Every 30 minutes",
        "every_hour": "Every hour",
        "every_2_hours": "Every 2 hours",
        "every_6_hours": "Every 6 hours",
        "every_12_hours": "Every 12 hours",
        "daily_9am": "Daily at 9:00 AM",
        "daily_midnight": "Daily at midnight",
        "weekdays_9am": "Weekdays at 9:00 AM",
        "weekly_monday": "Weekly on Monday",
        "weekly_friday": "Weekly on Friday",
        "monthly_first": "Monthly (1st at 9:00 AM)",
    }
    return labels.get(preset_name, preset_name)
