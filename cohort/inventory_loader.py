"""Unified inventory loader — merges three data sources into one list.

Sources:
    1. VS Code extension project registry (~/.claude/cohort-vscode-settings.json)
    2. CLAUDE.md Exports tables from registered projects
    3. BOSS YAML inventories (tool_inventory.yaml, project_inventory.yaml, INDEX.yaml)

The merged result is a deduplicated list of InventoryEntry objects, ready for
LLM-scored querying or JSON serialization.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from cohort.inventory_schema import InventoryEntry, today_iso

logger = logging.getLogger(__name__)

# ── Settings file location ───────────────────────────────────────────────

_VSCODE_SETTINGS_PATH = Path.home() / ".claude" / "cohort-vscode-settings.json"


# =====================================================================
# Source 1: VS Code extension project registry
# =====================================================================

def load_registry(settings_path: Path | None = None) -> list[InventoryEntry]:
    """Read the extension's project_registry and convert to inventory entries.

    Each registered project becomes a type=project entry. This is lightweight --
    it just records what projects exist and where. Exports are loaded separately.
    """
    path = settings_path or _VSCODE_SETTINGS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.debug("Could not read registry at %s: %s", path, exc)
        return []

    registry = data.get("project_registry", [])
    entries: list[InventoryEntry] = []
    for proj in registry:
        proj_path = proj.get("path", "")
        entries.append(InventoryEntry(
            id=proj.get("name", Path(proj_path).name if proj_path else "unknown"),
            source_project=proj.get("name", ""),
            entry_point=proj_path,
            keywords=[],  # projects get keywords from their Exports
            description=f"Registered project ({proj.get('profile', 'developer')} profile)",
            type="project",
            status="active",
            last_verified=today_iso(),
        ))
    return entries


# =====================================================================
# Source 2: CLAUDE.md Exports tables
# =====================================================================

# Matches markdown table rows: | capability | entry_point | description |
_TABLE_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*`?([^|`]+?)`?\s*\|\s*([^|]+?)\s*\|",
    re.MULTILINE,
)

# Skip header separator rows like |---|---|---|
_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$", re.MULTILINE)


def load_exports(project_path: str, project_name: str = "") -> list[InventoryEntry]:
    """Parse the ## Exports table from a project's CLAUDE.md.

    Expected table format (standard in the BOSS ecosystem):
        | Capability | Entry Point | What It Does |
        |------------|-------------|--------------|
        | llm-router | `tools/llm_router/llm_router.py` | Unified LLM routing |
    """
    claude_md = Path(project_path) / "CLAUDE.md"
    if not claude_md.exists():
        return []

    try:
        content = claude_md.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not read %s: %s", claude_md, exc)
        return []

    # Find the ## Exports section
    exports_match = re.search(r"^## Exports\b", content, re.MULTILINE)
    if not exports_match:
        return []

    # Extract from Exports heading to the next ## heading (or EOF)
    start = exports_match.end()
    next_heading = re.search(r"^## ", content[start:], re.MULTILINE)
    section = content[start:start + next_heading.start()] if next_heading else content[start:]

    name = project_name or Path(project_path).name
    entries: list[InventoryEntry] = []

    for match in _TABLE_ROW_RE.finditer(section):
        capability = match.group(1).strip()
        entry_point = match.group(2).strip()
        description = match.group(3).strip()

        # Skip header rows and separator rows
        if capability.lower() in ("capability", "what", "name"):
            continue
        if _SEPARATOR_RE.match(match.group(0)):
            continue
        if set(capability) <= {"-", " ", ":"}:
            continue

        entries.append(InventoryEntry(
            id=_slugify(capability),
            source_project=name,
            entry_point=entry_point,
            keywords=_extract_keywords(capability, description),
            description=description,
            type="export",
            status="active",
            last_verified=today_iso(),
        ))

    return entries


def _slugify(text: str) -> str:
    """Convert 'LLM Router' or 'llm-router' to 'llm-router'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _extract_keywords(capability: str, description: str) -> list[str]:
    """Pull meaningful keywords from capability name and description."""
    noise = frozenset("the a an is are was for of and or but with from by to in on".split())
    words = re.findall(r"[a-z0-9]+", f"{capability} {description}".lower())
    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        if w not in noise and len(w) > 2 and w not in seen:
            seen.add(w)
            result.append(w)
    return result[:12]


# =====================================================================
# Source 3: BOSS YAML inventories
# =====================================================================

def load_yaml_inventories(boss_root: str | Path | None = None) -> list[InventoryEntry]:
    """Read tool_inventory.yaml, project_inventory.yaml, and golden_patterns/INDEX.yaml.

    Returns entries from all three files. If boss_root is not provided or files
    are missing, returns an empty list (best-effort).
    """
    if boss_root is None:
        # Try common locations
        for candidate in [Path("G:/BOSS"), Path.home() / "BOSS"]:
            if (candidate / "data" / "tool_inventory.yaml").exists():
                boss_root = candidate
                break
        if boss_root is None:
            boss_root_env = os.environ.get("BOSS_ROOT")
            if boss_root_env:
                boss_root = Path(boss_root_env)
            else:
                return []

    boss_root = Path(boss_root)
    entries: list[InventoryEntry] = []

    # Try to use PyYAML, fall back to simple parser
    yaml_load = _get_yaml_loader()

    inventory_files = [
        (boss_root / "data" / "tool_inventory.yaml", "tool", "BOSS"),
        (boss_root / "data" / "project_inventory.yaml", "project", "BOSS"),
        (boss_root / "golden_patterns" / "INDEX.yaml", "pattern", "BOSS"),
    ]

    for filepath, entry_type, source in inventory_files:
        if not filepath.exists():
            logger.debug("Inventory file not found: %s", filepath)
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
            items = yaml_load(content)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                entries.append(InventoryEntry(
                    id=item.get("id", ""),
                    source_project=item.get("source_project", source),
                    entry_point=item.get("path", item.get("core_doc", "")),
                    keywords=item.get("keywords", []),
                    description=item.get("description", item.get("use_when", "")),
                    type=entry_type,
                    status="active",
                    last_verified=today_iso(),
                ))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", filepath, exc)

    return entries


def _get_yaml_loader():
    """Return a YAML loader function. Tries PyYAML first, falls back to basic parser."""
    try:
        import yaml
        return lambda content: yaml.safe_load(content)
    except ImportError:
        return _basic_yaml_list_loader


def _basic_yaml_list_loader(content: str) -> list[dict]:
    """Minimal YAML list-of-dicts parser for when PyYAML isn't available.

    Handles the simple format used by our inventory files:
        - id: foo
          keywords: [a, b, c]
          description: "something"
    """
    items: list[dict] = []
    current: dict[str, Any] = {}

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- "):
            if current:
                items.append(current)
            current = {}
            stripped = stripped[2:]

        if ":" in stripped and not stripped.startswith("-"):
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            # Parse inline lists: [a, b, c]
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]

            current[key] = value

    if current:
        items.append(current)

    return items


# =====================================================================
# Merge
# =====================================================================

def load_merged_inventory(
    settings_path: Path | None = None,
    boss_root: str | Path | None = None,
) -> list[InventoryEntry]:
    """Load all three sources and return a deduplicated merged list.

    Deduplication key: (type, id). If duplicates exist, the first occurrence
    wins (registry > exports > YAML, since registry is freshest).
    """
    all_entries: list[InventoryEntry] = []

    # Source 1: Extension registry (project entries)
    all_entries.extend(load_registry(settings_path))

    # Source 2: Exports from registered projects
    try:
        path = settings_path or _VSCODE_SETTINGS_PATH
        data = json.loads(path.read_text(encoding="utf-8"))
        registry = data.get("project_registry", [])
        for proj in registry:
            proj_path = proj.get("path", "")
            if proj.get("has_exports") and proj_path:
                all_entries.extend(load_exports(proj_path, proj.get("name", "")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass  # Registry already logged in load_registry

    # Source 3: BOSS YAML inventories
    all_entries.extend(load_yaml_inventories(boss_root))

    # Deduplicate by (type, id) — case-insensitive on id
    seen: set[tuple[str, str]] = set()
    merged: list[InventoryEntry] = []
    for entry in all_entries:
        key = (entry.type, entry.id.lower())
        if key not in seen and entry.id:
            seen.add(key)
            merged.append(entry)

    logger.info("Loaded %d inventory entries (%d after dedup)", len(all_entries), len(merged))
    return merged
