"""Code generator -- builds prompts and invokes the local LLM.

Produces FileChange objects from LLM output. Uses Cohort's existing
LocalRouter for inference (Ollama / cloud fallback).

Design choices:
- MODIFY tasks ask the LLM for replacement content of the target section,
  not unified diffs. LLMs are unreliable at producing clean diffs.
- CREATE tasks get full file content.
- The LLM output is wrapped in fenced code blocks for reliable parsing.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from cohort.codegen.models import CodegenTask, FileChange
from cohort.codegen.planner import CodegenPlan, FileContext

logger = logging.getLogger(__name__)


# =====================================================================
# Prompt templates
# =====================================================================

_SYSTEM_PROMPT = """\
You are a precise code generator. You produce code that is:
- Correct and complete (compiles/parses without errors)
- Minimal (only the changes requested, no extra refactoring)
- Well-formatted (consistent indentation with surrounding code)

Rules:
- Output ONLY code inside fenced blocks. No explanations outside the blocks.
- For each file, use this format:

```{language} path=relative/path/to/file.ext
<full file content or replacement content here>
```

- Do not include line numbers in the output.
- Do not add features, comments, or changes beyond what was requested.
- Preserve existing code style (quotes, indentation, naming conventions).
"""

_MODIFY_PROMPT = """\
## Task
{description}

## Target Files
{target_files_section}

## Instructions
Modify the target file(s) to implement the requested change.
Output the COMPLETE modified file content for each file, wrapped in a fenced \
code block with the path.

{deliverables_section}\
{context_section}\
"""

_CREATE_PROMPT = """\
## Task
{description}

## Files to Create
{file_list}

## Instructions
Create the requested file(s). Output the complete content for each file, \
wrapped in a fenced code block with the path.

{deliverables_section}\
{context_section}\
"""


# =====================================================================
# Output parser
# =====================================================================

# Matches: ```language path=some/file.ext\n...content...\n```
_FENCED_BLOCK_RE = re.compile(
    r"```\w*\s+path=([^\n]+)\n(.*?)```",
    re.DOTALL,
)

# Fallback: ```language\n...content...\n``` (no path annotation)
_PLAIN_BLOCK_RE = re.compile(
    r"```\w*\n(.*?)```",
    re.DOTALL,
)


def _parse_fenced_blocks(raw: str, expected_paths: list[str]) -> list[FileChange]:
    """Parse LLM output into FileChange objects.

    First tries annotated blocks (```lang path=...), then falls back
    to plain blocks matched by position to expected_paths.
    """
    changes: list[FileChange] = []

    # Try annotated blocks first
    for match in _FENCED_BLOCK_RE.finditer(raw):
        path = match.group(1).strip()
        content = match.group(2)
        # Normalize path separators
        path = path.replace("\\", "/")
        changes.append(FileChange(
            path=path,
            content=content,
            mode="modify" if any(p == path for p in expected_paths) else "create",
        ))

    if changes:
        return changes

    # Fallback: plain blocks matched by position
    blocks = _PLAIN_BLOCK_RE.findall(raw)
    for i, content in enumerate(blocks):
        if i < len(expected_paths):
            changes.append(FileChange(
                path=expected_paths[i],
                content=content,
                mode="modify",
            ))
        else:
            logger.warning("[!] Extra code block %d with no matching target path", i)

    return changes


# =====================================================================
# Prompt builder
# =====================================================================

def _build_target_files_section(targets: list[FileContext]) -> str:
    """Build the target files section with line-numbered content."""
    sections: list[str] = []
    for fc in targets:
        lines = fc.content.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        header = f"### `{fc.path}` ({fc.line_count} lines"
        if fc.truncated:
            header += ", truncated"
        header += ")"
        sections.append(f"{header}\n```\n{numbered}\n```")
    return "\n\n".join(sections)


def _build_context_section(contexts: list[FileContext]) -> str:
    """Build optional context files section."""
    if not contexts:
        return ""
    sections: list[str] = []
    for fc in contexts:
        sections.append(f"### `{fc.path}`\n```\n{fc.content}\n```")
    return "\n## Context Files\n" + "\n\n".join(sections) + "\n"


def _build_deliverables_section(deliverables: list[str]) -> str:
    """Build acceptance criteria section."""
    if not deliverables:
        return ""
    items = "\n".join(f"- {d}" for d in deliverables)
    return f"\n## Acceptance Criteria\n{items}\n\n"


def build_prompt(plan: CodegenPlan) -> str:
    """Build the full generation prompt from a plan."""
    task = plan.task
    deliverables = _build_deliverables_section(task.deliverables)
    context = _build_context_section(plan.context_contents)

    if task.task_type == "modify":
        targets = _build_target_files_section(plan.target_contents)
        return _MODIFY_PROMPT.format(
            description=task.description,
            target_files_section=targets,
            deliverables_section=deliverables,
            context_section=context,
        )
    else:
        file_list = "\n".join(f"- `{f}`" for f in task.target_files)
        return _CREATE_PROMPT.format(
            description=task.description,
            file_list=file_list,
            deliverables_section=deliverables,
            context_section=context,
        )


# =====================================================================
# Generator
# =====================================================================

@dataclass
class GenerateResult:
    """Raw result from the generator before verification."""

    changes: list[FileChange]
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None


def generate(plan: CodegenPlan) -> GenerateResult:
    """Generate code changes from a plan using the local LLM.

    Uses Cohort's LocalRouter for inference. Falls back gracefully
    if Ollama is unavailable.
    """
    if not plan.is_valid:
        return GenerateResult(
            changes=[],
            error=f"Invalid plan: {'; '.join(plan.errors)}",
        )

    # Build prompt
    prompt = build_prompt(plan)
    task = plan.task

    # Route through LocalRouter
    try:
        from cohort.local.router import LocalRouter
    except ImportError:
        return GenerateResult(
            changes=[],
            error="LocalRouter not available (cohort.local not installed)",
        )

    router = LocalRouter()
    start = time.monotonic()

    route_result = router.route(
        prompt=prompt,
        task_type="code",
        response_mode=task.response_mode,
        system=_SYSTEM_PROMPT,
    )

    elapsed = time.monotonic() - start

    if route_result is None:
        return GenerateResult(
            changes=[],
            elapsed_seconds=elapsed,
            error="LLM inference failed (Ollama unavailable or model not installed)",
        )

    # Parse output into FileChange objects
    changes = _parse_fenced_blocks(
        route_result.text,
        task.target_files,
    )

    if not changes:
        return GenerateResult(
            changes=[],
            model=route_result.model,
            tokens_in=route_result.tokens_in,
            tokens_out=route_result.tokens_out,
            elapsed_seconds=elapsed,
            error="LLM produced no parseable code blocks",
        )

    # For MODIFY tasks, attach original content
    if task.task_type == "modify":
        originals = {fc.path: fc.content for fc in plan.target_contents}
        for change in changes:
            change.original = originals.get(change.path, "")
            change.mode = "modify"

    # For CREATE tasks, set mode
    if task.task_type == "create":
        for change in changes:
            change.mode = "create"

    return GenerateResult(
        changes=changes,
        model=route_result.model,
        tokens_in=route_result.tokens_in,
        tokens_out=route_result.tokens_out,
        elapsed_seconds=elapsed,
    )
