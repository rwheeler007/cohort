"""Cohort Intel Fetcher -- RSS feed ingestion for the executive briefing.

Reads the user's feed configuration (saved during setup wizard) and fetches
articles via ``feedparser``.  Articles are stored in a simple JSON database
at ``{data_dir}/tech_intel/articles_db.json``.

feedparser is an optional dependency -- if missing, fetch operations return
empty results with a logged warning.

Usage::

    from cohort.intel_fetcher import IntelFetcher
    fetcher = IntelFetcher(data_dir=Path("data"))
    new_count = fetcher.fetch()          # fetch all configured feeds
    articles = fetcher.get_articles()    # recent articles
    articles = fetcher.get_top(limit=15, min_score=5)  # filtered
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import feedparser  # type: ignore[import-untyped]

    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False

# Max articles to retain in the database
_MAX_ARTICLES = 500
# Max age in days for articles
_MAX_AGE_DAYS = 30
# Per-feed fetch limit
_PER_FEED_LIMIT = 15
# Fetch timeout per feed (seconds)
_FETCH_TIMEOUT = 20


# =====================================================================
# Helpers
# =====================================================================


def _article_id(url: str, title: str) -> str:
    """Deterministic article ID from URL + title."""
    raw = f"{url}|{title}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _strip_html(text: str) -> str:
    """Strip HTML tags and entities from summary text."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_published(entry: dict[str, Any]) -> str:
    """Extract a published timestamp from a feedparser entry."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            return raw
    return datetime.now(timezone.utc).isoformat()


def _extract_yt_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    if not url:
        return None
    m = re.search(
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|v/))([a-zA-Z0-9_-]{11})",
        url,
    )
    return m.group(1) if m else None


def _keyword_prefilter(title: str, summary: str, keywords: list[str]) -> int:
    """Fast keyword prefilter using word-boundary matching.

    Returns a hit count (0 = no keyword match).  Used to decide whether
    an article is worth sending to the LLM for deeper scoring.
    """
    if not keywords:
        return 0
    text = f" {title} {summary} ".lower()
    hits = 0
    for kw in keywords:
        # Word-start boundary + allow trailing chars (plurals, suffixes)
        # e.g. "agent" matches "agents", "ai" won't match "chair"
        pattern = rf"\b{re.escape(kw.lower())}\w*"
        if re.search(pattern, text):
            hits += 1
    return hits


def _llm_relevance_score(
    title: str,
    summary: str,
    keywords: list[str],
    ollama_url: str = "http://127.0.0.1:11434",
) -> int | None:
    """Score article relevance 0-10 using local LLM.

    Returns None on failure (caller should fall back to keyword score).
    """
    try:
        from cohort.local.ollama import OllamaClient
    except ImportError:
        return None

    kw_str = ", ".join(keywords) if keywords else "general interest"
    prompt = (
        f"Rate this article's relevance to someone interested in: {kw_str}\n\n"
        f"Title: {title}\n"
        f"Summary: {summary[:300]}\n\n"
        "Reply with ONLY a single integer from 0 to 10. "
        "0 = completely irrelevant, 5 = somewhat related, 10 = highly relevant."
    )

    client = OllamaClient(base_url=ollama_url, timeout=30)
    from cohort.local.config import DEFAULT_MODEL

    result = client.generate(
        model=DEFAULT_MODEL,
        prompt=prompt,
        system="You are a relevance scorer. Reply with ONLY a single integer 0-10. No explanation.",
        temperature=0.0,
        think=False,
        keep_alive="0",
    )
    if not result or not result.text:
        return None

    # Extract first integer from response
    m = re.search(r"\b(\d{1,2})\b", result.text.strip())
    if m:
        score = int(m.group(1))
        return min(score, 10)
    return None


def _keyword_only_score(hits: int, keyword_count: int) -> int:
    """Keyword-only relevance scale (fallback when LLM unavailable)."""
    if keyword_count == 0:
        return 5
    if hits == 0:
        return 3
    if hits == 1:
        return 5
    if hits == 2:
        return 7
    return min(9 + (hits - 3), 10)


def _score_article(
    title: str,
    summary: str,
    keywords: list[str],
    relevance_mode: str = "hybrid",
    ollama_url: str = "http://127.0.0.1:11434",
) -> int:
    """Score article relevance using the configured mode.

    Modes:
        "off"      -- All articles score 5 (no filtering).
        "keywords" -- Fast keyword-only scoring.
        "llm"      -- LLM scoring for keyword-matched articles.
        "hybrid"   -- LLM base score + keyword hit boost.  A world news
                      article that happens to mention "AI" gets +1 per
                      keyword hit on top of the LLM's semantic score.
                      This surfaces cross-topic gems.
    """
    if relevance_mode == "off":
        return 5

    hits = _keyword_prefilter(title, summary, keywords)

    if relevance_mode == "keywords":
        return _keyword_only_score(hits, len(keywords))

    # "llm" or "hybrid" -- both use LLM, hybrid adds keyword boost
    use_boost = (relevance_mode == "hybrid")

    # Skip LLM for zero-hit articles when keyword list is broad
    if hits == 0 and len(keywords) > 3:
        return 2  # unlikely to be relevant, save the LLM call

    llm_score = _llm_relevance_score(title, summary, keywords, ollama_url)

    if llm_score is not None:
        if use_boost and hits > 0:
            # Keyword boost: +1 per hit, capped at 10
            # A news article scored 4 by LLM that also mentions 2 of
            # your keywords becomes 6 -- surfacing cross-topic overlap
            return min(llm_score + hits, 10)
        return llm_score

    # LLM failed, fall back to keyword scale
    return _keyword_only_score(hits, len(keywords))


# =====================================================================
# IntelFetcher
# =====================================================================


class IntelFetcher:
    """Fetches RSS feeds and maintains an articles database.

    Parameters
    ----------
    data_dir:
        Base data directory.  Config read from ``{data_dir}/content_config.json``,
        articles stored at ``{data_dir}/tech_intel/articles_db.json``.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._config_path = self._data_dir / "content_config.json"
        self._db_dir = self._data_dir / "tech_intel"
        self._db_path = self._db_dir / "articles_db.json"

    # =================================================================
    # Config
    # =================================================================

    def get_config(self) -> dict[str, Any]:
        """Load feed configuration, or empty dict."""
        if not self._config_path.exists():
            return {}
        try:
            return json.loads(self._config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def get_feeds(self) -> list[dict[str, str]]:
        """Return the configured feed list."""
        config = self.get_config()
        return config.get("feeds", [])

    def _save_config(self, config: dict[str, Any]) -> None:
        """Save feed configuration."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add_feed(self, url: str, name: str, category: str = "general") -> bool:
        """Add an RSS feed.  Returns True if added (False if duplicate URL)."""
        config = self.get_config()
        feeds = config.get("feeds", [])
        # Deduplicate by URL
        if any(f.get("url") == url for f in feeds):
            return False
        feeds.append({"url": url, "name": name, "category": category})
        config["feeds"] = feeds
        self._save_config(config)
        return True

    def remove_feed(self, url: str) -> bool:
        """Remove an RSS feed by URL.  Returns True if found and removed."""
        config = self.get_config()
        feeds = config.get("feeds", [])
        before = len(feeds)
        feeds = [f for f in feeds if f.get("url") != url]
        if len(feeds) == before:
            return False
        config["feeds"] = feeds
        self._save_config(config)
        return True

    def get_article_stats(self) -> dict[str, Any]:
        """Return article database statistics."""
        articles = self._load_db()
        if not articles:
            return {"total": 0, "sources": {}, "oldest": None, "newest": None}
        sources: dict[str, int] = {}
        for a in articles:
            src = a.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
        dates = [a.get("fetched_at", "") for a in articles if a.get("fetched_at")]
        return {
            "total": len(articles),
            "sources": dict(sorted(sources.items(), key=lambda x: -x[1])),
            "oldest": min(dates)[:10] if dates else None,
            "newest": max(dates)[:10] if dates else None,
        }

    def prune_articles(self, max_age_days: int = 30, keep_max: int = 500) -> dict[str, int]:
        """Prune old articles.  Returns {"removed": N, "kept": N}."""
        articles = self._load_db()
        before = len(articles)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).isoformat()
        articles = [a for a in articles if a.get("fetched_at", "") >= cutoff]
        # Enforce max
        if len(articles) > keep_max:
            articles.sort(key=lambda a: a.get("fetched_at", ""), reverse=True)
            articles = articles[:keep_max]
        self._save_db(articles)
        return {"removed": before - len(articles), "kept": len(articles)}

    # =================================================================
    # Database
    # =================================================================

    def _load_db(self) -> list[dict[str, Any]]:
        """Load articles database."""
        if not self._db_path.exists():
            return []
        try:
            data = json.loads(self._db_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_db(self, articles: list[dict[str, Any]]) -> None:
        """Save articles database."""
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.write_text(
            json.dumps(articles, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_articles(
        self,
        limit: int = 50,
        max_age_days: int = _MAX_AGE_DAYS,
    ) -> list[dict[str, Any]]:
        """Return recent articles, newest first."""
        articles = self._load_db()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).isoformat()

        recent = [a for a in articles if a.get("fetched_at", "") >= cutoff]
        recent.sort(
            key=lambda a: a.get("fetched_at", ""), reverse=True
        )
        return recent[:limit]

    def get_top(
        self,
        limit: int = 15,
        min_score: int = 5,
        max_age_days: int = _MAX_AGE_DAYS,
    ) -> list[dict[str, Any]]:
        """Return top-scored articles."""
        articles = self.get_articles(limit=200, max_age_days=max_age_days)
        scored = [a for a in articles if a.get("relevance_score", 0) >= min_score]
        scored.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)
        return scored[:limit]

    # =================================================================
    # Fetch
    # =================================================================

    def fetch(
        self,
        keywords: list[str] | None = None,
        relevance_mode: str | None = None,
        ollama_url: str = "http://127.0.0.1:11434",
    ) -> int:
        """Fetch all configured feeds and store new articles.

        Parameters
        ----------
        keywords:
            Optional keyword list for relevance scoring.  If None, reads
            from config ``interest_keywords`` field.
        relevance_mode:
            Scoring mode: "off", "keywords", "llm", or "hybrid" (default).
            If None, reads from config ``relevance_mode`` field.
        ollama_url:
            Ollama server URL for LLM scoring.

        Returns
        -------
        int
            Number of new articles added.
        """
        if not _HAS_FEEDPARSER:
            logger.warning(
                "[!] feedparser not installed -- cannot fetch RSS feeds. "
                "Install with: pip install feedparser"
            )
            return 0

        config = self.get_config()
        feeds = config.get("feeds", [])
        if not feeds:
            logger.info("[*] No feeds configured -- nothing to fetch")
            return 0

        if keywords is None:
            keywords = config.get("interest_keywords", [])
        if relevance_mode is None:
            relevance_mode = config.get("relevance_mode", "hybrid")

        existing = self._load_db()
        existing_ids = {a["id"] for a in existing}
        new_articles: list[dict[str, Any]] = []

        for feed_info in feeds:
            url = feed_info.get("url", "")
            name = feed_info.get("name", url)
            if not url:
                continue

            try:
                parsed = feedparser.parse(url)
                if parsed.bozo and not parsed.entries:
                    logger.warning("[!] Feed parse error for %s: %s", name, parsed.bozo_exception)
                    continue

                for entry in parsed.entries[:_PER_FEED_LIMIT]:
                    title = _strip_html(entry.get("title", ""))
                    link = entry.get("link", "")
                    summary = _strip_html(
                        entry.get("summary", entry.get("description", ""))
                    )

                    if not title or not link:
                        continue

                    aid = _article_id(link, title)
                    if aid in existing_ids:
                        continue

                    score = _score_article(title, summary, keywords, relevance_mode=relevance_mode, ollama_url=ollama_url)
                    yt_id = _extract_yt_id(link)

                    article: dict[str, Any] = {
                        "id": aid,
                        "title": title,
                        "url": link,
                        "summary": summary[:500] if summary else "",
                        "source": name,
                        "feed_url": url,
                        "published": _parse_published(entry),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "relevance_score": score,
                        "tags": _extract_tags(entry),
                    }
                    if yt_id:
                        article["youtube_id"] = yt_id

                    new_articles.append(article)
                    existing_ids.add(aid)

                logger.info("[OK] Fetched %s: %d entries", name, len(parsed.entries))

            except Exception as exc:
                logger.warning("[!] Error fetching %s: %s", name, exc)

        if new_articles:
            combined = new_articles + existing
            # Prune old articles
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=_MAX_AGE_DAYS)
            ).isoformat()
            combined = [a for a in combined if a.get("fetched_at", "") >= cutoff]
            # Cap total
            combined.sort(key=lambda a: a.get("fetched_at", ""), reverse=True)
            combined = combined[:_MAX_ARTICLES]
            self._save_db(combined)

        logger.info("[OK] Intel fetch complete: %d new articles", len(new_articles))
        return len(new_articles)

    # =================================================================
    # Project-aware scoring
    # =================================================================

    def score_for_projects(
        self,
        projects: list[dict[str, Any]],
        max_age_days: int = 7,
    ) -> int:
        """Score recent articles against all active projects.

        Adds/updates a ``project_scores`` dict on each article keyed by
        project ID.  Returns the number of articles updated.

        Parameters
        ----------
        projects:
            List of project dicts from content_projects.json.
        max_age_days:
            Only score articles newer than this.
        """
        from cohort.content_analyzer import score_article

        articles = self.get_articles(limit=500, max_age_days=max_age_days)
        updated = 0

        for article in articles:
            scores = article.get("project_scores", {})
            changed = False

            for proj in projects:
                pid = proj.get("id", "")
                if not pid or not proj.get("active", True):
                    continue

                # Skip if already scored for this project
                if pid in scores:
                    continue

                result = score_article(article, proj)
                scores[pid] = {
                    "score": result["score"],
                    "matched_keywords": result["matched_keywords"],
                    "pillar_matches": result["pillar_matches"],
                    "audience_match": result["audience_match"],
                    "negative_hit": result["negative_hit"],
                }
                changed = True

            if changed:
                article["project_scores"] = scores
                updated += 1

        if updated:
            self._save_db(articles)

        return updated

    def get_top_for_project(
        self,
        project_id: str,
        limit: int = 15,
        min_score: int = 5,
        max_age_days: int = _MAX_AGE_DAYS,
    ) -> list[dict[str, Any]]:
        """Return top articles scored for a specific project."""
        articles = self.get_articles(limit=200, max_age_days=max_age_days)
        result = []
        for a in articles:
            ps = a.get("project_scores", {}).get(project_id, {})
            proj_score = ps.get("score", 0)
            if proj_score >= min_score:
                # Attach project-specific score as top-level for easy sorting
                a_copy = dict(a)
                a_copy["relevance_score"] = proj_score
                a_copy["project_match"] = ps
                result.append(a_copy)

        result.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return result[:limit]


def _extract_tags(entry: dict[str, Any]) -> list[str]:
    """Extract category tags from a feedparser entry."""
    tags: list[str] = []
    for tag_info in entry.get("tags", []):
        term = tag_info.get("term", "")
        if term and len(term) < 40:
            tags.append(term)
        if len(tags) >= 5:
            break
    return tags
