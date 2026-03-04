"""Task execution layer for Cohort.

Manages the full task lifecycle:
    briefing (conversational intake) -> execution -> completion

Execution backends are configurable via settings:
    - ``cli``  : Claude CLI subprocess (default, proven pattern from agent_router)
    - ``api``  : Anthropic API direct call
    - ``chat`` : Post @mention to chat, let agent_router handle invocation
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from cohort.briefing import (
    build_briefing_prompt,
    build_execution_prompt,
)

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Manages the full task lifecycle: briefing -> execution -> completion."""

    def __init__(
        self,
        data_layer: Any,
        chat: Any,
        settings: dict[str, Any],
    ) -> None:
        self.data_layer = data_layer
        self.chat = chat
        self._sio: Any = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

        # Settings (hot-reloadable)
        self.execution_backend: str = settings.get("execution_backend", "cli")
        self.claude_cmd: str = settings.get(
            "claude_cmd",
            os.environ.get("COHORT_CLAUDE_CMD", "claude"),
        )
        self.api_key: str = settings.get("api_key", "")
        self.agents_root: Path | None = (
            Path(settings["agents_root"]) if settings.get("agents_root") else None
        )
        self.response_timeout: int = int(settings.get("response_timeout", 300))

    def set_sio(self, sio: Any) -> None:
        """Wire the Socket.IO server for broadcasting events."""
        self._sio = sio

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the ASGI event loop for sync->async bridging."""
        self._event_loop = loop

    def apply_settings(self, settings: dict[str, Any]) -> None:
        """Hot-reload settings."""
        if "execution_backend" in settings:
            self.execution_backend = settings["execution_backend"]
        if settings.get("claude_cmd"):
            self.claude_cmd = settings["claude_cmd"]
        if settings.get("api_key"):
            self.api_key = settings["api_key"]
        if settings.get("agents_root"):
            p = Path(settings["agents_root"])
            if p.exists():
                self.agents_root = p
        if settings.get("response_timeout"):
            try:
                self.response_timeout = int(settings["response_timeout"])
            except (TypeError, ValueError):
                pass

    # =================================================================
    # Briefing phase
    # =================================================================

    async def start_briefing(self, task: dict[str, Any]) -> None:
        """Begin the conversational task intake.

        1. Create a task channel
        2. Post the task brief as a system message
        3. Invoke the agent with a briefing-mode prompt
        4. Agent's response goes to chat (asks clarifying questions)
        """
        agent_id = task["agent_id"]
        task_id = task["task_id"]
        channel_id = f"task-{task_id}"

        # Create task channel
        self.chat.create_channel(
            name=channel_id,
            description=f"Task briefing: {task['description'][:80]}",
        )

        # Post opening brief
        brief_msg = self.chat.post_message(
            channel_id=channel_id,
            sender="system",
            content=(
                f"**Task Brief**\n"
                f"Agent: @{agent_id}\n"
                f"Priority: {task['priority']}\n\n"
                f"{task['description']}"
            ),
            message_type="task",
        )

        # Broadcast channel creation and the brief message
        await self._emit("channels_list", {
            "channels": [
                ch.to_dict() for ch in self.chat.list_channels(include_archived=False)
            ],
        })
        await self._emit("new_message", brief_msg.to_dict())

        # Store channel_id on the task for later reference
        task["channel_id"] = channel_id
        task["updated_at"] = datetime.now().isoformat()

        # Invoke the agent in briefing mode (in background thread)
        thread = threading.Thread(
            target=self._invoke_briefing_sync,
            args=(task, channel_id),
            daemon=True,
        )
        thread.start()

    def _invoke_briefing_sync(
        self,
        task: dict[str, Any],
        channel_id: str,
    ) -> None:
        """Load agent prompt, invoke CLI for briefing, post response to chat."""
        agent_id = task["agent_id"]

        # Load agent prompt
        agent_prompt = self._load_agent_prompt(agent_id)
        if not agent_prompt:
            self._post_and_broadcast(
                channel_id, agent_id,
                f"[!] Could not load prompt for {agent_id}. "
                f"Check that agents/{agent_id}/agent_prompt.md exists.",
            )
            return

        # Build the briefing prompt
        from cohort.agent_router import build_channel_context
        context = build_channel_context(channel_id)
        full_prompt = build_briefing_prompt(agent_prompt, task, context)

        # Typing indicator
        self._emit_sync("user_typing", {
            "sender": agent_id,
            "typing": True,
            "channel_id": channel_id,
        })

        logger.info("[>>] Briefing invocation for %s (task %s)", agent_id, task["task_id"])

        response_content = self._call_cli(full_prompt)

        # Stop typing
        self._emit_sync("user_typing", {
            "sender": agent_id,
            "typing": False,
            "channel_id": channel_id,
        })

        if not response_content:
            response_content = (
                f"I'm ready to help with this task.  "
                f"Could you provide a bit more detail about what you need?"
            )

        self._post_and_broadcast(channel_id, agent_id, response_content)

    # =================================================================
    # Execution phase
    # =================================================================

    async def execute_task(
        self,
        task: dict[str, Any],
        confirmed_brief: dict[str, Any],
    ) -> None:
        """Run the task after the user confirms the briefing.

        1. Check acceptance criteria (if agent_store is available)
        2. Update status to in_progress
        3. Invoke agent via configured backend
        4. Post result to task channel
        5. Mark complete with output
        """
        task_id = task["task_id"]
        agent_id = task["agent_id"]
        channel_id = task.get("channel_id", f"task-{task_id}")

        # Acceptance criteria gate: check if partnerships require consultation
        consultations = self._check_consultations(task)
        if consultations:
            partners_list = ", ".join(f"@{c['partner_id']}" for c in consultations)
            reasons = "; ".join(c["reason"] for c in consultations)
            self._post_and_broadcast(
                channel_id, "system",
                f"**[Gate]** Before execution, consultation recommended:\n"
                f"Partners: {partners_list}\n"
                f"Reason: {reasons}\n\n"
                f"Proceeding -- partners can review in this channel.",
            )
            # Attach consultation info to task for audit trail
            task["consultations"] = consultations

        # Transition to in_progress
        updated = self.data_layer.update_task_progress(task_id, "in_progress")
        if updated:
            await self._emit("cohort:task_progress", updated)
            await self._emit("cohort:team_update", self.data_layer.get_team_snapshot())

        # Run execution in a background thread to avoid blocking
        thread = threading.Thread(
            target=self._execute_sync,
            args=(task, confirmed_brief, channel_id),
            daemon=True,
        )
        thread.start()

    def _execute_sync(
        self,
        task: dict[str, Any],
        confirmed_brief: dict[str, Any],
        channel_id: str,
    ) -> None:
        """Background thread: run the actual execution."""
        agent_id = task["agent_id"]
        task_id = task["task_id"]
        backend = self.execution_backend

        logger.info(
            "[>>] Executing task %s for %s via %s backend",
            task_id, agent_id, backend,
        )

        # Typing indicator
        self._emit_sync("user_typing", {
            "sender": agent_id,
            "typing": True,
            "channel_id": channel_id,
        })

        try:
            if backend == "api":
                result = self._execute_api(task, confirmed_brief)
            elif backend == "chat":
                self._execute_chat(task, confirmed_brief, channel_id)
                return  # chat backend handles its own completion
            else:
                result = self._execute_cli_task(task, confirmed_brief)
        except Exception as exc:
            logger.exception("[X] Execution failed for task %s", task_id)
            result = f"[Error] Execution failed: {exc}"

        # Stop typing
        self._emit_sync("user_typing", {
            "sender": agent_id,
            "typing": False,
            "channel_id": channel_id,
        })

        # Post result to chat
        self._post_and_broadcast(channel_id, agent_id, result)

        # Mark task complete
        output = {
            "content": result,
            "backend": backend,
            "completed_at": datetime.now().isoformat(),
        }
        completed = self.data_layer.complete_task(task_id, output)
        if completed:
            self._emit_sync("cohort:task_complete", completed)
            self._emit_sync("cohort:output_ready", completed)
            self._emit_sync(
                "cohort:team_update",
                self.data_layer.get_team_snapshot(),
            )

    def _execute_cli_task(
        self,
        task: dict[str, Any],
        confirmed_brief: dict[str, Any],
    ) -> str:
        """Execute via Claude CLI subprocess."""
        agent_id = task["agent_id"]

        agent_prompt = self._load_agent_prompt(agent_id)
        if not agent_prompt:
            return f"[Error] No prompt found for agent {agent_id}"

        from cohort.agent_router import build_channel_context
        channel_id = task.get("channel_id", f"task-{task['task_id']}")
        context = build_channel_context(channel_id)

        full_prompt = build_execution_prompt(
            agent_prompt, task, confirmed_brief, context,
        )

        return self._call_cli(full_prompt)

    def _execute_api(
        self,
        task: dict[str, Any],
        confirmed_brief: dict[str, Any],
    ) -> str:
        """Execute via Anthropic API direct call."""
        if not self.api_key:
            logger.warning("[!] No API key set, falling back to CLI")
            return self._execute_cli_task(task, confirmed_brief)

        agent_id = task["agent_id"]
        agent_prompt = self._load_agent_prompt(agent_id)
        if not agent_prompt:
            return f"[Error] No prompt found for agent {agent_id}"

        brief_lines = "\n".join(
            f"- {k}: {v}" for k, v in confirmed_brief.items() if v
        )
        user_message = (
            f"Execute this approved task:\n\n"
            f"Original request: {task.get('description', '')}\n\n"
            f"Agreed plan:\n{brief_lines}\n\n"
            f"Produce the deliverable."
        )

        try:
            import anthropic
        except ImportError:
            logger.warning("[!] anthropic package not installed, falling back to CLI")
            return self._execute_cli_task(task, confirmed_brief)

        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=os.environ.get("COHORT_MODEL", "claude-sonnet-4-20250514"),
                max_tokens=4096,
                system=agent_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        except anthropic.APIStatusError as exc:
            logger.exception("[X] Anthropic API error for %s: %s", agent_id, exc)
            # Sanitized -- never leak headers, keys, or request details
            return f"[Error] API call failed: {exc.status_code} {type(exc).__name__}"
        except anthropic.APIConnectionError:
            logger.exception("[X] Anthropic connection error for %s", agent_id)
            return "[Error] API call failed: could not connect to Anthropic"
        except Exception:
            logger.exception("[X] API call failed for %s", agent_id)
            return "[Error] API call failed: unexpected error (see server logs)"

    def _execute_chat(
        self,
        task: dict[str, Any],
        confirmed_brief: dict[str, Any],
        channel_id: str,
    ) -> None:
        """Execute by posting an @mention and letting agent_router handle it."""
        agent_id = task["agent_id"]
        brief_lines = "\n".join(
            f"- {k}: {v}" for k, v in confirmed_brief.items() if v
        )
        content = (
            f"@{agent_id} Please execute this confirmed task:\n\n"
            f"{brief_lines}\n\n"
            f"Original request: {task.get('description', '')}"
        )
        msg = self.chat.post_message(
            channel_id=channel_id,
            sender="system",
            content=content,
            message_type="task",
        )
        self._emit_sync("new_message", msg.to_dict())

        # Route the @mention through agent_router
        mentions = msg.metadata.get("mentions", [])
        if mentions:
            from cohort.agent_router import route_mentions
            route_mentions(msg, mentions)

    # =================================================================
    # Helpers
    # =================================================================

    def _check_consultations(
        self, task: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Check if the assigned agent's partnerships require pre-execution consultation.

        Returns a list of required consultations, or empty list if none needed.
        Only returns consultations for partners that exist in the current deployment.
        """
        try:
            from cohort.agent_store import get_store
            store = get_store()
            if store is None:
                return []

            agent = store.get(task["agent_id"])
            if agent is None:
                return []

            available = {a.agent_id: a for a in store.list_agents()}

            from cohort.capability_router import find_required_consultations, _extract_keywords
            task_kw = _extract_keywords(task.get("description", ""))
            return find_required_consultations(agent, task_kw, available)
        except Exception as exc:
            logger.debug("Consultation check skipped: %s", exc)
            return []

    def _load_agent_prompt(self, agent_id: str) -> str | None:
        """Load an agent's prompt file, return None if missing."""
        from cohort.agent_router import get_agent_prompt_path
        prompt_path = get_agent_prompt_path(agent_id)
        if not prompt_path:
            logger.warning("[!] No prompt file for %s", agent_id)
            return None
        try:
            return prompt_path.read_text(encoding="utf-8")
        except Exception:
            logger.exception("[X] Failed to read prompt for %s", agent_id)
            return None

    def _call_cli(self, full_prompt: str) -> str:
        """Invoke Claude CLI with the given prompt and return response text."""
        try:
            result = subprocess.run(
                [self.claude_cmd, "-p", "-"],
                input=full_prompt,
                capture_output=True,
                text=True,
                cwd=str(self.agents_root) if self.agents_root else None,
                timeout=self.response_timeout,
                shell=True,
                encoding="utf-8",
                errors="replace",
            )

            content = result.stdout.strip()
            if result.returncode != 0 and not content:
                content = f"[Error] CLI failed: {result.stderr.strip()[:200]}"
                logger.error("[X] CLI error: %s", result.stderr[:200])
            return content

        except subprocess.TimeoutExpired:
            logger.error("[X] CLI timeout after %ds", self.response_timeout)
            return f"[Timeout] Response timed out after {self.response_timeout}s"
        except Exception as exc:
            logger.exception("[X] CLI exception")
            return f"[Error] CLI invocation failed: {exc}"

    def _post_and_broadcast(
        self,
        channel_id: str,
        sender: str,
        content: str,
    ) -> None:
        """Post a message to chat and broadcast via Socket.IO."""
        msg = self.chat.post_message(
            channel_id=channel_id,
            sender=sender,
            content=content,
        )
        self._emit_sync("new_message", msg.to_dict())

    def _emit_sync(self, event: str, data: dict) -> None:
        """Schedule a Socket.IO emit from a sync (background thread) context."""
        if self._sio is None:
            return
        try:
            loop = self._event_loop or asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._sio.emit(event, data), loop,
                )
            else:
                loop.run_until_complete(self._sio.emit(event, data))
        except RuntimeError:
            logger.debug("No event loop for emit_sync: %s", event)

    async def _emit(self, event: str, data: dict) -> None:
        """Emit a Socket.IO event from an async context."""
        if self._sio is None:
            return
        await self._sio.emit(event, data)
