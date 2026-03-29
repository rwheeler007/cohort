"""cohort profile -- user profile management."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cohort.cli._base import format_output


def _settings_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "settings.json"


def _load_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_settings(settings: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_profile_show(args: argparse.Namespace) -> int:
    """Show user profile."""
    settings = _load_settings()
    profile = settings.get("user_profile", {})

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(profile, json_flag=True)
    else:
        if not profile:
            print("  No user profile configured.")
            print("  Set one: python -m cohort profile set name \"Your Name\"")
        else:
            print("\n  User Profile")
            print("  " + "-" * 40)
            for k, v in sorted(profile.items()):
                print(f"  {k:20s}  {v}")

    return 0


def _cmd_profile_set(args: argparse.Namespace) -> int:
    """Set a profile field."""
    settings = _load_settings()
    profile = settings.setdefault("user_profile", {})

    profile[args.field] = args.value
    _save_settings(settings)

    print(f"  [OK] Profile: {args.field} = {args.value}")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort profile`` command."""

    profile_parser = subparsers.add_parser("profile", help="User profile management")
    profile_sub = profile_parser.add_subparsers(dest="profile_command")

    show_parser = profile_sub.add_parser("show", help="Show user profile")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")

    set_parser = profile_sub.add_parser("set", help="Set a profile field")
    set_parser.add_argument("field", help="Field name (e.g. name, avatar, role)")
    set_parser.add_argument("value", help="Field value")

    profile_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch profile commands."""
    sub = getattr(args, "profile_command", None)
    if sub == "show" or sub is None:
        return _cmd_profile_show(args)
    elif sub == "set":
        return _cmd_profile_set(args)
    else:
        print(f"Unknown profile subcommand: {sub}", file=sys.stderr)
        return 1
