"""cohort secret -- manage API keys and secrets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cohort.cli._base import format_output

# ---------------------------------------------------------------------------
# Provider registry: documents what fields each provider needs
# ---------------------------------------------------------------------------

PROVIDER_FIELDS: dict[str, dict] = {
    "anthropic":    {"key": "API key",          "extra_fields": [],
                     "pattern": r"^sk-ant-", "min_length": 40,
                     "hint": "Starts with sk-ant-, typically 100+ characters"},
    "openai":       {"key": "API key",          "extra_fields": [],
                     "pattern": r"^sk-", "min_length": 20,
                     "hint": "Starts with sk-, typically 50+ characters"},
    "youtube":      {"key": "API key",          "extra_fields": [],
                     "pattern": r"^AIza", "min_length": 39, "max_length": 39,
                     "hint": "Starts with AIza, exactly 39 characters"},
    "github":       {"key": "Token",            "extra_fields": [],
                     "pattern": r"^(ghp_|github_pat_|gho_|ghu_|ghs_|ghr_)", "min_length": 30,
                     "hint": "Starts with ghp_, github_pat_, gho_, ghu_, ghs_, or ghr_"},
    "cloudflare":   {"key": "API token",        "extra_fields": [],
                     "min_length": 20,
                     "hint": "40-character alphanumeric token"},
    "resend":       {"key": "API key",          "extra_fields": [],
                     "pattern": r"^re_", "min_length": 10,
                     "hint": "Starts with re_"},
    "serpapi":      {"key": "API key",          "extra_fields": []},
    "serper":       {"key": "API key",          "extra_fields": []},
    "rss":          {"key": "(optional)",       "extra_fields": []},
    "google":       {"key": "API key",          "extra_fields": ["cx"],
                     "pattern": r"^AIza", "min_length": 39, "max_length": 39,
                     "hint": "Starts with AIza, exactly 39 characters"},
    "linkedin":     {"key": "Client ID",        "extra_fields": ["client_secret"],
                     "min_length": 10,
                     "hint": "OAuth 2.0 Client ID from LinkedIn Developer Portal"},
    "twitter":      {"key": "API key",          "extra_fields": ["api_secret", "bearer_token"],
                     "min_length": 20,
                     "hint": "API Key (Consumer Key) from Twitter Developer Portal"},
    "reddit":       {"key": "Client ID",        "extra_fields": ["client_secret"],
                     "min_length": 10,
                     "hint": "OAuth Client ID from Reddit app preferences"},
    "aws":          {"key": "Access Key ID",    "extra_fields": ["secret_access_key", "region"],
                     "pattern": r"^AKIA[0-9A-Z]{16}$", "min_length": 20, "max_length": 20,
                     "hint": "Starts with AKIA, exactly 20 characters"},
    "email_smtp":   {"key": "Password",         "extra_fields": ["host", "port", "username"],
                     "hint": "App-specific password (not your login password)"},
    "email_imap":   {"key": "Password",         "extra_fields": ["host", "port", "username"],
                     "hint": "App-specific password (not your login password)"},
    "slack":        {"key": "Webhook URL",      "extra_fields": [],
                     "pattern": r"^https://hooks\.slack\.com/", "min_length": 40,
                     "hint": "Full webhook URL starting with https://hooks.slack.com/"},
    "discord":      {"key": "Webhook URL",      "extra_fields": [],
                     "pattern": r"^https://(discord\.com|discordapp\.com)/api/webhooks/", "min_length": 50,
                     "hint": "Full webhook URL starting with https://discord.com/api/webhooks/"},
    "webhook":      {"key": "API key",          "extra_fields": ["url"]},
    "custom":       {"key": "API key",          "extra_fields": ["url"]},
}


def _validate_key(provider: str, value: str) -> list[str]:
    """Validate a key value against the provider's expected format.

    Returns a list of warning strings (empty = valid).
    """
    import re

    pinfo = PROVIDER_FIELDS.get(provider)
    if not pinfo:
        return []

    warnings = []
    pattern = pinfo.get("pattern")
    min_len = pinfo.get("min_length")
    max_len = pinfo.get("max_length")

    if min_len and len(value) < min_len:
        warnings.append(f"Too short ({len(value)} chars, expected {min_len}+)")
    if max_len and len(value) > max_len:
        warnings.append(f"Too long ({len(value)} chars, expected max {max_len})")
    if pattern and not re.match(pattern, value):
        hint = pinfo.get("hint", f"Expected pattern: {pattern}")
        warnings.append(f"Format mismatch -- {hint}")

    return warnings


# Settings file location
def _settings_path() -> Path:
    """Return the path to cohort settings.json."""
    return Path(__file__).resolve().parent.parent.parent / "data" / "settings.json"


def _load_settings() -> dict:
    """Load settings, returning empty dict if missing."""
    p = _settings_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_settings(settings: dict) -> None:
    """Save settings to disk."""
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _find_service_key(settings: dict, provider: str) -> dict | None:
    """Find a service key entry by type, name, or id."""
    for sk in settings.get("service_keys", []):
        if sk.get("type") == provider:
            return sk
        if sk.get("name", "").lower().startswith(provider.lower()):
            return sk
        if sk.get("id", "").startswith(provider):
            return sk
    return None


def _mask_value(val) -> str:
    """Mask a credential value for display."""
    if not val:
        return "(empty)"
    if isinstance(val, dict) and "_enc" in val:
        return "[encoded]"
    if isinstance(val, str) and len(val) > 8:
        return "****" + val[-4:]
    if isinstance(val, str):
        return "****"
    return "[encoded]"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_secret_list(args: argparse.Namespace) -> int:
    """List configured secrets (masked)."""
    settings = _load_settings()
    secrets: list[dict] = []

    # Main API key
    api_key = settings.get("api_key", "")
    if api_key:
        secrets.append({"name": "api_key", "preview": _mask_value(api_key)})

    # Cloud API key
    cloud_key = settings.get("cloud_api_key", "")
    if cloud_key:
        secrets.append({"name": "cloud_api_key", "preview": _mask_value(cloud_key)})

    # Service keys
    for sk in settings.get("service_keys", []):
        svc_type = sk.get("type", "")
        name = sk.get("name", svc_type or "unknown")
        key_val = sk.get("key", "")
        extra_raw = sk.get("extra", "")

        if not key_val and not extra_raw:
            continue

        entry: dict = {
            "name": f"service:{svc_type or name}",
            "display_name": name,
            "key": _mask_value(key_val),
        }

        # Show extra fields (masked)
        if extra_raw:
            # Might be encoded or plaintext JSON
            from cohort.secret_store import is_encoded
            if is_encoded(extra_raw):
                entry["extra"] = "[encoded]"
            elif isinstance(extra_raw, str) and extra_raw.strip().startswith("{"):
                try:
                    extra_obj = json.loads(extra_raw)
                    entry["extra"] = {k: _mask_value(v) for k, v in extra_obj.items()}
                except (json.JSONDecodeError, TypeError):
                    entry["extra"] = "[set]"
            elif extra_raw:
                entry["extra"] = "[set]"

        secrets.append(entry)

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(secrets, json_flag=True)
    else:
        if not secrets:
            print("  No secrets configured.")
        else:
            print(f"\n  Configured Secrets ({len(secrets)})")
            print("  " + "-" * 55)
            for s in secrets:
                key_preview = s.get("key", s.get("preview", ""))
                print(f"  {s['name']:28s}  key: {key_preview}")
                if "extra" in s:
                    extra = s["extra"]
                    if isinstance(extra, dict):
                        for ek, ev in extra.items():
                            print(f"  {'':28s}  {ek}: {ev}")
                    else:
                        print(f"  {'':28s}  extra: {extra}")

    return 0


def _cmd_secret_set(args: argparse.Namespace) -> int:
    """Set a secret value."""
    from cohort.secret_store import _encrypt_extra, encode_secret

    settings = _load_settings()
    name = args.name
    value = args.value
    extra_json = getattr(args, "extra", None)

    # Encode before storing
    encoded = encode_secret(value)

    if name == "api_key":
        settings["api_key"] = encoded
    elif name == "cloud_api_key":
        settings["cloud_api_key"] = encoded
    elif name.startswith("service:"):
        provider = name[len("service:"):]
        service_keys = settings.setdefault("service_keys", [])

        # Validate key format before storing
        warnings = _validate_key(provider, value)
        if warnings:
            print(f"  [!] Validation warnings for {provider}:")
            for w in warnings:
                print(f"      - {w}")
            pinfo = PROVIDER_FIELDS.get(provider)
            if pinfo and pinfo.get("hint"):
                print(f"      Expected: {pinfo['hint']}")
            print(f"      Storing anyway -- use 'cohort secret test service:{provider}' to verify connectivity.")

        # Find existing entry by type, name, or id
        sk = _find_service_key(settings, provider)
        if sk:
            sk["key"] = encoded
            if extra_json:
                sk["extra"] = _encrypt_extra(extra_json)
        else:
            new_entry: dict = {
                "id": f"{provider}_default",
                "type": provider,
                "name": provider,
                "key": encoded,
                "extra": _encrypt_extra(extra_json) if extra_json else "",
            }
            service_keys.append(new_entry)

        # Show what extra fields this provider supports
        pinfo = PROVIDER_FIELDS.get(provider)
        if pinfo and pinfo["extra_fields"] and not extra_json:
            fields = ", ".join(pinfo["extra_fields"])
            print(f"  [*] {provider} also supports extra fields: {fields}")
            print("      Use --extra '{\"field\": \"value\"}' to set them.")
    else:
        print(f"[X] Unknown secret name: {name}", file=sys.stderr)
        print("    Valid names: api_key, cloud_api_key, service:<provider>", file=sys.stderr)
        print(f"    Known providers: {', '.join(sorted(PROVIDER_FIELDS.keys()))}", file=sys.stderr)
        return 1

    _save_settings(settings)
    extra_note = " (with extra fields)" if extra_json else ""
    print(f"  [OK] Secret '{name}' stored (encoded){extra_note}")
    return 0


def _cmd_secret_remove(args: argparse.Namespace) -> int:
    """Remove a secret."""
    settings = _load_settings()
    name = args.name

    if name == "api_key":
        settings.pop("api_key", None)
    elif name == "cloud_api_key":
        settings.pop("cloud_api_key", None)
    elif name.startswith("service:"):
        provider = name[len("service:"):]
        service_keys = settings.get("service_keys", [])
        before = len(service_keys)
        settings["service_keys"] = [
            sk for sk in service_keys
            if sk.get("type") != provider
            and not sk.get("name", "").lower().startswith(provider.lower())
        ]
        if len(settings["service_keys"]) == before:
            print(f"[X] No service key found for '{provider}'.", file=sys.stderr)
            return 1
    else:
        print(f"[X] Unknown secret name: {name}", file=sys.stderr)
        return 1

    _save_settings(settings)
    print(f"  [OK] Secret '{name}' removed")
    return 0


def _cmd_secret_providers(args: argparse.Namespace) -> int:
    """List all known providers and their required fields."""
    json_flag = getattr(args, "json", False)

    if json_flag:
        format_output(PROVIDER_FIELDS, json_flag=True)
    else:
        print("\n  Known Providers")
        print("  " + "-" * 70)
        for ptype, info in sorted(PROVIDER_FIELDS.items()):
            key_desc = info["key"]
            extras = info["extra_fields"]
            extras_str = f"  extra: {', '.join(extras)}" if extras else ""
            hint = info.get("hint", "")
            hint_str = f"\n  {'':16s}  {hint}" if hint else ""
            print(f"  {ptype:16s}  key = {key_desc}{extras_str}{hint_str}")
        print()
        print("  Usage: cohort secret set service:<provider> <key_value>")
        print("         cohort secret set service:<provider> <key_value> --extra '{\"field\": \"val\"}'")

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort secret`` command group."""

    secret_parser = subparsers.add_parser("secret", help="Manage API keys and secrets")
    secret_sub = secret_parser.add_subparsers(dest="secret_command")

    # list
    list_parser = secret_sub.add_parser("list", help="List configured secrets (masked)")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # set
    set_parser = secret_sub.add_parser("set", help="Store a secret (encoded at rest)")
    set_parser.add_argument("name", help="Secret name: api_key, cloud_api_key, or service:<provider>")
    set_parser.add_argument("value", help="Main credential value (will be encoded)")
    set_parser.add_argument(
        "--extra", default=None,
        help='JSON string with extra fields, e.g. \'{"secret_access_key": "...", "region": "us-east-1"}\'',
    )

    # remove
    remove_parser = secret_sub.add_parser("remove", help="Remove a secret")
    remove_parser.add_argument("name", help="Secret name to remove")

    # providers
    providers_parser = secret_sub.add_parser("providers", help="List all known providers and required fields")
    providers_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Default for bare 'cohort secret'
    secret_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch secret commands."""
    sub = getattr(args, "secret_command", None)
    if sub == "list" or sub is None:
        return _cmd_secret_list(args)
    elif sub == "set":
        return _cmd_secret_set(args)
    elif sub == "remove":
        return _cmd_secret_remove(args)
    elif sub == "providers":
        return _cmd_secret_providers(args)
    else:
        print(f"Unknown secret subcommand: {sub}", file=sys.stderr)
        return 1
