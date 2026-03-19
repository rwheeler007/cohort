"""Task planner for codegen pipeline.

Analyzes a CodegenTask, reads target files, gathers context,
and produces a structured plan the generator can act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cohort.codegen.models import CodegenTask
from cohort.codegen.safety import validate_all_targets

logger = logging.getLogger(__name__)

# Maximum lines to read from a single file for context
MAX_FILE_LINES = 500

# Maximum total context characters to keep the prompt within model limits
MAX_CONTEXT_CHARS = 40_000


@dataclass
class FileContext:
    """Contents of a file read for context."""

    path: str  # relative
    content: str
    line_count: int
    truncated: bool = False


@dataclass
class CodegenPlan:
    """Structured plan produced by the planner.

    Contains everything the generator needs to build a prompt:
    the task, the current state of target files, and any
    additional context files.
    """

    task: CodegenTask
    target_contents: list[FileContext] = field(default_factory=list)
    context_contents: list[FileContext] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Plan is valid if there are no blocking errors."""
        return len(self.errors) == 0

    @property
    def total_context_chars(self) -> int:
        """Total characters of file content in the plan."""
        total = sum(fc.content.__len__() for fc in self.target_contents)
        total += sum(fc.content.__len__() for fc in self.context_contents)
        return total


def _read_file_context(
    path: str,
    project_root: str,
    max_lines: int = MAX_FILE_LINES,
) -> FileContext | None:
    """Read a file and return its content as FileContext.

    Returns None if the file doesn't exist or can't be read.
    """
    full_path = Path(project_root) / path
    if not full_path.exists():
        return None

    try:
        text = full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("[!] Could not read %s: %s", path, exc)
        return None

    lines = text.splitlines(keepends=True)
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
        text = "".join(lines)

    return FileContext(
        path=path,
        content=text,
        line_count=len(lines),
        truncated=truncated,
    )


def plan(task: CodegenTask) -> CodegenPlan:
    """Analyze a task and produce a generation plan.

    Steps:
        1. Validate target paths (safety check)
        2. Read target files (for MODIFY tasks)
        3. Read context files
        4. Check total context size

    Returns:
        CodegenPlan with file contents and any errors.
    """
    result = CodegenPlan(task=task)

    # Step 1: Validate target paths
    safety_errors = validate_all_targets(
        task.target_files,
        task.project_root,
        task.allowed_paths or None,
    )
    if safety_errors:
        result.errors.extend(safety_errors)
        return result

    # Step 2: Read target files (for MODIFY tasks)
    if task.task_type == "modify":
        for target in task.target_files:
            fc = _read_file_context(target, task.project_root)
            if fc is None:
                result.errors.append(f"Target file not found: {target}")
            else:
                result.target_contents.append(fc)
    else:
        # CREATE: verify targets don't already exist
        for target in task.target_files:
            full = Path(task.project_root) / target
            if full.exists():
                result.errors.append(
                    f"CREATE target already exists: {target}"
                )

    if result.errors:
        return result

    # Step 3: Read context files
    for ctx_path in task.context_files:
        fc = _read_file_context(ctx_path, task.project_root)
        if fc is not None:
            result.context_contents.append(fc)
        else:
            logger.info("[*] Context file not found (skipping): %s", ctx_path)

    # Step 4: Check total context size
    if result.total_context_chars > MAX_CONTEXT_CHARS:
        logger.warning(
            "[!] Total context is %d chars (limit %d). "
            "Largest files may be truncated in the prompt.",
            result.total_context_chars,
            MAX_CONTEXT_CHARS,
        )

    return result
