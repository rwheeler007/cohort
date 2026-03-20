"""Cohort Browser Backend -- Playwright browser automation for MCP tools.

Provides a ``BrowserBackend`` protocol with two implementations:

* **PlaywrightDirectBackend** -- uses the ``playwright`` library directly.
  This is the primary/default backend.  No external process required.
* **PlaywrightMCPBackend** -- proxies to the ``@playwright/mcp`` npm server
  via MCP client SDK.  Future optimisation, currently stubbed.

Concurrency model:
    - One Chromium process, reused across calls.
    - One action at a time **per agent** (asyncio.Lock per agent_id).
    - Max concurrent BrowserContexts capped (configurable).
    - Default-deny for RFC1918 / localhost URLs.

Usage::

    backend = get_browser_backend()
    result = await backend.navigate("agent_1", "https://example.com")
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# =====================================================================
# Constants
# =====================================================================

# RFC1918 + loopback + link-local ranges blocked by default
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Default max concurrent browser contexts
DEFAULT_MAX_CONTEXTS = 3

# Default page timeout (ms)
DEFAULT_TIMEOUT_MS = 30_000

# Max text output length
MAX_TEXT_LENGTH = 25_000


# =====================================================================
# Data types
# =====================================================================


@dataclass
class BrowserResult:
    """Standard return from a browser action."""

    success: bool
    data: str = ""
    error: str = ""
    url: str = ""
    title: str = ""
    action: str = ""

    def to_str(self) -> str:
        """Format for MCP tool return."""
        if not self.success:
            return f"Error: {self.error}"
        parts = []
        if self.title:
            parts.append(f"Title: {self.title}")
        if self.url:
            parts.append(f"URL: {self.url}")
        if self.data:
            parts.append(self.data)
        return "\n".join(parts) if parts else "[OK]"


@dataclass
class AgentBrowserState:
    """Per-agent browser context and lock."""

    agent_id: str
    context: Any = None  # playwright BrowserContext
    page: Any = None  # playwright Page (active tab)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pages: dict[str, Any] = field(default_factory=dict)  # tab_id -> Page


# =====================================================================
# URL safety
# =====================================================================


def is_url_allowed(
    url: str,
    *,
    allow_local: bool = False,
    blocklist: list[str] | None = None,
    allowlist: list[str] | None = None,
) -> tuple[bool, str]:
    """Check if a URL is safe to navigate to.

    Returns (allowed, reason).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL"

    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"

    # Scheme check
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme: {parsed.scheme}"

    # Blocklist check (glob patterns)
    if blocklist:
        for pattern in blocklist:
            if _glob_match(hostname, pattern):
                return False, f"URL blocked by pattern: {pattern}"

    # Allowlist check (if set, only these pass -- and skip RFC1918 check)
    if allowlist:
        matched = any(_glob_match(hostname, p) for p in allowlist)
        if not matched:
            return False, "URL not in allowlist"
        # Allowlisted URLs skip the private IP check
        return True, ""

    # RFC1918 / localhost check
    if not allow_local:
        try:
            addrs = socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in addrs:
                ip = ipaddress.ip_address(sockaddr[0])
                for net in _PRIVATE_NETWORKS:
                    if ip in net:
                        return False, f"Private/local IP blocked: {ip}"
        except socket.gaierror:
            return False, f"DNS resolution failed for {hostname}"

    return True, ""


def _glob_match(hostname: str, pattern: str) -> bool:
    """Simple glob matching for hostnames. Supports leading *. wildcard."""
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.com"
        return hostname.endswith(suffix) or hostname == pattern[2:]
    return hostname == pattern


# =====================================================================
# Protocol
# =====================================================================


@runtime_checkable
class BrowserBackend(Protocol):
    """Interface for browser automation backends."""

    async def start(self) -> None:
        """Start the browser process."""
        ...

    async def stop(self) -> None:
        """Stop the browser and clean up all contexts."""
        ...

    async def is_available(self) -> bool:
        """Check if the backend is ready."""
        ...

    # -- Navigation --

    async def navigate(
        self, agent_id: str, url: str, *, wait_until: str = "domcontentloaded"
    ) -> BrowserResult:
        ...

    async def navigate_back(self, agent_id: str) -> BrowserResult:
        ...

    async def close_page(self, agent_id: str) -> BrowserResult:
        ...

    # -- Content reading --

    async def snapshot(self, agent_id: str) -> BrowserResult:
        """Get accessibility tree snapshot of current page."""
        ...

    async def screenshot(
        self, agent_id: str, *, full_page: bool = False
    ) -> BrowserResult:
        """Take screenshot, return file path."""
        ...

    async def get_text(self, agent_id: str) -> BrowserResult:
        """Extract visible text content from current page."""
        ...

    async def console_messages(self, agent_id: str) -> BrowserResult:
        ...

    async def network_requests(self, agent_id: str) -> BrowserResult:
        ...

    # -- Interaction --

    async def click(
        self, agent_id: str, selector: str, **kwargs: Any
    ) -> BrowserResult:
        ...

    async def fill(
        self, agent_id: str, selector: str, value: str
    ) -> BrowserResult:
        ...

    async def type_text(
        self, agent_id: str, selector: str, text: str, **kwargs: Any
    ) -> BrowserResult:
        ...

    async def press_key(self, agent_id: str, key: str) -> BrowserResult:
        ...

    async def select_option(
        self, agent_id: str, selector: str, value: str
    ) -> BrowserResult:
        ...

    async def hover(self, agent_id: str, selector: str) -> BrowserResult:
        ...

    async def drag(
        self, agent_id: str, source: str, target: str
    ) -> BrowserResult:
        ...

    async def file_upload(
        self, agent_id: str, selector: str, paths: list[str]
    ) -> BrowserResult:
        ...

    async def handle_dialog(
        self, agent_id: str, action: str, *, prompt_text: str = ""
    ) -> BrowserResult:
        ...

    # -- Advanced: coordinate-based --

    async def mouse_click_xy(
        self, agent_id: str, x: float, y: float, *, button: str = "left"
    ) -> BrowserResult:
        ...

    async def mouse_move_xy(
        self, agent_id: str, x: float, y: float
    ) -> BrowserResult:
        ...

    async def mouse_drag_xy(
        self, agent_id: str, start_x: float, start_y: float, end_x: float, end_y: float
    ) -> BrowserResult:
        ...

    async def mouse_wheel(
        self, agent_id: str, delta_x: float, delta_y: float
    ) -> BrowserResult:
        ...

    async def resize(
        self, agent_id: str, width: int, height: int
    ) -> BrowserResult:
        ...

    # -- Advanced: JS eval --

    async def evaluate(
        self, agent_id: str, expression: str
    ) -> BrowserResult:
        ...

    # -- Advanced: cookies / storage --

    async def cookie_list(self, agent_id: str) -> BrowserResult:
        ...

    async def cookie_set(
        self, agent_id: str, name: str, value: str, **kwargs: Any
    ) -> BrowserResult:
        ...

    async def cookie_delete(self, agent_id: str, name: str) -> BrowserResult:
        ...

    async def cookie_clear(self, agent_id: str) -> BrowserResult:
        ...

    async def storage_get(
        self, agent_id: str, key: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        ...

    async def storage_set(
        self, agent_id: str, key: str, value: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        ...

    async def storage_delete(
        self, agent_id: str, key: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        ...

    async def storage_clear(
        self, agent_id: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        ...

    async def storage_list(
        self, agent_id: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        ...

    # -- Advanced: network mocking --

    async def route_set(
        self, agent_id: str, url_pattern: str, response_body: str, **kwargs: Any
    ) -> BrowserResult:
        ...

    async def route_list(self, agent_id: str) -> BrowserResult:
        ...

    async def route_remove(self, agent_id: str, url_pattern: str) -> BrowserResult:
        ...

    # -- Tabs --

    async def tabs_list(self, agent_id: str) -> BrowserResult:
        ...

    async def tab_new(self, agent_id: str, *, url: str = "") -> BrowserResult:
        ...

    async def tab_select(self, agent_id: str, tab_id: str) -> BrowserResult:
        ...

    async def tab_close(self, agent_id: str, tab_id: str) -> BrowserResult:
        ...

    # -- Wait --

    async def wait_for(
        self, agent_id: str, *, text: str = "", selector: str = "", timeout_ms: int = 5000
    ) -> BrowserResult:
        ...

    # -- PDF --

    async def pdf_save(self, agent_id: str, path: str) -> BrowserResult:
        ...

    # -- Verification --

    async def verify_text_visible(self, agent_id: str, text: str) -> BrowserResult:
        ...

    async def verify_element_visible(self, agent_id: str, selector: str) -> BrowserResult:
        ...


# =====================================================================
# PlaywrightDirectBackend
# =====================================================================


class PlaywrightDirectBackend:
    """Browser backend using the playwright library directly.

    One Chromium process shared across all agents.  Each agent gets an
    isolated BrowserContext with its own cookies, storage, and pages.
    A per-agent asyncio.Lock ensures one action at a time per agent.
    A semaphore caps total concurrent contexts.
    """

    def __init__(
        self,
        *,
        max_contexts: int = DEFAULT_MAX_CONTEXTS,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        allow_local: bool = False,
        blocklist: list[str] | None = None,
        allowlist: list[str] | None = None,
        screenshot_dir: str = "",
    ) -> None:
        self._max_contexts = max_contexts
        self._timeout_ms = timeout_ms
        self._allow_local = allow_local
        self._blocklist = blocklist or []
        self._allowlist = allowlist or []
        self._screenshot_dir = screenshot_dir

        self._pw: Any = None  # playwright context manager
        self._browser: Any = None  # Browser instance
        self._agents: dict[str, AgentBrowserState] = {}
        self._context_semaphore = asyncio.Semaphore(max_contexts)
        self._started = False

    # -- Lifecycle --

    async def start(self) -> None:
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
            self._started = True
            logger.info("[OK] Browser backend started (Chromium, headless)")
        except Exception as exc:
            logger.error("[X] Failed to start browser backend: %s", exc)
            raise

    async def stop(self) -> None:
        for state in self._agents.values():
            if state.context:
                try:
                    await state.context.close()
                except Exception:
                    pass
        self._agents.clear()
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._started = False
        logger.info("[OK] Browser backend stopped")

    async def is_available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return self._started
        except ImportError:
            return False

    # -- Agent context management --

    async def _get_agent_state(self, agent_id: str) -> AgentBrowserState:
        """Get or create an isolated browser context for an agent."""
        if agent_id not in self._agents:
            if not self._started:
                await self.start()
            await self._context_semaphore.acquire()
            context = await self._browser.new_context()
            page = await context.new_page()
            state = AgentBrowserState(
                agent_id=agent_id,
                context=context,
                page=page,
            )
            state.pages["tab_0"] = page
            # Collect console messages
            page._console_messages: list[str] = []
            page.on("console", lambda msg: page._console_messages.append(
                f"[{msg.type}] {msg.text}"
            ))
            # Collect network requests
            page._network_log: list[dict] = []
            page.on("request", lambda req: page._network_log.append({
                "method": req.method,
                "url": req.url,
            }))
            self._agents[agent_id] = state
        return self._agents[agent_id]

    async def _release_agent(self, agent_id: str) -> None:
        """Close an agent's browser context and release the semaphore."""
        state = self._agents.pop(agent_id, None)
        if state and state.context:
            try:
                await state.context.close()
            except Exception:
                pass
            self._context_semaphore.release()

    def _check_url(self, url: str) -> tuple[bool, str]:
        """Validate URL against safety rules."""
        return is_url_allowed(
            url,
            allow_local=self._allow_local,
            blocklist=self._blocklist,
            allowlist=self._allowlist,
        )

    async def _ensure_page(self, agent_id: str) -> tuple[AgentBrowserState, Any]:
        """Return the agent state and its active page."""
        state = await self._get_agent_state(agent_id)
        if state.page is None or state.page.is_closed():
            state.page = await state.context.new_page()
            tab_id = f"tab_{len(state.pages)}"
            state.pages[tab_id] = state.page
        return state, state.page

    # -- Navigation --

    async def navigate(
        self, agent_id: str, url: str, *, wait_until: str = "domcontentloaded"
    ) -> BrowserResult:
        allowed, reason = self._check_url(url)
        if not allowed:
            return BrowserResult(success=False, error=reason, action="navigate")

        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.goto(url, timeout=self._timeout_ms, wait_until=wait_until)
                title = await page.title()
                return BrowserResult(
                    success=True,
                    url=page.url,
                    title=title,
                    action="navigate",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="navigate"
                )

    async def navigate_back(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.go_back(timeout=self._timeout_ms)
                title = await page.title()
                return BrowserResult(
                    success=True, url=page.url, title=title, action="navigate_back"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="navigate_back"
                )

    async def close_page(self, agent_id: str) -> BrowserResult:
        await self._release_agent(agent_id)
        return BrowserResult(success=True, action="close_page", data="Browser context closed.")

    # -- Content reading --

    async def snapshot(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                tree = await page.accessibility.snapshot()
                if tree is None:
                    return BrowserResult(
                        success=True, data="(empty accessibility tree)",
                        url=page.url, action="snapshot",
                    )
                text = _format_accessibility_tree(tree)
                if len(text) > MAX_TEXT_LENGTH:
                    text = text[:MAX_TEXT_LENGTH] + "\n\n[...truncated]"
                return BrowserResult(
                    success=True, data=text, url=page.url, action="snapshot"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="snapshot"
                )

    async def screenshot(
        self, agent_id: str, *, full_page: bool = False
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                import tempfile
                from pathlib import Path

                if self._screenshot_dir:
                    out_dir = Path(self._screenshot_dir)
                else:
                    out_dir = Path(tempfile.gettempdir()) / "cohort_browser_screenshots"
                out_dir.mkdir(parents=True, exist_ok=True)

                filename = f"{agent_id}_{id(page)}_{asyncio.get_event_loop().time():.0f}.png"
                path = out_dir / filename
                await page.screenshot(path=str(path), full_page=full_page)
                return BrowserResult(
                    success=True,
                    data=f"Screenshot saved: {path}",
                    url=page.url,
                    action="screenshot",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="screenshot"
                )

    async def get_text(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                text = await page.evaluate("""
                    () => {
                        for (const el of document.querySelectorAll(
                            'script, style, nav, footer, header, [role="navigation"]'
                        )) { el.remove(); }
                        return document.body.innerText;
                    }
                """)
                text = (text or "").strip()
                if len(text) > MAX_TEXT_LENGTH:
                    text = text[:MAX_TEXT_LENGTH] + "\n\n[...truncated]"
                return BrowserResult(
                    success=True, data=text, url=page.url, action="get_text"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="get_text"
                )

    async def console_messages(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            msgs = getattr(page, "_console_messages", [])
            if not msgs:
                return BrowserResult(
                    success=True, data="(no console messages)", action="console_messages"
                )
            text = "\n".join(msgs[-100:])  # last 100
            return BrowserResult(
                success=True, data=text, url=page.url, action="console_messages"
            )

    async def network_requests(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            log = getattr(page, "_network_log", [])
            if not log:
                return BrowserResult(
                    success=True, data="(no network requests)", action="network_requests"
                )
            lines = [f"{r['method']} {r['url']}" for r in log[-100:]]
            return BrowserResult(
                success=True, data="\n".join(lines), url=page.url, action="network_requests"
            )

    # -- Interaction --

    async def click(
        self, agent_id: str, selector: str, **kwargs: Any
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.click(selector, timeout=self._timeout_ms, **kwargs)
                return BrowserResult(
                    success=True, data=f"Clicked: {selector}",
                    url=page.url, action="click",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="click"
                )

    async def fill(
        self, agent_id: str, selector: str, value: str
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.fill(selector, value, timeout=self._timeout_ms)
                return BrowserResult(
                    success=True, data=f"Filled '{selector}' with value",
                    url=page.url, action="fill",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="fill"
                )

    async def type_text(
        self, agent_id: str, selector: str, text: str, **kwargs: Any
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.type(selector, text, timeout=self._timeout_ms, **kwargs)
                return BrowserResult(
                    success=True, data=f"Typed into '{selector}'",
                    url=page.url, action="type_text",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="type_text"
                )

    async def press_key(self, agent_id: str, key: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.keyboard.press(key)
                return BrowserResult(
                    success=True, data=f"Pressed: {key}",
                    url=page.url, action="press_key",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="press_key"
                )

    async def select_option(
        self, agent_id: str, selector: str, value: str
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.select_option(selector, value, timeout=self._timeout_ms)
                return BrowserResult(
                    success=True, data=f"Selected '{value}' in '{selector}'",
                    url=page.url, action="select_option",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="select_option"
                )

    async def hover(self, agent_id: str, selector: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.hover(selector, timeout=self._timeout_ms)
                return BrowserResult(
                    success=True, data=f"Hovered: {selector}",
                    url=page.url, action="hover",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="hover"
                )

    async def drag(
        self, agent_id: str, source: str, target: str
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.drag_and_drop(source, target, timeout=self._timeout_ms)
                return BrowserResult(
                    success=True, data=f"Dragged '{source}' to '{target}'",
                    url=page.url, action="drag",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="drag"
                )

    async def file_upload(
        self, agent_id: str, selector: str, paths: list[str]
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.set_input_files(selector, paths, timeout=self._timeout_ms)
                return BrowserResult(
                    success=True,
                    data=f"Uploaded {len(paths)} file(s) to '{selector}'",
                    url=page.url, action="file_upload",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="file_upload"
                )

    async def handle_dialog(
        self, agent_id: str, action: str, *, prompt_text: str = ""
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                if action == "accept":
                    page.on("dialog", lambda d: d.accept(prompt_text) if prompt_text else d.accept())
                elif action == "dismiss":
                    page.on("dialog", lambda d: d.dismiss())
                else:
                    return BrowserResult(
                        success=False, error=f"Unknown action: {action}. Use 'accept' or 'dismiss'.",
                        action="handle_dialog",
                    )
                return BrowserResult(
                    success=True, data=f"Dialog handler set: {action}",
                    url=page.url, action="handle_dialog",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="handle_dialog"
                )

    # -- Advanced: coordinate-based --

    async def mouse_click_xy(
        self, agent_id: str, x: float, y: float, *, button: str = "left"
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.mouse.click(x, y, button=button)
                return BrowserResult(
                    success=True, data=f"Clicked at ({x}, {y}) [{button}]",
                    url=page.url, action="mouse_click_xy",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="mouse_click_xy"
                )

    async def mouse_move_xy(
        self, agent_id: str, x: float, y: float
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.mouse.move(x, y)
                return BrowserResult(
                    success=True, data=f"Mouse moved to ({x}, {y})",
                    url=page.url, action="mouse_move_xy",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="mouse_move_xy"
                )

    async def mouse_drag_xy(
        self, agent_id: str, start_x: float, start_y: float, end_x: float, end_y: float
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.mouse.move(start_x, start_y)
                await page.mouse.down()
                await page.mouse.move(end_x, end_y)
                await page.mouse.up()
                return BrowserResult(
                    success=True,
                    data=f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})",
                    url=page.url, action="mouse_drag_xy",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="mouse_drag_xy"
                )

    async def mouse_wheel(
        self, agent_id: str, delta_x: float, delta_y: float
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.mouse.wheel(delta_x, delta_y)
                return BrowserResult(
                    success=True, data=f"Scrolled ({delta_x}, {delta_y})",
                    url=page.url, action="mouse_wheel",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="mouse_wheel"
                )

    async def resize(
        self, agent_id: str, width: int, height: int
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.set_viewport_size({"width": width, "height": height})
                return BrowserResult(
                    success=True, data=f"Viewport resized to {width}x{height}",
                    url=page.url, action="resize",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="resize"
                )

    # -- Advanced: JS eval --

    async def evaluate(
        self, agent_id: str, expression: str
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                result = await page.evaluate(expression)
                text = str(result)
                if len(text) > MAX_TEXT_LENGTH:
                    text = text[:MAX_TEXT_LENGTH] + "\n\n[...truncated]"
                return BrowserResult(
                    success=True, data=text, url=page.url, action="evaluate"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="evaluate"
                )

    # -- Advanced: cookies --

    async def cookie_list(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            try:
                cookies = await state.context.cookies()
                if not cookies:
                    return BrowserResult(
                        success=True, data="(no cookies)", action="cookie_list"
                    )
                lines = [
                    f"{c['name']}={c['value'][:50]} (domain={c.get('domain', '?')})"
                    for c in cookies
                ]
                return BrowserResult(
                    success=True, data="\n".join(lines), action="cookie_list"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="cookie_list"
                )

    async def cookie_set(
        self, agent_id: str, name: str, value: str, **kwargs: Any
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            try:
                cookie: dict[str, Any] = {"name": name, "value": value}
                cookie.update(kwargs)
                # Playwright requires url or domain+path
                if "url" not in cookie and "domain" not in cookie:
                    _, page = await self._ensure_page(agent_id)
                    cookie["url"] = page.url
                await state.context.add_cookies([cookie])
                return BrowserResult(
                    success=True, data=f"Cookie set: {name}", action="cookie_set"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="cookie_set"
                )

    async def cookie_delete(self, agent_id: str, name: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            try:
                cookies = await state.context.cookies()
                remaining = [c for c in cookies if c["name"] != name]
                await state.context.clear_cookies()
                if remaining:
                    await state.context.add_cookies(remaining)
                return BrowserResult(
                    success=True, data=f"Cookie deleted: {name}", action="cookie_delete"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="cookie_delete"
                )

    async def cookie_clear(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            try:
                await state.context.clear_cookies()
                return BrowserResult(
                    success=True, data="All cookies cleared", action="cookie_clear"
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="cookie_clear"
                )

    # -- Advanced: localStorage / sessionStorage --

    async def storage_get(
        self, agent_id: str, key: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                storage = "localStorage" if storage_type == "local" else "sessionStorage"
                val = await page.evaluate(f"{storage}.getItem({key!r})")
                return BrowserResult(
                    success=True,
                    data=str(val) if val is not None else "(null)",
                    url=page.url, action="storage_get",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="storage_get"
                )

    async def storage_set(
        self, agent_id: str, key: str, value: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                storage = "localStorage" if storage_type == "local" else "sessionStorage"
                await page.evaluate(f"{storage}.setItem({key!r}, {value!r})")
                return BrowserResult(
                    success=True, data=f"Set {storage}.{key}", url=page.url, action="storage_set",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="storage_set"
                )

    async def storage_delete(
        self, agent_id: str, key: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                storage = "localStorage" if storage_type == "local" else "sessionStorage"
                await page.evaluate(f"{storage}.removeItem({key!r})")
                return BrowserResult(
                    success=True, data=f"Deleted {storage}.{key}", url=page.url, action="storage_delete",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="storage_delete"
                )

    async def storage_clear(
        self, agent_id: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                storage = "localStorage" if storage_type == "local" else "sessionStorage"
                await page.evaluate(f"{storage}.clear()")
                return BrowserResult(
                    success=True, data=f"Cleared {storage}", url=page.url, action="storage_clear",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="storage_clear"
                )

    async def storage_list(
        self, agent_id: str, *, storage_type: str = "local"
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                storage = "localStorage" if storage_type == "local" else "sessionStorage"
                items = await page.evaluate(f"""
                    () => {{
                        const s = {storage};
                        const result = {{}};
                        for (let i = 0; i < s.length; i++) {{
                            const k = s.key(i);
                            result[k] = s.getItem(k);
                        }}
                        return result;
                    }}
                """)
                if not items:
                    return BrowserResult(
                        success=True, data=f"({storage} is empty)",
                        url=page.url, action="storage_list",
                    )
                lines = [f"{k}={str(v)[:100]}" for k, v in items.items()]
                return BrowserResult(
                    success=True, data="\n".join(lines),
                    url=page.url, action="storage_list",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="storage_list"
                )

    # -- Advanced: network mocking --

    async def route_set(
        self, agent_id: str, url_pattern: str, response_body: str, **kwargs: Any
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                status = kwargs.get("status", 200)
                content_type = kwargs.get("content_type", "text/plain")

                async def handler(route: Any) -> None:
                    await route.fulfill(
                        status=status,
                        content_type=content_type,
                        body=response_body,
                    )

                await page.route(url_pattern, handler)
                return BrowserResult(
                    success=True, data=f"Route set: {url_pattern}",
                    url=page.url, action="route_set",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="route_set"
                )

    async def route_list(self, agent_id: str) -> BrowserResult:
        # Playwright doesn't expose active routes; track manually if needed
        return BrowserResult(
            success=True,
            data="Route listing requires manual tracking (not yet implemented).",
            action="route_list",
        )

    async def route_remove(self, agent_id: str, url_pattern: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.unroute(url_pattern)
                return BrowserResult(
                    success=True, data=f"Route removed: {url_pattern}",
                    url=page.url, action="route_remove",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="route_remove"
                )

    # -- Tabs --

    async def tabs_list(self, agent_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            lines = []
            for tab_id, page in state.pages.items():
                if page.is_closed():
                    continue
                active = " [active]" if page is state.page else ""
                try:
                    title = await page.title()
                except Exception:
                    title = "(unknown)"
                lines.append(f"{tab_id}: {page.url} - {title}{active}")
            return BrowserResult(
                success=True,
                data="\n".join(lines) if lines else "(no open tabs)",
                action="tabs_list",
            )

    async def tab_new(self, agent_id: str, *, url: str = "") -> BrowserResult:
        if url:
            allowed, reason = self._check_url(url)
            if not allowed:
                return BrowserResult(success=False, error=reason, action="tab_new")

        state = await self._get_agent_state(agent_id)
        async with state.lock:
            try:
                page = await state.context.new_page()
                tab_id = f"tab_{len(state.pages)}"
                state.pages[tab_id] = page
                state.page = page
                if url:
                    await page.goto(url, timeout=self._timeout_ms)
                return BrowserResult(
                    success=True, data=f"New tab: {tab_id}",
                    url=page.url, action="tab_new",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="tab_new"
                )

    async def tab_select(self, agent_id: str, tab_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            page = state.pages.get(tab_id)
            if not page or page.is_closed():
                return BrowserResult(
                    success=False, error=f"Tab not found: {tab_id}", action="tab_select"
                )
            state.page = page
            await page.bring_to_front()
            return BrowserResult(
                success=True, data=f"Switched to {tab_id}",
                url=page.url, action="tab_select",
            )

    async def tab_close(self, agent_id: str, tab_id: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            page = state.pages.pop(tab_id, None)
            if not page:
                return BrowserResult(
                    success=False, error=f"Tab not found: {tab_id}", action="tab_close"
                )
            if page is state.page:
                # Switch to another open tab
                state.page = None
                for _, p in state.pages.items():
                    if not p.is_closed():
                        state.page = p
                        break
            try:
                await page.close()
            except Exception:
                pass
            return BrowserResult(
                success=True, data=f"Closed {tab_id}", action="tab_close"
            )

    # -- Wait --

    async def wait_for(
        self, agent_id: str, *, text: str = "", selector: str = "", timeout_ms: int = 5000
    ) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                if text:
                    await page.wait_for_selector(
                        f"text={text}", timeout=timeout_ms
                    )
                    return BrowserResult(
                        success=True, data=f"Text found: {text}",
                        url=page.url, action="wait_for",
                    )
                elif selector:
                    await page.wait_for_selector(selector, timeout=timeout_ms)
                    return BrowserResult(
                        success=True, data=f"Element found: {selector}",
                        url=page.url, action="wait_for",
                    )
                else:
                    await page.wait_for_timeout(timeout_ms)
                    return BrowserResult(
                        success=True, data=f"Waited {timeout_ms}ms",
                        url=page.url, action="wait_for",
                    )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="wait_for"
                )

    # -- PDF --

    async def pdf_save(self, agent_id: str, path: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                await page.pdf(path=path)
                return BrowserResult(
                    success=True, data=f"PDF saved: {path}",
                    url=page.url, action="pdf_save",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="pdf_save"
                )

    # -- Verification --

    async def verify_text_visible(self, agent_id: str, text: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                locator = page.get_by_text(text)
                visible = await locator.is_visible()
                return BrowserResult(
                    success=True,
                    data=f"Text '{text}': {'visible' if visible else 'NOT visible'}",
                    url=page.url, action="verify_text_visible",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="verify_text_visible"
                )

    async def verify_element_visible(self, agent_id: str, selector: str) -> BrowserResult:
        state = await self._get_agent_state(agent_id)
        async with state.lock:
            _, page = await self._ensure_page(agent_id)
            try:
                visible = await page.is_visible(selector)
                return BrowserResult(
                    success=True,
                    data=f"Element '{selector}': {'visible' if visible else 'NOT visible'}",
                    url=page.url, action="verify_element_visible",
                )
            except Exception as exc:
                return BrowserResult(
                    success=False, error=str(exc), action="verify_element_visible"
                )


# =====================================================================
# PlaywrightMCPBackend (stub)
# =====================================================================


class PlaywrightMCPBackend:
    """Future: proxy to @playwright/mcp npm server via MCP client SDK.

    All methods raise NotImplementedError.  The protocol interface is
    preserved so this can be swapped in when implemented.
    """

    async def start(self) -> None:
        raise NotImplementedError("PlaywrightMCPBackend not yet implemented")

    async def stop(self) -> None:
        raise NotImplementedError("PlaywrightMCPBackend not yet implemented")

    async def is_available(self) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        async def _not_impl(*args: Any, **kwargs: Any) -> BrowserResult:
            return BrowserResult(
                success=False,
                error="PlaywrightMCPBackend not yet implemented. Using PlaywrightDirectBackend.",
                action=name,
            )
        return _not_impl


# =====================================================================
# Helpers
# =====================================================================


def _format_accessibility_tree(node: dict, indent: int = 0) -> str:
    """Format a Playwright accessibility snapshot into readable text."""
    lines = []
    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")

    prefix = "  " * indent
    parts = [role]
    if name:
        parts.append(f'"{name}"')
    if value:
        parts.append(f"[{value}]")
    lines.append(f"{prefix}{' '.join(parts)}")

    for child in node.get("children", []):
        lines.append(_format_accessibility_tree(child, indent + 1))

    return "\n".join(lines)


# =====================================================================
# Permission tiers
# =====================================================================

# Maps each backend method to its required permission tier
BROWSER_PERMISSION_TIERS = {
    # browse (read-only)
    "navigate": "browser_read",
    "navigate_back": "browser_read",
    "snapshot": "browser_read",
    "screenshot": "browser_read",
    "get_text": "browser_read",
    "console_messages": "browser_read",
    "network_requests": "browser_read",
    "tabs_list": "browser_read",
    "wait_for": "browser_read",
    "verify_text_visible": "browser_read",
    "verify_element_visible": "browser_read",
    "close_page": "browser_read",

    # interact
    "click": "browser_interact",
    "fill": "browser_interact",
    "type_text": "browser_interact",
    "press_key": "browser_interact",
    "select_option": "browser_interact",
    "hover": "browser_interact",
    "drag": "browser_interact",
    "file_upload": "browser_interact",
    "handle_dialog": "browser_interact",
    "mouse_click_xy": "browser_interact",
    "mouse_move_xy": "browser_interact",
    "mouse_drag_xy": "browser_interact",
    "mouse_wheel": "browser_interact",
    "resize": "browser_interact",
    "tab_new": "browser_interact",
    "tab_select": "browser_interact",
    "tab_close": "browser_interact",

    # advanced (full control)
    "evaluate": "browser_advanced",
    "cookie_list": "browser_advanced",
    "cookie_set": "browser_advanced",
    "cookie_delete": "browser_advanced",
    "cookie_clear": "browser_advanced",
    "storage_get": "browser_advanced",
    "storage_set": "browser_advanced",
    "storage_delete": "browser_advanced",
    "storage_clear": "browser_advanced",
    "storage_list": "browser_advanced",
    "route_set": "browser_advanced",
    "route_list": "browser_advanced",
    "route_remove": "browser_advanced",
    "pdf_save": "browser_advanced",
}

# Tier hierarchy: advanced includes interact includes read
TIER_HIERARCHY = {
    "browser_read": {"browser_read"},
    "browser_interact": {"browser_read", "browser_interact"},
    "browser_advanced": {"browser_read", "browser_interact", "browser_advanced"},
}


def check_browser_permission(
    action: str, agent_tier: str
) -> tuple[bool, str]:
    """Check if an agent's browser tier allows a given action.

    Returns (allowed, reason).
    """
    required = BROWSER_PERMISSION_TIERS.get(action)
    if required is None:
        return False, f"Unknown browser action: {action}"

    allowed_tiers = TIER_HIERARCHY.get(agent_tier, set())
    if required in allowed_tiers:
        return True, ""
    return False, f"Action '{action}' requires '{required}', agent has '{agent_tier}'"


# =====================================================================
# Factory
# =====================================================================

_backend_instance: PlaywrightDirectBackend | None = None


def get_browser_backend(**kwargs: Any) -> PlaywrightDirectBackend:
    """Get or create the singleton browser backend."""
    global _backend_instance
    if _backend_instance is None:
        _backend_instance = PlaywrightDirectBackend(**kwargs)
    return _backend_instance
