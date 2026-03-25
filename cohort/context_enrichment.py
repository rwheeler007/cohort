"""Channel discussion context enrichment for task execution.

Reads messages from a source channel, filters noise, and summarizes
design-relevant context using the local LLM.  Returns structured markdown
that gets injected into the execution prompt.

Best-effort: never raises, returns "" on any failure.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_DISCUSSION_TOKENS = 500
APPROX_CHARS_PER_TOKEN = 4
MAX_TRANSCRIPT_CHARS = MAX_DISCUSSION_TOKENS * APPROX_CHARS_PER_TOKEN * 2  # generous
MAX_PER_MESSAGE_CHARS = 800
MAX_MESSAGES = 30

_NOISE_SENDERS = frozenset({"system", "code_queue_worker", "task_executor", "worker"})
_MIN_MESSAGE_LENGTH = 20

_DISCUSSION_SUMMARY_PROMPT = """Extract design context from this channel discussion for a developer about to implement a task.

Task: {task_description}

Channel transcript:
{transcript}

Return ONLY the following three sections (omit any section if no relevant content exists):

## Design Decisions
[Key choices made and rationale]

## Implementation Specifics
[Concrete details: APIs, data structures, patterns, file names]

## Constraints
[Things to avoid, hard boundaries, anti-patterns explicitly rejected]

Be concise. Focus on actionable detail, not summaries of the discussion."""


def _filter_messages(messages: list[dict]) -> list[dict]:
    """Filter to design-relevant messages only."""
    filtered = []
    for msg in messages:
        sender = (msg.get("sender") or "").lower()
        content = (msg.get("content") or "").strip()
        if sender in _NOISE_SENDERS:
            continue
        if len(content) < _MIN_MESSAGE_LENGTH:
            continue
        if content.startswith("["):
            continue
        filtered.append({"sender": sender, "content": content})
    return filtered[-MAX_MESSAGES:]  # most recent


def enrich_channel_discussion(
    channel_id: str | None,
    task_description: str,
    chat: Any,
) -> str:
    """Summarize channel discussion into structured design context.

    Args:
        channel_id: Source channel to read messages from.
        task_description: Task description (used to focus extraction).
        chat: ChatManager instance with get_channel_messages().

    Returns:
        Markdown-formatted context string, or "" on any failure.
    """
    if not channel_id:
        return ""
    try:
        # Read messages from the ChatManager
        messages = _get_messages(chat, channel_id)
        if not messages:
            return ""

        filtered = _filter_messages(messages)
        if not filtered:
            return ""

        # Build transcript
        transcript_parts = []
        total_chars = 0
        for msg in filtered:
            line = f"{msg['sender']}: {msg['content'][:MAX_PER_MESSAGE_CHARS]}"
            if total_chars + len(line) > MAX_TRANSCRIPT_CHARS:
                break
            transcript_parts.append(line)
            total_chars += len(line)

        if not transcript_parts:
            return ""

        transcript = "\n".join(transcript_parts)
        prompt = _DISCUSSION_SUMMARY_PROMPT.format(
            task_description=task_description[:500],
            transcript=transcript,
        )

        result = _call_local_llm(prompt)
        return result.strip() if result else ""

    except Exception as e:
        logger.debug("Context enrichment failed (non-fatal): %s", e)
        return ""


def _get_messages(chat: Any, channel_id: str) -> list[dict]:
    """Retrieve messages from chat manager. Adapts to available interface.

    ChatManager.get_channel_messages() returns list[Message] dataclass
    instances.  We convert them to dicts for uniform processing.
    """
    # Primary: ChatManager.get_channel_messages (returns list[Message])
    fn = getattr(chat, "get_channel_messages", None)
    if fn:
        try:
            result = fn(channel_id)
            if isinstance(result, list):
                # Convert Message dataclass instances to dicts
                out = []
                for m in result:
                    if hasattr(m, "to_dict"):
                        out.append(m.to_dict())
                    elif isinstance(m, dict):
                        out.append(m)
                    else:
                        # Fallback: extract known fields
                        out.append({
                            "sender": getattr(m, "sender", ""),
                            "content": getattr(m, "content", ""),
                        })
                return out
        except Exception:
            pass

    # Fallback: try other common method names
    for method in ("get_messages", "read_channel"):
        fn = getattr(chat, method, None)
        if fn:
            try:
                result = fn(channel_id)
                if isinstance(result, list):
                    return [
                        m.to_dict() if hasattr(m, "to_dict") else m
                        for m in result
                    ]
                if isinstance(result, dict):
                    return result.get("messages", [])
            except Exception:
                continue

    return []


def _call_local_llm(prompt: str) -> str:
    """Call local LLM for summarization. Returns "" on failure."""
    try:
        from cohort.local import LocalRouter

        router = LocalRouter()
        result = router.route(
            prompt,
            task_type="reasoning",
            response_mode="smart",
        )
        if result is not None and result.text:
            return result.text
    except Exception:
        pass

    # Fallback: direct Ollama HTTP call
    try:
        import urllib.request
        import json

        data = json.dumps({
            "model": "qwen3.5:9b",
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("response", "")
    except Exception:
        pass

    return ""
