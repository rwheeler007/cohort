"""cohort model -- local model management CLI."""

from __future__ import annotations

import argparse
import json
import sys

from cohort.cli._base import format_output, require_ollama

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_models(models: list[str]) -> str:
    """Pretty-print model list from Ollama."""
    if not models:
        return "  No models installed."

    lines: list[str] = [
        f"\n  Installed Models ({len(models)})",
        "  " + "-" * 50,
    ]
    for m in sorted(models):
        lines.append(f"    {m}")
    return "\n".join(lines)


def _format_tier_settings(settings: dict) -> str:
    """Pretty-print tier model assignments."""
    lines: list[str] = [
        "\n  Response Tier Settings",
        "  " + "-" * 50,
    ]
    for tier in ("smart", "smarter", "smartest"):
        tier_info = settings.get(tier, {})
        primary = tier_info.get("primary", "?")
        fallback = tier_info.get("fallback") or "none"
        label = {"smart": "[S]  Smart", "smarter": "[S+] Smarter", "smartest": "[S++] Smartest"}.get(tier, tier)
        lines.append(f"  {label}")
        lines.append(f"    Primary:  {primary}")
        lines.append(f"    Fallback: {fallback}")
    return "\n".join(lines)


def _format_budget(budget: dict) -> str:
    """Pretty-print budget limits."""
    lines: list[str] = [
        "\n  Token Budget Limits",
        "  " + "-" * 50,
    ]
    daily = budget.get("daily_token_limit", 0)
    monthly = budget.get("monthly_token_limit", 0)
    hourly = budget.get("escalation_per_hour", 0)
    lines.append(f"  Daily token limit:     {daily:,}")
    lines.append(f"  Monthly token limit:   {monthly:,}")
    lines.append(f"  Escalations per hour:  {hourly}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_model_list(args: argparse.Namespace) -> int:
    """List installed Ollama models."""
    if not require_ollama():
        return 1

    from cohort.local.ollama import OllamaClient

    client = OllamaClient()
    models = client.list_models()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(models, json_flag=True)
    else:
        print(_format_models(models))

    return 0


def _cmd_model_pull(args: argparse.Namespace) -> int:
    """Pull a model from Ollama."""
    if not require_ollama():
        return 1

    import urllib.request

    model_name = args.model_name
    print(f"  Pulling {model_name}...")

    try:
        body = json.dumps({"name": model_name}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/pull",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            for line in resp:
                try:
                    data = json.loads(line.decode("utf-8"))
                    status = data.get("status", "")
                    if "completed" in status.lower() or status == "success":
                        print(f"  [OK] {model_name} pulled successfully.")
                        return 0
                    # Show progress for download
                    total = data.get("total", 0)
                    completed = data.get("completed", 0)
                    if total:
                        pct = completed / total * 100
                        print(f"\r  {status}: {pct:.0f}%", end="", flush=True)
                    elif status:
                        print(f"  {status}")
                except json.JSONDecodeError:
                    pass
        print(f"\n  [OK] {model_name} pull complete.")
        return 0
    except Exception as e:
        print(f"  [X] Failed to pull {model_name}: {e}", file=sys.stderr)
        return 1


def _cmd_model_remove(args: argparse.Namespace) -> int:
    """Remove a model from Ollama."""
    if not require_ollama():
        return 1

    import urllib.request

    model_name = args.model_name

    try:
        body = json.dumps({"name": model_name}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/delete",
            data=body,
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 200:
                print(f"  [OK] Removed {model_name}.")
                return 0
    except Exception as e:
        print(f"  [X] Failed to remove {model_name}: {e}", file=sys.stderr)
        return 1
    return 1


def _cmd_model_tier(args: argparse.Namespace) -> int:
    """Show or set tier model assignments."""
    from cohort.local.config import get_tier_settings, save_tier_settings

    tier_sub = getattr(args, "tier_command", None)

    if tier_sub == "set":
        tier = args.tier
        model = args.model
        settings = get_tier_settings()
        if tier not in settings:
            print(f"  [X] Unknown tier: {tier}. Use smart, smarter, or smartest.", file=sys.stderr)
            return 1
        settings[tier]["primary"] = model
        fallback = getattr(args, "fallback", None)
        if fallback:
            settings[tier]["fallback"] = fallback
        if save_tier_settings(settings):
            print(f"  [OK] Set {tier} primary to: {model}")
            if fallback:
                print(f"       Fallback: {fallback}")
            return 0
        else:
            print("  [X] Failed to save tier settings.", file=sys.stderr)
            return 1
    else:
        # Default: show
        settings = get_tier_settings()
        json_flag = getattr(args, "json", False)
        if json_flag:
            format_output(settings, json_flag=True)
        else:
            print(_format_tier_settings(settings))
        return 0


def _cmd_model_budget(args: argparse.Namespace) -> int:
    """Show or set token budget limits."""
    from cohort.local.config import TIER_SETTINGS_PATH, get_budget_limits

    budget_sub = getattr(args, "budget_command", None)

    if budget_sub == "set":
        # Load existing settings, update budget section
        try:
            if TIER_SETTINGS_PATH.is_file():
                data = json.loads(TIER_SETTINGS_PATH.read_text(encoding="utf-8"))
            else:
                data = {}
        except (json.JSONDecodeError, OSError):
            data = {}

        budget = data.get("budget", {})
        daily = getattr(args, "daily", None)
        monthly = getattr(args, "monthly", None)
        hourly = getattr(args, "hourly", None)

        if daily is not None:
            budget["daily_token_limit"] = daily
        if monthly is not None:
            budget["monthly_token_limit"] = monthly
        if hourly is not None:
            budget["escalation_per_hour"] = hourly

        if not any(x is not None for x in (daily, monthly, hourly)):
            print("  [X] Provide at least one of --daily, --monthly, or --hourly.", file=sys.stderr)
            return 1

        data["budget"] = budget
        try:
            TIER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            TIER_SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("  [OK] Budget limits updated.")
            for k, v in budget.items():
                print(f"    {k}: {v:,}")
            return 0
        except OSError as e:
            print(f"  [X] Failed to save: {e}", file=sys.stderr)
            return 1
    else:
        # Default: show
        budget = get_budget_limits()
        json_flag = getattr(args, "json", False)
        if json_flag:
            format_output(budget, json_flag=True)
        else:
            print(_format_budget(budget))
        return 0


def _cmd_model_recommend(args: argparse.Namespace) -> int:
    """Recommend a model based on available VRAM."""
    from cohort.local.config import get_model_for_vram
    from cohort.local.detect import detect_hardware

    vram = getattr(args, "vram", None)
    if vram is None:
        info = detect_hardware()
        vram = getattr(info, "total_vram_free_mb", 0)
        if not vram:
            vram = getattr(info, "total_vram_mb", 0)
        print(f"  Detected free VRAM: {vram:,} MB")

    model = get_model_for_vram(vram)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output({"vram_mb": vram, "recommended_model": model}, json_flag=True)
    else:
        print(f"  Recommended model for {vram:,} MB VRAM: {model}")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort model`` command group."""

    model_parser = subparsers.add_parser("model", help="Local model management")
    model_sub = model_parser.add_subparsers(dest="model_command")

    # list
    list_parser = model_sub.add_parser("list", help="List installed Ollama models")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # pull
    pull_parser = model_sub.add_parser("pull", help="Pull a model from Ollama")
    pull_parser.add_argument("model_name", help="Model name (e.g., qwen3.5:9b)")

    # remove
    rm_parser = model_sub.add_parser("remove", help="Remove a model from Ollama")
    rm_parser.add_argument("model_name", help="Model name to remove")

    # tier
    tier_parser = model_sub.add_parser("tier", help="Show or set response tier model assignments")
    tier_sub = tier_parser.add_subparsers(dest="tier_command")

    tier_show_parser = tier_sub.add_parser("show", help="Show current tier settings (default)")
    tier_show_parser.add_argument("--json", action="store_true", help="Output as JSON")

    tier_set_parser = tier_sub.add_parser("set", help="Set a tier's primary model")
    tier_set_parser.add_argument("tier", choices=["smart", "smarter", "smartest"], help="Tier name")
    tier_set_parser.add_argument("model", help="Model name")
    tier_set_parser.add_argument("--fallback", help="Fallback model or strategy")

    tier_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # budget
    budget_parser = model_sub.add_parser("budget", help="Show or set token budget limits")
    budget_sub = budget_parser.add_subparsers(dest="budget_command")

    budget_show_parser = budget_sub.add_parser("show", help="Show current budget limits (default)")
    budget_show_parser.add_argument("--json", action="store_true", help="Output as JSON")

    budget_set_parser = budget_sub.add_parser("set", help="Set budget limits")
    budget_set_parser.add_argument("--daily", type=int, help="Daily token limit")
    budget_set_parser.add_argument("--monthly", type=int, help="Monthly token limit")
    budget_set_parser.add_argument("--hourly", type=int, help="Escalations per hour")

    budget_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # recommend
    rec_parser = model_sub.add_parser("recommend", help="Recommend a model for available VRAM")
    rec_parser.add_argument("--vram", type=int, help="VRAM in MB (auto-detect if omitted)")
    rec_parser.add_argument("--json", action="store_true", help="Output as JSON")

    model_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch model commands."""
    sub = getattr(args, "model_command", None)
    if sub == "list" or sub is None:
        return _cmd_model_list(args)
    elif sub == "pull":
        return _cmd_model_pull(args)
    elif sub == "remove":
        return _cmd_model_remove(args)
    elif sub == "tier":
        return _cmd_model_tier(args)
    elif sub == "budget":
        return _cmd_model_budget(args)
    elif sub == "recommend":
        return _cmd_model_recommend(args)
    else:
        print(f"Unknown model subcommand: {sub}", file=sys.stderr)
        return 1
