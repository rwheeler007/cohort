"""System tray application for Cohort.

Provides a Windows system tray icon with:
- Status indicator (starting/running/error)
- Open in Browser action
- Server port display
- Quit action that cleanly shuts down the server

Zero external dependencies beyond pystray + Pillow (bundled with installer).
Falls back gracefully if pystray is not installed (server-only mode).

Usage::

    from cohort.tray import run_tray
    run_tray(port=5100)  # blocking, manages server lifecycle
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import webbrowser
from typing import Any

logger = logging.getLogger(__name__)

# =====================================================================
# Icon generation (no external image files needed)
# =====================================================================

def _create_icon_image(color: str = "#D97757") -> Any:
    """Create a simple Cohort tray icon programmatically.

    Draws a filled circle with a 'C' letter on a 64x64 canvas.
    Uses Pillow (PIL) which is bundled with the installer.

    Args:
        color: Hex color for the icon background.

    Returns:
        PIL.Image.Image instance.
    """
    from PIL import Image, ImageDraw, ImageFont

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Parse hex color
    c = color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    # Draw filled circle
    draw.ellipse([2, 2, size - 3, size - 3], fill=(r, g, b, 255))

    # Draw 'C' letter in white
    try:
        font = ImageFont.truetype("segoeui.ttf", 36)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("arial.ttf", 36)
        except (OSError, IOError):
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), "C", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2
    ty = (size - th) // 2 - 2
    draw.text((tx, ty), "C", fill=(255, 255, 255, 255), font=font)

    return img


# =====================================================================
# Server management thread
# =====================================================================

class _ServerThread(threading.Thread):
    """Runs the Cohort uvicorn server in a background thread."""

    def __init__(self, host: str, port: int, data_dir: str):
        super().__init__(daemon=True, name="cohort-server")
        self.host = host
        self.port = port
        self.data_dir = data_dir
        self.server: Any = None
        self.ready = threading.Event()
        self.error: str | None = None

    def run(self) -> None:
        try:
            import uvicorn

            from cohort.server import create_app

            app = create_app(data_dir=self.data_dir)

            config = uvicorn.Config(
                app,
                host=self.host,
                port=self.port,
                log_level="info",
            )
            self.server = uvicorn.Server(config)

            # Signal readiness once the server starts accepting
            original_startup = self.server.startup

            async def _startup_with_signal(*args: Any, **kwargs: Any) -> Any:
                result = await original_startup(*args, **kwargs)
                self.ready.set()
                return result

            self.server.startup = _startup_with_signal  # type: ignore[assignment]

            self.server.run()
        except Exception as exc:
            self.error = str(exc)
            self.ready.set()  # Unblock waiters even on failure
            logger.error("[X] Server failed to start: %s", exc)

    def shutdown(self) -> None:
        """Signal the uvicorn server to shut down."""
        if self.server is not None:
            self.server.should_exit = True


# =====================================================================
# Tray application
# =====================================================================

def run_tray(
    host: str = "127.0.0.1",
    port: int = 5100,
    data_dir: str = "data",
    open_browser: bool = True,
) -> int:
    """Start Cohort server + system tray icon.

    This is the main entry point for the Windows installer's "launch"
    action. It starts the server in a background thread and shows a
    system tray icon with status and controls.

    Args:
        host: Server bind address.
        port: Server port.
        data_dir: Cohort data directory.
        open_browser: Whether to open browser on startup.

    Returns:
        Exit code (0 = clean shutdown, 1 = error).
    """
    try:
        import pystray
    except ImportError:
        logger.warning(
            "[!] pystray not installed -- running server without tray icon. "
            "Install with: pip install pystray Pillow"
        )
        return _run_server_only(host, port, data_dir, open_browser)

    # Start server thread
    server = _ServerThread(host, port, data_dir)
    server.start()

    # Wait for server to be ready (max 30s)
    server.ready.wait(timeout=30)

    if server.error:
        print(f"[X] Server failed to start: {server.error}", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{port}"
    logger.info("[OK] Cohort server running at %s", url)

    if open_browser:
        # Small delay to let the server fully bind
        time.sleep(0.5)
        webbrowser.open(url)

    # Build tray menu
    def on_open(icon: Any, item: Any) -> None:
        webbrowser.open(url)

    def on_quit(icon: Any, item: Any) -> None:
        server.shutdown()
        icon.stop()

    icon_image = _create_icon_image()

    menu = pystray.Menu(
        pystray.MenuItem(f"Cohort - localhost:{port}", on_open, default=True),
        pystray.MenuItem("Open in Browser", on_open),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("cohort", icon_image, "Cohort", menu)

    # Run tray icon (blocking -- returns when user clicks Quit)
    icon.run()

    # Clean shutdown
    server.shutdown()
    server.join(timeout=5)

    logger.info("[OK] Cohort shut down cleanly")
    return 0


def _run_server_only(
    host: str, port: int, data_dir: str, open_browser: bool,
) -> int:
    """Fallback: run server without tray icon."""
    if open_browser:
        # Open browser after a short delay
        def _open() -> None:
            time.sleep(2)
            webbrowser.open(f"http://127.0.0.1:{port}")

        t = threading.Thread(target=_open, daemon=True)
        t.start()

    from cohort.server import serve
    serve(host=host, port=port, data_dir=data_dir)
    return 0
