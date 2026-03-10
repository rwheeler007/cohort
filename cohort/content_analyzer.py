"""Generic content analyzer -- scores articles against user-defined project strategies.

Replaces the hardcoded PartSpec-specific analyzer. All domain knowledge comes
from the project's content strategy config, not from baked-in constants.
"""

from __future__ import annotations

import re
from typing import Any


def score_article(
    article: dict[str, Any],
    project: dict[str, Any],
) -> dict[str, Any]:
    """Score an article against a project's content strategy.

    Args:
        article: Dict with at least ``title`` and/or ``summary`` text fields.
        project: A project dict from content_projects.json.

    Returns:
        Dict with ``score`` (0-10), ``matched_keywords``, ``pillar_matches``,
        ``audience_match``, and ``negative_hit`` (bool).
    """
    text = _extract_text(article)
    text_lower = text.lower()

    keywords = project.get("keywords", {})
    critical = keywords.get("critical", [])
    standard = keywords.get("standard", [])
    negative = keywords.get("negative", [])
    pillars = project.get("content_pillars", [])
    audiences = project.get("audiences", [])

    # ── Negative keyword gate ──
    for neg in negative:
        if neg.lower() in text_lower:
            return {
                "score": 0,
                "matched_keywords": [],
                "pillar_matches": [],
                "audience_match": None,
                "negative_hit": True,
                "reason": f"Excluded by negative keyword: {neg}",
            }

    # ── Keyword scoring ──
    matched_critical = []
    matched_standard = []

    for kw in critical:
        if kw.lower() in text_lower:
            matched_critical.append(kw)

    for kw in standard:
        if kw.lower() in text_lower:
            matched_standard.append(kw)

    raw_score = len(matched_critical) * 2 + len(matched_standard)

    # Bonus for keyword clusters
    if len(matched_critical) >= 3:
        raw_score += 3  # "perfect storm" bonus
    elif len(matched_critical) >= 2:
        raw_score += 1  # "duo" bonus

    # ── Content pillar matching ──
    pillar_matches = []
    for pillar in pillars:
        pillar_words = pillar.lower().split()
        if any(w in text_lower for w in pillar_words):
            pillar_matches.append(pillar)
            raw_score += 1  # pillar match bonus

    # ── Audience detection ──
    audience_match = None
    for aud in audiences:
        pain_points = aud.get("pain_points", [])
        hits = sum(1 for pp in pain_points if pp.lower() in text_lower)
        if hits > 0:
            audience_match = aud.get("name", "unknown")
            raw_score += hits  # pain point relevance
            break  # best match

    # Normalize to 0-10
    score = min(10, raw_score)

    return {
        "score": score,
        "matched_keywords": matched_critical + matched_standard,
        "pillar_matches": pillar_matches,
        "audience_match": audience_match,
        "negative_hit": False,
    }


def build_llm_scoring_prompt(
    article: dict[str, Any],
    project: dict[str, Any],
) -> str:
    """Build an LLM prompt for relevance scoring using the project's strategy.

    Used when relevance_mode is 'llm' or 'hybrid'.
    """
    text = _extract_text(article)
    pillars = ", ".join(project.get("content_pillars", [])) or "general"
    brand_voice = ", ".join(project.get("brand_voice", [])) or "professional"

    audiences_desc = ""
    for aud in project.get("audiences", []):
        name = aud.get("name", "general audience")
        pains = ", ".join(aud.get("pain_points", []))
        audiences_desc += f"- {name}"
        if pains:
            audiences_desc += f" (pain points: {pains})"
        audiences_desc += "\n"

    if not audiences_desc:
        audiences_desc = "- General audience\n"

    return f"""Score this article's relevance to the following brand on a scale of 0-10.

Brand: {project.get('name', 'Unknown')}
Description: {project.get('description', 'No description')}
Content pillars: {pillars}
Brand voice: {brand_voice}
Target audiences:
{audiences_desc}
Article:
{text[:2000]}

Respond with ONLY a JSON object: {{"score": <0-10>, "reason": "<one sentence>"}}"""


def _extract_text(article: dict[str, Any]) -> str:
    """Extract searchable text from an article dict."""
    parts = []
    for key in ("title", "summary", "description", "content", "text"):
        val = article.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return " ".join(parts)
