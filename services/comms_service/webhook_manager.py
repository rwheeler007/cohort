"""
Webhook Manager for Cohort Communications Service.

Handles notification routing to custom webhook endpoints (via HTTP POST)
and optionally to SMACK chat (via SocketIO) when enabled. All
notifications are logged to daily JSON files for auditing.

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

try:
    import socketio
    _SOCKETIO_AVAILABLE = True
except ImportError:
    _SOCKETIO_AVAILABLE = False

from models import (
    NotificationPriority,
    NotificationRequest,
    NotificationResponse,
    WebhookConfig,
)

logger = logging.getLogger("comms_service.webhook_manager")


class WebhookManager:
    """Routes notifications to custom webhooks and optionally SMACK.

    SMACK integration is disabled by default. Set the environment variable
    ``USE_SMACK=1`` to enable SocketIO-based SMACK notification routing.
    When disabled, SMACK channel targets are silently skipped (logged only).
    """

    SMACK_URL = os.getenv("SMACK_URL", "http://localhost:5000")
    MAX_QUEUE_SIZE = 200  # Cap to prevent unbounded growth
    USE_SMACK = os.getenv("USE_SMACK", "0") == "1" and _SOCKETIO_AVAILABLE

    def __init__(self, config_path: Path, log_path: Path) -> None:
        self.config_path = Path(config_path)
        self.log_path = Path(log_path)
        self._configs: Dict[str, WebhookConfig] = {}
        self._sio: Optional[socketio.Client] = None
        self._sio_connected = False

        # Ensure directories exist
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.mkdir(parents=True, exist_ok=True)

        # Persistent queue for undelivered SMACK notifications
        self._queue_path = self.log_path / "_smack_queue.json"
        self._smack_queue: List[dict] = self._load_queue()

        # Load webhook configs on init
        self._configs = self.load_config()
        logger.info("[OK] WebhookManager initialised  config=%s  log=%s  queued=%d",
                     self.config_path, self.log_path, len(self._smack_queue))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_notification(self, request: NotificationRequest) -> NotificationResponse:
        """Route a notification to every channel listed in the request.

        Channel format:
            - ``smack:<channel_name>``   -> SMACK toast via SocketIO
            - ``webhook:<config_name>``  -> HTTP POST to configured URL
        """
        channels_sent: list[str] = []
        channels_failed: list[str] = []

        for channel in request.channels:
            try:
                prefix, name = channel.split(":", 1)
            except ValueError:
                logger.warning("[!] Invalid channel format: %s (expected prefix:name)", channel)
                channels_failed.append(channel)
                continue

            success = False
            if prefix == "smack":
                success = self.send_smack(
                    channel=name,
                    title=request.title,
                    message=request.message,
                    priority=request.priority.value,
                )
            elif prefix == "webhook":
                success = self.send_webhook(
                    config_name=name,
                    title=request.title,
                    message=request.message,
                    priority=request.priority.value,
                )
            else:
                logger.warning("[!] Unknown channel prefix: %s", prefix)

            if success:
                channels_sent.append(channel)
            else:
                channels_failed.append(channel)

        # Log the full notification attempt
        self._log_notification(request, channels_sent, channels_failed)

        overall_success = len(channels_sent) > 0
        return NotificationResponse(
            success=overall_success,
            channels_sent=channels_sent,
            channels_failed=channels_failed,
        )

    # ------------------------------------------------------------------
    # SMACK integration
    # ------------------------------------------------------------------

    def send_smack(self, channel: str, title: str, message: str, priority: str) -> bool:
        """Send a persistent message to a SMACK channel via send_message event.

        Disabled by default. Set USE_SMACK=1 environment variable to enable.
        When disabled, messages are logged but not delivered.
        """
        if not self.USE_SMACK:
            logger.debug("[*] SMACK disabled -- skipping notification to #%s", channel)
            return False

        # Format as markdown message with title header
        content = f"**{title}**\n\n{message}"

        payload = {
            "channel_id": channel,
            "content": content,
            "sender": "Comms Service",
            "metadata": {"priority": priority, "source": "comms_service"}
        }

        try:
            self._ensure_smack_connection()
            if not self._sio_connected:
                self._enqueue_smack(payload)
                return False

            self._sio.emit("send_message", payload)
            logger.info("[OK] SMACK message sent to #%s", channel)
            return True

        except Exception as exc:
            logger.warning("[!] SMACK send failed for #%s: %s", channel, exc)
            self._enqueue_smack(payload)
            return False

    def _ensure_smack_connection(self) -> None:
        """Connect to the SMACK SocketIO server if not already connected."""
        if not self.USE_SMACK:
            return
        if self._sio_connected:
            return

        try:
            if self._sio is None:
                self._sio = socketio.Client(
                    reconnection=True,
                    reconnection_attempts=3,
                    reconnection_delay=1,
                    logger=False,
                    engineio_logger=False,
                )

                @self._sio.event
                def connect():
                    self._sio_connected = True
                    logger.info("[OK] Connected to SMACK server at %s", self.SMACK_URL)
                    self._flush_queue()

                @self._sio.event
                def disconnect():
                    self._sio_connected = False
                    logger.info("[*] Disconnected from SMACK server")

            self._sio.connect(self.SMACK_URL, wait_timeout=5)

        except Exception as exc:
            self._sio_connected = False
            logger.warning("[!] Could not connect to SMACK at %s: %s", self.SMACK_URL, exc)

    def disconnect_smack(self) -> None:
        """Cleanly disconnect from the SMACK server."""
        if self._sio is not None and self._sio_connected:
            try:
                self._sio.disconnect()
            except Exception:
                pass
            self._sio_connected = False
            logger.info("[OK] SMACK connection closed")

    # ------------------------------------------------------------------
    # SMACK notification queue (persist-and-replay)
    # ------------------------------------------------------------------

    def _load_queue(self) -> List[dict]:
        """Load the persistent SMACK notification queue from disk."""
        if self._queue_path.exists():
            try:
                content = self._queue_path.read_text(encoding="utf-8").strip()
                if content:
                    data = json.loads(content)
                    if isinstance(data, list):
                        return data
            except Exception as exc:
                logger.warning("[!] Failed to load SMACK queue: %s", exc)
        return []

    def _save_queue(self) -> None:
        """Persist the current queue to disk."""
        try:
            self._queue_path.write_text(
                json.dumps(self._smack_queue, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[!] Failed to save SMACK queue: %s", exc)

    def _enqueue_smack(self, payload: dict) -> None:
        """Add a failed SMACK notification to the persistent queue."""
        payload["queued_at"] = datetime.utcnow().isoformat()
        self._smack_queue.append(payload)

        # Trim oldest if over cap
        if len(self._smack_queue) > self.MAX_QUEUE_SIZE:
            dropped = len(self._smack_queue) - self.MAX_QUEUE_SIZE
            self._smack_queue = self._smack_queue[dropped:]
            logger.warning("[!] SMACK queue overflow - dropped %d oldest messages", dropped)

        self._save_queue()
        logger.info("[*] Queued SMACK notification for #%s (%d in queue)",
                     payload.get("channel", "?"), len(self._smack_queue))

    def _flush_queue(self) -> None:
        """Replay all queued SMACK notifications.  Called on connect."""
        if not self._smack_queue:
            return

        count = len(self._smack_queue)
        logger.info("[>>] Flushing %d queued SMACK notification(s)...", count)

        sent = 0
        failed = []
        for payload in self._smack_queue:
            try:
                # Remove queue metadata before sending
                send_payload = {k: v for k, v in payload.items() if k != "queued_at"}
                self._sio.emit("send_message", send_payload)
                sent += 1
            except Exception as exc:
                logger.warning("[!] Failed to replay queued message: %s", exc)
                failed.append(payload)

        # Keep only messages that failed to replay
        self._smack_queue = failed
        self._save_queue()
        logger.info("[OK] Flushed %d/%d queued SMACK notifications", sent, count)

    def get_queue_status(self) -> dict:
        """Return current queue stats (for the /health or status endpoint)."""
        return {
            "queued_count": len(self._smack_queue),
            "max_queue_size": self.MAX_QUEUE_SIZE,
            "oldest": self._smack_queue[0].get("queued_at") if self._smack_queue else None,
        }

    # ------------------------------------------------------------------
    # Custom webhooks
    # ------------------------------------------------------------------

    def send_webhook(self, config_name: str, title: str, message: str, priority: str) -> bool:
        """HTTP POST a notification payload to a configured webhook URL."""
        config = self._configs.get(config_name)
        if config is None:
            logger.warning("[!] Webhook config not found: %s", config_name)
            return False

        if not config.enabled:
            logger.info("[*] Webhook '%s' is disabled - skipping", config_name)
            return False

        payload = {
            "title": title,
            "message": message,
            "priority": priority,
            "source": "comms_service",
            "timestamp": datetime.utcnow().isoformat(),
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    config.url,
                    json=payload,
                    headers=config.headers,
                )
            if resp.is_success:
                logger.info("[OK] Webhook '%s' responded %d", config_name, resp.status_code)
                return True
            else:
                logger.warning("[!] Webhook '%s' responded %d: %s",
                               config_name, resp.status_code, resp.text[:200])
                return False

        except httpx.TimeoutException:
            logger.warning("[!] Webhook '%s' timed out", config_name)
            return False
        except Exception as exc:
            logger.warning("[!] Webhook '%s' failed: %s", config_name, exc)
            return False

    def test_webhook(self, config_name: str) -> bool:
        """Send a test notification through a named webhook.

        Returns True if the webhook responds with a success status code.
        """
        logger.info("[>>] Testing webhook '%s'...", config_name)
        return self.send_webhook(
            config_name=config_name,
            title="BOSS Comms - Webhook Test",
            message="This is a test notification from the BOSS Communications Service.",
            priority=NotificationPriority.INFO.value,
        )

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def load_config(self) -> Dict[str, WebhookConfig]:
        """Load webhook configurations from the JSON config file.

        Returns an empty dict if the file does not exist or is invalid.
        """
        if not self.config_path.exists():
            logger.info("[*] No webhook config file found at %s - starting empty", self.config_path)
            return {}

        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            configs: Dict[str, WebhookConfig] = {}
            for name, entry in raw.items():
                configs[name] = WebhookConfig(**entry)
            logger.info("[OK] Loaded %d webhook config(s) from %s", len(configs), self.config_path)
            return configs
        except Exception as exc:
            logger.warning("[!] Failed to load webhook config: %s", exc)
            return {}

    def save_config(self, configs: Dict[str, WebhookConfig]) -> None:
        """Persist webhook configurations to the JSON config file."""
        self._configs = configs
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        serialised = {
            name: cfg.model_dump() for name, cfg in configs.items()
        }

        self.config_path.write_text(
            json.dumps(serialised, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("[OK] Saved %d webhook config(s) to %s", len(configs), self.config_path)

    def reload_config(self) -> None:
        """Re-read configs from disk (useful after external edits)."""
        self._configs = self.load_config()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_notification(
        self,
        request: NotificationRequest,
        channels_sent: list[str],
        channels_failed: list[str],
    ) -> None:
        """Append a notification record to today's daily log file."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        log_file = self.log_path / f"{today}.json"

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent_id": request.agent_id,
            "title": request.title,
            "message": request.message,
            "priority": request.priority.value,
            "channels_requested": request.channels,
            "channels_sent": channels_sent,
            "channels_failed": channels_failed,
        }

        try:
            existing: list = []
            if log_file.exists():
                content = log_file.read_text(encoding="utf-8").strip()
                if content:
                    existing = json.loads(content)
                    if not isinstance(existing, list):
                        existing = [existing]

            existing.append(entry)
            log_file.write_text(
                json.dumps(existing, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[!] Failed to write notification log: %s", exc)
