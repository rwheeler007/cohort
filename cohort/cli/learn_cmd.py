"""cohort learn -- conversation learning and user profile CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_profile(profile: dict) -> str:
    """Pretty-print user profile."""
    lines: list[str] = [
        "\n  User Profile",
        "  " + "-" * 50,
    ]
    lines.append(f"  Version:  {profile.get('version', '?')}")
    updated = profile.get("last_updated", "?")
    lines.append(f"  Updated:  {updated[:10] if len(updated) >= 10 else updated}")

    paragraph = profile.get("core_paragraph", "")
    if paragraph:
        lines.append(f"\n  {paragraph[:200]}")

    adaptations = profile.get("adaptation_rules", {})
    if adaptations:
        lines.append("\n  Adaptation Rules:")
        for k, v in adaptations.items():
            if k == "custom_rules":
                if v:
                    lines.append(f"    custom_rules: {len(v)} rules")
            else:
                lines.append(f"    {k}: {v}")

    return "\n".join(lines)


def _format_learning_config(config: dict) -> str:
    """Pretty-print learning system configuration."""
    lines: list[str] = [
        "\n  Learning System Status",
        "  " + "-" * 50,
    ]
    lines.append(f"  Enabled:              {config.get('enabled', '?')}")
    lines.append(f"  Min response length:  {config.get('min_response_length', '?')} chars")
    lines.append(f"  Gate threshold:       {config.get('gate_threshold', '?')} signals")
    lines.append(f"  Dedup threshold:      {config.get('dedup_threshold', '?')}")
    lines.append(f"  Max facts per agent:  {config.get('max_facts_per_agent', '?')}")
    lines.append(f"  Profile evolve days:  {config.get('profile_evolve_days', '?')}")
    lines.append(f"  Profile min prefs:    {config.get('profile_min_new_prefs', '?')}")
    lines.append(f"  Skip agents:          {', '.join(config.get('skip_agents', []))}")

    profile_exists = config.get("profile_exists", False)
    lines.append(f"\n  Profile file exists:  {profile_exists}")
    lines.append(f"  Profile path:         {config.get('profile_path', '?')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_learn_profile(args: argparse.Namespace) -> int:
    """Show the current user profile."""
    from cohort.learning import load_profile

    profile = load_profile()
    if profile is None:
        print("  No user profile found.")
        print("  Create one: python -m cohort learn bootstrap --name 'Your Name' --role 'Your Role'")
        return 0

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(profile, json_flag=True)
    else:
        print(_format_profile(profile))
    return 0


def _cmd_learn_bootstrap(args: argparse.Namespace) -> int:
    """Create initial user profile."""
    from cohort.learning import bootstrap_profile, load_profile

    existing = load_profile()
    if existing and not getattr(args, "force", False):
        print("  [!] Profile already exists. Use --force to overwrite.")
        print(f"      Name: {existing.get('display_name', '?')}")
        return 1

    name = args.name
    role = args.role
    paragraph = getattr(args, "paragraph", "") or ""

    profile = bootstrap_profile(
        display_name=name,
        display_role=role,
        core_paragraph=paragraph,
    )

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(profile, json_flag=True)
    else:
        print(f"  [OK] Created user profile for: {name}")
        print(f"       Role: {role}")
    return 0


def _cmd_learn_status(args: argparse.Namespace) -> int:
    """Show learning system configuration and status."""
    from cohort.learning import _PROFILE_PATH
    from cohort.local.config import (
        LEARNING_DEDUP_THRESHOLD,
        LEARNING_ENABLED,
        LEARNING_GATE_THRESHOLD,
        LEARNING_MAX_FACTS_PER_AGENT,
        LEARNING_MIN_RESPONSE_LENGTH,
        LEARNING_PROFILE_EVOLVE_DAYS,
        LEARNING_PROFILE_MIN_NEW_PREFS,
        LEARNING_SKIP_AGENTS,
    )

    config = {
        "enabled": LEARNING_ENABLED,
        "min_response_length": LEARNING_MIN_RESPONSE_LENGTH,
        "gate_threshold": LEARNING_GATE_THRESHOLD,
        "dedup_threshold": LEARNING_DEDUP_THRESHOLD,
        "max_facts_per_agent": LEARNING_MAX_FACTS_PER_AGENT,
        "profile_evolve_days": LEARNING_PROFILE_EVOLVE_DAYS,
        "profile_min_new_prefs": LEARNING_PROFILE_MIN_NEW_PREFS,
        "skip_agents": list(LEARNING_SKIP_AGENTS),
        "profile_exists": _PROFILE_PATH.exists(),
        "profile_path": str(_PROFILE_PATH),
    }

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(config, json_flag=True)
    else:
        print(_format_learning_config(config))
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort learn`` command group."""

    learn_parser = subparsers.add_parser("learn", help="Conversation learning and user profile")
    learn_sub = learn_parser.add_subparsers(dest="learn_command")

    # profile
    profile_parser = learn_sub.add_parser("profile", help="Show user profile (default)")
    profile_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # bootstrap
    boot_parser = learn_sub.add_parser("bootstrap", help="Create initial user profile")
    boot_parser.add_argument("--name", required=True, help="Display name")
    boot_parser.add_argument("--role", required=True, help="Role/title")
    boot_parser.add_argument("--paragraph", "-p", help="Core description paragraph")
    boot_parser.add_argument("--force", action="store_true", help="Overwrite existing profile")
    boot_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # status
    status_parser = learn_sub.add_parser("status", help="Show learning system status/config")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")

    learn_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch learn commands."""
    sub = getattr(args, "learn_command", None)
    if sub == "profile" or sub is None:
        return _cmd_learn_profile(args)
    elif sub == "bootstrap":
        return _cmd_learn_bootstrap(args)
    elif sub == "status":
        return _cmd_learn_status(args)
    else:
        print(f"Unknown learn subcommand: {sub}", file=sys.stderr)
        return 1
