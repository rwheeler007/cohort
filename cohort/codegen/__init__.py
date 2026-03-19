"""Code generation module for Cohort.

Standalone code generation and verification pipeline:
    Plan (analyze task + gather context)
    -> Generate (LLM produces diffs/files)
    -> Verify (syntax + tests + optional E2E)

Public API::

    from cohort.codegen import generate, CodegenTask

    task = CodegenTask(
        description="Add export button to channel settings",
        target_files=["cohort/server.py", "cohort/static/cohort.js"],
        task_type="modify",
    )
    result = await generate(task)
    if result.success:
        result.apply()  # write changes to disk
"""

from __future__ import annotations

from cohort.codegen.models import (
    CodegenResult,
    CodegenTask,
    FileChange,
    VerificationReport,
    VerificationResult,
)
from cohort.codegen.pipeline import generate

__all__ = [
    "CodegenResult",
    "CodegenTask",
    "FileChange",
    "VerificationReport",
    "VerificationResult",
    "generate",
]
