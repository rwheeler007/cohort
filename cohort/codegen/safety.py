"""Write safety and path validation for codegen.

Hard-floor protection that cannot be overridden by callers.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

# =====================================================================
# Forbidden patterns -- NEVER writable regardless of caller whitelist
# =====================================================================

FORBIDDEN_PATTERNS: list[str] = [
    "*.env",
    "*.env.*",
    ".env",
    ".env.*",
    "*credential*",
    "*secret*",
    "*.pem",
    "*.key",
    "*.p12",
    ".git/*",
    ".git",
    "node_modules/*",
    "__pycache__/*",
    "*.pyc",
    "*.pyo",
    "venv/*",
    ".venv/*",
]


def is_forbidden(path: str) -> bool:
    """Check if a path matches any forbidden pattern.

    Uses case-insensitive matching. Checks both the full path
    and the filename component.
    """
    path_lower = path.lower().replace("\\", "/")
    name_lower = PurePosixPath(path_lower).name

    for pattern in FORBIDDEN_PATTERNS:
        pattern_lower = pattern.lower()
        if fnmatch.fnmatch(path_lower, pattern_lower):
            return True
        if fnmatch.fnmatch(name_lower, pattern_lower):
            return True
        # Also check if any path segment matches directory patterns
        if pattern_lower.endswith("/*"):
            dir_name = pattern_lower[:-2]
            if f"/{dir_name}/" in f"/{path_lower}/" or path_lower.startswith(f"{dir_name}/"):
                return True
    return False


def validate_target_path(
    path: str,
    project_root: str,
    allowed_paths: list[str] | None = None,
) -> str | None:
    """Validate a target file path for safety.

    Returns:
        None if the path is safe, or an error message if rejected.
    """
    # Reject path traversal
    if ".." in path:
        return f"Path traversal rejected: {path}"

    # Reject absolute paths
    if Path(path).is_absolute():
        return f"Absolute path rejected: {path}"

    # Check hard-floor forbidden patterns
    if is_forbidden(path):
        return f"Forbidden pattern match: {path}"

    # Resolve and verify it stays within project_root
    resolved = (Path(project_root) / path).resolve()
    root_resolved = Path(project_root).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return f"Path escapes project root: {path}"

    # Check caller whitelist (if provided)
    if allowed_paths:
        path_normalized = path.replace("\\", "/")
        in_whitelist = any(
            path_normalized.startswith(ap.replace("\\", "/"))
            for ap in allowed_paths
        )
        if not in_whitelist:
            return f"Path not in allowed_paths: {path}"

    return None


def validate_all_targets(
    paths: list[str],
    project_root: str,
    allowed_paths: list[str] | None = None,
) -> list[str]:
    """Validate all target paths. Returns list of error messages (empty = all safe)."""
    errors: list[str] = []
    for p in paths:
        err = validate_target_path(p, project_root, allowed_paths)
        if err:
            errors.append(err)
    return errors
