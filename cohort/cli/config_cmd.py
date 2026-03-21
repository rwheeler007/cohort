"""cohort config -- inspect merged configuration."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _format_config(config: dict) -> str:
    """Pretty-print merged configuration."""
    lines: list[str] = [
        "\n  Cohort Configuration",
        "  " + "=" * 55,
    ]

    # Tier settings
    tiers = config.get("tiers", {})
    if tiers:
        lines.append("\n  Response Tiers")
        lines.append("  " + "-" * 50)
        for tier in ("smart", "smarter", "smartest"):
            info = tiers.get(tier, {})
            label = {"smart": "[S]  Smart", "smarter": "[S+] Smarter", "smartest": "[S++] Smartest"}.get(tier, tier)
            primary = info.get("primary", "?")
            fallback = info.get("fallback") or "none"
            lines.append(f"    {label}:  {primary}  (fallback: {fallback})")

    # Budget
    budget = config.get("budget", {})
    if budget:
        lines.append("\n  Token Budget")
        lines.append("  " + "-" * 50)
        daily = budget.get("daily_token_limit", 0)
        monthly = budget.get("monthly_token_limit", 0)
        hourly = budget.get("escalation_per_hour", 0)
        lines.append(f"    Daily:   {daily:>12,} tokens")
        lines.append(f"    Monthly: {monthly:>12,} tokens")
        lines.append(f"    Hourly:  {hourly:>12} escalations")

    # Temperatures
    temps = config.get("temperatures", {})
    if temps:
        lines.append("\n  Task Temperatures")
        lines.append("  " + "-" * 50)
        for task, temp in sorted(temps.items()):
            lines.append(f"    {task:<20s} {temp}")

    # Response mode params
    modes = config.get("response_modes", {})
    if modes:
        lines.append("\n  Response Mode Parameters")
        lines.append("  " + "-" * 50)
        for mode, params in modes.items():
            think = params.get("think", False)
            num_predict = params.get("num_predict", "?")
            lines.append(f"    {mode:<12s} think={think}  num_predict={num_predict}")

    # Secrets summary
    secrets = config.get("secrets", {})
    if secrets:
        lines.append(f"\n  Configured Secrets: {secrets.get('count', 0)} services")

    # Ollama
    ollama = config.get("ollama", {})
    if ollama:
        lines.append(f"\n  Ollama: {'reachable' if ollama.get('healthy') else 'not reachable'}"
                      f"  models: {ollama.get('model_count', 0)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def _cmd_config_show(args: argparse.Namespace) -> int:
    """Show merged configuration from all sources."""
    from cohort.local.config import (
        get_tier_settings, get_budget_limits,
        TASK_TEMPERATURES, RESPONSE_MODE_PARAMS,
    )

    config: dict = {}

    # Tiers
    config["tiers"] = get_tier_settings()

    # Budget
    config["budget"] = get_budget_limits()

    # Temperatures
    config["temperatures"] = dict(TASK_TEMPERATURES)

    # Response modes
    config["response_modes"] = {}
    for mode, params in RESPONSE_MODE_PARAMS.items():
        config["response_modes"][mode] = {
            "think": params.get("think", False),
            "num_predict": params.get("num_predict", 0),
            "keep_alive": params.get("keep_alive", ""),
        }

    # Secrets count
    try:
        from cohort.cli.secret_cmd import _load_settings
        settings = _load_settings()
        services = settings.get("service_keys", {})
        config["secrets"] = {"count": len(services)}
    except Exception:
        config["secrets"] = {"count": 0}

    # Ollama status
    try:
        from cohort.local.ollama import OllamaClient
        client = OllamaClient()
        healthy = client.health_check()
        models = client.list_models() if healthy else []
        config["ollama"] = {"healthy": healthy, "model_count": len(models)}
    except Exception:
        config["ollama"] = {"healthy": False, "model_count": 0}

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(config, json_flag=True)
    else:
        print(_format_config(config))

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort config`` command."""

    config_parser = subparsers.add_parser("config", help="Inspect merged configuration")
    config_sub = config_parser.add_subparsers(dest="config_command")

    show_parser = config_sub.add_parser("show", help="Show all config (default)")
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")

    config_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch config commands."""
    return _cmd_config_show(args)
