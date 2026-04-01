"""Observer Mode — AI watches the screen and provides guidance.

The observer captures periodic screenshots, detects meaningful changes,
sends them to a vision LLM, and emits structured guidance (annotations,
summaries, and directions) to the VS Code webview.

Design principles:
- Observe only — never clicks, types, or modifies anything.
- Single session — one observer at a time (cost control).
- Change detection — skip LLM calls when the screen is static.
- Reuses existing DesktopBackend screenshot pipeline and PIL drawing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from cohort.desktop.config import DesktopConfig, ObserverConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ObserverState(str, Enum):
    IDLE = "idle"
    OBSERVING = "observing"
    PAUSED = "paused"


@dataclass
class ObserverGuidance:
    guidance_id: str
    timestamp: str
    summary: str
    detail: str
    annotations: List[Dict[str, Any]]
    confidence: float
    context_hint: str
    screenshot_path: str

    def to_dict(self) -> dict:
        return {
            "guidance_id": self.guidance_id,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "detail": self.detail,
            "annotations": self.annotations,
            "confidence": self.confidence,
            "context_hint": self.context_hint,
            "screenshot_path": self.screenshot_path,
        }


@dataclass
class ObserverSession:
    session_id: str = "observer"
    state: ObserverState = ObserverState.IDLE
    desktop_session_id: str = "default"
    user_goal: str = ""
    context_window: List[Dict[str, Any]] = field(default_factory=list)
    latest_guidance: Optional[ObserverGuidance] = None
    guidance_history: List[ObserverGuidance] = field(default_factory=list)
    observation_count: int = 0
    last_screenshot_path: Optional[str] = None
    created_at: Optional[str] = None
    error: str = ""


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

_COLORS = {
    "red": (255, 51, 51),
    "yellow": (255, 215, 0),
    "cyan": (0, 229, 255),
    "green": (51, 255, 51),
    "white": (255, 255, 255),
    "orange": (255, 165, 0),
}


def _color_rgb(name: str) -> tuple:
    return _COLORS.get(name.lower(), (255, 51, 51))


# ---------------------------------------------------------------------------
# Font loading (same pattern as backend.py _stamp_screenshot)
# ---------------------------------------------------------------------------

_font_cache: Dict[int, ImageFont.FreeTypeFont] = {}


def _get_font(size: int = 14) -> ImageFont.FreeTypeFont:
    if size in _font_cache:
        return _font_cache[size]
    for name in ("consolab.ttf", "consola.ttf", "arial.ttf"):
        try:
            f = ImageFont.truetype(name, size)
            _font_cache[size] = f
            return f
        except (OSError, IOError):
            continue
    f = ImageFont.load_default()
    _font_cache[size] = f
    return f


# ---------------------------------------------------------------------------
# Annotation drawing
# ---------------------------------------------------------------------------

def _draw_arrowhead(draw: ImageDraw.Draw, x: int, y: int,
                    angle: float, size: int, color: tuple) -> None:
    """Draw a small triangle arrowhead at (x, y) pointing in *angle* radians."""
    pts = []
    for offset in (-2.5, 2.5):
        ax = x - size * math.cos(angle + offset)
        ay = y - size * math.sin(angle + offset)
        pts.append((int(ax), int(ay)))
    pts.append((x, y))
    draw.polygon(pts, fill=color)


def draw_annotations(screenshot_path: str, annotations: List[Dict[str, Any]]) -> str:
    """Draw guidance annotations on a *copy* of the screenshot.

    Returns the path to the annotated image (``*_annotated.jpg``).
    """
    img = Image.open(screenshot_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _get_font(14)
    font_lg = _get_font(16)

    for ann in annotations:
        atype = ann.get("type", "label")
        rgb = _color_rgb(ann.get("color", "red"))
        rgba = (*rgb, 200)
        fill_rgba = (*rgb, 40)

        if atype == "box":
            x, y = int(ann.get("x", 0)), int(ann.get("y", 0))
            w, h = int(ann.get("w", 80)), int(ann.get("h", 40))
            draw.rectangle([(x, y), (x + w, y + h)], fill=fill_rgba, outline=rgba, width=3)
            label = ann.get("text", "")
            if label:
                _draw_label(draw, label, x, max(y - 20, 0), rgb, font_lg)

        elif atype == "arrow":
            x1, y1 = int(ann.get("x", 0)), int(ann.get("y", 0))
            x2, y2 = int(ann.get("end_x", x1 + 40)), int(ann.get("end_y", y1 + 40))
            draw.line([(x1, y1), (x2, y2)], fill=rgba, width=3)
            angle = math.atan2(y2 - y1, x2 - x1)
            _draw_arrowhead(draw, x2, y2, angle, 12, rgb)
            label = ann.get("text", "")
            if label:
                _draw_label(draw, label, x1, max(y1 - 20, 0), rgb, font_lg)

        elif atype == "label":
            x, y = int(ann.get("x", 0)), int(ann.get("y", 0))
            _draw_label(draw, ann.get("text", ""), x, y, rgb, font_lg)

    # Composite overlay onto original
    result = Image.alpha_composite(img, overlay).convert("RGB")

    annotated_path = screenshot_path.replace(".jpg", "_annotated.jpg")
    result.save(annotated_path, "JPEG", quality=90)
    return annotated_path


def _draw_label(draw: ImageDraw.Draw, text: str, x: int, y: int,
                color: tuple, font: ImageFont.FreeTypeFont | None = None) -> None:
    """Draw a text label with a dark background for readability."""
    font = font or _get_font(14)
    bbox = draw.textbbox((x, y), text, font=font)
    pad = 3
    draw.rectangle(
        [(bbox[0] - pad, bbox[1] - pad), (bbox[2] + pad, bbox[3] + pad)],
        fill=(0, 0, 0, 180),
    )
    draw.text((x, y), text, fill=(*color, 255), font=font)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def _screen_changed(prev_path: Optional[str], curr_path: str,
                    threshold: float = 5.0) -> bool:
    """Compare two screenshots; return True if change exceeds threshold %.

    Resizes both to 256x192 thumbnails for a fast comparison.
    """
    if prev_path is None:
        return True
    try:
        prev = Image.open(prev_path).convert("RGB").resize((256, 192), Image.NEAREST)
        curr = Image.open(curr_path).convert("RGB").resize((256, 192), Image.NEAREST)
    except Exception:
        return True

    prev_px = prev.load()
    curr_px = curr.load()
    changed = 0
    total = 256 * 192
    px_threshold = 30  # per-channel difference considered "changed"

    for yy in range(192):
        for xx in range(256):
            r1, g1, b1 = prev_px[xx, yy]
            r2, g2, b2 = curr_px[xx, yy]
            if (abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)) > px_threshold * 3:
                changed += 1

    pct = (changed / total) * 100.0
    return pct >= threshold


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an Observer AI assistant. You are watching a user's screen in real-time.
Your role is to help them find things — not to do things for them.

The user is working on: {goal}

Recent context (last few observations):
{context}

Current active window: {active_window}

Analyze the screenshot and provide guidance. Respond in this exact JSON format:
{{
  "summary": "One sentence describing what to do next or where to look",
  "detail": "2-3 sentences explaining why and how",
  "annotations": [
    {{"type": "box", "x": 100, "y": 200, "w": 150, "h": 40, "text": "Click here", "color": "red"}},
    {{"type": "arrow", "x": 300, "y": 100, "end_x": 350, "end_y": 150, "text": "Look here", "color": "yellow"}},
    {{"type": "label", "x": 50, "y": 50, "text": "Note this", "color": "cyan"}}
  ],
  "confidence": 0.85,
  "context_hint": "User appears to be editing a config file"
}}

Rules:
- Only suggest LOOKING at things or CLICKING on visible UI elements.
- Annotation coordinates are relative to the screenshot (top-left = 0,0).
- If nothing useful to suggest, respond: {{"summary": "", "confidence": 0.0}}
- Keep annotations minimal (max 3).
- Be specific: "Click the Save button in the top-right toolbar" not "Save your work".
- Return ONLY the JSON object, no markdown fences or extra text.
"""


def _build_prompt(session: ObserverSession, active_window: str) -> tuple[str, str]:
    """Build (system_prompt, user_text) for the vision LLM call."""
    ctx_lines = []
    for entry in session.context_window[-session_config_size(session):]:
        ctx_lines.append(f"- [{entry.get('ts', '?')}] {entry.get('hint', 'unknown')}")
    context_str = "\n".join(ctx_lines) if ctx_lines else "(first observation)"

    system = _SYSTEM_PROMPT.format(
        goal=session.user_goal or "general computer work",
        context=context_str,
        active_window=active_window or "unknown",
    )
    user_text = "Analyze this screenshot and provide guidance."
    return system, user_text


def session_config_size(session: ObserverSession) -> int:
    """Return context window size (default 5)."""
    return 5  # overridden at call site from config


# ---------------------------------------------------------------------------
# Guidance parsing
# ---------------------------------------------------------------------------

def _parse_guidance(raw_text: str, screenshot_path: str) -> Optional[ObserverGuidance]:
    """Parse the LLM JSON response into an ObserverGuidance."""
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("[observer] Failed to parse LLM response as JSON: %.200s", text)
        return None

    summary = data.get("summary", "").strip()
    confidence = float(data.get("confidence", 0.0))
    if not summary or confidence < 0.01:
        return None  # nothing useful

    return ObserverGuidance(
        guidance_id=uuid.uuid4().hex[:8],
        timestamp=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        detail=data.get("detail", ""),
        annotations=data.get("annotations", [])[:3],
        confidence=confidence,
        context_hint=data.get("context_hint", ""),
        screenshot_path=screenshot_path,
    )


# ---------------------------------------------------------------------------
# Singleton observer + thread management
# ---------------------------------------------------------------------------

_session: Optional[ObserverSession] = None
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None

# Set by the server at startup so the observer can access shared state.
_backend = None       # DesktopBackend instance
_config: Optional[DesktopConfig] = None
_cloud_settings: Optional[dict] = None  # settings dict for get_cloud_backend()
_emit_fn = None       # async fn(event, data) for Socket.IO broadcast


def configure(
    backend,
    config: DesktopConfig,
    cloud_settings: dict,
    emit_fn=None,
) -> None:
    """Called once at server startup to wire shared state."""
    global _backend, _config, _cloud_settings, _emit_fn
    _backend = backend
    _config = config
    _cloud_settings = cloud_settings
    _emit_fn = emit_fn


def get_status() -> dict:
    with _lock:
        if _session is None:
            return {"state": "idle"}
        result = {
            "state": _session.state.value,
            "desktop_session_id": _session.desktop_session_id,
            "user_goal": _session.user_goal,
            "observation_count": _session.observation_count,
            "created_at": _session.created_at,
            "error": _session.error,
        }
        if _session.latest_guidance:
            result["latest_guidance"] = _session.latest_guidance.to_dict()
        return result


def get_history(limit: int = 20) -> list[dict]:
    with _lock:
        if _session is None:
            return []
        items = _session.guidance_history[-limit:]
        return [g.to_dict() for g in items]


def start_observer(
    user_goal: str = "",
    desktop_session_id: str = "default",
) -> dict:
    """Start the observer loop. Returns status dict."""
    global _session, _thread

    with _lock:
        if _session is not None and _session.state != ObserverState.IDLE:
            return {"error": "Observer already running", "state": _session.state.value}

        _session = ObserverSession(
            desktop_session_id=desktop_session_id,
            user_goal=user_goal,
            state=ObserverState.OBSERVING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    _thread = threading.Thread(target=_observer_loop, daemon=True, name="desktop-observer")
    _thread.start()
    log.info("[OK] Observer started (goal=%r, session=%s)", user_goal, desktop_session_id)
    return get_status()


def stop_observer() -> dict:
    global _session
    with _lock:
        if _session is None:
            return {"state": "idle"}
        _session.state = ObserverState.IDLE
    log.info("[OK] Observer stopped")
    return {"state": "idle"}


def pause_observer() -> dict:
    with _lock:
        if _session is None or _session.state != ObserverState.OBSERVING:
            return get_status()
        _session.state = ObserverState.PAUSED
    log.info("[OK] Observer paused")
    return get_status()


def resume_observer() -> dict:
    with _lock:
        if _session is None or _session.state != ObserverState.PAUSED:
            return get_status()
        _session.state = ObserverState.OBSERVING
    log.info("[OK] Observer resumed")
    return get_status()


def set_goal(user_goal: str) -> dict:
    with _lock:
        if _session is None:
            return {"error": "Observer not running"}
        _session.user_goal = user_goal
    return get_status()


# ---------------------------------------------------------------------------
# Observation loop (daemon thread)
# ---------------------------------------------------------------------------

def _observer_loop() -> None:
    """Main observation loop — runs as a daemon thread."""
    from cohort.local.cloud import get_cloud_backend

    ocfg: ObserverConfig = _config.observer if _config else ObserverConfig()
    interval = ocfg.interval_seconds

    log.info("[observer] Loop started (interval=%ds, threshold=%.1f%%)",
             interval, ocfg.change_threshold)

    while True:
        with _lock:
            if _session is None or _session.state == ObserverState.IDLE:
                break
            if _session.state == ObserverState.PAUSED:
                pass  # fall through to sleep

        # Check paused outside lock
        with _lock:
            state = _session.state if _session else ObserverState.IDLE
        if state == ObserverState.PAUSED:
            time.sleep(1)
            continue
        if state == ObserverState.IDLE:
            break

        try:
            _observe_once(ocfg, get_cloud_backend)
        except Exception:
            log.exception("[observer] Observation error")
            with _lock:
                if _session:
                    _session.error = "Observation error — see server logs"

        time.sleep(interval)

    log.info("[observer] Loop exited")


def _observe_once(ocfg: ObserverConfig, get_cloud_backend_fn) -> None:
    """Single observation iteration."""
    if _backend is None or _session is None:
        return

    # 1. Capture screenshot
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            _backend.screenshot(_session.desktop_session_id)
        )
    finally:
        loop.close()

    if not result.success:
        log.warning("[observer] Screenshot failed: %s", result.error)
        return

    # Extract file path from result data (format: "Screenshot saved: /path/to/file.jpg")
    screenshot_path = _extract_path(result.data)
    if not screenshot_path:
        return

    # 2. Change detection
    with _lock:
        prev_path = _session.last_screenshot_path
    if not _screen_changed(prev_path, screenshot_path, ocfg.change_threshold):
        return

    # 3. Get active window for context
    try:
        loop2 = asyncio.new_event_loop()
        try:
            aw_result = loop2.run_until_complete(
                _backend.get_active_window(_session.desktop_session_id)
            )
        finally:
            loop2.close()
        active_window = aw_result.data if aw_result.success else ""
    except Exception:
        active_window = ""

    # 4. Build prompt
    with _lock:
        system_prompt, user_text = _build_prompt(_session, active_window)

    # 5. Call vision LLM
    if _cloud_settings is None:
        log.warning("[observer] No cloud settings — skipping LLM call")
        return

    cloud = get_cloud_backend_fn(_cloud_settings)
    if cloud is None:
        log.warning("[observer] No cloud backend configured — skipping LLM call")
        return

    try:
        resp = cloud.complete_vision(
            system_prompt=system_prompt,
            user_text=user_text,
            image_path=screenshot_path,
            max_tokens=ocfg.max_tokens,
            temperature=0.3,
        )
    except Exception:
        log.exception("[observer] Vision LLM call failed")
        return

    # 6. Parse guidance
    guidance = _parse_guidance(resp.text, screenshot_path)
    if guidance is None:
        # LLM had nothing useful — still update state
        with _lock:
            _session.last_screenshot_path = screenshot_path
            _session.observation_count += 1
        return

    # 7. Draw annotations on screenshot copy
    if guidance.annotations:
        try:
            annotated = draw_annotations(screenshot_path, guidance.annotations)
            guidance.screenshot_path = annotated
        except Exception:
            log.exception("[observer] Annotation drawing failed")

    # 8. Update session state
    with _lock:
        _session.latest_guidance = guidance
        _session.guidance_history.append(guidance)
        if len(_session.guidance_history) > ocfg.max_guidance_history:
            _session.guidance_history = _session.guidance_history[-ocfg.max_guidance_history:]
        _session.context_window.append({
            "ts": guidance.timestamp,
            "hint": guidance.context_hint,
            "summary": guidance.summary,
        })
        if len(_session.context_window) > ocfg.context_window_size:
            _session.context_window = _session.context_window[-ocfg.context_window_size:]
        _session.last_screenshot_path = screenshot_path
        _session.observation_count += 1

    # 9. Emit to VS Code via Socket.IO
    _emit_guidance(guidance)

    log.info("[observer] Guidance #%d: %s (conf=%.2f)",
             _session.observation_count, guidance.summary[:80], guidance.confidence)


def _emit_guidance(guidance: ObserverGuidance) -> None:
    """Push guidance to connected dashboards via Socket.IO."""
    if _emit_fn is None:
        return
    try:
        data = guidance.to_dict()
        # Try to schedule on the running event loop
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(_emit_fn("cohort:observer_guidance", data), loop)
        except RuntimeError:
            # No running loop — use asyncio.run as fallback
            asyncio.run(_emit_fn("cohort:observer_guidance", data))
    except Exception:
        log.debug("[observer] Socket.IO emit failed", exc_info=True)


def _extract_path(data: str) -> Optional[str]:
    """Extract file path from DesktopResult data string."""
    # Format: "Screenshot saved: /path/to/file.jpg"
    if ":" in data:
        path_str = data.split(":", 1)[1].strip()
        if Path(path_str).exists():
            return path_str
    # Maybe the data itself is a path
    if Path(data).exists():
        return data
    return None
