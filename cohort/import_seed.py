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
    name = frontmatter.get("name", path.stem)
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

    return unique_stored
