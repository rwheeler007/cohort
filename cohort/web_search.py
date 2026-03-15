"""
Cohort Web Search -- Inlined web search using ddgs (primary) or external API providers.

Ported from BOSS tools/web_search/service.py. No separate service process needed.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiting (in-memory)
# ---------------------------------------------------------------------------

_request_count = 0
_last_reset = datetime.now()
_daily_count = 0
_daily_reset = datetime.now().date()
RATE_LIMIT_PER_MINUTE = 30
RATE_LIMIT_PER_DAY = 250


def _check_rate_limit() -> tuple[bool, str]:
    """Check if request is within rate limit."""
    global _request_count, _last_reset, _daily_count, _daily_reset

    now = datetime.now()
    today = now.date()

    if today > _daily_reset:
        _daily_count = 0
        _daily_reset = today

    if _daily_count >= RATE_LIMIT_PER_DAY:
        return False, f"Daily limit exceeded ({RATE_LIMIT_PER_DAY}/day). Resets at midnight."

    elapsed = (now - _last_reset).total_seconds()
    if elapsed >= 60:
        _request_count = 0
        _last_reset = now

    if _request_count >= RATE_LIMIT_PER_MINUTE:
        return False, f"Rate limit exceeded ({RATE_LIMIT_PER_MINUTE}/minute). Wait {60 - int(elapsed)} seconds."

    _request_count += 1
    _daily_count += 1
    return True, ""


# ---------------------------------------------------------------------------
# Search providers
# ---------------------------------------------------------------------------

def _search_ddgs(query: str, num_results: int = 5) -> List[Dict[str, Any]]:
    """Search using DuckDuckGo (ddgs library). Free, no API key needed."""
    from ddgs import DDGS

    results = []
    with DDGS() as ddgs:
        raw = list(ddgs.text(query=query, max_results=num_results))
        for i, item in enumerate(raw):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "snippet": item.get("body", ""),
                "source": "duckduckgo",
                "position": i + 1,
            })
    return results


def _search_external(
    query: str,
    num_results: int,
    provider: str,
    api_key: str,
    google_cx: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search using an external API provider (Google, SerpAPI, Serper)."""
    import urllib.request
    import urllib.parse
    import json as _json

    results: List[Dict[str, Any]] = []

    if provider == "google":
        if not google_cx:
            raise RuntimeError("GOOGLE_CX (Search Engine ID) not configured for Google provider")
        params = urllib.parse.urlencode({
            "key": api_key,
            "cx": google_cx,
            "q": query,
            "num": min(num_results, 10),
        })
        url = f"https://www.googleapis.com/customsearch/v1?{params}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        for i, item in enumerate(data.get("items", [])[:num_results]):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "source": item.get("displayLink", ""),
                "position": i + 1,
            })

    elif provider == "serpapi":
        params = urllib.parse.urlencode({
            "q": query,
            "api_key": api_key,
            "engine": "google",
            "num": num_results,
        })
        url = f"https://serpapi.com/search?{params}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        for i, item in enumerate(data.get("organic_results", [])[:num_results]):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "source": item.get("source", ""),
                "position": i + 1,
            })

    elif provider == "serper":
        import urllib.request
        payload = _json.dumps({"q": query, "num": num_results}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.serper.dev/search",
            data=payload,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        for i, item in enumerate(data.get("organic", [])[:num_results]):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link") or item.get("url", ""),
                "snippet": item.get("snippet", "") or item.get("description", ""),
                "source": item.get("source", ""),
                "position": i + 1,
            })

    return results


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

def search(
    query: str,
    num_results: int = 5,
    service_keys: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Search the web. Tries ddgs first (free), falls back to configured API providers.

    Args:
        query: Search query string
        num_results: Number of results to return
        service_keys: List of service key dicts from settings (for external providers)

    Returns:
        Dict with 'results', 'provider', 'query', 'total_results'
    """
    allowed, reason = _check_rate_limit()
    if not allowed:
        return {"error": reason, "results": [], "query": query, "total_results": 0}

    start = datetime.now()

    # Try ddgs first (free, no API key)
    try:
        from ddgs import DDGS  # noqa: F401
        results = _search_ddgs(query, num_results)
        elapsed = (datetime.now() - start).total_seconds() * 1000
        return {
            "results": results,
            "provider": "duckduckgo",
            "query": query,
            "total_results": len(results),
            "search_time_ms": round(elapsed, 2),
        }
    except ImportError:
        pass
    except Exception as e:
        logger.warning("ddgs search failed: %s", e)

    # Try configured external providers
    if service_keys:
        for svc in service_keys:
            svc_type = svc.get("type", "")
            key = svc.get("key", "")
            if not key:
                continue
            if svc_type in ("serpapi", "serper", "google"):
                try:
                    google_cx = svc.get("extra", {}).get("cx") if svc_type == "google" else None
                    results = _search_external(query, num_results, svc_type, key, google_cx)
                    elapsed = (datetime.now() - start).total_seconds() * 1000
                    return {
                        "results": results,
                        "provider": svc_type,
                        "query": query,
                        "total_results": len(results),
                        "search_time_ms": round(elapsed, 2),
                    }
                except Exception as e:
                    logger.warning("External search (%s) failed: %s", svc_type, e)

    return {
        "error": "No search provider available. Install ddgs (pip install ddgs) or configure a search API key.",
        "results": [],
        "query": query,
        "total_results": 0,
    }
