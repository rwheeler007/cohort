"""Data models for the codegen pipeline.

Pure dataclasses -- no I/O, no dependencies beyond stdlib.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """Type of code generation task."""

    CREATE = "create"
    MODIFY = "modify"


@dataclass
class CodegenTask:
    """Input specification for a code generation task.

    Attributes:
        description: Natural language description of what to build/change.
        target_files: Relative paths to files to create or modify.
        task_type: Whether to create new files or modify existing ones.
        project_root: Absolute path to the project root. Defaults to cwd.
        context_files: Additional files to include as context for the LLM.
        deliverables: Acceptance criteria the result must satisfy.
        allowed_paths: Caller-defined whitelist of writable directories
            (relative to project_root). Additive restriction on top of
            the module's hard-floor FORBIDDEN_PATTERNS.
        response_mode: Inference tier -- "smart", "smarter", or "smartest".
    """

    description: str
    target_files: list[str]
    task_type: Literal["create", "modify"] = "modify"
    project_root: str = ""
    context_files: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    response_mode: str = "smarter"

    def __post_init__(self) -> None:
        if not self.project_root:
            self.project_root = str(Path.cwd())

    def resolve_path(self, relative: str) -> Path:
        """Resolve a relative path against project_root."""
        return Path(self.project_root) / relative


@dataclass
class FileChange:
    """A single file change produced by the generator.

    For MODIFY tasks: ``content`` holds the replacement text for the
    target section (not a unified diff -- LLMs are unreliable at
    producing clean diffs). ``original`` holds what was there before.

    For CREATE tasks: ``content`` holds the full file content,
    ``original`` is empty.
    """

    path: str  # relative to project_root
    content: str
    original: str = ""
    mode: Literal["create", "modify"] = "modify"

    @property
    def is_create(self) -> bool:
        return self.mode == "create"


@dataclass
class VerificationResult:
    """Result of a single verification step."""

    name: str  # e.g. "syntax", "pytest", "e2e_smoke"
    passed: bool
    message: str = ""
    duration_seconds: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationReport:
    """Aggregated verification results across all layers."""

    results: list[VerificationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True only if ALL verification steps passed."""
        return all(r.passed for r in self.results) if self.results else False

    @property
    def summary(self) -> str:
        """One-line summary of pass/fail counts."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        if failed == 0:
            return f"[OK] All {total} checks passed"
        names = [r.name for r in self.results if not r.passed]
        return f"[X] {failed}/{total} checks failed: {', '.join(names)}"

    def to_dict(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.results]


@dataclass
class CodegenResult:
    """Output of the codegen pipeline.

    Contains the generated changes and verification results.
    Changes are NOT applied to disk until ``apply()`` is called.
    """

    success: bool
    changes: list[FileChange] = field(default_factory=list)
    verification: VerificationReport = field(default_factory=VerificationReport)
    error: str | None = None
    project_root: str = ""

    # Generation metadata
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_seconds: float = 0.0

    def apply(self) -> list[str]:
        """Write all changes to disk.

        Returns:
            List of absolute paths that were written.

        Raises:
            RuntimeError: If verification did not pass.
            FileExistsError: If a CREATE target already exists.
        """
        if not self.success:
            raise RuntimeError(
                f"Cannot apply failed result: {self.error or self.verification.summary}"
            )

        root = Path(self.project_root)
        written: list[str] = []

        for change in self.changes:
            target = root / change.path
            if change.is_create and target.exists():
                raise FileExistsError(
                    f"CREATE target already exists: {change.path}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8")
            written.append(str(target))
            logger.info("[OK] Wrote %s", change.path)

        return written

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "elapsed_seconds": self.elapsed_seconds,
            "verification": self.verification.to_dict(),
            "changes": [
                {"path": c.path, "mode": c.mode, "lines": c.content.count("\n") + 1}
                for c in self.changes
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
