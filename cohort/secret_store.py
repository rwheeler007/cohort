"""Obfuscate API keys at rest in settings.json.

Keys are XOR-masked with a machine-derived key (hostname + username) then
base64-encoded.  Stored as ``{"_enc": "<base64>"}`` so load/save can
distinguish encrypted values from plain strings.

This is **obfuscation, not encryption** -- it prevents casual exposure
(someone browsing the JSON won't see raw API keys) but does not protect
against a determined attacker with filesystem access on the same machine.
For production secrets management, use environment variables or a vault.

Zero external dependencies -- stdlib only.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import platform
from typing import Any


def _derive_mask() -> bytes:
    """Derive a 64-byte repeating mask from machine-specific data."""
    seed = f"{platform.node()}:{getpass.getuser()}:cohort-secret-store"
    return hashlib.sha512(seed.encode()).digest()


def _xor_bytes(data: bytes, mask: bytes) -> bytes:
    """XOR data against a repeating mask."""
    out = bytearray(len(data))
    mask_len = len(mask)
    for i, b in enumerate(data):
        out[i] = b ^ mask[i % mask_len]
    return bytes(out)


def encode_secret(plaintext: str) -> dict[str, str]:
    """Encode a secret string into an obfuscated dict marker.

    Returns ``{"_enc": "<base64>"}`` suitable for JSON serialization.
    """
    if not plaintext:
        return {"_enc": ""}
    masked = _xor_bytes(plaintext.encode("utf-8"), _derive_mask())
    return {"_enc": base64.b64encode(masked).decode("ascii")}


def decode_secret(value: Any) -> str:
    """Decode a secret from its stored form.

    Accepts either:
      - A plain string (legacy/unencrypted) -- returned as-is.
      - A dict ``{"_enc": "<base64>"}`` -- decoded and unmasked.
      - Anything else -- returns empty string.
    """
    if isinstance(value, str):
        return value  # Legacy plaintext -- will be re-encrypted on next save
    if isinstance(value, dict) and "_enc" in value:
        encoded = value["_enc"]
        if not encoded:
            return ""
        try:
            masked = base64.b64decode(encoded)
            return _xor_bytes(masked, _derive_mask()).decode("utf-8")
        except Exception:
            return ""
    return ""


def is_encoded(value: Any) -> bool:
    """Check whether a value is already in encoded form."""
    return isinstance(value, dict) and "_enc" in value


# ---------------------------------------------------------------------------
# Helpers for settings.json migration
# ---------------------------------------------------------------------------

def _encrypt_extra(extra: Any) -> Any:
    """Encrypt the ``extra`` field of a service key entry.

    The extra field is a JSON string containing provider-specific credentials
    (e.g. ``{"secret_access_key": "...", "region": "us-east-1"}``).  We
    encrypt the *entire* JSON string as one blob so that all sub-fields
    (including client_secret, bearer_token, etc.) are protected at rest.

    Accepts and returns:
      - Empty string ``""`` -> unchanged
      - Plain JSON string   -> ``{"_enc": "<base64>"}``
      - Already encoded     -> unchanged
    """
    if not extra:
        return extra
    if is_encoded(extra):
        return extra
    if isinstance(extra, str):
        return encode_secret(extra)
    # dict -- shouldn't happen (should be JSON string), but encode it
    if isinstance(extra, dict):
        return encode_secret(json.dumps(extra))
    return extra


def _decrypt_extra(extra: Any) -> str:
    """Decrypt the ``extra`` field back to a JSON string.

    Returns:
      - Empty string if empty/missing
      - Plain JSON string (legacy or newly decoded)
    """
    if not extra:
        return ""
    if is_encoded(extra):
        return decode_secret(extra)
    if isinstance(extra, str):
        return extra  # Legacy plaintext -- will be re-encrypted on next save
    if isinstance(extra, dict):
        # Might be a raw dict (shouldn't happen), serialize it
        return json.dumps(extra)
    return ""


def encrypt_settings_secrets(settings: dict[str, Any]) -> dict[str, Any]:
    """Encrypt all known secret fields in a settings dict (in-place).

    Handles:
      - ``settings["api_key"]`` (Anthropic API key)
      - ``settings["cloud_api_key"]`` (cloud provider key for Smartest mode)
      - ``settings["service_keys"][*]["key"]`` (service credentials)
      - ``settings["service_keys"][*]["extra"]`` (provider-specific secrets)
    """
    # Main API key
    api_key = settings.get("api_key", "")
    if isinstance(api_key, str) and api_key:
        settings["api_key"] = encode_secret(api_key)

    # Cloud API key (Smartest mode)
    cloud_key = settings.get("cloud_api_key", "")
    if isinstance(cloud_key, str) and cloud_key:
        settings["cloud_api_key"] = encode_secret(cloud_key)

    # Service keys
    for svc in settings.get("service_keys", []):
        key_val = svc.get("key", "")
        if isinstance(key_val, str) and key_val:
            svc["key"] = encode_secret(key_val)
        # Extra field (may contain client_secret, bearer_token, etc.)
        extra_val = svc.get("extra", "")
        if extra_val:
            svc["extra"] = _encrypt_extra(extra_val)

    return settings


def decrypt_settings_secrets(settings: dict[str, Any]) -> dict[str, Any]:
    """Decrypt all known secret fields in a settings dict (in-place).

    Transparently handles both legacy plaintext and encoded values.
    """
    # Main API key
    settings["api_key"] = decode_secret(settings.get("api_key", ""))

    # Cloud API key (Smartest mode)
    settings["cloud_api_key"] = decode_secret(settings.get("cloud_api_key", ""))

    # Service keys
    for svc in settings.get("service_keys", []):
        svc["key"] = decode_secret(svc.get("key", ""))
        # Extra field
        svc["extra"] = _decrypt_extra(svc.get("extra", ""))

    return settings
