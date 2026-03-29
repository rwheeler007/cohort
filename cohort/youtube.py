"""
Cohort YouTube -- Inlined YouTube Data API v3 + transcript support.

Ported from BOSS tools/youtube_service/service.py. No separate service process needed.
"""

import logging
import re
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
RATE_LIMIT_PER_DAY = 1000


def _check_rate_limit() -> Optional[str]:
    """Check rate limit. Returns error message if exceeded, None if OK."""
    global _request_count, _last_reset, _daily_count, _daily_reset

    now = datetime.now()

    if (now - _last_reset).total_seconds() >= 60:
        _request_count = 0
        _last_reset = now

    if now.date() > _daily_reset:
        _daily_count = 0
        _daily_reset = now.date()

    if _request_count >= RATE_LIMIT_PER_MINUTE:
        return "Rate limit exceeded (per minute)"
    if _daily_count >= RATE_LIMIT_PER_DAY:
        return "Rate limit exceeded (daily)"

    _request_count += 1
    _daily_count += 1
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso8601_duration(duration: str) -> int:
    """Convert ISO 8601 duration (PT19S, PT8M45S) to seconds."""
    try:
        import isodate
        return int(isodate.parse_duration(duration).total_seconds())
    except Exception:
        return 0


def _extract_chapters(description: str) -> List[Dict[str, Any]]:
    """Extract chapter markers from video description."""
    chapters: List[Dict[str, Any]] = []
    pattern = r'(\d{1,2}):(\d{2})\s+(.+?)(?:\n|$)'
    matches = re.findall(pattern, description)

    for i, (mins, secs, label) in enumerate(matches):
        start_seconds = int(mins) * 60 + int(secs)
        end_seconds = None
        if i < len(matches) - 1:
            next_mins, next_secs, _ = matches[i + 1]
            end_seconds = int(next_mins) * 60 + int(next_secs)
        chapters.append({
            "start": start_seconds,
            "end": end_seconds,
            "label": label.strip(),
        })
    return chapters


def _build_client(api_key: str):
    """Build YouTube API client."""
    from googleapiclient.discovery import build
    return build('youtube', 'v3', developerKey=api_key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_videos(
    query: str,
    api_key: str,
    max_results: int = 10,
    channel_id: Optional[str] = None,
    order: str = "relevance",
) -> Dict[str, Any]:
    """Search YouTube videos."""
    err = _check_rate_limit()
    if err:
        return {"error": err}

    try:
        youtube = _build_client(api_key)
    except Exception as e:
        return {"error": f"YouTube API client error: {e}"}

    try:
        search_params: Dict[str, Any] = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'maxResults': max_results,
            'order': order,
        }
        if channel_id:
            search_params['channelId'] = channel_id

        search_response = youtube.search().list(**search_params).execute()
        video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]

        if not video_ids:
            return {"query": query, "results": [], "total_results": 0}

        videos_response = youtube.videos().list(
            part='snippet,contentDetails,statistics',
            id=','.join(video_ids),
        ).execute()

        results = []
        for video in videos_response.get('items', []):
            vid = video['id']
            snippet = video['snippet']
            content = video['contentDetails']
            stats = video['statistics']
            desc = snippet.get('description', '')
            chapters = _extract_chapters(desc)

            results.append({
                "video_id": vid,
                "title": snippet['title'],
                "description": desc[:500],
                "channel_title": snippet['channelTitle'],
                "channel_id": snippet['channelId'],
                "thumbnail_url": snippet['thumbnails']['high']['url'],
                "published_at": snippet['publishedAt'][:10],
                "duration_seconds": _parse_iso8601_duration(content['duration']),
                "view_count": int(stats.get('viewCount', 0)),
                "like_count": int(stats.get('likeCount', 0)),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "chapters": chapters or None,
            })

        return {
            "query": query,
            "results": results,
            "total_results": search_response['pageInfo']['totalResults'],
        }

    except Exception as e:
        logger.error("YouTube search error: %s", e)
        return {"error": f"YouTube API error: {e}"}


def get_video(video_id: str, api_key: str) -> Dict[str, Any]:
    """Get detailed video metadata."""
    err = _check_rate_limit()
    if err:
        return {"error": err}

    try:
        youtube = _build_client(api_key)
        response = youtube.videos().list(
            part='snippet,contentDetails,statistics',
            id=video_id,
        ).execute()

        if not response.get('items'):
            return {"error": f"Video not found: {video_id}"}

        video = response['items'][0]
        snippet = video['snippet']
        content = video['contentDetails']
        stats = video['statistics']
        desc = snippet.get('description', '')

        return {
            "video_id": video_id,
            "title": snippet['title'],
            "description": desc,
            "channel_title": snippet['channelTitle'],
            "channel_id": snippet['channelId'],
            "thumbnail_url": snippet['thumbnails']['high']['url'],
            "published_at": snippet['publishedAt'][:10],
            "duration_seconds": _parse_iso8601_duration(content['duration']),
            "view_count": int(stats.get('viewCount', 0)),
            "like_count": int(stats.get('likeCount', 0)),
            "comment_count": int(stats.get('commentCount', 0)),
            "tags": snippet.get('tags', []),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "chapters": _extract_chapters(desc),
        }

    except Exception as e:
        logger.error("Video metadata error: %s", e)
        return {"error": f"YouTube API error: {e}"}


def get_transcript(
    video_id: str,
    language: str = "en",
) -> Dict[str, Any]:
    """Get transcript/captions for a video. Does NOT consume YouTube API quota."""
    err = _check_rate_limit()
    if err:
        return {"error": err}

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            NoTranscriptAvailable,
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
        )
    except ImportError:
        return {"error": "youtube-transcript-api not installed. Run: pip install youtube-transcript-api"}

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id, languages=[language])

        segments = []
        for snippet in transcript.snippets:
            segments.append({
                "text": snippet.text,
                "start": snippet.start,
                "duration": snippet.duration,
            })

        full_text = " ".join(seg["text"] for seg in segments)

        return {
            "video_id": video_id,
            "language": language,
            "segments": segments,
            "full_text": full_text,
            "segment_count": len(segments),
        }

    except TranscriptsDisabled:
        return {"error": f"Transcripts are disabled for video: {video_id}"}
    except (NoTranscriptFound, NoTranscriptAvailable):
        return {"error": f"No transcript found for video {video_id} in language '{language}'"}
    except VideoUnavailable:
        return {"error": f"Video unavailable: {video_id}"}
    except Exception as e:
        logger.error("Transcript error for %s: %s", video_id, e)
        return {"error": f"Transcript fetch error: {e}"}


def list_transcript_languages(video_id: str) -> Dict[str, Any]:
    """List available transcript languages for a video."""
    err = _check_rate_limit()
    if err:
        return {"error": err}

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, VideoUnavailable
    except ImportError:
        return {"error": "youtube-transcript-api not installed"}

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)

        available = []
        for t in transcript_list:
            available.append({
                "language": t.language,
                "language_code": t.language_code,
                "is_generated": t.is_generated,
            })

        return {"video_id": video_id, "transcripts": available}

    except TranscriptsDisabled:
        return {"error": f"Transcripts are disabled for video: {video_id}"}
    except VideoUnavailable:
        return {"error": f"Video unavailable: {video_id}"}
    except Exception as e:
        logger.error("Transcript list error for %s: %s", video_id, e)
        return {"error": f"Transcript list error: {e}"}
