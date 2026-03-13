"""Cohort project manifest (.cohort file).

Every project linked to Cohort contains a .cohort file in its root.  This file:

  - Points back to the Cohort installation (for agent resolution)
  - Declares where project-scoped working memory lives
  - Records the user's permission defaults for Claude Code sub-agents

The file is created by ``cohort new`` or ``cohort link`` and is safe to commit
to version control (it contains no secrets -- only paths and permission flags).

Usage::

    from cohort.project_manifest import CohortManifest

    # Create a manifest for a new project
    manifest = CohortManifest.create(project_dir=Path("/my/project"), cohort_root=Path("/cohort"))
    manifest.write()

    # Load an existing manifest
    manifest = CohortManifest.load(project_dir=Path("/my/project"))
    print(manifest.permissions.profile)
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


MANIFEST_FILENAME = ".cohort"
WORKING_MEMORY_DIR = ".cohort-memory"
MANIFEST_VERSION = "1"


# =====================================================================
# Permissions block
# =====================================================================

@dataclass
class ProjectPermissions:
    """Per-project Claude Code sub-agent permission settings.

    Defaults come from the user's Cohort settings (data/settings.json).
    Override per-project by editing .cohort directly.
    """

    profile: str = "developer"
    """Tool profile name from data/tool_permissions.json (readonly/developer/researcher/minimal)."""

    allow_paths: list[str] = field(default_factory=list)
    """Absolute path prefixes the agent is allowed to edit.  Empty = project root only."""

    deny_paths: list[str] = field(default_factory=list)
    """Absolute path prefixes the agent must never touch."""

    allowed_tools: list[str] = field(default_factory=lambda: [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    ])
    """Explicit tool allow-list.  Overrides the profile's defaults if non-empty."""

    max_turns: int = 15
    """Maximum agent turns per task."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectPermissions":
        return cls(
            profile=data.get("profile", "developer"),
            allow_paths=data.get("allow_paths", []),
            deny_paths=data.get("deny_paths", []),
            allowed_tools=data.get("allowed_tools", ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]),
            max_turns=data.get("max_turns", 15),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_cohort_settings(cls, settings: dict[str, Any], project_dir: Path) -> "ProjectPermissions":
        """Build project permissions from Cohort global settings defaults."""
        perms = settings.get("default_permissions", {})
        profile = perms.get("profile", "developer")
        max_turns = perms.get("max_turns", 15)

        # Default allow_paths to the project directory itself
        allow_paths = perms.get("allow_paths", [str(project_dir)])
        deny_paths = perms.get("deny_paths", [])
        allowed_tools = perms.get("allowed_tools", ["Read", "Write", "Edit", "Bash", "Glob", "Grep"])

        return cls(
            profile=profile,
            allow_paths=allow_paths,
            deny_paths=deny_paths,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        )


# =====================================================================
# Manifest
# =====================================================================

@dataclass
class CohortManifest:
    """The .cohort project manifest.

    Fields:
        version         Schema version for forward compatibility.
        cohort_root     Absolute path to the Cohort installation.
        project_name    Human-friendly project name.
        working_memory_path  Relative path (from project root) to working memory dir.
        permissions     Sub-agent permission settings for this project.
    """

    version: str
    cohort_root: str
    project_name: str
    working_memory_path: str
    permissions: ProjectPermissions

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        project_dir: Path,
        cohort_root: Path,
        *,
        project_name: Optional[str] = None,
        permissions: Optional[ProjectPermissions] = None,
        cohort_settings: Optional[dict[str, Any]] = None,
    ) -> "CohortManifest":
        """Build a manifest for a new project.

        Args:
            project_dir:      Root directory of the new project.
            cohort_root:      Path to the Cohort installation.
            project_name:     Human name (defaults to directory name).
            permissions:      Explicit permissions (overrides cohort_settings).
            cohort_settings:  Raw dict from data/settings.json for pulling defaults.
        """
        name = project_name or project_dir.name

        if permissions is None:
            if cohort_settings:
                permissions = ProjectPermissions.from_cohort_settings(cohort_settings, project_dir)
            else:
                permissions = ProjectPermissions(allow_paths=[str(project_dir)])

        return cls(
            version=MANIFEST_VERSION,
            cohort_root=str(cohort_root),
            project_name=name,
            working_memory_path=WORKING_MEMORY_DIR,
            permissions=permissions,
        )

    @classmethod
    def load(cls, project_dir: Path) -> "CohortManifest":
        """Load a manifest from a project directory.

        Raises FileNotFoundError if .cohort does not exist.
        Raises ValueError if the file is malformed.
        """
        path = project_dir / MANIFEST_FILENAME
        if not path.exists():
            raise FileNotFoundError(f"No .cohort manifest found in {project_dir}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Could not parse .cohort manifest: {exc}") from exc

        return cls(
            version=data.get("version", MANIFEST_VERSION),
            cohort_root=data.get("cohort_root", ""),
            project_name=data.get("project_name", project_dir.name),
            working_memory_path=data.get("working_memory_path", WORKING_MEMORY_DIR),
            permissions=ProjectPermissions.from_dict(data.get("permissions", {})),
        )

    @classmethod
    def find(cls, start: Path) -> Optional["CohortManifest"]:
        """Walk up from start looking for a .cohort manifest.  Returns None if not found."""
        current = start.resolve()
        for parent in [current, *current.parents]:
            try:
                return cls.load(parent)
            except FileNotFoundError:
                continue
            except ValueError:
                return None
        return None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def write(self, project_dir: Path) -> Path:
        """Write the manifest to project_dir/.cohort.  Returns the written path."""
        data = {
            "version": self.version,
            "cohort_root": self.cohort_root,
            "project_name": self.project_name,
            "working_memory_path": self.working_memory_path,
            "permissions": self.permissions.to_dict(),
        }
        path = project_dir / MANIFEST_FILENAME
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path

    def ensure_working_memory_dir(self, project_dir: Path) -> Path:
        """Create the working memory directory if it doesn't exist."""
        wm_dir = project_dir / self.working_memory_path
        wm_dir.mkdir(parents=True, exist_ok=True)
        # Write a .gitignore so working memory isn't committed
        gitignore = wm_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("# Agent working memory -- project-specific, do not commit\n*\n", encoding="utf-8")
        return wm_dir

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def cohort_root_path(self) -> Path:
        return Path(self.cohort_root)

    @property
    def agents_source(self) -> Path:
        return self.cohort_root_path / ".claude" / "agents"

    @property
    def skills_source(self) -> Path:
        return self.cohort_root_path / ".claude" / "skills"


# =====================================================================
# Cohort settings loader
# =====================================================================

def load_cohort_settings(cohort_root: Path) -> dict[str, Any]:
    """Load data/settings.json from cohort_root.  Returns empty dict on any error."""
    path = cohort_root / "data" / "settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_cohort_settings(cohort_root: Path, settings: dict[str, Any]) -> None:
    """Write data/settings.json to cohort_root."""
    path = cohort_root / "data" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def get_default_permissions(cohort_root: Path, project_dir: Path) -> ProjectPermissions:
    """Read default_permissions from Cohort settings, scoped to project_dir."""
    settings = load_cohort_settings(cohort_root)
    return ProjectPermissions.from_cohort_settings(settings, project_dir)
