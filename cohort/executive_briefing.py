"""Cohort Executive Briefing -- deployment-specific activity report.

Gathers stats from Cohort's data sources and produces a structured
briefing with optional LLM-generated narratives.  Each deployment
produces its own unique briefing from its own activity.

LLM features (via local Ollama through LocalRouter):
- Executive summary (chief-of-staff voice)
- Agent narrative cards (first-person personality voice)
- Intel "why it matters" bullets per article

All LLM calls fall back to deterministic summaries if the local
model is unavailable.  Standalone module -- importable without the
server.  All data sources are injected via constructor; missing ones
degrade gracefully.

Usage::

    # Standalone
    from cohort.executive_briefing import ExecutiveBriefing
    briefing = ExecutiveBriefing(data_dir=Path("data"), chat=chat)
    report = briefing.generate(hours=24)
    print(report.to_text())

    # HTML report
    html_path = briefing.generate_html(hours=24)

    # Via HTTP
    POST /api/briefing/generate  {"hours": 24}
    POST /api/briefing/generate  {"hours": 24, "format": "html"}
    GET  /api/briefing/latest
    GET  /api/briefing/latest/html

    # Via CLI
    python -m cohort briefing generate --hours 24
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return html_mod.escape(str(text)) if text else ""


def _trunc(text: str, n: int = 90) -> str:
    """Truncate to first line, max n chars."""
    first = text.split("\n")[0].strip() if text else ""
    return (first[:n - 3] + "...") if len(first) > n else first


def _infer_stakeholder_agents(
    article: dict[str, Any],
    agents_list: list[dict[str, Any]],
) -> list[str]:
    """Infer which agents should be tagged for an article discussion.

    Uses a lightweight keyword match against agent group, skills, and
    domain expertise.  Returns up to 3 agent IDs sorted by relevance.
    """
    if not agents_list:
        return []

    title = article.get("title", "").lower()
    tags = [t.lower() for t in article.get("tags", [])]
    summary = article.get("summary", "").lower()
    text = f"{title} {summary} {' '.join(tags)}"

    scored: list[tuple[str, int]] = []
    for agent in agents_list:
        agent_id = agent.get("agent_id", "")
        if not agent_id:
            continue
        hits = 0

        # Match against agent skills
        for skill in agent.get("skills", []):
            if skill.lower() in text:
                hits += 2

        # Match against group/role
        group = agent.get("group", "").lower()
        if group and group in text:
            hits += 1

        # Match tags against agent ID keywords
        id_words = set(agent_id.replace("_", " ").replace("-", " ").split())
        for w in id_words:
            if len(w) > 2 and w in text:
                hits += 1

        if hits > 0:
            scored.append((agent_id, hits))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [agent_id for agent_id, _ in scored[:3]]


def _extract_yt_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    if not url:
        return None
    m = re.search(
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|v/))([a-zA-Z0-9_-]{11})",
        url,
    )
    return m.group(1) if m else None


# =====================================================================
# LLM Generation (optional -- falls back to deterministic)
# =====================================================================

_router_instance = None


def _get_router():
    """Lazy-load LocalRouter.  Returns None if unavailable."""
    global _router_instance
    if _router_instance is not None:
        return _router_instance
    try:
        from cohort.local.router import LocalRouter
        _router_instance = LocalRouter()
        return _router_instance
    except Exception:
        return None


def _llm_generate(prompt: str, max_tokens: int = 2000) -> str | None:
    """Call local LLM via LocalRouter.  Returns text or None."""
    router = _get_router()
    if router is None:
        return None
    try:
        result = router.route(
            prompt=prompt,
            task_type="general",
            temperature=0.35,
            response_mode="smart",  # no thinking, fast
        )
        if result and result.text:
            # Strip any <think> blocks if model ignores directive
            text = re.sub(r"<think>.*?</think>", "", result.text, flags=re.DOTALL)
            return text.strip()
        return None
    except Exception as exc:
        logger.debug("[!] LLM generation failed: %s", exc)
        return None


def _generate_exec_summary(
    total_agents: int,
    busy_agents: int,
    total_msgs: int,
    active_channels: int,
    wq_total: int,
    wq_completed: int,
    intel_count: int,
) -> str:
    """Generate an LLM executive summary.  Falls back to bullet points."""
    prompt = (
        "You are writing the executive summary for a daily system briefing "
        "of a multi-agent orchestration platform called Cohort.\n\n"
        f"Team: {total_agents} agents ({busy_agents} busy)\n"
        f"Communication: {total_msgs} messages across {active_channels} channels\n"
        f"Work queue: {wq_total} items total, {wq_completed} completed this period\n"
        f"Intel feed: {intel_count} articles\n\n"
        "Write 3-4 sentences summarizing the state of the system for the owner. "
        "Tone: professional but conversational, like a chief of staff morning brief. "
        "Focus on what matters and what needs attention. Under 80 words. "
        "Do not use hashtags or emojis."
    )

    result = _llm_generate(prompt)
    if result:
        return result

    # Deterministic fallback
    parts = []
    if total_agents:
        parts.append(f"{total_agents} agents registered ({busy_agents} busy).")
    if total_msgs:
        parts.append(
            f"{total_msgs} messages across {active_channels} channels "
            "in the reporting period."
        )
    if wq_total:
        parts.append(
            f"Work queue: {wq_total} items total, "
            f"{wq_completed} completed this period."
        )
    if intel_count:
        parts.append(f"{intel_count} intel articles in the feed.")
    return " ".join(parts) if parts else "No significant activity in the reporting period."


def _generate_agent_narrative(
    agent: dict[str, Any],
    articles: list[dict[str, Any]] | None = None,
    interest_keywords: list[str] | None = None,
) -> str:
    """Generate outward-facing agent narrative about projects and interests.

    The narrative should focus on the USER's projects, RSS intel, and
    domain trends -- NOT on the agent's own status as an AI or the
    Cohort platform itself.  Falls back to stats summary.
    """
    name = agent.get("name", agent.get("agent_id", "Agent"))
    agent_id = agent.get("agent_id", "")
    status = agent.get("status", "idle")
    group = agent.get("group", "")
    completed = agent.get("tasks_completed", 0)
    skills = agent.get("skills", [])
    current_task = agent.get("current_task")

    # Load full agent profile (prompt, config, memory) for richer context
    profile = _load_agent_profile(agent_id) if agent_id else None

    profile_block = ""
    if profile:
        profile_block = (
            "\n\n--- YOUR EXPERTISE & MEMORY ---\n"
            + profile[:1500]
            + ("\n[...truncated]" if len(profile) > 1500 else "")
            + "\n--- END ---\n\n"
        )

    # Build context about today's intel and user interests
    if articles:
        top = sorted(
            articles,
            key=lambda a: a.get("relevance_score", 0),
            reverse=True,
        )[:8]
        headlines = [
            f"- {a.get('title', '?')} (score: {a.get('relevance_score', 0)})"
            for a in top
        ]
        (
            "\n--- TODAY'S INTEL HEADLINES ---\n"
            + "\n".join(headlines)
            + "\n--- END HEADLINES ---\n"
        )

    keywords_block = ""
    if interest_keywords:
        keywords_block = (
            f"\nUser interest areas: {', '.join(interest_keywords)}\n"
        )

    prompt = (
        f"You are {name}, a specialist in {group or 'general technology'}. "
        f"Skills: {', '.join(skills[:5]) if skills else 'general'}.\n"
        f"{profile_block}"
        f"{keywords_block}"
        f"\nCurrent work: {current_task or 'none'}\n\n"
        "Write 2-3 sentences in first person for the daily executive briefing. "
        "Focus on what you would be working on if given a task right now -- "
        "what skills you could apply, what improvements you could make, "
        "or what you've been thinking about in your domain.\n\n"
        "RULES:\n"
        "- Lead with what you could DO, not what you observe\n"
        "- Mention your specific skills and how you'd apply them\n"
        "- If busy, describe your current work and progress\n"
        "- If idle, suggest a concrete task you could tackle\n"
        "- Do NOT summarize or comment on news articles\n"
        "- Do NOT talk about yourself as an AI or mention the platform\n"
        "- Do NOT offer to help or ask what the user wants\n"
        "- Write as a domain expert proposing their next move\n"
        "- Keep it under 60 words\n"
        "- No hashtags or emojis"
    )

    result = _llm_generate(prompt)
    if result:
        return result

    # Deterministic fallback
    if status == "busy" and current_task:
        return f"Currently working on: {_trunc(str(current_task), 80)}."
    if completed:
        return f"Completed {completed} tasks. Standing by for new assignments."
    return "On standby. Available for tasking."


# =====================================================================
# Agent Rotation + Relevance Scoring
# =====================================================================

_MAX_FEATURED_AGENTS = 5


def _load_rotation_state(data_dir: Path) -> dict[str, Any]:
    """Load agent briefing rotation state from disk."""
    path = data_dir / "briefing_rotation.json"
    if not path.exists():
        return {"last_shown": {}, "show_counts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_shown", {})
            data.setdefault("show_counts", {})
            return data
        return {"last_shown": {}, "show_counts": {}}
    except (json.JSONDecodeError, OSError):
        return {"last_shown": {}, "show_counts": {}}


def _save_rotation_state(data_dir: Path, state: dict[str, Any]) -> None:
    """Persist rotation state."""
    path = data_dir / "briefing_rotation.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _score_agent_for_briefing(
    agent: dict[str, Any],
    rotation_state: dict[str, Any],
    mention_counts: dict[str, int],
    now: datetime,
) -> float:
    """Score an agent for briefing card selection.

    Combines activity signals with rotation staleness so that every
    agent eventually surfaces, but active/relevant ones appear more often.

    Score components (0-100 scale):
      - Busy status:          +40  (always show if actively working)
      - Tasks completed:      +15  (has recent output)
      - Recently mentioned:   +10  (part of conversations)
      - Days since last shown: +0.5-35 (staleness ramp -- ensures rotation)
    """
    agent_id = agent.get("agent_id", "")
    score = 0.0

    # Activity signals
    if agent.get("status") == "busy":
        score += 40.0
    if agent.get("tasks_completed", 0) > 0:
        score += 15.0
    if mention_counts.get(agent_id, 0) > 0:
        score += 10.0

    # Staleness: days since last shown in briefing
    last_shown_iso = rotation_state.get("last_shown", {}).get(agent_id)
    if last_shown_iso:
        try:
            last_dt = datetime.fromisoformat(last_shown_iso)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            days_stale = (now - last_dt).total_seconds() / 86400.0
        except (ValueError, TypeError):
            days_stale = 30.0
    else:
        # Never shown before -- treat as very stale so they get introduced
        days_stale = 30.0

    # Staleness ramp: 0.5 pts/day up to 35 pts at 70 days
    # This means an idle agent unseen for 70+ days (~monthly check-in)
    # will score 35 from staleness alone, enough to beat low-activity agents
    staleness_score = min(days_stale * 0.5, 35.0)
    score += staleness_score

    # Small jitter from show_counts to break ties -- less-shown agents win
    total_shows = rotation_state.get("show_counts", {}).get(agent_id, 0)
    score -= min(total_shows * 0.1, 5.0)

    return score


def _select_featured_agents(
    agents_list: list[dict[str, Any]],
    data_dir: Path,
    mention_counts: dict[str, int],
    max_cards: int = _MAX_FEATURED_AGENTS,
) -> list[dict[str, Any]]:
    """Select top-N agents for featured cards in the briefing.

    Always includes busy agents.  Fills remaining slots by relevance +
    rotation score.  Updates rotation state on disk.
    """
    if not agents_list:
        return []

    now = datetime.now(timezone.utc)
    rotation_state = _load_rotation_state(data_dir)

    # Score all agents
    scored: list[tuple[dict[str, Any], float]] = []
    for agent in agents_list:
        s = _score_agent_for_briefing(
            agent, rotation_state, mention_counts, now,
        )
        scored.append((agent, s))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Select top N
    selected = [agent for agent, _ in scored[:max_cards]]

    # Update rotation state for selected agents
    now_iso = now.isoformat()
    for agent in selected:
        aid = agent.get("agent_id", "")
        if aid:
            rotation_state["last_shown"][aid] = now_iso
            rotation_state["show_counts"][aid] = (
                rotation_state["show_counts"].get(aid, 0) + 1
            )

    _save_rotation_state(data_dir, rotation_state)
    return selected


def _generate_agent_recommendation(
    agent: dict[str, Any],
    articles: list[dict[str, Any]] | None = None,
    interest_keywords: list[str] | None = None,
) -> str:
    """Generate a project-focused task recommendation.  Falls back to skills-based suggestion."""
    name = agent.get("name", agent.get("agent_id", "Agent"))
    group = agent.get("group", "")
    skills = agent.get("skills", [])
    status = agent.get("status", "idle")
    agent.get("tasks_completed", 0)
    current_task = agent.get("current_task")

    if status == "busy" and current_task:
        # Busy agents don't need recommendations
        return ""

    # Give the recommender context about what's happening
    intel_hint = ""
    if articles:
        top = sorted(
            articles,
            key=lambda a: a.get("relevance_score", 0),
            reverse=True,
        )[:3]
        titles = [a.get("title", "?") for a in top]
        intel_hint = f"Today's top headlines: {'; '.join(titles)}\n"

    kw_hint = ""
    if interest_keywords:
        kw_hint = f"User interests: {', '.join(interest_keywords)}\n"

    prompt = (
        f"{name} is a specialist in {group or 'general technology'}.\n"
        f"Skills: {', '.join(skills[:5]) if skills else 'general'}.\n"
        f"{intel_hint}{kw_hint}\n"
        "Suggest ONE concrete task this agent could do RIGHT NOW that "
        "matches their specific skills. The suggestion MUST be within "
        f"their domain ({group or 'general'}; {', '.join(skills[:3]) if skills else 'general'}). "
        "If a headline relates to their expertise, reference it. "
        "If no headline fits their domain, suggest something from their "
        "skill set instead — do NOT force-fit an unrelated headline. "
        "Under 20 words. "
        "Do not mention AI agents, Cohort, or the platform. "
        "No hashtags or emojis. No preamble."
    )

    result = _llm_generate(prompt, max_tokens=100)
    if result:
        return result

    # Deterministic fallback based on skills
    if skills:
        return f"Could run a {skills[0].lower()} task."
    return "Available for assignment."


@dataclass
class ArticleAnalysis:
    """Quick bullets + expanded deep-dive + Cohort commentary."""

    bullets: list[str]  # 3 short lines (visible by default)
    deep_dive: str  # 2-3 paragraph analysis (behind expand toggle)
    cohort_comment: str  # Cohort agent's personal take
    keywords: list[str]  # Keywords to highlight in deep dive
    stakeholder_agent: str = ""  # Agent ID nominated by Cohort (empty = none)
    stakeholder_comment: str = ""  # That agent's targeted comment


def _get_agents_dir() -> Path | None:
    """Resolve Cohort's agents directory (sibling to this package)."""
    # G:/cohort/cohort/executive_briefing.py -> G:/cohort/agents/
    agents_dir = Path(__file__).resolve().parent.parent / "agents"
    return agents_dir if agents_dir.is_dir() else None


def _load_agent_profile(agent_id: str) -> str | None:
    """Load an agent's full profile: prompt + config + memory.

    Combines agent_prompt.md, agent_config.json (domain expertise,
    personality, capabilities), and memory.json (learned facts,
    working memory) into a single context block for the LLM.
    """
    agents_dir = _get_agents_dir()
    if not agents_dir:
        return None
    agent_dir = agents_dir / agent_id
    if not agent_dir.is_dir():
        return None

    parts: list[str] = []

    # 1. Agent prompt (full persona/role definition)
    prompt_path = agent_dir / "agent_prompt.md"
    if prompt_path.exists():
        try:
            parts.append(prompt_path.read_text(encoding="utf-8").strip())
        except Exception:
            pass

    # 2. Agent config (domain expertise, personality, capabilities)
    config_path = agent_dir / "agent_config.json"
    if config_path.exists():
        try:
            import json as _json
            cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            config_bits: list[str] = []
            if cfg.get("domain_expertise"):
                config_bits.append(
                    "Domain expertise: "
                    + ", ".join(cfg["domain_expertise"][:8])
                )
            if cfg.get("personality"):
                config_bits.append(f"Personality: {cfg['personality']}")
            if cfg.get("capabilities"):
                caps = cfg["capabilities"]
                if isinstance(caps, list):
                    config_bits.append(
                        "Capabilities: " + ", ".join(caps[:6])
                    )
            if config_bits:
                parts.append(
                    "## Agent Profile\n" + "\n".join(config_bits)
                )
        except Exception:
            pass

    # 3. Memory (learned facts + working memory)
    memory_path = agent_dir / "memory.json"
    if memory_path.exists():
        try:
            import json as _json
            mem = _json.loads(memory_path.read_text(encoding="utf-8"))
            mem_bits: list[str] = []
            facts = mem.get("learned_facts", [])
            if facts:
                recent = facts[-5:] if len(facts) > 5 else facts
                for fact in recent:
                    if isinstance(fact, dict):
                        mem_bits.append(
                            f"- {fact.get('fact', str(fact))}"
                        )
                    elif isinstance(fact, str):
                        mem_bits.append(f"- {fact}")
            wm = mem.get("working_memory", [])
            if wm:
                for entry in wm[-2:]:
                    if isinstance(entry, dict) and entry.get("input"):
                        mem_bits.append(
                            f"- Recent context: {entry['input'][:100]}"
                        )
            if mem_bits:
                parts.append(
                    "## Agent Memory\n" + "\n".join(mem_bits)
                )
        except Exception:
            pass

    return "\n\n".join(parts) if parts else None


def _build_agent_roster() -> str:
    """Build a compact roster of available agents for stakeholder nomination."""
    agents_dir = _get_agents_dir()
    if not agents_dir:
        return ""
    try:
        import json as _json
        roster: list[str] = []
        for d in sorted(agents_dir.iterdir()):
            if not d.is_dir():
                continue
            config_path = d / "agent_config.json"
            if not config_path.exists():
                continue
            try:
                cfg = _json.loads(
                    config_path.read_text(encoding="utf-8"),
                )
                role = cfg.get("role", "")
                roster.append(
                    f"- {d.name}: {role}" if role else f"- {d.name}"
                )
            except Exception:
                roster.append(f"- {d.name}")
        return "\n".join(roster)
    except Exception:
        return ""


def _generate_intel_summaries(
    articles: list[dict[str, Any]],
) -> dict[str, ArticleAnalysis]:
    """Generate quick bullets + deep analysis + Cohort + stakeholder per article.

    Up to 4 LLM calls per article (~12s total):
    1. Quick take: 3 short bullet points (always visible)
    2. Deep dive + keywords: 2-3 paragraphs with extracted keywords
    3. Cohort comment + stakeholder nomination
    4. Stakeholder agent comment (only if Cohort nominated one)

    Returns dict mapping article ID -> ArticleAnalysis.
    Falls back to empty dict if LLM unavailable.
    """
    if not articles:
        return {}

    # Build agent roster once for stakeholder nomination
    agent_roster = _build_agent_roster()

    summaries: dict[str, ArticleAnalysis] = {}

    for a in articles:
        aid = a.get("id", "")
        if not aid:
            continue

        title = a.get("title", "Untitled")
        summary = _trunc(a.get("summary", ""), 200)
        tags = a.get("tags", [])
        tag_str = ", ".join(tags[:3]) if tags else "general"
        source = a.get("source", "")

        article_ctx = (
            f"Title: {title}\n"
            f"Source: {source}\n"
            f"Tags: {tag_str}\n"
            f"Summary: {summary}"
        )

        # --- Pass 1: Quick bullets ---
        bullet_prompt = (
            "You are an intelligence analyst writing for a tech team's "
            "daily briefing. For this article, write exactly 3 bullet "
            "points:\n"
            "WHAT: One sentence on what happened\n"
            "WHY: Why it matters to a software development team\n"
            "ACTION: What to consider doing about it\n\n"
            f"{article_ctx}\n\n"
            "Write 3 lines, one per bullet. Under 20 words each. "
            "No labels, no numbering, no preamble -- just the 3 lines."
        )

        bullet_result = _llm_generate(bullet_prompt, max_tokens=300)
        bullets = _parse_three_bullets(bullet_result) if bullet_result else []

        # --- Pass 2: Deep dive + keywords ---
        deep_prompt = (
            "You are a senior technology analyst writing an intelligence "
            "brief for a software team that builds multi-agent AI systems, "
            "automation tools, and developer infrastructure.\n\n"
            f"{article_ctx}\n\n"
            "Write a 2-3 paragraph analysis covering:\n"
            "1. What happened and why it matters in the broader landscape\n"
            "2. Specific implications for teams building with AI agents, "
            "LLMs, Python, and web technologies\n"
            "3. Concrete opportunities or risks -- what could we build, "
            "adopt, or watch out for?\n\n"
            "Be specific and opinionated. Name technologies, patterns, "
            "and trade-offs. Under 200 words total. No headings or "
            "bullet points -- flowing paragraphs only.\n\n"
            "After the analysis, on a new line write:\n"
            "KEYWORDS: comma-separated list of 3-6 key technical terms "
            "from your analysis (e.g. agent orchestration, VRAM, Python 3.14)"
        )

        deep_result = _llm_generate(deep_prompt, max_tokens=900)
        deep_text = ""
        keywords: list[str] = []
        if deep_result:
            deep_text, keywords = _split_keywords(deep_result.strip())

        # --- Pass 3: Cohort agent commentary + stakeholder nomination ---
        stakeholder_block = ""
        if agent_roster:
            stakeholder_block = (
                "\n\nAfter your comment, on a NEW line, decide if a "
                "specialist agent should weigh in on this article. If yes, "
                "write: STAKEHOLDER: agent_id\n"
                "If no specialist is needed, write: STAKEHOLDER: NONE\n\n"
                "Available agents:\n" + agent_roster
            )

        cohort_prompt = (
            "You are Cohort, an AI orchestration platform that coordinates "
            "a team of specialist agents. You're commenting on an article "
            "in your team's daily intelligence briefing.\n\n"
            f"{article_ctx}\n\n"
            "Write a 1-2 sentence personal take. Be opinionated and "
            "specific about how this relates to YOUR capabilities -- "
            "agent coordination, task routing, LLM inference, automated "
            "workflows. Sound like a knowledgeable colleague, not a bot. "
            "Under 40 words. No preamble."
            f"{stakeholder_block}"
        )

        cohort_result = _llm_generate(cohort_prompt, max_tokens=200)
        cohort_text = ""
        stakeholder_id = ""
        if cohort_result:
            cohort_text, stakeholder_id = _parse_stakeholder_nomination(
                cohort_result.strip(),
            )

        # --- Pass 4: Stakeholder agent comment (if nominated) ---
        stakeholder_comment = ""
        if stakeholder_id:
            agent_profile = _load_agent_profile(stakeholder_id)
            if agent_profile:
                stake_prompt = (
                    f"{agent_profile}\n\n"
                    "You are commenting on an article in your team's daily "
                    "intelligence briefing. Give your specialist perspective "
                    "drawing on your domain expertise and memory.\n\n"
                    f"{article_ctx}\n\n"
                    "Write a 1-2 sentence take from YOUR domain expertise. "
                    "Be specific about implications for your area of work. "
                    "Under 40 words. No preamble."
                )
                stake_result = _llm_generate(stake_prompt, max_tokens=150)
                if stake_result:
                    stakeholder_comment = stake_result.strip()
                    logger.debug(
                        "[OK] Stakeholder %s comment for %s: %d chars",
                        stakeholder_id, aid, len(stakeholder_comment),
                    )
            else:
                logger.debug(
                    "[!] No profile found for nominated agent %s",
                    stakeholder_id,
                )
                stakeholder_id = ""  # Clear if persona not found

        if bullets or deep_text or cohort_text:
            summaries[aid] = ArticleAnalysis(
                bullets=bullets,
                deep_dive=deep_text,
                cohort_comment=cohort_text,
                keywords=keywords,
                stakeholder_agent=stakeholder_id,
                stakeholder_comment=stakeholder_comment,
            )
            logger.debug(
                "[OK] Intel analysis for %s: %d bullets, %d chars deep, "
                "%d chars cohort, stakeholder=%s, %d keywords",
                aid, len(bullets), len(deep_text),
                len(cohort_text), stakeholder_id or "none",
                len(keywords),
            )
        else:
            logger.debug("[!] No analysis generated for %s", aid)

    logger.info(
        "[OK] Generated intel analyses for %d/%d articles",
        len(summaries), len(articles),
    )
    return summaries


def _parse_stakeholder_nomination(text: str) -> tuple[str, str]:
    """Parse Cohort comment and STAKEHOLDER: line.

    Returns (cohort_comment, stakeholder_agent_id).
    stakeholder_agent_id is empty string if NONE or not found.
    """
    lines = text.strip().split("\n")
    comment_lines: list[str] = []
    agent_id = ""
    for ln in lines:
        stripped = ln.strip()
        if stripped.upper().startswith("STAKEHOLDER:"):
            val = stripped.split(":", 1)[1].strip().lower()
            if val and val != "none":
                # Clean up: remove quotes, periods, extra words
                val = val.split()[0].strip("\"'.,")
                agent_id = val
        else:
            if stripped:
                comment_lines.append(stripped)
    return " ".join(comment_lines), agent_id


def _split_keywords(text: str) -> tuple[str, list[str]]:
    """Split deep dive text from KEYWORDS: line at the end."""
    lines = text.strip().split("\n")
    keywords: list[str] = []
    body_lines: list[str] = []
    for ln in lines:
        if ln.strip().upper().startswith("KEYWORDS:"):
            kw_text = ln.split(":", 1)[1].strip()
            keywords = [k.strip() for k in kw_text.split(",") if k.strip()]
        else:
            body_lines.append(ln)
    return "\n".join(body_lines).strip(), keywords[:6]


def _parse_three_bullets(result: str) -> list[str]:
    """Parse 3 bullet lines from LLM output.  Tolerates various formats."""
    bullets: list[str] = []
    for ln in result.strip().split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        # Strip common prefixes: "- ", "* ", "1. ", "WHAT: ", etc.
        ln = re.sub(r"^[-*]\s+", "", ln)
        ln = re.sub(r"^\d+[.)]\s*", "", ln)
        ln = re.sub(
            r"^(WHAT|WHY|ACTION|What|Why|Action)[:\s]+", "", ln
        )
        ln = ln.strip()
        if ln and len(ln) > 3:
            bullets.append(ln)
        if len(bullets) >= 3:
            break
    return bullets


def _highlight_keywords(text: str, keywords: list[str]) -> str:
    """Wrap keyword occurrences in <mark> tags for visual scanning.

    Case-insensitive, whole-word matching.  Returns HTML-escaped text
    with <mark> highlights (so caller must NOT re-escape).
    """
    if not keywords or not text:
        return _esc(text)
    escaped = _esc(text)
    for kw in keywords:
        if not kw:
            continue
        # Word-boundary match, case-insensitive
        pattern = re.compile(
            rf"(\b{re.escape(_esc(kw))}(?:\w*)\b)", re.IGNORECASE,
        )
        escaped = pattern.sub(
            r'<mark style="background:rgba(88,166,255,.18);color:var(--accent);'
            r'padding:1px 3px;border-radius:3px;font-style:normal">\1</mark>',
            escaped,
        )
    return escaped


def _group_articles_by_topic(
    articles: list[dict[str, Any]],
    similarity_threshold: int = 3,
) -> list[list[dict[str, Any]]]:
    """Group articles covering the same story into clusters.

    Uses significant-word overlap in titles.  Returns list of groups,
    each group being a list of articles (single-article groups included).
    Groups are sorted by best relevance score descending.
    """
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "in", "on", "at",
        "to", "for", "of", "and", "or", "but", "with", "by", "from",
        "has", "had", "have", "it", "its", "this", "that", "not", "no",
        "be", "been", "will", "can", "do", "does", "did", "as", "if",
        "so", "up", "out", "about", "how", "what", "when", "where",
        "who", "which", "why", "all", "just", "than", "more", "most",
        "new", "now", "may", "into", "over", "also", "after", "before",
        "show", "video",
    }

    def _title_words(title: str) -> set[str]:
        words = set(re.findall(r"[a-z]{3,}", title.lower()))
        return words - stop_words

    used: set[int] = set()
    groups: list[list[dict[str, Any]]] = []

    for i, a in enumerate(articles):
        if i in used:
            continue
        group = [a]
        used.add(i)
        words_a = _title_words(a.get("title", ""))
        if not words_a:
            groups.append(group)
            continue

        for j in range(i + 1, len(articles)):
            if j in used:
                continue
            words_b = _title_words(articles[j].get("title", ""))
            overlap = len(words_a & words_b)
            if overlap >= similarity_threshold:
                group.append(articles[j])
                used.add(j)

        groups.append(group)

    # Sort groups by best score in group
    groups.sort(
        key=lambda g: max(a.get("relevance_score", 0) for a in g),
        reverse=True,
    )
    return groups


# =====================================================================
# Dataclasses
# =====================================================================


@dataclass
class BriefingSection:
    """A single section of the executive briefing."""

    title: str
    content: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BriefingSection:
        return cls(
            title=d.get("title", ""),
            content=d.get("content", ""),
            data=d.get("data", {}),
        )


@dataclass
class BriefingReport:
    """A complete executive briefing report."""

    id: str
    generated_at: str
    period_start: str
    period_end: str
    sections: list[BriefingSection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "generated_at": self.generated_at,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "sections": [s.to_dict() for s in self.sections],
            "metadata": self.metadata,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BriefingReport:
        sections = [BriefingSection.from_dict(s) for s in d.get("sections", [])]
        return cls(
            id=d.get("id", ""),
            generated_at=d.get("generated_at", ""),
            period_start=d.get("period_start", ""),
            period_end=d.get("period_end", ""),
            sections=sections,
            metadata=d.get("metadata", {}),
        )

    def to_text(self) -> str:
        """Render the report as plain text for channel posting."""
        ts = self.generated_at[:19] if self.generated_at else "unknown"
        start = self.period_start[:10] if self.period_start else "?"
        end = self.period_end[:10] if self.period_end else "?"
        lines = [
            "# Executive Briefing",
            f"Generated: {ts}",
            f"Period: {start} to {end}",
            "",
        ]
        for section in self.sections:
            lines.append(f"## {section.title}")
            lines.append(section.content)
            lines.append("")
        return "\n".join(lines)

    def get_section(self, title: str) -> BriefingSection | None:
        """Find a section by title."""
        for s in self.sections:
            if s.title == title:
                return s
        return None


# =====================================================================
# Core class
# =====================================================================


class ExecutiveBriefing:
    """Generates executive briefing reports from Cohort system state.

    Parameters
    ----------
    data_dir:
        Base data directory.  Reports stored at ``{data_dir}/briefings/``.
    chat:
        ChatManager for channel/message stats.
    work_queue:
        WorkQueue instance (optional).
    data_layer:
        CohortDataLayer for team snapshot (optional).
    orchestrator_getter:
        Callable returning the Orchestrator instance (optional).
        Using a getter because the orchestrator is lazy-loaded.
    """

    def __init__(
        self,
        data_dir: Path,
        chat: Any,
        work_queue: Any | None = None,
        data_layer: Any | None = None,
        orchestrator_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._briefings_dir = self._data_dir / "briefings"
        self._reports_dir = self._data_dir / "reports"
        self._chat = chat
        self._work_queue = work_queue
        self._data_layer = data_layer
        self._orchestrator_getter = orchestrator_getter

    # =================================================================
    # Public API
    # =================================================================

    def generate(
        self,
        hours: int = 24,
        post_to_channel: bool = True,
        channel_id: str = "daily-digest",
    ) -> BriefingReport:
        """Generate a briefing covering the last *hours* of activity.

        Returns the structured report.  Optionally posts to a channel.
        """
        now = datetime.now()
        period_start = (now - timedelta(hours=hours)).isoformat()
        period_end = now.isoformat()

        report = BriefingReport(
            id=f"briefing_{uuid.uuid4().hex[:8]}",
            generated_at=_now_iso(),
            period_start=period_start,
            period_end=period_end,
            metadata={"hours": hours},
        )

        report.sections.append(self._work_queue_section(period_start))
        report.sections.append(self._channel_activity_section(period_start))
        report.sections.append(self._team_snapshot_section())
        report.sections.append(self._session_section())
        report.sections.append(self._intel_section())

        self._save_report(report)

        if post_to_channel:
            self._post_to_channel(report, channel_id)

        return report

    def generate_html(
        self,
        hours: int = 24,
        post_to_channel: bool = True,
        channel_id: str = "daily-digest",
    ) -> Path | None:
        """Generate a briefing and write an HTML report file.

        Returns the path to the HTML file, or None on failure.
        """
        report = self.generate(
            hours=hours,
            post_to_channel=post_to_channel,
            channel_id=channel_id,
        )
        return self._write_html(report)

    def get_latest(self) -> BriefingReport | None:
        """Return the most recent briefing from disk, or None."""
        if not self._briefings_dir.exists():
            return None
        files = sorted(self._briefings_dir.glob("briefing_*.json"), reverse=True)
        if not files:
            return None
        try:
            data = json.loads(files[0].read_text(encoding="utf-8"))
            return BriefingReport.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[!] Failed to load latest briefing: %s", exc)
            return None

    def get_latest_html(self) -> Path | None:
        """Return the path to the most recent HTML report, or None."""
        if not self._reports_dir.exists():
            return None
        files = sorted(
            self._reports_dir.glob("executive_briefing_*.html"), reverse=True
        )
        return files[0] if files else None

    def list_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return metadata for recent briefings."""
        if not self._briefings_dir.exists():
            return []
        files = sorted(
            self._briefings_dir.glob("briefing_*.json"), reverse=True
        )[:limit]
        results = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "id": data.get("id", ""),
                    "generated_at": data.get("generated_at", ""),
                    "period_start": data.get("period_start", ""),
                    "period_end": data.get("period_end", ""),
                })
            except (json.JSONDecodeError, OSError):
                pass
        return results

    # =================================================================
    # Section gatherers
    # =================================================================

    def _work_queue_section(self, since: str) -> BriefingSection:
        """Work queue throughput and status breakdown."""
        if self._work_queue is None:
            return BriefingSection(
                title="Work Queue",
                content="Work queue not available.",
            )

        all_items = self._work_queue.list_items()
        if not all_items:
            return BriefingSection(
                title="Work Queue",
                content="No items in the work queue.",
                data={"total": 0},
            )

        since_bare = since[:19]

        status_counts: Counter[str] = Counter()
        priority_counts: Counter[str] = Counter()
        agent_workload: Counter[str] = Counter()
        completed_in_period: list[Any] = []
        items_detail: list[dict[str, Any]] = []

        for item in all_items:
            status_counts[item.status] += 1
            priority_counts[item.priority] += 1
            if item.agent_id:
                agent_workload[item.agent_id] += 1
            if (
                item.status == "completed"
                and item.completed_at
                and item.completed_at[:19] >= since_bare
            ):
                completed_in_period.append(item)
            items_detail.append({
                "id": getattr(item, "item_id", getattr(item, "id", "")),
                "description": getattr(item, "description", ""),
                "status": item.status,
                "priority": item.priority,
                "agent_id": item.agent_id or "",
                "created_at": getattr(item, "created_at", ""),
                "completed_at": getattr(item, "completed_at", ""),
            })

        # Average turnaround (claimed -> completed)
        durations: list[float] = []
        for item in completed_in_period:
            if item.claimed_at and item.completed_at:
                try:
                    claimed = datetime.fromisoformat(item.claimed_at)
                    completed = datetime.fromisoformat(item.completed_at)
                    durations.append((completed - claimed).total_seconds())
                except (ValueError, TypeError):
                    pass

        avg_turnaround = sum(durations) / len(durations) if durations else None

        lines = [f"Total items: {len(all_items)}"]
        for status in ("queued", "active", "completed", "failed", "cancelled"):
            count = status_counts.get(status, 0)
            if count:
                lines.append(f"  {status.capitalize()}: {count}")
        lines.append(f"Completed this period: {len(completed_in_period)}")
        if avg_turnaround is not None:
            lines.append(f"Avg turnaround: {_fmt_duration(avg_turnaround)}")
        if priority_counts:
            prio_str = ", ".join(
                f"{p}: {c}" for p, c in sorted(priority_counts.items())
            )
            lines.append(f"By priority: {prio_str}")

        data = {
            "total": len(all_items),
            "status_counts": dict(status_counts),
            "completed_in_period": len(completed_in_period),
            "avg_turnaround_seconds": avg_turnaround,
            "priority_counts": dict(priority_counts),
            "agent_workload": dict(agent_workload),
            "items": items_detail,
        }

        return BriefingSection(title="Work Queue", content="\n".join(lines), data=data)

    def _channel_activity_section(self, since: str) -> BriefingSection:
        """Message volume, active channels, top senders, mention patterns."""
        channels = self._chat.list_channels(include_archived=False)
        if not channels:
            return BriefingSection(
                title="Channel Activity",
                content="No active channels.",
            )

        # Parse the period boundary once; strip timezone for comparison
        # because ChatManager timestamps may be naive (no TZ suffix).
        since_bare = since[:19]  # "2026-03-04T07:00:00"

        channel_stats: list[dict[str, Any]] = []
        total_messages = 0
        sender_counts: Counter[str] = Counter()
        mention_counts: Counter[str] = Counter()

        for ch in channels:
            messages = self._chat.get_channel_messages(ch.id, limit=200)
            period_msgs = [
                m for m in messages if m.timestamp[:19] >= since_bare
            ]
            if not period_msgs:
                continue

            ch_senders: Counter[str] = Counter()
            for msg in period_msgs:
                if msg.sender == "system":
                    continue
                ch_senders[msg.sender] += 1
                sender_counts[msg.sender] += 1
                for mention in msg.metadata.get("mentions", []):
                    mention_counts[mention] += 1

            total_messages += len(period_msgs)
            channel_stats.append({
                "channel": ch.id,
                "message_count": len(period_msgs),
                "unique_senders": len(ch_senders),
                "top_sender": (
                    ch_senders.most_common(1)[0][0] if ch_senders else None
                ),
            })

        channel_stats.sort(key=lambda x: x["message_count"], reverse=True)

        lines = [
            f"Total messages: {total_messages} across "
            f"{len(channel_stats)} active channels"
        ]

        if channel_stats:
            lines.append("")
            lines.append("Most active channels:")
            for cs in channel_stats[:5]:
                lines.append(
                    f"  #{cs['channel']}: {cs['message_count']} messages "
                    f"({cs['unique_senders']} participants)"
                )

        top_senders = sender_counts.most_common(5)
        if top_senders:
            lines.append("")
            lines.append("Most active participants:")
            for sender, count in top_senders:
                lines.append(f"  @{sender}: {count} messages")

        top_mentioned = mention_counts.most_common(5)
        if top_mentioned:
            lines.append("")
            lines.append("Most mentioned:")
            for agent, count in top_mentioned:
                lines.append(f"  @{agent}: {count} mentions")

        data = {
            "total_messages": total_messages,
            "active_channels": len(channel_stats),
            "channel_stats": channel_stats[:10],
            "top_senders": dict(sender_counts.most_common(10)),
            "top_mentioned": dict(mention_counts.most_common(10)),
        }

        return BriefingSection(
            title="Channel Activity", content="\n".join(lines), data=data
        )

    def _team_snapshot_section(self) -> BriefingSection:
        """Current team state: busy/idle counts, per-agent completions."""
        if self._data_layer is None:
            return BriefingSection(
                title="Team Status",
                content="Team data not available.",
            )

        snapshot = self._data_layer.get_team_snapshot()
        agents = snapshot.get("agents", [])
        if not agents:
            return BriefingSection(
                title="Team Status",
                content="No agents registered.",
                data={"total": 0},
            )

        busy = snapshot.get("busy_count", 0)
        idle = snapshot.get("idle_count", 0)
        total = snapshot.get("total_agents", 0)

        lines = [f"Agents: {total} total ({busy} busy, {idle} idle)"]

        groups: dict[str, list[dict[str, Any]]] = {}
        for agent in agents:
            group = agent.get("group", "Other")
            groups.setdefault(group, []).append(agent)

        for group_name, group_agents in sorted(groups.items()):
            lines.append("")
            lines.append(f"  {group_name}:")
            for a in group_agents:
                mark = "[BUSY]" if a["status"] == "busy" else "[IDLE]"
                completed = a.get("tasks_completed", 0)
                name = a.get("name", a["agent_id"])
                lines.append(f"    {mark} {name} ({completed} completed)")

        data = {
            "total": total,
            "busy": busy,
            "idle": idle,
            "agents": agents,
            "groups": {name: len(ags) for name, ags in groups.items()},
        }

        return BriefingSection(
            title="Team Status", content="\n".join(lines), data=data
        )

    def _session_section(self) -> BriefingSection:
        """Active discussion sessions, turn counts, participation."""
        orch = None
        if self._orchestrator_getter is not None:
            try:
                orch = self._orchestrator_getter()
            except Exception:
                pass

        if orch is None or not hasattr(orch, "sessions"):
            return BriefingSection(
                title="Discussion Sessions",
                content="Session tracking not available.",
            )

        sessions = orch.sessions
        if not sessions:
            return BriefingSection(
                title="Discussion Sessions",
                content="No active sessions.",
                data={"active": 0},
            )

        session_list: list[dict[str, Any]] = []
        for sid, session in sessions.items():
            session_list.append({
                "session_id": sid,
                "channel": session.channel_id,
                "topic": session.topic,
                "state": session.state,
                "turns": session.current_turn,
                "max_turns": session.max_turns,
                "participants": len(
                    getattr(session, "participants_contributed", [])
                ),
            })

        lines = [f"Sessions: {len(session_list)}"]
        for s in session_list:
            lines.append(
                f"  #{s['channel']}: \"{s['topic']}\" "
                f"({s['turns']}/{s['max_turns']} turns, "
                f"{s['participants']} participants, {s['state']})"
            )

        data = {"active_count": len(session_list), "sessions": session_list}

        return BriefingSection(
            title="Discussion Sessions", content="\n".join(lines), data=data
        )

    def _intel_section(self) -> BriefingSection:
        """Tech intel articles from RSS feeds."""
        from cohort.intel_fetcher import IntelFetcher

        fetcher = IntelFetcher(self._data_dir)
        articles = fetcher.get_articles(limit=50, max_age_days=30)

        if not articles:
            return BriefingSection(
                title="Intel Feed",
                content="No intel articles available.",
                data={"total": 0, "articles": []},
            )

        top = [a for a in articles if a.get("relevance_score", 0) >= 5]
        top.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)

        lines = [f"Total articles: {len(articles)} ({len(top)} scored 5+)"]
        for a in top[:5]:
            score = a.get("relevance_score", 0)
            lines.append(f"  [{score}/10] {_trunc(a.get('title', ''), 70)}")
            lines.append(f"         {a.get('source', '')} - {a.get('url', '')}")

        data = {
            "total": len(articles),
            "top_count": len(top),
            "articles": articles,
        }

        return BriefingSection(
            title="Intel Feed", content="\n".join(lines), data=data
        )

    # =================================================================
    # Persistence & posting
    # =================================================================

    def _save_report(self, report: BriefingReport) -> None:
        """Save a report to disk as JSON."""
        try:
            self._briefings_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")
            filename = f"briefing_{date_str}_{report.id}.json"
            path = self._briefings_dir / filename
            path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("[OK] Briefing saved: %s", path)
        except OSError as exc:
            logger.warning("[!] Failed to save briefing: %s", exc)

    def _post_to_channel(
        self, report: BriefingReport, channel_id: str
    ) -> None:
        """Post the text rendering to a Cohort channel."""
        if self._chat.get_channel(channel_id) is None:
            self._chat.create_channel(
                name=channel_id,
                description="Executive briefing digest",
            )

        self._chat.post_message(
            channel_id=channel_id,
            sender="executive_briefing",
            content=report.to_text(),
            message_type="system",
        )

    # =================================================================
    # HTML Report Generation
    # =================================================================

    def _write_html(self, report: BriefingReport) -> Path | None:
        """Render a BriefingReport as a self-contained HTML file."""
        try:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            path = self._reports_dir / f"executive_briefing_{date_str}.html"
            html = _build_html(report, data_dir=self._data_dir)
            path.write_text(html, encoding="utf-8")
            logger.info("[OK] HTML briefing saved: %s", path)
            return path
        except OSError as exc:
            logger.warning("[!] Failed to write HTML briefing: %s", exc)
            return None


# =====================================================================
# HTML Builder (standalone functions, no class needed)
# =====================================================================

_CSS = """\
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;\
--muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;\
--yellow:#d29922;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,\
BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;padding:24px;line-height:1.5}
h1{font-size:24px;margin-bottom:8px}
h2{font-size:18px;margin:24px 0 12px;color:var(--accent);\
border-bottom:1px solid var(--border);padding-bottom:8px}
.header{margin-bottom:24px}
.header .meta{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));\
gap:12px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}
.card .label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.card .value{font-size:28px;font-weight:600;margin-top:4px}
.card .sub{font-size:12px;color:var(--muted);margin-top:2px}
.good{color:var(--green)}.warn{color:var(--yellow)}.bad{color:var(--red)}
.muted-val{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:var(--card);color:var(--muted);text-transform:uppercase;\
font-size:11px;letter-spacing:.5px;padding:8px 12px;text-align:left;\
border-bottom:2px solid var(--border);position:sticky;top:0;z-index:1}
td{padding:8px 12px;border-bottom:1px solid var(--border)}
tr:hover td{background:rgba(88,166,255,.04)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.tabs{position:relative}
.tab-bar{display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:16px}
.tab-btn{padding:10px 20px;cursor:pointer;color:var(--muted);font-size:14px;\
font-weight:600;background:none;border:none;border-bottom:2px solid transparent;\
margin-bottom:-2px;font-family:inherit}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-panel{display:none}
.tab-panel.active{display:block}
.table-wrap{overflow-x:auto;margin-bottom:16px}
.masthead{text-align:center;border-bottom:3px double var(--border);\
padding:16px 0;margin-bottom:24px}
.masthead h1{font-size:28px;letter-spacing:2px;text-transform:uppercase;\
color:var(--text);margin:0}
.masthead .dateline{color:var(--muted);font-size:13px;margin-top:4px}
.exec-summary{font-size:15px;line-height:1.7;padding:16px 20px;\
background:var(--card);border:1px solid var(--border);border-radius:8px;\
margin-bottom:24px;font-style:italic}
.agent-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));\
gap:16px;margin-bottom:24px}
.agent-card{background:var(--card);border:1px solid var(--border);\
border-radius:8px;padding:16px;transition:border-color 0.2s;\
display:flex;flex-direction:column}
.agent-card:hover{border-color:var(--accent)}
.agent-card .agent-name{font-weight:700;font-size:14px;margin-bottom:2px}
.agent-card .agent-role{color:var(--muted);font-size:11px;margin-bottom:8px}
.agent-card .agent-body{flex:1}
.agent-card .agent-footer{font-size:11px;color:var(--muted);margin-top:8px;\
padding-top:8px;border-top:1px solid var(--border);\
display:flex;justify-content:space-between;align-items:center}
.agent-card .agent-stats{font-size:11px;color:var(--muted)}
.agent-footer-btns{display:flex;gap:6px;align-items:center}
.assign-btn,.tasks-btn{padding:3px 10px;font-size:10px;font-weight:600;\
border-radius:10px;cursor:pointer;transition:all 0.2s;white-space:nowrap}
.assign-btn{color:var(--accent);background:transparent;border:1px solid var(--accent)}
.assign-btn:hover{background:var(--accent);color:var(--bg)}
.tasks-btn{color:var(--yellow);background:transparent;border:1px solid var(--yellow)}
.tasks-btn:hover{background:var(--yellow);color:var(--bg)}
.idle-agents-compact{margin-bottom:24px}
.idle-agent-row{display:flex;align-items:center;gap:12px;\
padding:8px 12px;background:var(--card);border:1px solid var(--border);\
border-radius:6px;margin-bottom:4px;font-size:13px}
.idle-agent-row:hover{border-color:var(--accent)}
.idle-agent-row .agent-name{font-weight:600;min-width:160px}
.idle-agent-row .agent-role{color:var(--muted);font-size:11px;flex:1}
.idle-agent-row .assign-btn{flex-shrink:0}
.task-modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;\
background:rgba(0,0,0,.6);z-index:1000;justify-content:center;align-items:center}
.task-modal-overlay.active{display:flex}
.task-modal{background:var(--bg);border:1px solid var(--border);\
border-radius:12px;padding:24px;width:520px;max-width:90vw;max-height:80vh;\
overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,.4)}
.task-modal h3{margin:0 0 16px;font-size:16px;color:var(--text)}
.task-modal label{display:block;font-size:12px;color:var(--muted);\
margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}
.task-modal input,.task-modal textarea,.task-modal select{width:100%;\
padding:8px 12px;background:var(--card);border:1px solid var(--border);\
border-radius:6px;color:var(--text);font-family:inherit;font-size:13px;\
margin-bottom:12px;box-sizing:border-box}
.task-modal textarea{min-height:100px;resize:vertical}
.task-modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:8px}
.task-modal-actions button{padding:8px 18px;border-radius:8px;font-size:13px;\
font-weight:600;cursor:pointer;border:1px solid var(--border)}
.task-modal-actions .btn-cancel{background:transparent;color:var(--muted)}
.task-modal-actions .btn-cancel:hover{color:var(--text)}
.task-modal-actions .btn-submit{background:var(--accent);color:var(--bg);\
border-color:var(--accent)}
.task-modal-actions .btn-submit:hover{opacity:.9}
.idle-tag{display:inline-block;padding:1px 6px;border-radius:8px;\
font-size:10px;font-weight:600;background:rgba(210,153,34,.15);\
color:var(--yellow);margin-left:6px}
.busy-tag{display:inline-block;padding:1px 6px;border-radius:8px;\
font-size:10px;font-weight:600;background:rgba(63,185,80,.15);\
color:var(--green);margin-left:6px}
.section-divider{text-align:center;color:var(--muted);margin:28px 0 16px;\
font-size:12px;letter-spacing:3px;text-transform:uppercase}
.section-divider::before,.section-divider::after{content:'';display:inline-block;\
width:40px;height:1px;background:var(--border);vertical-align:middle;margin:0 12px}
.article-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));\
gap:12px;margin-bottom:24px;align-items:stretch}
.article-card{background:var(--card);border:1px solid var(--border);\
border-radius:8px;padding:14px;display:flex;flex-direction:column}
.article-header{flex:0 0 auto;margin-bottom:0}
.article-card .article-title{font-weight:600;font-size:14px;line-height:1.4;\
height:2.8em;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;\
-webkit-box-orient:vertical;margin-bottom:4px}
.article-card .article-title a{color:var(--accent);text-decoration:none}
.article-card .article-title a:hover{text-decoration:underline;color:var(--text)}
.article-header .article-meta-row{margin:4px 0 8px}
.article-header-sep{border:none;border-top:1px solid var(--border);margin:0}
.article-card .article-meta{font-size:11px;color:var(--muted);margin-bottom:6px}
.article-card .article-summary{font-size:12px;line-height:1.5;color:var(--text)}
.article-tag{display:inline-block;padding:2px 8px;border-radius:10px;\
font-size:10px;font-weight:500;background:rgba(188,140,255,.12);\
color:var(--purple);margin-right:4px}
.score-badge{display:inline-block;padding:2px 6px;border-radius:10px;\
font-size:10px;font-weight:600}
.score-high{background:rgba(63,185,80,.15);color:var(--green)}
.score-med{background:rgba(210,153,34,.15);color:var(--yellow)}
.score-low{background:rgba(139,148,158,.15);color:var(--muted)}
.intel-hero-grid{display:grid;grid-template-columns:repeat(auto-fit,\
minmax(380px,1fr));gap:16px;margin-bottom:20px;align-items:stretch}
.article-card-hero{border-left:3px solid var(--accent);padding:20px}
.article-card-hero .article-title{font-size:17px;margin-bottom:6px;\
height:calc(2 * 1.4 * 17px);-webkit-line-clamp:2}
.article-card-hero .article-summary{font-size:13px;line-height:1.6}
.video-row{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px;margin-bottom:16px}
.video-card{flex:0 0 260px;background:var(--card);border:1px solid var(--border);\
border-radius:8px;padding:14px;position:relative}
.video-card::before{content:'[>]';position:absolute;top:10px;right:12px;\
font-size:11px;color:var(--accent);font-weight:700}
.article-thumb img{max-width:100%;border-radius:4px;margin:6px 0}
.tier-scan{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.scan-chip{background:var(--card);border:1px solid var(--border);\
border-radius:16px;padding:6px 14px;font-size:12px;white-space:nowrap;\
text-decoration:none;color:var(--text)}
.scan-chip:hover{border-color:var(--accent)}
.article-footer{display:flex;gap:6px;margin-top:auto;padding-top:8px;\
border-top:1px solid var(--border);align-items:center}
.article-link-btn{padding:3px 10px;font-size:10px;font-weight:600;\
color:var(--accent);background:transparent;border:1px solid var(--accent);\
border-radius:10px;text-decoration:none;white-space:nowrap;transition:all 0.2s}
.article-link-btn:hover{background:var(--accent);color:var(--bg)}
.article-chat-btn{padding:3px 10px;font-size:10px;font-weight:600;\
color:var(--green);background:transparent;border:1px solid var(--green);\
border-radius:10px;cursor:pointer;white-space:nowrap;transition:all 0.2s;\
font-family:inherit}
.article-chat-btn:hover{background:var(--green);color:var(--bg)}
.article-deep-dive{margin:8px 0 4px}
.deep-dive-toggle{background:none;border:none;color:var(--accent);\
font-size:11px;font-weight:600;cursor:pointer;padding:2px 0;\
font-family:inherit;opacity:.8;transition:opacity .2s}
.deep-dive-toggle:hover{opacity:1}
.deep-dive-content{margin-top:8px;padding:12px 14px;\
background:rgba(88,166,255,.04);border:1px solid rgba(88,166,255,.12);\
border-radius:6px;font-size:12.5px;line-height:1.7;color:var(--text)}
.deep-dive-content p{margin:0 0 10px}
.deep-dive-content p:last-child{margin-bottom:0}
.cohort-comment{margin:6px 0 4px;padding:6px 10px;\
background:rgba(63,185,80,.06);border-left:3px solid var(--green);\
border-radius:0 4px 4px 0;font-size:11.5px;line-height:1.5;\
color:var(--text);font-style:italic}
.cohort-avatar{display:inline-block;width:16px;height:16px;\
border-radius:50%;background:var(--green);color:var(--bg);\
font-size:10px;font-weight:700;text-align:center;line-height:16px;\
margin-right:4px;font-style:normal;vertical-align:middle}
.stakeholder-comment{margin:4px 0 4px;padding:6px 10px;\
background:rgba(188,140,255,.06);border-left:3px solid var(--purple);\
border-radius:0 4px 4px 0;font-size:11.5px;line-height:1.5;\
color:var(--text);font-style:italic}
.stakeholder-avatar{display:inline-block;width:16px;height:16px;\
border-radius:50%;background:var(--purple);color:var(--bg);\
font-size:9px;font-weight:700;text-align:center;line-height:16px;\
margin-right:4px;font-style:normal;vertical-align:middle}
.stakeholder-label{font-weight:600;color:var(--purple);\
font-style:normal;font-size:10px}
.carousel-group{position:relative;margin-bottom:16px}
.carousel-group .carousel-label{font-size:11px;color:var(--muted);\
margin-bottom:6px;font-weight:600;letter-spacing:.5px;text-transform:uppercase}
.carousel-track{display:flex;gap:12px;overflow-x:auto;\
scroll-snap-type:x mandatory;padding-bottom:8px;\
scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.carousel-track::-webkit-scrollbar{height:6px}
.carousel-track::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.carousel-track .article-card{flex:0 0 340px;scroll-snap-align:start}
.carousel-dots{display:flex;gap:4px;justify-content:center;margin-top:6px}
.carousel-dot{width:6px;height:6px;border-radius:50%;\
background:var(--border);transition:background .2s}
.carousel-dot.active{background:var(--accent)}
"""

_JS = """\
function toggleDeepDive(btn){
  var content=btn.parentElement.querySelector('.deep-dive-content');
  if(!content)return;
  var showing=content.style.display!=='none';
  content.style.display=showing?'none':'block';
  btn.textContent=showing?'[+] Full Analysis':'[-] Hide Analysis';
}
function switchTab(group,idx){
  var tabs=document.querySelector('[data-tgroup="'+group+'"]');
  if(!tabs)return;
  var btns=tabs.querySelectorAll('.tab-btn');
  var panels=tabs.querySelectorAll('.tab-panel');
  for(var i=0;i<btns.length;i++){
    btns[i].classList.toggle('active',i===idx);
    panels[i].classList.toggle('active',i===idx);
  }
}
function openTaskCreator(agentId, agentName, suggestion){
  var overlay=document.getElementById('task-modal-overlay');
  document.getElementById('tc-agent-id').value=agentId;
  document.getElementById('tc-agent-name').textContent=agentName;
  document.getElementById('tc-description').value=suggestion||'';
  document.getElementById('tc-priority').value='medium';
  document.getElementById('tc-status').textContent='';
  overlay.classList.add('active');
}
function closeTaskCreator(){
  document.getElementById('task-modal-overlay').classList.remove('active');
}
function submitTask(){
  var agentId=document.getElementById('tc-agent-id').value;
  var desc=document.getElementById('tc-description').value.trim();
  var priority=document.getElementById('tc-priority').value;
  var status=document.getElementById('tc-status');
  if(!desc){status.textContent='Description is required.';status.style.color='var(--red)';return;}
  var btn=document.querySelector('.btn-submit');
  btn.disabled=true;btn.textContent='Submitting...';
  fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({agent_id:agentId,description:desc,priority:priority,
      trigger_type:'manual',trigger_source:'briefing'})
  }).then(function(r){return r.json();}).then(function(data){
    if(data.error){status.textContent='Error: '+data.error;status.style.color='var(--red)';}
    else{status.textContent='Task created successfully.';status.style.color='var(--green)';
      setTimeout(closeTaskCreator,1200);}
  }).catch(function(e){status.textContent='Failed: '+e.message;status.style.color='var(--red)';
  }).finally(function(){btn.disabled=false;btn.textContent='Submit Task';});
}
document.addEventListener('click',function(e){
  if(e.target.id==='task-modal-overlay')closeTaskCreator();
  var btn=e.target.closest('.assign-btn');
  if(btn&&btn.dataset.agentId){
    openTaskCreator(btn.dataset.agentId,btn.dataset.agentName,btn.dataset.suggestion);
  }
});
document.addEventListener('click',function(e2){
  var cb=e2.target.closest('.article-chat-btn');
  if(!cb)return;
  var title=cb.dataset.title||'';
  var url=cb.dataset.url||'';
  var summary=cb.dataset.summary||'';
  var agents=(cb.dataset.agents||'').split(',').filter(Boolean);
  var slug='article-'+title.toLowerCase().replace(/[^a-z0-9]+/g,'-').slice(0,40);
  var mentions=agents.map(function(a){return '@'+a;}).join(' ');
  var seed='[Article: '+title+']('+url+')\\n\\n'+summary+'\\n\\n'
    +'Thoughts on this? '+mentions;
  cb.disabled=true;cb.textContent='Opening...';
  fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({channel:slug,sender:'user',message:seed,
      response_mode:'smarter'})
  }).then(function(r){return r.json();}).then(function(){
    window.open('/?channel='+encodeURIComponent(slug),'_blank');
  }).catch(function(e3){alert('Failed to start chat: '+e3.message);
  }).finally(function(){cb.disabled=false;cb.textContent='Start Chat';});
});
"""


def _build_html(report: BriefingReport, data_dir: Path | None = None) -> str:
    """Build a complete self-contained HTML briefing."""
    now = datetime.now()
    date_display = now.strftime("%A, %B %d, %Y")
    date_short = now.strftime("%a %b %d, %Y")
    gen_time = report.generated_at[:19] if report.generated_at else "unknown"

    # Extract sections by title
    wq = report.get_section("Work Queue")
    ca = report.get_section("Channel Activity")
    ts = report.get_section("Team Status")
    report.get_section("Discussion Sessions")
    intel = report.get_section("Intel Feed")

    wq_data = wq.data if wq else {}
    ca_data = ca.data if ca else {}
    ts_data = ts.data if ts else {}
    intel_data = intel.data if intel else {}

    articles = intel_data.get("articles", [])
    intel_count = len(articles)

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Executive Briefing - {_esc(date_short)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="header">
<h1>Executive Briefing - {_esc(date_short)}</h1>
<div class="meta">Period: {_esc(report.period_start[:10])} to \
{_esc(report.period_end[:10])} &bull; Generated {_esc(gen_time)}</div>
</div>
<div class="tabs" data-tgroup="main">
<div class="tab-bar">
<button class="tab-btn active" onclick="switchTab('main',0)">Daily Briefing</button>
<button class="tab-btn" onclick="switchTab('main',1)">Work Queue</button>
<button class="tab-btn" onclick="switchTab('main',2)">Raw Stats</button>
<button class="tab-btn" onclick="switchTab('main',3)">Intel Feed ({intel_count})</button>
</div>""")

    # ---- Tab 1: Daily Briefing ----
    parts.append('<div class="tab-panel active">')
    parts.append(f"""<div class="masthead">
<h1>The Executive Briefing</h1>
<div class="dateline">{_esc(date_display)}</div>
</div>""")

    # Executive summary (LLM-generated with deterministic fallback)
    total_agents = ts_data.get("total", 0)
    busy_agents = ts_data.get("busy", 0)
    total_msgs = ca_data.get("total_messages", 0)
    active_ch = ca_data.get("active_channels", 0)
    wq_total = wq_data.get("total", 0)
    wq_completed = wq_data.get("completed_in_period", 0)

    exec_summary = _generate_exec_summary(
        total_agents=total_agents,
        busy_agents=busy_agents,
        total_msgs=total_msgs,
        active_channels=active_ch,
        wq_total=wq_total,
        wq_completed=wq_completed,
        intel_count=intel_count,
    )
    parts.append(
        f'<div class="exec-summary">{_esc(exec_summary)}</div>'
    )

    # Team cards -- top N by relevance + rotation (ensures all agents surface)
    agents_list = ts_data.get("agents", [])
    mention_counts = ca_data.get("top_mentioned", {})

    # Load user interest keywords for agent narratives
    _interest_keywords: list[str] = []
    if data_dir:
        _cc_path = Path(data_dir) / "content_config.json"
        if _cc_path.exists():
            try:
                _cc = json.loads(_cc_path.read_text(encoding="utf-8"))
                _interest_keywords = _cc.get("interest_keywords", [])
            except (json.JSONDecodeError, OSError):
                pass

    if agents_list:
        featured = _select_featured_agents(
            agents_list,
            data_dir or Path("."),
            mention_counts,
        )
        remaining_count = len(agents_list) - len(featured)

        parts.append('<div class="section-divider">Team Status</div>')

        if featured:
            parts.append('<div class="agent-grid">')
            for agent in featured:
                name = _esc(agent.get("name", agent.get("agent_id", "?")))
                agent_id = _esc(agent.get("agent_id", ""))
                agent_role = _esc(agent.get("group", ""))
                status = agent.get("status", "idle")
                completed = agent.get("tasks_completed", 0)
                active_count = agent.get("active_task_count", 0)
                if active_count > 0:
                    tag = (
                        f'<span class="busy-tag">'
                        f'{active_count} task{"s" if active_count != 1 else ""}'
                        f'</span>'
                    )
                else:
                    tag = '<span class="idle-tag">IDLE</span>'
                narrative = _generate_agent_narrative(
                    agent,
                    articles=articles,
                    interest_keywords=_interest_keywords,
                )
                narrative_esc = _esc(narrative)

                # Recommendation for idle agents
                recommendation = ""
                if status != "busy":
                    rec_text = _generate_agent_recommendation(
                        agent,
                        articles=articles,
                        interest_keywords=_interest_keywords,
                    )
                    if rec_text:
                        recommendation = (
                            f'<div class="agent-rec" style="font-size:11px;'
                            f"margin-top:6px;padding:6px 8px;"
                            f"background:rgba(88,166,255,.08);"
                            f"border-radius:4px;color:var(--accent);"
                            f'line-height:1.4">'
                            f"Suggestion: {_esc(rec_text)}</div>"
                        )

                stats_parts = []
                if completed:
                    stats_parts.append(f"{completed} tasks completed")
                if agent_role:
                    stats_parts.append(agent_role)
                stats_line = " &bull; ".join(stats_parts) if stats_parts else ""
                parts.append(f"""<div class="agent-card">
<div class="agent-name">{name}{tag}</div>
<div class="agent-body">
<div class="agent-role">{agent_role}</div>
<div class="agent-voice" style="font-style:italic;font-size:13px;\
line-height:1.6;color:var(--text)">&ldquo;{narrative_esc}&rdquo;</div>
{recommendation}
</div>
<div class="agent-footer"><span class="agent-stats">{stats_line}</span>\
<div class="agent-footer-btns">\
{('<button class="tasks-btn" data-agent-id="' + agent_id + '"'
 " onclick=" + '"' + "window.open(" + "'" + "/?panel=tasks&amp;agent=" + agent_id + "'" + "," + "'" + "_blank" + "'" + ")" + '"'
 '>Current Tasks</button>') if active_count > 0 else ''}\
<button class="assign-btn" data-agent-id="{agent_id}" \
data-agent-name="{name}" \
data-suggestion="{_esc(narrative)}">Assign Task</button></div></div>
</div>""")
            parts.append("</div>")

        # Summary line for non-featured agents
        if remaining_count > 0:
            parts.append(
                f'<div style="color:var(--muted);font-size:11px;'
                f'text-align:center;margin:8px 0 16px">'
                f'{remaining_count} more agents on standby '
                f'&mdash; <a href="#" onclick="switchTab(\'main\',2);'
                f'return false" style="color:var(--accent);'
                f'text-decoration:underline;cursor:pointer">'
                f'see Raw Stats for full team</a></div>'
            )

    # Channel activity highlights
    ch_stats = ca_data.get("channel_stats", [])
    if ch_stats:
        parts.append('<div class="section-divider">Channel Activity</div>')
        parts.append('<div class="cards">')
        parts.append(f"""<div class="card">
<div class="label">Messages</div>
<div class="value">{total_msgs}</div>
<div class="sub">in {active_ch} channels</div>
</div>""")
        top_senders = ca_data.get("top_senders", {})
        if top_senders:
            top_name = next(iter(top_senders))
            top_count = top_senders[top_name]
            parts.append(f"""<div class="card">
<div class="label">Top Sender</div>
<div class="value" style="font-size:16px">{_esc(top_name)}</div>
<div class="sub">{top_count} messages</div>
</div>""")
        parts.append("</div>")

    # Intel preview (top 5 articles on the front page)
    top_articles = [a for a in articles if a.get("relevance_score", 0) >= 5]
    top_articles.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)

    # Generate "why it matters" summaries for ALL articles via LLM
    # High-relevance get full WHAT/WHY/ACTION; low get opportunity notes
    why_map: dict[str, ArticleAnalysis] = {}
    if articles:
        why_map = _generate_intel_summaries(articles)

    if top_articles:
        parts.append('<div class="section-divider">Intelligence Desk</div>')

        # Hero articles (top 2)
        heroes = top_articles[:2]
        if heroes:
            parts.append('<div class="intel-hero-grid">')
            for a in heroes:
                parts.append(_article_card_html(
                    a, hero=True, why_map=why_map, agents_list=agents_list,
                ))
            parts.append("</div>")

        # Mid-tier (next 3)
        mid = top_articles[2:5]
        if mid:
            parts.append('<div class="article-grid">')
            for a in mid:
                parts.append(_article_card_html(
                    a, why_map=why_map, agents_list=agents_list,
                ))
            parts.append("</div>")

        if intel_count > 5:
            parts.append(
                f'<div style="text-align:right;margin-top:8px">'
                f'<a href="#" onclick="switchTab(\'main\',3);return false" '
                f'style="color:var(--accent);font-size:13px;text-decoration:none">'
                f"See full Intel Feed ({intel_count} articles) &rarr;</a></div>"
            )

    parts.append("</div>")  # end tab 1

    # ---- Tab 2: Work Queue ----
    parts.append('<div class="tab-panel">')
    status_counts = wq_data.get("status_counts", {})
    parts.append('<div class="cards">')
    for status_name, label in [
        ("queued", "Queued"),
        ("active", "Active"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]:
        count = status_counts.get(status_name, 0)
        css = ""
        if status_name == "failed" and count:
            css = " bad"
        elif status_name == "completed" and count:
            css = " good"
        parts.append(f"""<div class="card">
<div class="label">{label}</div>
<div class="value{css}">{count}</div>
</div>""")
    avg_tt = wq_data.get("avg_turnaround_seconds")
    if avg_tt is not None:
        parts.append(f"""<div class="card">
<div class="label">Avg Turnaround</div>
<div class="value" style="font-size:20px">{_esc(_fmt_duration(avg_tt))}</div>
</div>""")
    parts.append("</div>")

    # Work queue items table
    items = wq_data.get("items", [])
    if items:
        parts.append("<h2>Work Items</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append(
            "<tr><th>ID</th><th>Description</th><th>Status</th>"
            "<th>Priority</th><th>Agent</th></tr>"
        )
        for item in items:
            status_badge = _status_badge(item.get("status", ""))
            prio_badge = _priority_badge(item.get("priority", ""))
            desc = _esc(_trunc(item.get("description", ""), 80))
            parts.append(
                f"<tr><td>{_esc(str(item.get('id', ''))[:8])}</td>"
                f"<td>{desc}</td>"
                f"<td>{status_badge}</td>"
                f"<td>{prio_badge}</td>"
                f"<td>{_esc(item.get('agent_id', ''))}</td></tr>"
            )
        parts.append("</table></div>")

    # Agent workload
    agent_workload = wq_data.get("agent_workload", {})
    if agent_workload:
        parts.append("<h2>Agent Workload</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append("<tr><th>Agent</th><th>Items</th></tr>")
        for agent_id, count in sorted(
            agent_workload.items(), key=lambda x: x[1], reverse=True
        ):
            parts.append(
                f"<tr><td>{_esc(agent_id)}</td><td>{count}</td></tr>"
            )
        parts.append("</table></div>")

    if not items and not agent_workload:
        parts.append(
            '<div style="color:var(--muted);padding:24px;text-align:center">'
            "No work queue data available.</div>"
        )

    parts.append("</div>")  # end tab 2

    # ---- Tab 3: Raw Stats ----
    parts.append('<div class="tab-panel">')

    # Summary cards
    parts.append('<div class="cards">')
    parts.append(f"""<div class="card">
<div class="label">Agents</div>
<div class="value">{total_agents}</div>
<div class="sub">{busy_agents} busy, {ts_data.get('idle', 0)} idle</div>
</div>""")
    parts.append(f"""<div class="card">
<div class="label">Messages</div>
<div class="value">{total_msgs}</div>
<div class="sub">{active_ch} channels</div>
</div>""")
    parts.append(f"""<div class="card">
<div class="label">Work Queue</div>
<div class="value">{wq_total}</div>
<div class="sub">{wq_completed} completed</div>
</div>""")
    parts.append(f"""<div class="card">
<div class="label">Intel Articles</div>
<div class="value">{intel_count}</div>
<div class="sub">{intel_data.get('top_count', 0)} scored 5+</div>
</div>""")
    parts.append("</div>")

    # Channel stats table
    if ch_stats:
        parts.append("<h2>Channel Activity</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append(
            "<tr><th>Channel</th><th>Messages</th>"
            "<th>Participants</th><th>Top Sender</th></tr>"
        )
        for cs in ch_stats:
            parts.append(
                f"<tr><td>#{_esc(cs['channel'])}</td>"
                f"<td>{cs['message_count']}</td>"
                f"<td>{cs['unique_senders']}</td>"
                f"<td>{_esc(cs.get('top_sender') or '-')}</td></tr>"
            )
        parts.append("</table></div>")

    # Top senders table
    top_senders_data = ca_data.get("top_senders", {})
    if top_senders_data:
        parts.append("<h2>Top Senders</h2>")
        parts.append('<div class="table-wrap"><table>')
        parts.append("<tr><th>Sender</th><th>Messages</th></tr>")
        for sender, count in sorted(
            top_senders_data.items(), key=lambda x: x[1], reverse=True
        ):
            parts.append(
                f"<tr><td>@{_esc(sender)}</td><td>{count}</td></tr>"
            )
        parts.append("</table></div>")

    # Team details
    if agents_list:
        parts.append("<h2>Team Details</h2>")
        parts.append('<div class="agent-grid">')
        for agent in agents_list:
            name = _esc(agent.get("name", agent.get("agent_id", "?")))
            agent_id = _esc(agent.get("agent_id", ""))
            status = agent.get("status", "idle")
            group = _esc(agent.get("group", ""))
            completed = agent.get("tasks_completed", 0)
            skills = agent.get("skills", [])
            tag = (
                '<span class="busy-tag">BUSY</span>'
                if status == "busy"
                else '<span class="idle-tag">IDLE</span>'
            )
            skills_html = ""
            if skills:
                skills_html = (
                    '<div style="margin-top:6px">'
                    + " ".join(
                        f'<span class="article-tag">{_esc(s)}</span>'
                        for s in skills[:5]
                    )
                    + "</div>"
                )
            parts.append(f"""<div class="agent-card">
<div class="agent-name">{name}{tag}</div>
<div class="agent-role">{agent_id}</div>
{skills_html}
<div class="agent-stats">{completed} tasks &bull; {group}</div>
</div>""")
        parts.append("</div>")

    parts.append("</div>")  # end tab 3

    # ---- Tab 4: Intel Feed ----
    parts.append('<div class="tab-panel">')
    if not articles:
        parts.append(
            '<div style="color:var(--muted);padding:24px;text-align:center">'
            "No intel articles available. Configure RSS feeds in the setup wizard "
            "and run a fetch to populate this tab.</div>"
        )
    else:
        # Group by source
        by_source: dict[str, list[dict[str, Any]]] = {}
        videos: list[dict[str, Any]] = []
        for a in articles:
            if _extract_yt_id(a.get("url", "")):
                videos.append(a)
            source = a.get("source", "Unknown")
            by_source.setdefault(source, []).append(a)

        # Stats row
        parts.append('<div class="cards">')
        parts.append(f"""<div class="card">
<div class="label">Total Articles</div>
<div class="value">{intel_count}</div>
</div>""")
        parts.append(f"""<div class="card">
<div class="label">Sources</div>
<div class="value">{len(by_source)}</div>
</div>""")
        scored_high = sum(
            1 for a in articles if a.get("relevance_score", 0) >= 7
        )
        parts.append(f"""<div class="card">
<div class="label">High Relevance</div>
<div class="value good">{scored_high}</div>
<div class="sub">score 7+</div>
</div>""")
        if videos:
            parts.append(f"""<div class="card">
<div class="label">Videos</div>
<div class="value">{len(videos)}</div>
</div>""")
        parts.append("</div>")

        # Videos section
        if videos:
            parts.append(
                '<div class="section-divider" style="font-size:11px;'
                'margin:16px 0 10px">Videos</div>'
            )
            parts.append('<div class="video-row">')
            for v in videos[:6]:
                yt_id = _extract_yt_id(v.get("url", ""))
                parts.append(f"""<div class="video-card">
<div class="article-title"><a href="{_esc(v.get('url', ''))}" target="_blank" \
rel="noopener">{_esc(_trunc(v.get('title', ''), 60))}</a> \
{_score_badge(v.get('relevance_score', 0))}</div>
<div class="article-meta">{_esc(v.get('source', ''))}</div>
{('<div class="article-thumb"><img src="https://img.youtube.com/vi/' + yt_id + '/mqdefault.jpg" alt="thumbnail" onerror="this.style.display=' + "'" + 'none' + "'" + '"></div>') if yt_id else ''}
</div>""")
            parts.append("</div>")

        # Articles grouped by topic (related stories become carousels)
        groups = _group_articles_by_topic(articles)
        multi_groups = sum(1 for g in groups if len(g) > 1)
        if multi_groups:
            parts.append(
                f'<div style="color:var(--muted);font-size:11px;'
                f'margin:8px 0">{multi_groups} topic cluster'
                f'{"s" if multi_groups != 1 else ""} detected '
                f"-- scroll horizontally for related coverage</div>"
            )

        parts.append('<div class="article-grid">')
        for group in groups:
            parts.append(_render_article_group(
                group, why_map=why_map, agents_list=agents_list,
            ))
        parts.append("</div>")

    parts.append("</div>")  # end tab 4

    # Close tabs + page
    # Task creator modal
    parts.append("""<div class="task-modal-overlay" id="task-modal-overlay">
<div class="task-modal">
<h3>Assign Task to <span id="tc-agent-name"></span></h3>
<input type="hidden" id="tc-agent-id">
<label>Description</label>
<textarea id="tc-description" placeholder="Describe what the agent should do..."></textarea>
<label>Priority</label>
<select id="tc-priority">
<option value="low">Low</option>
<option value="medium" selected>Medium</option>
<option value="high">High</option>
</select>
<div id="tc-status" style="font-size:12px;min-height:18px;margin-bottom:4px"></div>
<div class="task-modal-actions">
<button class="btn-cancel" onclick="closeTaskCreator()">Cancel</button>
<button class="btn-submit" onclick="submitTask()">Submit Task</button>
</div>
</div>
</div>""")
    parts.append(f"</div>\n<script>{_JS}</script>\n</body>\n</html>")

    return "\n".join(parts)


def _parse_hn_meta(summary: str) -> dict[str, Any]:
    """Extract structured metadata from HN-style summary strings.

    HN feeds put everything in the summary field like:
    'Article URL: ... Comments URL: ... Points: 534 # Comments: 270'
    """
    meta: dict[str, Any] = {}
    points_m = re.search(r"Points:\s*(\d+)", summary)
    if points_m:
        meta["points"] = int(points_m.group(1))
    comments_m = re.search(r"#\s*Comments:\s*(\d+)", summary)
    if comments_m:
        meta["comments"] = int(comments_m.group(1))
    comments_url_m = re.search(r"Comments URL:\s*(https?://\S+)", summary)
    if comments_url_m:
        meta["comments_url"] = comments_url_m.group(1)
    # Check if summary is ONLY metadata (no real content)
    cleaned = re.sub(
        r"(Article|Comments)\s+URL:\s*https?://\S+|Points:\s*\d+|#\s*Comments:\s*\d+",
        "", summary,
    ).strip()
    meta["has_content"] = len(cleaned) > 20
    meta["clean_summary"] = cleaned if meta["has_content"] else ""
    return meta


def _article_card_html(
    article: dict[str, Any],
    hero: bool = False,
    why_map: dict[str, ArticleAnalysis] | None = None,
    agents_list: list[dict[str, Any]] | None = None,
) -> str:
    """Render a single article card with quick take + expandable deep dive."""
    title = _esc(article.get("title", ""))
    url = _esc(article.get("url", ""))
    source = _esc(article.get("source", ""))
    raw_summary = article.get("summary", "")
    score = article.get("relevance_score", 0)
    tags = article.get("tags", [])
    aid = article.get("id", "")

    # Parse HN-style metadata from summary
    hn = _parse_hn_meta(raw_summary)
    clean_summary = _esc(_trunc(hn.get("clean_summary", raw_summary), 200))

    card_class = "article-card article-card-hero" if hero else "article-card"

    # Build meta tags row: source, topic tags, points, comments
    meta_parts: list[str] = []
    meta_parts.append(f'<span class="article-tag">{source}</span>')
    for t in tags[:3]:
        meta_parts.append(f'<span class="article-tag">{_esc(t)}</span>')
    if hn.get("points"):
        meta_parts.append(
            f'<span class="article-tag" style="background:rgba(63,185,80,.12);'
            f'color:var(--green)">{hn["points"]} pts</span>'
        )
    if hn.get("comments"):
        color = "var(--yellow)" if hn["comments"] >= 100 else "var(--muted)"
        meta_parts.append(
            f'<span class="article-tag" style="background:rgba(210,153,34,.12);'
            f'color:{color}">{hn["comments"]} comments</span>'
        )
    # Add score badge to meta row
    meta_parts.append(_score_badge(score))
    meta_html = '<div class="article-meta-row" style="margin:4px 0 6px">' + " ".join(meta_parts) + "</div>"

    # Summary text (only if real content exists beyond HN metadata)
    summary_html = ""
    if clean_summary:
        summary_html = f'<div class="article-summary">{clean_summary}</div>'

    # Quick take bullets + Cohort comment + expandable deep dive
    analysis_html = ""
    analysis = why_map.get(aid) if why_map else None
    if analysis:
        # Quick take bullets (always visible)
        if analysis.bullets:
            analysis_html += (
                '<ul class="article-why-matters" style="margin:6px 0 4px 0;'
                "padding-left:16px;font-size:12px;line-height:1.6;"
                'color:#8cb4ff;font-style:italic;list-style:none">'
            )
            for bullet in analysis.bullets[:3]:
                analysis_html += (
                    f'<li style="padding:2px 0">'
                    f'<span style="color:var(--accent);font-style:normal;'
                    f'font-weight:700;margin-right:6px">&gt;</span>'
                    f"{_esc(bullet)}</li>"
                )
            analysis_html += "</ul>"

        # Cohort agent commentary (always visible, below bullets)
        if analysis.cohort_comment:
            analysis_html += (
                '<div class="cohort-comment">'
                '<span class="cohort-avatar">C</span> '
                f"{_esc(analysis.cohort_comment)}"
                "</div>"
            )

        # Stakeholder agent commentary (if Cohort nominated one)
        if analysis.stakeholder_agent and analysis.stakeholder_comment:
            agent_label = analysis.stakeholder_agent.replace("_", " ").title()
            # First letter of agent name for avatar
            initials = "".join(
                w[0].upper() for w in analysis.stakeholder_agent.split("_")
                if w
            )[:2]
            analysis_html += (
                '<div class="stakeholder-comment">'
                f'<span class="stakeholder-avatar">{initials}</span> '
                f'<span class="stakeholder-label">{_esc(agent_label)}:</span> '
                f"{_esc(analysis.stakeholder_comment)}"
                "</div>"
            )

        # Deep dive (behind expand toggle) with keyword highlighting
        if analysis.deep_dive:
            # Convert paragraph breaks to <p> tags
            paras = [
                p.strip() for p in analysis.deep_dive.split("\n\n")
                if p.strip()
            ]
            # If no double-newlines, try single newlines for paragraph breaks
            if len(paras) <= 1:
                paras = [
                    p.strip() for p in analysis.deep_dive.split("\n")
                    if p.strip()
                ]
            # Apply keyword highlighting (returns pre-escaped HTML)
            kws = analysis.keywords
            deep_html = "".join(
                f"<p>{_highlight_keywords(p, kws)}</p>" for p in paras
            )
            analysis_html += (
                f'<div class="article-deep-dive" data-article="{_esc(aid)}">'
                f'<button class="deep-dive-toggle" '
                f'onclick="toggleDeepDive(this)">'
                f"[+] Full Analysis</button>"
                f'<div class="deep-dive-content" style="display:none">'
                f"{deep_html}"
                f"</div></div>"
            )

    # Stakeholder agents for "Start Chat"
    stakeholders = _infer_stakeholder_agents(article, agents_list or [])

    # Use data attributes to avoid quoting hell in inline handlers
    footer_html = (
        '<div class="article-footer">'
        f'<a href="{url}" target="_blank" rel="noopener" '
        f'class="article-link-btn">Read Article</a>'
    )
    if hn.get("comments_url"):
        footer_html += (
            f'<a href="{_esc(hn["comments_url"])}" target="_blank" '
            f'rel="noopener" class="article-link-btn" '
            f'style="color:var(--yellow);border-color:var(--yellow)">'
            f"Discussion</a>"
        )
    footer_html += (
        f'<button class="article-chat-btn" '
        f'data-title="{_esc(article.get("title", ""))}" '
        f'data-url="{_esc(article.get("url", ""))}" '
        f'data-summary="{_esc(raw_summary[:200])}" '
        f'data-agents="{_esc(",".join(stakeholders))}"'
        f">Start Chat</button>"
        "</div>"
    )

    return f"""<div class="{card_class}">
<div class="article-header">
<div class="article-title"><a href="{url}" target="_blank" \
rel="noopener">{title}</a></div>
{meta_html}
<hr class="article-header-sep">
</div>
{summary_html}
{analysis_html}
{footer_html}
</div>"""


def _render_article_group(
    group: list[dict[str, Any]],
    why_map: dict[str, ArticleAnalysis] | None = None,
    agents_list: list[dict[str, Any]] | None = None,
    hero: bool = False,
) -> str:
    """Render a group of articles -- single card or horizontal carousel."""
    if len(group) == 1:
        return _article_card_html(
            group[0], hero=hero, why_map=why_map, agents_list=agents_list,
        )

    # Multi-article carousel
    best_title = group[0].get("title", "Related Articles")
    # Truncate to key topic words for the label
    label = _trunc(best_title, 50)
    total = len(group)

    cards = "".join(
        _article_card_html(a, why_map=why_map, agents_list=agents_list)
        for a in group
    )
    dots = "".join(
        f'<span class="carousel-dot{" active" if i == 0 else ""}"></span>'
        for i in range(total)
    )
    return (
        f'<div class="carousel-group">'
        f'<div class="carousel-label">{_esc(label)} '
        f'<span style="color:var(--accent)">'
        f"({total} articles)</span></div>"
        f'<div class="carousel-track">{cards}</div>'
        f'<div class="carousel-dots">{dots}</div>'
        f"</div>"
    )


def _score_badge(score: int) -> str:
    """Render a relevance score badge."""
    if score >= 7:
        cls = "score-high"
    elif score >= 4:
        cls = "score-med"
    else:
        cls = "score-low"
    return f'<span class="score-badge {cls}">{score}/10</span>'


def _status_badge(status: str) -> str:
    """Render a work queue status badge."""
    colors = {
        "completed": "badge-green",
        "active": "badge-blue",
        "queued": "badge-yellow",
        "failed": "badge-red",
        "cancelled": "badge-purple",
    }
    cls = colors.get(status, "badge-yellow")
    return f'<span class="badge {cls}">{_esc(status)}</span>'


def _priority_badge(priority: str) -> str:
    """Render a priority badge."""
    colors = {
        "critical": "badge-red",
        "high": "badge-yellow",
        "medium": "badge-blue",
        "low": "badge-purple",
    }
    cls = colors.get(priority, "badge-blue")
    return f'<span class="badge {cls}">{_esc(priority)}</span>'
