"""cohort cloud -- cloud LLM provider management CLI."""

from __future__ import annotations

import argparse
import sys

from cohort.cli._base import format_output

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_cloud_providers(args: argparse.Namespace) -> int:
    """List supported cloud LLM providers."""
    from cohort.local.cloud import _DEFAULT_MODELS, list_providers

    providers = list_providers()
    json_flag = getattr(args, "json", False)

    if json_flag:
        data = [{"provider": p, "default_model": _DEFAULT_MODELS.get(p, "?")} for p in providers]
        format_output(data, json_flag=True)
    else:
        print(f"\n  Cloud LLM Providers ({len(providers)})")
        print("  " + "-" * 45)
        for p in providers:
            model = _DEFAULT_MODELS.get(p, "?")
            print(f"    {p:<15s}  default: {model}")
    return 0


def _cmd_cloud_check(args: argparse.Namespace) -> int:
    """Check cloud API configuration."""
    from cohort.local.cloud import list_providers

    # Load settings from cohort_settings.json
    try:
        from cohort.cli.secret_cmd import _load_settings
        settings = _load_settings()
        service_keys = settings.get("service_keys", [])

        # service_keys is a list of dicts with "type" and "key" fields
        provider_keys = {}
        for entry in service_keys:
            if isinstance(entry, dict):
                svc_type = entry.get("type", "")
                provider_keys[svc_type] = entry

        results = []
        for provider in list_providers():
            entry = provider_keys.get(provider, {})
            has_key = bool(entry.get("key"))
            name = entry.get("name", provider)
            results.append({
                "provider": provider,
                "configured": has_key,
                "name": name,
                "key_preview": "[encoded]" if has_key else "not set",
            })
    except Exception:
        results = [{"provider": "unknown", "configured": False, "key_preview": "error loading settings"}]

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(results, json_flag=True)
    else:
        print("\n  Cloud API Status")
        print("  " + "-" * 45)
        for r in results:
            icon = "[OK]" if r["configured"] else "[X]"
            print(f"  {icon} {r['provider']:<15s}  {r['key_preview']}")

        configured = sum(1 for r in results if r["configured"])
        if configured == 0:
            print("\n  No cloud providers configured.")
            print("  Set one: python -m cohort secret set service:anthropic <api-key>")
    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort cloud`` command group."""

    cloud_parser = subparsers.add_parser("cloud", help="Cloud LLM provider management")
    cloud_sub = cloud_parser.add_subparsers(dest="cloud_command")

    prov_p = cloud_sub.add_parser("providers", help="List supported cloud providers")
    prov_p.add_argument("--json", action="store_true", help="Output as JSON")

    check_p = cloud_sub.add_parser("check", help="Check cloud API configuration")
    check_p.add_argument("--json", action="store_true", help="Output as JSON")

    cloud_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch cloud commands."""
    sub = getattr(args, "cloud_command", None)
    if sub == "providers":
        return _cmd_cloud_providers(args)
    elif sub == "check" or sub is None:
        return _cmd_cloud_check(args)
    else:
        print(f"Unknown cloud subcommand: {sub}", file=sys.stderr)
        return 1
