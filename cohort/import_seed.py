"""Preference import from external sources (ChatGPT, Claude Code).

Parses exported conversation history or structured memory files,
extracts durable user preferences via the local Qwen model, and
returns fact candidates for user approval before storage.

All processing is local -- nothing leaves the machine.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from cohort.agent import LearnedFact

# Profile location (same as learning.py)
_PROFILE_PATH = Path.home() / ".cohort" / "profile.json"

from cohort.local.config import (
    DEFAULT_MODEL,
    IMPORT_BATCH_SIZE,
    IMPORT_EXTRACTION_PARAMS,
    IMPORT_EXTRACTION_PROMPT,
    IMPORT_MAX_CONVERSATIONS,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Data Structures
# =====================================================================

class ConversationSummary:
    """Lightweight metadata for the title picker."""

    __slots__ = ("id", "title", "folder", "message_count", "create_time")

    def __init__(
        self,
        id: str,
        title: str,
        folder: str | None,
        message_count: int,
        create_time: float | None,
    ):
        self.id = id
        self.title = title
        self.folder = folder
        self.message_count = message_count
        self.create_time = create_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "folder": self.folder,
            "message_count": self.message_count,
            "create_time": self.create_time,
        }


# =====================================================================
# ChatGPT Export Parser
# =====================================================================

def parse_chatgpt_titles(data: list[dict]) -> list[dict]:
    """Parse conversations.json and return title/folder metadata.

    Args:
        data: The parsed conversations.json array.

    Returns:
        List of ConversationSummary dicts for the UI title picker.
    """
    summaries = []
    for conv in data:
        if not isinstance(conv, dict):
            continue

        conv_id = conv.get("id", "")
        title = conv.get("title", "Untitled")
        folder = conv.get("folder_id")
        mapping = conv.get("mapping", {})

        # Count actual user/assistant messages (skip null/system nodes)
        msg_count = sum(
            1 for node in mapping.values()
            if isinstance(node, dict)
            and node.get("message")
            and node["message"].get("author", {}).get("role") in ("user", "assistant")
        )

        create_time = conv.get("create_time")

        summaries.append(ConversationSummary(
            id=conv_id,
            title=title,
            folder=folder,
            message_count=msg_count,
            create_time=create_time,
        ).to_dict())

    # Sort by create_time descending (newest first)
    summaries.sort(key=lambda s: s.get("create_time") or 0, reverse=True)
    return summaries[:IMPORT_MAX_CONVERSATIONS]


def flatten_conversation(conv: dict) -> list[dict[str, str]]:
    """Walk the message tree from current_node backward to produce a linear conversation.

    Returns:
        List of {"role": "user"|"assistant", "content": "..."} in chronological order.
    """
    mapping = conv.get("mapping", {})
    current_id = conv.get("current_node")
    if not current_id or not mapping:
        return []

    messages: list[dict[str, str]] = []
    visited: set[str] = set()

    while current_id and current_id not in visited:
        visited.add(current_id)
        node = mapping.get(current_id)
        if not node:
            break

        msg = node.get("message")
        if msg:
            role = msg.get("author", {}).get("role", "")
            if role in ("user", "assistant"):
                # Extract text from content.parts
                content = _extract_text_parts(msg.get("content", {}))
                if content.strip():
                    messages.append({"role": role, "content": content})

        current_id = node.get("parent")

    messages.reverse()
    return messages


def _extract_text_parts(content: dict) -> str:
    """Extract text from ChatGPT content.parts, skipping images/attachments."""
    parts = content.get("parts", [])
    text_parts = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
        # Skip dicts (image pointers, asset refs, etc.)
    return "\n".join(text_parts)


def extract_from_chatgpt(
    conversations: list[dict],
    selected_ids: set[str],
    client: Any,
    model: str,
) -> list[dict[str, str]]:
    """Extract preference facts from selected ChatGPT conversations.

    Args:
        conversations: Full conversations.json data.
        selected_ids: Set of conversation IDs the user selected.
        client: OllamaClient instance.
        model: Model name for extraction.

    Returns:
        List of {"fact": "...", "confidence": "...", "category": "...", "source": "chatgpt:title"}.
    """
    # Build lookup
    conv_map = {}
    for conv in conversations:
        if isinstance(conv, dict) and conv.get("id") in selected_ids:
            conv_map[conv["id"]] = conv

    all_facts: list[dict[str, str]] = []
    seen_facts: set[str] = set()  # Dedup within this import

    for conv_id in selected_ids:
        conv = conv_map.get(conv_id)
        if not conv:
            continue

        messages = flatten_conversation(conv)
        if not messages:
            continue

        title = conv.get("title", "Untitled")

        # Process in batches of message pairs
        pairs = _pair_user_assistant(messages)
        for batch_start in range(0, len(pairs), IMPORT_BATCH_SIZE):
            batch = pairs[batch_start:batch_start + IMPORT_BATCH_SIZE]
            batch_text = _format_batch_for_extraction(batch)

            facts = _run_extraction(batch_text, client, model)
            for fact in facts:
                # Dedup within this import session
                fact_key = fact["fact"].lower().strip()
                if fact_key in seen_facts:
                    continue
                seen_facts.add(fact_key)
                fact["source"] = f"chatgpt:{title}"
                all_facts.append(fact)

    return all_facts


def _pair_user_assistant(messages: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Pair consecutive user->assistant messages."""
    pairs = []
    i = 0
    while i < len(messages) - 1:
        if messages[i]["role"] == "user" and messages[i + 1]["role"] == "assistant":
            pairs.append((messages[i]["content"], messages[i + 1]["content"]))
            i += 2
        else:
            i += 1
    return pairs


def _format_batch_for_extraction(pairs: list[tuple[str, str]]) -> str:
    """Format a batch of user/assistant pairs for the extraction prompt."""
    parts = []
    for user_msg, asst_msg in pairs:
        # Truncate long messages
        parts.append(f"User: {user_msg[:500]}\nAssistant: {asst_msg[:1000]}")
    return "\n---\n".join(parts)


# =====================================================================
# Claude Code Memory Parser
# =====================================================================

def parse_claude_memory(claude_dir: Path | None = None) -> list[dict[str, str]]:
    """Parse ~/.claude/ memory files and extract preference/feedback facts.

    Looks for:
    - memory/ directory with markdown files (frontmatter + content)
    - CLAUDE.md project files with user preferences

    Returns:
        List of {"fact": "...", "confidence": "high", "category": "preference",
                 "source": "claude_code:filename"}.
    """
    if claude_dir is None:
        claude_dir = Path.home() / ".claude"

    if not claude_dir.is_dir():
        return []

    facts: list[dict[str, str]] = []

    # 1. Parse memory/ files
    memory_dir = claude_dir / "memory"
    if memory_dir.is_dir():
        for md_file in sorted(memory_dir.glob("*.md")):
            file_facts = _parse_memory_file(md_file)
            facts.extend(file_facts)

    # 2. Parse project memory directories
    projects_dir = claude_dir / "projects"
    if projects_dir.is_dir():
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            proj_memory = project_dir / "memory"
            if proj_memory.is_dir():
                for md_file in sorted(proj_memory.glob("*.md")):
                    file_facts = _parse_memory_file(md_file)
                    facts.extend(file_facts)

    return facts


def _parse_memory_file(path: Path) -> list[dict[str, str]]:
    """Parse a single markdown memory file with YAML frontmatter.

    Expected format:
    ```
    ---
    name: memory name
    description: one-line description
    type: user|feedback|project|reference
    ---
    Content here...
    ```
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []

    # Parse frontmatter
    frontmatter, body = _split_frontmatter(text)
    if not body.strip():
        return []

    mem_type = frontmatter.get("type", "").lower()
    frontmatter.get("name", path.stem)
    source = f"claude_code:{path.name}"

    # Only import user preferences and feedback — skip project/reference
    if mem_type not in ("user", "feedback"):
        return []

    facts = []

    # For feedback type, the content IS the fact (a behavioral rule)
    if mem_type == "feedback":
        # Extract the main rule (first line or sentence)
        lines = [l.strip() for l in body.strip().split("\n") if l.strip()]
        if lines:
            rule = lines[0]
            # Strip markdown formatting
            rule = re.sub(r"^\*\*(.+?)\*\*:?\s*", r"\1: ", rule)
            if _is_user_preference(rule):
                facts.append({
                    "fact": rule,
                    "confidence": "high",
                    "category": "preference",
                    "source": source,
                })

    elif mem_type == "user":
        # Extract factual statements about the user — but only preferences,
        # not project-specific technical details like paths, API configs, etc.
        lines = [l.strip() for l in body.strip().split("\n") if l.strip()]
        for line in lines:
            # Skip markdown headers and short lines
            if line.startswith("#") or len(line) < 15:
                continue
            # Strip list markers
            cleaned = re.sub(r"^[-*]\s+", "", line)
            if len(cleaned) >= 15 and _is_user_preference(cleaned):
                facts.append({
                    "fact": cleaned,
                    "confidence": "high",
                    "category": "preference",
                    "source": source,
                })

    return facts


def _is_user_preference(text: str) -> bool:
    """Check if a text line describes a user preference vs a technical detail.

    Returns True for things like:
    - "User prefers pytest over unittest"
    - "Use terse responses with no trailing summaries"

    Returns False for things like:
    - "Python path: C:\\Users\\..."
    - "Git bash on Windows: schtasks..."
    - "Educator API: Use session.record_answers..."
    """
    lower = text.lower()

    # Reject: lines that look like path/config references
    if any(marker in lower for marker in (
        ":\\", "c:/", "/usr/", "/home/", "localhost", "127.0.0.1",
        ".exe", ".json", ".yaml", ".py:", ".js:",
        "api key", "api:", "endpoint:", "port:", "url:",
    )):
        return False

    # Reject: lines that are mostly code or technical commands
    if text.count("`") >= 4:  # Inline code references
        return False

    # Accept: lines with preference markers
    pref_markers = (
        "prefer", "always", "never", "don't", "do not",
        "instead of", "rather than", "likes", "wants",
        "style", "approach", "response", "communicate",
        "avoid", "use ", "keep ", "make sure",
        "should", "must", "important",
    )
    return any(m in lower for m in pref_markers)


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from markdown body."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text

    fm_text = match.group(1)
    body = match.group(2)

    # Simple YAML parsing (key: value lines only)
    fm: dict[str, str] = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()

    return fm, body


def detect_claude_dir() -> dict[str, Any]:
    """Check if ~/.claude/ exists and what's available.

    Returns:
        {"exists": bool, "path": str, "memory_files": int, "project_count": int}
    """
    claude_dir = Path.home() / ".claude"
    result: dict[str, Any] = {
        "exists": claude_dir.is_dir(),
        "path": str(claude_dir),
        "memory_files": 0,
        "project_count": 0,
    }

    if not claude_dir.is_dir():
        return result

    memory_dir = claude_dir / "memory"
    if memory_dir.is_dir():
        result["memory_files"] = len(list(memory_dir.glob("*.md")))

    projects_dir = claude_dir / "projects"
    if projects_dir.is_dir():
        project_count = 0
        for pd in projects_dir.iterdir():
            if pd.is_dir() and (pd / "memory").is_dir():
                project_count += 1
        result["project_count"] = project_count

    return result


# =====================================================================
# Regex-Based Fallback Extraction (no model needed)
# =====================================================================

# Patterns that indicate user preferences in conversation text
_PREF_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "I prefer X over Y" / "I prefer X"
    (re.compile(r"(?:i|I)\s+prefer\s+(.+?)(?:\s+over\s+.+)?[.\n]", re.IGNORECASE), "preference"),
    # "I always/never use X"
    (re.compile(r"(?:i|I)\s+(always|never)\s+use\s+(.+?)[.\n]", re.IGNORECASE), "tool_usage"),
    # "I use X instead of Y" / "I use X"
    (re.compile(r"(?:i|I)\s+use\s+(\S+(?:\s+\S+)?)\s+(?:instead\s+of|rather\s+than|not)\s+(.+?)[.\n]", re.IGNORECASE), "tool_usage"),
    # "don't/do not X" as instructions
    (re.compile(r"(?:please\s+)?(?:don'?t|do\s+not)\s+(.+?)[.\n]", re.IGNORECASE), "correction"),
    # "always X" as instructions
    (re.compile(r"(?:please\s+)?always\s+(.+?)[.\n]", re.IGNORECASE), "correction"),
    # "keep responses/answers X"
    (re.compile(r"keep\s+(?:your\s+)?(?:responses?|answers?)\s+(.+?)[.\n]", re.IGNORECASE), "preference"),
    # "I like X" style
    (re.compile(r"(?:i|I)\s+(?:like|want|need)\s+(.+?)[.\n]", re.IGNORECASE), "preference"),
]


def extract_facts_regex(conversations: list[dict], selected_ids: set[str]) -> list[dict[str, str]]:
    """Extract preference facts using regex patterns only (no model).

    Fallback for when no local model is available. Catches obvious
    preference statements like "I prefer X", "always use Y", "don't do Z".
    Less thorough than Qwen but zero dependencies.
    """
    all_facts: list[dict[str, str]] = []
    seen: set[str] = set()

    conv_map = {c["id"]: c for c in conversations if isinstance(c, dict) and c.get("id") in selected_ids}

    for conv_id in selected_ids:
        conv = conv_map.get(conv_id)
        if not conv:
            continue

        messages = flatten_conversation(conv)
        title = conv.get("title", "Untitled")

        # Only scan user messages (preferences come from the user)
        user_texts = [m["content"] for m in messages if m["role"] == "user"]
        full_text = "\n".join(user_texts)

        for pattern, category in _PREF_PATTERNS:
            for match in pattern.finditer(full_text):
                # Build the fact from the full match
                raw = match.group(0).strip().rstrip(".")
                # Normalize: "I prefer X over Y" -> "User prefers X over Y"
                fact = re.sub(r"^(?:i|I)\s+", "User ", raw)
                fact = re.sub(r"^(?:please\s+)?", "", fact, flags=re.IGNORECASE)

                # Capitalize and clean
                fact = fact[0].upper() + fact[1:] if fact else fact
                if not fact or len(fact) < 10:
                    continue

                key = fact.lower().strip()
                if key in seen:
                    continue
                seen.add(key)

                all_facts.append({
                    "fact": fact,
                    "confidence": "medium",
                    "category": category,
                    "source": f"chatgpt:{title}",
                })

    return all_facts


# =====================================================================
# Copy-Paste Prompt Generator
# =====================================================================

PROFILE_PROMPT = '''I'm setting up a new AI assistant tool called Cohort and I want it to know \
my preferences from the start. Can you help me create a preference profile based on what you \
know about how I work?

Please answer each section based on our conversation history. If you're not sure about \
something, skip it. Output as a simple list — one preference per line.

**Communication Style:**
- How long do I like responses? (minimal / short / medium / detailed)
- Do I prefer bullet points or prose?
- Am I direct or do I like context first?

**Technical Preferences** (if applicable):
- What programming languages do I use most?
- What tools, frameworks, or libraries do I prefer?
- Any tools I've said I avoid or dislike?
- Editor, terminal, OS preferences?
- Testing, linting, formatting preferences?

**Work Style:**
- Do I like options presented or just the best answer?
- Do I prefer step-by-step guidance or high-level direction?
- Anything I've asked you to always do or never do?

**Anything else** you've noticed about my preferences that would help another AI work with me?

Format each preference as a single clear sentence starting with "User prefers..." or \
"User uses..." or "Always..." or "Never..." — one per line, nothing else.'''


def get_profile_prompt() -> str:
    """Return the prompt users should paste into their existing AI."""
    return PROFILE_PROMPT


def parse_profile_paste(text: str) -> list[dict[str, str]]:
    """Parse the output from the profile prompt (pasted back by user).

    Expects one preference per line, optionally with bullet markers.
    Returns facts ready for the preview/commit flow.
    """
    facts: list[dict[str, str]] = []
    seen: set[str] = set()

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Strip bullet markers, numbers, dashes
        cleaned = re.sub(r"^[-*\d.)\s]+", "", line).strip()

        # Skip headers and meta-text
        if cleaned.startswith(("**", "##", "Communication", "Technical", "Work Style",
                               "Anything else", "Format each", "Here", "Based on")):
            continue

        if len(cleaned) < 10:
            continue

        # Determine category
        lower = cleaned.lower()
        if any(kw in lower for kw in ("use ", "uses ", "editor", "framework", "library",
                                       "tool", "language", "terminal")):
            category = "tool_usage"
        elif any(kw in lower for kw in ("never", "don't", "avoid", "stop")):
            category = "correction"
        elif any(kw in lower for kw in ("always", "must", "require")):
            category = "procedure"
        else:
            category = "preference"

        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)

        facts.append({
            "fact": cleaned,
            "confidence": "high",
            "category": category,
            "source": "profile_prompt",
        })

    return facts


# =====================================================================
# Config File Preference Extraction (no model needed)
# =====================================================================

def extract_from_config_files(file_contents: dict[str, str]) -> list[dict[str, str]]:
    """Extract preferences from project config files.

    Supports: pyproject.toml, .editorconfig, .prettierrc, tsconfig.json,
    .eslintrc, package.json (partial), .vscode/settings.json.

    Args:
        file_contents: {filename: content_string} dict.

    Returns:
        List of preference facts.
    """
    facts: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(fact: str, category: str = "tool_usage") -> None:
        key = fact.lower()
        if key not in seen and len(fact) >= 10:
            seen.add(key)
            facts.append({"fact": fact, "confidence": "high",
                          "category": category, "source": "config_file"})

    for filename, content in file_contents.items():
        fname = filename.lower()

        if fname == "pyproject.toml" or fname.endswith("/pyproject.toml"):
            _parse_pyproject(content, _add)

        elif fname == ".editorconfig" or fname.endswith("/.editorconfig"):
            _parse_editorconfig(content, _add)

        elif fname == "package.json" or fname.endswith("/package.json"):
            _parse_package_json(content, _add)

        elif fname == "tsconfig.json" or fname.endswith("/tsconfig.json"):
            _add("User uses TypeScript")

        elif fname in (".prettierrc", ".prettierrc.json") or fname.endswith(("/.prettierrc", "/.prettierrc.json")):
            _parse_prettierrc(content, _add)

    return facts


def _parse_pyproject(content: str, add: Any) -> None:
    """Extract preferences from pyproject.toml."""
    # Build system
    if "[tool.poetry]" in content:
        add("User uses Poetry for Python dependency management")
    elif "[build-system]" in content and "hatchling" in content:
        add("User uses Hatch for Python project management")
    elif "[build-system]" in content and "setuptools" in content:
        add("User uses setuptools for Python packaging")

    # Linting / formatting
    if "[tool.ruff]" in content:
        add("User uses ruff for Python linting and formatting")
    if "[tool.black]" in content:
        add("User uses Black for Python code formatting")
    if "[tool.isort]" in content:
        add("User uses isort for Python import sorting")
    if "[tool.mypy]" in content:
        add("User uses mypy for Python type checking")
    if "[tool.pyright]" in content:
        add("User uses Pyright for Python type checking")

    # Testing
    if "[tool.pytest" in content:
        add("User uses pytest for Python testing")

    # Line length
    line_match = re.search(r"line[_-]length\s*=\s*(\d+)", content)
    if line_match:
        add(f"User prefers {line_match.group(1)} character line length")

    # Python version
    ver_match = re.search(r"python_requires\s*=\s*[\"']>=?(\d+\.\d+)", content)
    if ver_match:
        add(f"User targets Python {ver_match.group(1)}+")
    ver_match2 = re.search(r"requires-python\s*=\s*[\"']>=?(\d+\.\d+)", content)
    if ver_match2:
        add(f"User targets Python {ver_match2.group(1)}+")


def _parse_editorconfig(content: str, add: Any) -> None:
    """Extract preferences from .editorconfig."""
    if "indent_style = space" in content.lower():
        size_match = re.search(r"indent_size\s*=\s*(\d+)", content)
        size = size_match.group(1) if size_match else "4"
        add(f"User prefers {size}-space indentation")
    elif "indent_style = tab" in content.lower():
        add("User prefers tab indentation")

    if "trim_trailing_whitespace = true" in content.lower():
        add("User trims trailing whitespace")

    if "insert_final_newline = true" in content.lower():
        add("User requires final newline in files")


def _parse_package_json(content: str, add: Any) -> None:
    """Extract preferences from package.json."""
    try:
        pkg = json.loads(content)
    except json.JSONDecodeError:
        return

    deps = {}
    deps.update(pkg.get("dependencies", {}))
    deps.update(pkg.get("devDependencies", {}))

    # Frameworks
    if "react" in deps:
        add("User uses React")
    if "vue" in deps:
        add("User uses Vue.js")
    if "next" in deps:
        add("User uses Next.js")
    if "svelte" in deps or "@sveltejs/kit" in deps:
        add("User uses Svelte")

    # Testing
    if "vitest" in deps:
        add("User uses Vitest for JavaScript testing")
    elif "jest" in deps:
        add("User uses Jest for JavaScript testing")

    # Linting
    if "eslint" in deps:
        add("User uses ESLint for JavaScript linting")
    if "prettier" in deps:
        add("User uses Prettier for code formatting")
    if "biome" in deps or "@biomejs/biome" in deps:
        add("User uses Biome for JavaScript linting and formatting")

    # Runtime
    if "typescript" in deps:
        add("User uses TypeScript")

    # Package manager (from packageManager field)
    pm = pkg.get("packageManager", "")
    if "pnpm" in pm:
        add("User uses pnpm as JavaScript package manager")
    elif "yarn" in pm:
        add("User uses Yarn as JavaScript package manager")
    elif "bun" in pm:
        add("User uses Bun as JavaScript runtime and package manager")


def _parse_prettierrc(content: str, add: Any) -> None:
    """Extract preferences from .prettierrc."""
    try:
        cfg = json.loads(content)
    except json.JSONDecodeError:
        return

    if cfg.get("semi") is False:
        add("User prefers no semicolons in JavaScript", "preference")
    elif cfg.get("semi") is True:
        add("User prefers semicolons in JavaScript", "preference")

    if cfg.get("singleQuote"):
        add("User prefers single quotes in JavaScript", "preference")

    tw = cfg.get("tabWidth")
    if tw:
        add(f"User prefers {tw}-space indentation in JavaScript", "preference")

    if cfg.get("useTabs"):
        add("User prefers tabs in JavaScript", "preference")


# =====================================================================
# Shared Extraction Engine
# =====================================================================

def _run_extraction(text: str, client: Any, model: str) -> list[dict[str, str]]:
    """Run fact extraction on a text block using local Qwen.

    Returns list of {"fact": "...", "confidence": "...", "category": "..."}.
    """
    prompt = IMPORT_EXTRACTION_PROMPT.format(text=text[:4000])

    try:
        result = client.generate(
            model=model,
            prompt=prompt,
            temperature=IMPORT_EXTRACTION_PARAMS["temperature"],
            think=IMPORT_EXTRACTION_PARAMS["think"],
            keep_alive=IMPORT_EXTRACTION_PARAMS["keep_alive"],
            options={"num_predict": IMPORT_EXTRACTION_PARAMS["num_predict"]},
        )
    except Exception:
        logger.debug("[!] Import extraction call failed", exc_info=True)
        return []

    if result is None or not result.text.strip():
        return []

    return _parse_facts_json(result.text.strip())


def _parse_facts_json(text: str) -> list[dict[str, str]]:
    """Parse JSON facts from model output."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return _validate_facts(data)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                return _validate_facts(data)
        except json.JSONDecodeError:
            pass

    return []


def _validate_facts(facts: list[Any]) -> list[dict[str, str]]:
    """Validate and normalize extracted facts."""
    valid_categories = {"domain_fact", "procedure", "preference", "correction", "tool_usage"}
    valid = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        fact_text = item.get("fact", "").strip()
        if not fact_text or len(fact_text) < 10:
            continue

        confidence = item.get("confidence", "medium")
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"

        category = item.get("category", "preference")
        if category not in valid_categories:
            category = "preference"

        valid.append({
            "fact": fact_text,
            "confidence": confidence,
            "category": category,
        })

    return valid[:10]  # Cap per batch


# =====================================================================
# Commit Approved Facts
# =====================================================================

def commit_facts(
    facts: list[dict[str, str]],
    agent_store: Any,
) -> int:
    """Store user-approved facts into all agent memories.

    Facts from import are user-global preferences. Each agent gets the
    full set so every agent can personalize responses. Dedup handles
    overlap with any existing per-agent facts.

    Returns:
        Total number of unique facts stored (across all agents).
    """
    from cohort.learning import _is_duplicate
    from cohort.memory_manager import MemoryManager

    mm = MemoryManager(agent_store)
    now = datetime.now().isoformat()

    agents = agent_store.list_agents(include_hidden=True)
    if not agents:
        return 0

    unique_stored = 0

    for agent in agents:
        agent_id = agent.agent_id
        memory = agent_store.load_memory(agent_id)
        existing_facts = memory.learned_facts if memory else []

        for raw in facts:
            source = raw.get("source", "import")

            is_dup, _ = _is_duplicate(raw["fact"], existing_facts)
            if is_dup:
                continue

            fact = LearnedFact(
                fact=raw["fact"],
                learned_from=f"import:{source}",
                timestamp=now,
                confidence=raw.get("confidence", "high"),
            )
            mm.add_learned_fact(agent_id, fact)
            existing_facts.append(fact)

            # Count unique facts (only on first agent)
            if agent is agents[0]:
                unique_stored += 1

    # Immediately distill profile from imported facts (bypass age check)
    if unique_stored > 0:
        _distill_profile_from_import(facts, agent_store)

    return unique_stored


def _distill_profile_from_import(
    facts: list[dict[str, str]],
    agent_store: Any,
) -> None:
    """Build/update user profile immediately from imported facts.

    Unlike the learning system's _maybe_evolve_profile (which waits 7 days),
    this runs right after import so the user gets personalized adaptation
    rules from their first conversation.

    If no local model is available, builds the profile from facts directly
    using heuristics instead of Qwen distillation.
    """

    # Collect preference-like facts
    pref_facts = [f["fact"] for f in facts if f.get("category") in (
        "preference", "correction", "tool_usage", "procedure",
    )]
    if not pref_facts:
        return

    # Try model-based distillation first
    try:
        from cohort.local.config import LEARNING_PROFILE_DISTILL_PROMPT
        from cohort.local.ollama import OllamaClient

        client = OllamaClient(timeout=60)
        if client.health_check():
            observations = "\n".join(f"- {f}" for f in pref_facts[:50])
            prompt = LEARNING_PROFILE_DISTILL_PROMPT.format(observations=observations)

            result = client.generate(
                model=DEFAULT_MODEL,
                prompt=prompt,
                temperature=0.15,
                think=False,
                keep_alive="2m",
                options={"num_predict": 2048},
            )

            if result and result.text.strip():
                _apply_distilled_profile(result.text.strip())
                return
    except Exception:
        pass

    # Fallback: heuristic profile from facts (no model needed)
    _build_heuristic_profile(pref_facts)


def _apply_distilled_profile(model_output: str) -> None:
    """Apply Qwen-distilled profile JSON to ~/.cohort/profile.json."""
    from cohort.learning import load_profile

    try:
        new_profile = json.loads(model_output)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", model_output, re.DOTALL)
        if not match:
            return
        try:
            new_profile = json.loads(match.group())
        except json.JSONDecodeError:
            return

    profile = load_profile() or {
        "version": "1.0",
        "core_paragraph": "",
        "adaptation_rules": {},
    }

    if "core_paragraph" in new_profile:
        profile["core_paragraph"] = new_profile["core_paragraph"]
    if "adaptation_rules" in new_profile and isinstance(new_profile["adaptation_rules"], dict):
        if "adaptation_rules" not in profile:
            profile["adaptation_rules"] = {}
        profile["adaptation_rules"].update(new_profile["adaptation_rules"])

    profile["last_updated"] = datetime.now().isoformat()
    profile["version"] = "1.0"

    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    logger.info("[OK] Distilled user profile from import (%s)", _PROFILE_PATH)


def _build_heuristic_profile(pref_facts: list[str]) -> None:
    """Build a basic profile from facts using heuristics (no model)."""
    from cohort.learning import load_profile

    profile = load_profile() or {
        "version": "1.0",
        "core_paragraph": "",
        "adaptation_rules": {},
    }

    rules = profile.get("adaptation_rules", {})

    # Scan facts for communication preferences
    for fact in pref_facts:
        lower = fact.lower()
        if any(kw in lower for kw in ("concise", "short", "brief", "under 3", "minimal")):
            rules["response_length"] = "short"
        elif any(kw in lower for kw in ("detailed", "thorough", "comprehensive")):
            rules["response_length"] = "detailed"

        if "bullet" in lower:
            rules.setdefault("custom_rules", [])
            if "User prefers bullet points" not in rules["custom_rules"]:
                rules["custom_rules"].append("User prefers bullet points")

        if any(kw in lower for kw in ("direct", "no filler", "straight to")):
            rules["summarize_back"] = False

    # Build core paragraph from tool/language facts
    tools = [f for f in pref_facts if any(kw in f.lower() for kw in (
        "uses ", "prefers ", "python", "javascript", "typescript",
    ))]
    if tools:
        profile["core_paragraph"] = "User preferences: " + "; ".join(tools[:8]) + "."

    profile["adaptation_rules"] = rules
    profile["last_updated"] = datetime.now().isoformat()
    profile["version"] = "1.0"

    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    logger.info("[OK] Built heuristic profile from import (%s)", _PROFILE_PATH)
