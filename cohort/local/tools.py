"""Local tool definitions and executor for Ollama native tool calling.

Provides JSON schemas (OpenAI-compatible format for Ollama's /api/chat tools
param) and Python executor functions for: Read, Glob, Grep, Bash, Write, Edit.

Safety: all file operations validated against agents_root. No traversal above
it. Subprocess commands have a blocklist and timeout. Tool execution never
raises -- returns error strings on failure.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum output sizes to prevent context blowup
_MAX_FILE_SIZE = 100_000  # 100KB
_MAX_WRITE_SIZE = 50_000  # 50KB
_MAX_OUTPUT_SIZE = 10_000  # 10KB for bash/grep output
_MAX_GLOB_RESULTS = 50
_MAX_GREP_MATCHES = 50
_BASH_TIMEOUT = 30  # seconds

# Dangerous commands that should never be executed
_BASH_BLOCKLIST = [
    "rm -rf", "rm -r /", "rmdir /s",
    "git push", "git push --force",
    "format ", "del /f", "rd /s",
    "shutdown", "restart",
    "DROP TABLE", "DROP DATABASE",
    "curl | bash", "wget | bash",
]


# =====================================================================
# Path safety
# =====================================================================

def _validate_path(path_str: str, agents_root: Path) -> Path:
    """Resolve and validate a path is under agents_root.

    Raises ValueError if the path escapes the allowed root.
    """
    # Resolve the path (handles .., symlinks, etc.)
    resolved = Path(path_str).resolve()
    root_resolved = agents_root.resolve()

    # Check containment
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"Path '{path_str}' resolves outside project root. "
            f"Access restricted to: {root_resolved}"
        )
    return resolved


# =====================================================================
# Tool executors
# =====================================================================

def _exec_read(arguments: dict[str, Any], agents_root: Path) -> str:
    """Read a file's contents."""
    file_path = arguments.get("file_path", "")
    if not file_path:
        return "Error: file_path is required"

    try:
        resolved = _validate_path(file_path, agents_root)
        if not resolved.exists():
            return f"Error: file not found: {file_path}"
        if not resolved.is_file():
            return f"Error: not a file: {file_path}"

        size = resolved.stat().st_size
        if size > _MAX_FILE_SIZE:
            return f"Error: file too large ({size:,} bytes, max {_MAX_FILE_SIZE:,})"

        content = resolved.read_text(encoding="utf-8", errors="replace")
        # Add line numbers like Claude Code's Read tool
        lines = content.splitlines()
        numbered = [f"{i+1:>5}\t{line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error reading file: {e}"


def _exec_glob(arguments: dict[str, Any], agents_root: Path) -> str:
    """Find files matching a glob pattern."""
    pattern = arguments.get("pattern", "")
    base_path = arguments.get("path", "")

    if not pattern:
        return "Error: pattern is required"

    try:
        if base_path:
            base = _validate_path(base_path, agents_root)
        else:
            base = agents_root

        matches = sorted(base.glob(pattern))[:_MAX_GLOB_RESULTS]
        if not matches:
            return f"No files found matching: {pattern}"

        # Return relative paths for readability
        results = []
        for m in matches:
            try:
                rel = m.relative_to(agents_root)
                results.append(str(rel))
            except ValueError:
                results.append(str(m))

        suffix = f"\n(showing first {_MAX_GLOB_RESULTS})" if len(matches) == _MAX_GLOB_RESULTS else ""
        return "\n".join(results) + suffix
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error globbing: {e}"


def _exec_grep(arguments: dict[str, Any], agents_root: Path) -> str:
    """Search file contents for a pattern."""
    pattern = arguments.get("pattern", "")
    search_path = arguments.get("path", "")

    if not pattern:
        return "Error: pattern is required"

    try:
        if search_path:
            base = _validate_path(search_path, agents_root)
        else:
            base = agents_root

        # Pure Python grep (no subprocess dependency)
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regex pattern: {e}"

        matches: list[str] = []
        files_to_search: list[Path] = []

        if base.is_file():
            files_to_search = [base]
        elif base.is_dir():
            # Search common code files, skip binary/hidden
            for ext in ("*.py", "*.js", "*.ts", "*.html", "*.css", "*.json",
                        "*.yaml", "*.yml", "*.md", "*.txt", "*.cfg", "*.toml"):
                files_to_search.extend(base.rglob(ext))

        for fpath in files_to_search:
            if len(matches) >= _MAX_GREP_MATCHES:
                break
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        try:
                            rel = fpath.relative_to(agents_root)
                        except ValueError:
                            rel = fpath
                        matches.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(matches) >= _MAX_GREP_MATCHES:
                            break
            except (OSError, UnicodeDecodeError):
                continue

        if not matches:
            return f"No matches found for pattern: {pattern}"

        result = "\n".join(matches)
        if len(matches) == _MAX_GREP_MATCHES:
            result += f"\n(truncated at {_MAX_GREP_MATCHES} matches)"
        return result
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error searching: {e}"


def _exec_bash(arguments: dict[str, Any], agents_root: Path) -> str:
    """Execute a shell command."""
    command = arguments.get("command", "")
    if not command:
        return "Error: command is required"

    # Safety: check blocklist
    cmd_lower = command.lower()
    for blocked in _BASH_BLOCKLIST:
        if blocked.lower() in cmd_lower:
            return f"Error: command blocked for safety: contains '{blocked}'"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(agents_root),
            timeout=_BASH_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n--- stderr ---\n"
            output += result.stderr

        if not output:
            output = f"(command completed with exit code {result.returncode})"

        # Truncate if too long
        if len(output) > _MAX_OUTPUT_SIZE:
            output = output[:_MAX_OUTPUT_SIZE] + f"\n... (truncated at {_MAX_OUTPUT_SIZE:,} chars)"

        return output
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {_BASH_TIMEOUT}s"
    except Exception as e:
        return f"Error executing command: {e}"


def _exec_write(arguments: dict[str, Any], agents_root: Path) -> str:
    """Write content to a file."""
    file_path = arguments.get("file_path", "")
    content = arguments.get("content", "")

    if not file_path:
        return "Error: file_path is required"
    if not content:
        return "Error: content is required"
    if len(content) > _MAX_WRITE_SIZE:
        return f"Error: content too large ({len(content):,} chars, max {_MAX_WRITE_SIZE:,})"

    try:
        resolved = _validate_path(file_path, agents_root)
        # Create parent directories if needed
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content):,} chars to {file_path}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error writing file: {e}"


def _exec_edit(arguments: dict[str, Any], agents_root: Path) -> str:
    """Edit a file by replacing old_string with new_string."""
    file_path = arguments.get("file_path", "")
    old_string = arguments.get("old_string", "")
    new_string = arguments.get("new_string", "")

    if not file_path:
        return "Error: file_path is required"
    if not old_string:
        return "Error: old_string is required"

    try:
        resolved = _validate_path(file_path, agents_root)
        if not resolved.exists():
            return f"Error: file not found: {file_path}"

        content = resolved.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}"
        if count > 1:
            return f"Error: old_string found {count} times in {file_path} (must be unique)"

        new_content = content.replace(old_string, new_string, 1)
        resolved.write_text(new_content, encoding="utf-8")
        return f"Successfully edited {file_path}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error editing file: {e}"


# =====================================================================
# Tool schemas (OpenAI-compatible format for Ollama)
# =====================================================================

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "Read": {
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Read the contents of a file. Returns the file text with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to read (relative to project root or absolute)",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    "Glob": {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": "Find files matching a glob pattern. Returns a list of matching file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match (e.g., '**/*.py', 'src/**/*.ts')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search in (default: project root)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    "Grep": {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": "Search file contents for a regex pattern. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in (default: project root)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    "Bash": {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Execute a shell command and return its output. Working directory is the project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
    "Write": {
        "type": "function",
        "function": {
            "name": "Write",
            "description": "Write content to a file. Creates the file and parent directories if they don't exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    "Edit": {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": "Edit a file by replacing old_string with new_string. The old_string must appear exactly once in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace (must be unique in the file)",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement string",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
}

_TOOL_EXECUTORS = {
    "Read": _exec_read,
    "Glob": _exec_glob,
    "Grep": _exec_grep,
    "Bash": _exec_bash,
    "Write": _exec_write,
    "Edit": _exec_edit,
}


# =====================================================================
# Public API
# =====================================================================

def build_tool_schemas(allowed_tools: list[str]) -> list[dict[str, Any]]:
    """Build Ollama-compatible tool schemas filtered by allowed tools.

    Args:
        allowed_tools: Tool names the agent is permitted to use
            (e.g., ["Read", "Glob", "Grep", "Bash"])

    Returns:
        List of tool schema dicts for Ollama's /api/chat tools param.
        Only includes tools that have local implementations.
    """
    schemas = []
    for tool_name in allowed_tools:
        if tool_name in _TOOL_SCHEMAS:
            schemas.append(_TOOL_SCHEMAS[tool_name])
    return schemas


def _check_file_permission(
    name: str,
    arguments: dict[str, Any],
    file_permissions: list[dict[str, str]],
) -> str | None:
    """Check file permissions before executing a file-touching tool.

    Returns an error string if access is denied, or None if allowed.
    """
    if not file_permissions:
        return None

    from cohort.tool_permissions import resolve_file_access

    # Tools that read files
    _READ_TOOLS = {"Read", "Glob", "Grep"}
    # Tools that write files
    _WRITE_TOOLS = {"Write", "Edit"}

    if name not in _READ_TOOLS and name not in _WRITE_TOOLS:
        return None  # Non-file tools (e.g., Bash) are not gated here

    # Extract the target path from arguments
    target = arguments.get("file_path") or arguments.get("path") or arguments.get("pattern", "")
    if not target:
        return None  # Let the executor handle missing args

    access = resolve_file_access(target, file_permissions)

    if access == "none":
        return (
            f"[!] Permission denied: {name} on '{target}'. "
            f"This path is not in the allowed file list. "
            f"The operator can enable access in Settings > Tool Permissions."
        )
    if access == "read" and name in _WRITE_TOOLS:
        return (
            f"[!] Permission denied: {name} on '{target}'. "
            f"This path is read-only. "
            f"The operator can change access in Settings > Tool Permissions."
        )

    return None


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    agents_root: Path,
    file_permissions: list[dict[str, str]] | None = None,
) -> str:
    """Execute a tool by name and return the result as a string.

    Never raises exceptions -- returns an error string on any failure.
    This is critical for the tool loop: a failed tool should not crash
    the entire agent response pipeline.

    Args:
        name: Tool name (e.g., "Read", "Bash")
        arguments: Tool arguments dict from the model's tool_call
        agents_root: Project root for path validation
        file_permissions: Resolved file permission rules (deny-all default).
            If provided, file-touching tools are gated before execution.

    Returns:
        Tool result as a string (may be an error message).
    """
    # File permission gate
    if file_permissions:
        denial = _check_file_permission(name, arguments, file_permissions)
        if denial:
            logger.info("[!] File permission denied: %s on %s",
                        name, arguments.get("file_path") or arguments.get("path", ""))
            return denial

    executor = _TOOL_EXECUTORS.get(name)
    if executor is None:
        return f"Error: unknown tool '{name}'. Available: {', '.join(_TOOL_EXECUTORS.keys())}"

    try:
        result = executor(arguments, agents_root)
        logger.debug("[OK] Tool %s executed: %d chars result", name, len(result))
        return result
    except Exception as e:
        logger.warning("[!] Tool %s failed: %s", name, e)
        return f"Error executing {name}: {e}"
