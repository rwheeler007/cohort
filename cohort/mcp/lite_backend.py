"""Lite MCP backend -- file-backed, no server required.

Provides the same interface as CohortClient but operates directly
on local storage.  Used when no Cohort server is running, giving
open source users working MCP tools without the web app.

All methods are async for interface compatibility with CohortClient
even though the underlying operations are synchronous.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cohort.agent import AgentConfig, AgentMemory
from cohort.agent_store import AgentStore
from cohort.chat import ChatManager
from cohort.personas import load_persona
from cohort.registry import JsonFileStorage, create_storage

logger = logging.getLogger(__name__)


class LiteBackend:
    """In-process backend using file storage.

    Drop-in replacement for :class:`~cohort.mcp.client.CohortClient`.
    All methods match the CohortClient signatures so the MCP server can
    use either backend transparently.

    Parameters
    ----------
    data_dir:
        Root data directory for channels, messages, agents, etc.
    agents_dir:
        Path to the agents directory.  If *None*, tries
        ``COHORT_AGENTS_DIR`` env var, then ``{data_dir}/agents``.
    checklist_path:
        Optional path to the JSON checklist file.
    """

    def __init__(
        self,
        data_dir: str | Path = "data",
        agents_dir: str | Path | None = None,
        checklist_path: Path | str | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._storage = create_storage(self._data_dir)
        self._chat = ChatManager(self._storage)

        # Local task store (file-backed)
        from cohort.task_store import TaskStore
        self._task_store = TaskStore(self._data_dir)

        # Resolve agents directory
        if agents_dir:
            self._agents_dir = Path(agents_dir)
        else:
            env = os.environ.get("COHORT_AGENTS_DIR")
            if env:
                self._agents_dir = Path(env)
            else:
                self._agents_dir = self._data_dir / "agents"

        self._agent_store = AgentStore(
            agents_dir=self._agents_dir if self._agents_dir.is_dir() else None,
        )

        self._checklist_path = Path(checklist_path) if checklist_path else None
        self._router_ready = False

    def _ensure_router(self) -> bool:
        """Lazily initialise the agent router (once)."""
        if self._router_ready:
            return True
        try:
            from cohort.agent_router import setup_agent_router
            setup_agent_router(
                chat=self._chat,
                sio=None,  # no Socket.IO in lite mode; file watcher picks up changes
                agents_root=self._agents_dir,
                store=self._agent_store,
            )
            self._router_ready = True
            return True
        except Exception as exc:
            logger.debug("[!] lite backend: agent router init failed - %s", exc)
            return False

    # -- channels -----------------------------------------------------------

    async def get_channels(self) -> list[dict[str, Any]] | None:
        try:
            channels = self._chat.list_channels(include_archived=True)
            return [ch.to_dict() for ch in channels]
        except Exception as exc:
            logger.debug("[!] lite backend: get_channels error - %s", exc)
            return None

    async def create_channel(
        self,
        name: str,
        description: str = "",
        members: list[str] | None = None,
        is_private: bool = False,
        topic: str = "",
    ) -> dict[str, Any] | None:
        try:
            ch = self._chat.create_channel(
                name=name,
                description=description,
                members=members,
                is_private=is_private,
                topic=topic,
            )
            return {"success": True, "channel": ch.to_dict()}
        except Exception as exc:
            logger.debug("[!] lite backend: create_channel error - %s", exc)
            return {"success": False, "error": str(exc)}

    async def get_messages(
        self, channel: str, limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        try:
            messages = self._chat.get_channel_messages(channel, limit=limit)
            return [m.to_dict() for m in messages]
        except Exception as exc:
            logger.debug("[!] lite backend: get_messages error - %s", exc)
            return None

    async def post_message(
        self, channel: str, sender: str, message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            # Auto-create channel if it doesn't exist
            if self._chat.get_channel(channel) is None:
                self._chat.create_channel(name=channel, description=channel)
            msg = self._chat.post_message(
                channel_id=channel, sender=sender, content=message,
                metadata=metadata,
            )

            # Route @mentions to agent response pipeline
            mentions = getattr(msg, "metadata", {}).get("mentions", [])
            if mentions and self._ensure_router():
                response_mode = (metadata or {}).get("response_mode", "smarter")
                if response_mode not in ("smart", "smarter", "smartest"):
                    response_mode = "smarter"
                from cohort.agent_router import route_mentions
                route_mentions(msg, mentions, response_mode=response_mode)

            return {"success": True, "message_id": msg.id}
        except Exception as exc:
            logger.debug("[!] lite backend: post_message error - %s", exc)
            return {"success": False, "error": str(exc)}

    async def condense_channel(
        self, channel: str, keep_last: int = 5,
    ) -> dict[str, Any] | None:
        try:
            all_messages = self._chat.get_channel_messages(channel, limit=10000)
            total = len(all_messages)
            if total <= keep_last:
                return {"success": True, "archived_count": 0, "message": "Nothing to condense"}

            keep_ids = {m.id for m in all_messages[-keep_last:]}
            archived_count = total - keep_last

            # Remove old messages via storage
            if hasattr(self._storage, '_read_json'):
                # JsonFileStorage
                raw = self._storage._read_json(self._storage._messages_path, [])
                new = [m for m in raw if m.get("channel_id") != channel or m.get("id") in keep_ids]
                self._storage._write_json(self._storage._messages_path, new)
            elif hasattr(self._storage, '_conn'):
                # SqliteStorage
                placeholders = ",".join("?" for _ in keep_ids)
                self._storage._conn.execute(
                    f"DELETE FROM messages WHERE channel_id = ? AND id NOT IN ({placeholders})",
                    [channel, *keep_ids],
                )
                self._storage._conn.commit()

            return {"success": True, "archived_count": archived_count, "kept": keep_last}
        except Exception as exc:
            logger.debug("[!] lite backend: condense_channel error - %s", exc)
            return {"success": False, "error": str(exc)}

    # -- agents -------------------------------------------------------------

    async def list_agents(self) -> list[dict[str, Any]] | None:
        try:
            agents = self._agent_store.list_agents(include_hidden=True)
            return [a.to_dict() for a in agents]
        except Exception as exc:
            logger.debug("[!] lite backend: list_agents error - %s", exc)
            return None

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        try:
            config = self._agent_store.load_agent(agent_id)
            if config is None:
                return {"error": f"Agent '{agent_id}' not found"}
            return config.to_dict()
        except Exception as exc:
            logger.debug("[!] lite backend: get_agent error - %s", exc)
            return None

    async def get_agent_memory(self, agent_id: str) -> dict[str, Any] | None:
        try:
            memory = self._agent_store.load_memory(agent_id)
            if memory is None:
                return {"working_memory": [], "learned_facts": []}
            return memory.to_dict()
        except Exception as exc:
            logger.debug("[!] lite backend: get_agent_memory error - %s", exc)
            return None

    async def create_agent(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        # Agent creation with full directory scaffolding requires the server.
        # In lite mode, we do a basic version: write config to disk.
        try:
            if not self._agents_dir.is_dir():
                return {"success": False, "error": "No agents directory configured"}

            # Derive agent_id from name
            name = spec.get("name", "")
            agent_id = name.lower().replace(" ", "_").replace("-", "_")
            if not agent_id:
                return {"success": False, "error": "Agent name is required"}

            agent_dir = self._agents_dir / agent_id
            if agent_dir.exists():
                return {"success": False, "error": f"Agent '{agent_id}' already exists"}

            agent_dir.mkdir(parents=True)

            config = {
                "agent_id": agent_id,
                "name": name,
                "role": spec.get("role", ""),
                "status": "active",
                "personality": spec.get("personality", ""),
                "primary_task": spec.get("primary_task", ""),
                "capabilities": spec.get("capabilities", []),
                "domain_expertise": spec.get("domain_expertise", []),
                "triggers": spec.get("triggers", []),
                "avatar": spec.get("avatar", ""),
                "color": spec.get("color", "#95A5A6"),
                "group": spec.get("group", "Agents"),
                "agent_type": spec.get("agent_type", "specialist"),
            }
            (agent_dir / "agent_config.json").write_text(
                json.dumps(config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (agent_dir / "memory.json").write_text(
                json.dumps({"working_memory": [], "learned_facts": [], "collaborators": {}}, indent=2),
                encoding="utf-8",
            )

            # Reload store cache
            self._agent_store._loaded_all = False

            return {"success": True, "agent_id": agent_id}
        except Exception as exc:
            logger.debug("[!] lite backend: create_agent error - %s", exc)
            return {"success": False, "error": str(exc)}

    async def clean_agent_memory(
        self, agent_id: str, keep_last: int = 10, dry_run: bool = False,
    ) -> dict[str, Any] | None:
        try:
            memory = self._agent_store.load_memory(agent_id)
            if memory is None:
                return {"success": False, "error": f"No memory for '{agent_id}'"}

            working = memory.working_memory or []
            total = len(working)
            to_remove = max(0, total - keep_last)

            if dry_run or to_remove == 0:
                return {
                    "success": True,
                    "working_memory_removed": to_remove,
                    "working_memory_kept": min(total, keep_last),
                }

            memory.working_memory = working[-keep_last:]
            self._agent_store.save_memory(agent_id, memory)
            return {
                "success": True,
                "working_memory_removed": to_remove,
                "working_memory_kept": len(memory.working_memory),
            }
        except Exception as exc:
            logger.debug("[!] lite backend: clean_agent_memory error - %s", exc)
            return {"success": False, "error": str(exc)}

    async def add_agent_fact(
        self, agent_id: str, fact: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            memory = self._agent_store.load_memory(agent_id)
            if memory is None:
                memory = AgentMemory()

            from cohort.agent import LearnedFact
            new_fact = LearnedFact(
                fact=fact.get("fact", ""),
                learned_from=fact.get("learned_from", "mcp"),
                confidence=fact.get("confidence", "medium"),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            if memory.learned_facts is None:
                memory.learned_facts = []
            memory.learned_facts.append(new_fact)
            self._agent_store.save_memory(agent_id, memory)
            return {"success": True}
        except Exception as exc:
            logger.debug("[!] lite backend: add_agent_fact error - %s", exc)
            return {"success": False, "error": str(exc)}

    # -- search & mentions --------------------------------------------------

    async def search_messages(
        self, query: str, channel: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        try:
            results = self._chat.search_messages(query, channel_id=channel)
            return [m.to_dict() for m in results[-limit:]]
        except Exception as exc:
            logger.debug("[!] lite backend: search_messages error - %s", exc)
            return None

    async def get_mentions(
        self, agent_id: str, limit: int = 50,
    ) -> list[dict[str, Any]] | None:
        try:
            mention_pattern = f"@{agent_id}"
            matches: list[dict[str, Any]] = []
            for ch in self._chat.list_channels():
                for msg in self._chat.get_channel_messages(ch.id, limit=200):
                    if mention_pattern in msg.content:
                        d = msg.to_dict()
                        d["_channel"] = ch.id
                        matches.append(d)
            return matches[-limit:]
        except Exception as exc:
            logger.debug("[!] lite backend: get_mentions error - %s", exc)
            return None

    # -- sessions -----------------------------------------------------------

    async def start_session(
        self,
        channel: str,
        agents: list[str],
        prompt: str,
        sender: str = "claude_code",
    ) -> dict[str, Any] | None:
        try:
            session_id = f"s_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

            # Auto-create channel if needed
            if self._chat.get_channel(channel) is None:
                self._chat.create_channel(name=channel, description=prompt[:100])

            # Post system kickoff message
            participants_list = ", ".join(f"@{a}" for a in agents)
            self._chat.post_message(
                channel_id=channel,
                sender="system",
                content=(
                    f"[SESSION] Discussion started: **{prompt[:200]}**\n\n"
                    f"**Participants**: {participants_list}\n"
                    f"**Session ID**: `{session_id}`"
                ),
                message_type="system",
            )

            # Post the user's prompt
            msg = self._chat.post_message(
                channel_id=channel,
                sender=sender,
                content=prompt,
                metadata={"mentions": agents, "session_id": session_id},
            )

            # Track session locally
            if not hasattr(self, '_sessions'):
                self._sessions: dict[str, dict[str, Any]] = {}
            self._sessions[session_id] = {
                "session_id": session_id,
                "channel_id": channel,
                "agents": agents,
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            # Route mentions to agents for responses
            if agents and self._ensure_router():
                from cohort.agent_router import route_mentions
                route_mentions(msg, agents, response_mode="smarter")

            return {
                "success": True,
                "session_id": session_id,
                "channel_id": channel,
                "status": "active",
                "participants": agents,
            }
        except Exception as exc:
            logger.debug("[!] lite backend: start_session error - %s", exc)
            return {"success": False, "error": str(exc)}

    # Deprecated alias
    start_roundtable = start_session

    async def get_session_status(
        self, session_id: str,
    ) -> dict[str, Any] | None:
        if hasattr(self, '_sessions') and session_id in self._sessions:
            return self._sessions[session_id]
        return {
            "session_id": session_id,
            "status": "unknown",
            "message": "Session not found in local store.",
        }

    # Deprecated alias
    get_roundtable_status = get_session_status

    # -- agent persona ------------------------------------------------------

    async def get_agent_persona(self, agent_id: str) -> str | None:
        try:
            # Try agent_prompt.md first
            prompt = self._agent_store.get_prompt(agent_id)
            if prompt:
                return prompt
            # Fall back to persona
            persona = load_persona(agent_id)
            if persona:
                return persona
            # Fall back to config name/role
            config = self._agent_store.load_agent(agent_id)
            if config:
                return f"You are {config.name}. {config.role}."
            return None
        except Exception as exc:
            logger.debug("[!] lite backend: get_agent_persona error - %s", exc)
            return None

    # -- task queue ---------------------------------------------------------

    async def get_task_queue(
        self, status: str | None = None,
    ) -> list[dict[str, Any]] | None:
        try:
            return self._task_store.list_tasks(status_filter=status)
        except Exception as exc:
            logger.debug("[!] lite backend: get_task_queue error - %s", exc)
            return []

    async def get_outputs_for_review(self) -> list[dict[str, Any]] | None:
        try:
            tasks = self._task_store.list_tasks(status_filter="complete")
            return [t for t in tasks if t.get("output")]
        except Exception as exc:
            logger.debug("[!] lite backend: get_outputs_for_review error - %s", exc)
            return []

    async def create_task(
        self,
        agent_id: str,
        description: str,
        priority: str = "medium",
        trigger_type: str = "manual",
        trigger_source: str = "user",
        tool: str | None = None,
        success_criteria: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            action = None
            outcome = None
            if tool:
                action = {"tool": tool, "tool_ref": None, "parameters": {}}
            if success_criteria:
                outcome = {"type": "report", "success_criteria": success_criteria,
                           "artifact_ref": None, "verified": False}
            task = self._task_store.create_task(
                agent_id=agent_id,
                description=description,
                priority=priority,
                action=action,
                outcome=outcome,
            )
            return {"success": True, **task} if task else {"success": False, "error": "Task creation failed"}
        except Exception as exc:
            logger.debug("[!] lite backend: create_task error - %s", exc)
            return {"success": False, "error": str(exc)}

    # -- work queue (uses task store with work-item semantics) ---------------

    async def get_work_queue(
        self, status: str | None = None,
    ) -> list[dict[str, Any]] | None:
        # Work queue items are tasks with trigger_type="mcp"
        try:
            tasks = self._task_store.list_tasks(status_filter=status)
            return [t for t in tasks if t.get("trigger_type") == "mcp"]
        except Exception as exc:
            logger.debug("[!] lite backend: get_work_queue error - %s", exc)
            return []

    async def enqueue_work_item(
        self,
        description: str,
        requester: str = "claude_code",
        priority: str = "medium",
        agent_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any] | None:
        try:
            task = self._task_store.create_task(
                agent_id=agent_id or "",
                description=description,
                priority=priority,
                trigger_type="mcp",
            )
            return {"success": True, "item_id": task.get("task_id", "")} if task else {"success": False}
        except Exception as exc:
            logger.debug("[!] lite backend: enqueue_work_item error - %s", exc)
            return {"success": False, "error": str(exc)}

    async def claim_work_item(self) -> dict[str, Any] | None:
        try:
            tasks = self._task_store.list_tasks(status_filter="assigned")
            mcp_tasks = [t for t in tasks if t.get("trigger_type") == "mcp"]
            if not mcp_tasks:
                return {"success": False, "error": "No work items available"}
            task = mcp_tasks[0]
            updated = self._task_store.update_task(task["task_id"], status="in_progress")
            return {"success": True, "item": updated}
        except Exception as exc:
            logger.debug("[!] lite backend: claim_work_item error - %s", exc)
            return {"success": False, "error": str(exc)}

    async def update_work_item(
        self,
        item_id: str,
        status: str,
        result: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            if status == "complete" and result:
                updated = self._task_store.complete_task(item_id, output={"content": result})
            else:
                updated = self._task_store.update_task(item_id, status=status)
            return {"success": True, "item": updated}
        except Exception as exc:
            logger.debug("[!] lite backend: update_work_item error - %s", exc)
            return {"success": False, "error": str(exc)}

    async def get_work_item(self, item_id: str) -> dict[str, Any] | None:
        try:
            tasks = self._task_store.list_tasks()
            return next((t for t in tasks if t.get("task_id") == item_id), None)
        except Exception:
            return None

    # -- briefing -----------------------------------------------------------

    async def generate_briefing(
        self,
        hours: int = 24,
        post_to_channel: bool = True,
        channel: str = "daily-digest",
    ) -> dict[str, Any] | None:
        try:
            # Gather recent messages across all channels
            cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
            all_activity: list[dict[str, Any]] = []
            for ch in self._chat.list_channels():
                msgs = self._chat.get_channel_messages(ch.id, limit=200)
                for m in msgs:
                    try:
                        ts = datetime.fromisoformat(m.timestamp.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        continue
                    if ts >= cutoff:
                        all_activity.append({
                            "channel": ch.id,
                            "sender": m.sender,
                            "content": m.content[:300],
                            "timestamp": m.timestamp,
                        })

            if not all_activity:
                return {"success": True, "report": {"summary": "No activity in the last {} hours.".format(hours)}}

            # Build a prompt for Ollama
            activity_text = "\n".join(
                f"[{a['channel']}] {a['sender']}: {a['content']}"
                for a in all_activity[:50]  # Cap to avoid huge prompts
            )
            system_prompt = (
                "You are an executive briefing assistant. Summarize the following team activity "
                "into a concise briefing with sections: Key Decisions, Active Discussions, "
                "Action Items, and Blockers. Be brief and actionable."
            )
            user_prompt = f"Team activity from the last {hours} hours:\n\n{activity_text}"

            # Call Ollama
            try:
                from cohort.local.ollama import OllamaClient
                client = OllamaClient(timeout=60)
                if not client.health_check():
                    # Fallback: simple activity summary without LLM
                    summary = self._simple_briefing(all_activity, hours)
                    report = {"summary": summary, "generated_at": datetime.now(timezone.utc).isoformat()}
                else:
                    available = client.list_models()
                    model = next(
                        (m for m in ["gemma3:12b", "qwen2.5:14b", "llama3.1:8b", "mistral:7b"]
                         if m in available),
                        available[0] if available else None,
                    )
                    if not model:
                        summary = self._simple_briefing(all_activity, hours)
                        report = {"summary": summary, "generated_at": datetime.now(timezone.utc).isoformat()}
                    else:
                        result = client.generate(model=model, prompt=user_prompt, system=system_prompt)
                        summary = result.get("response", "") if isinstance(result, dict) else str(result)
                        report = {
                            "summary": summary,
                            "model": model,
                            "activity_count": len(all_activity),
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                        }
            except ImportError:
                summary = self._simple_briefing(all_activity, hours)
                report = {"summary": summary, "generated_at": datetime.now(timezone.utc).isoformat()}

            # Post to channel if requested
            if post_to_channel:
                if self._chat.get_channel(channel) is None:
                    self._chat.create_channel(name=channel, description="Daily briefings")
                self._chat.post_message(
                    channel_id=channel,
                    sender="system",
                    content=f"**Executive Briefing** ({hours}h)\n\n{report['summary']}",
                    message_type="system",
                )

            self._latest_briefing = report
            return {"success": True, "report": report}
        except Exception as exc:
            logger.debug("[!] lite backend: generate_briefing error - %s", exc)
            return {"success": False, "error": str(exc)}

    def _simple_briefing(self, activity: list[dict[str, Any]], hours: int) -> str:
        """Generate a simple activity summary without LLM."""
        channels = set(a["channel"] for a in activity)
        senders = set(a["sender"] for a in activity)
        return (
            f"**Activity Summary** (last {hours}h)\n\n"
            f"- **{len(activity)}** messages across **{len(channels)}** channels\n"
            f"- **Active participants**: {', '.join(sorted(senders))}\n"
            f"- **Active channels**: {', '.join(sorted(channels))}\n"
        )

    async def get_latest_briefing(self) -> dict[str, Any] | None:
        return getattr(self, '_latest_briefing', None)

    # -- checklist (file-based) ---------------------------------------------

    async def read_checklist(self) -> dict[str, Any] | None:
        if not self._checklist_path:
            return {"items": []}
        try:
            if not self._checklist_path.exists():
                return {"items": []}
            return json.loads(self._checklist_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("[!] lite backend: checklist read error - %s", exc)
            return None

    async def write_checklist(self, data: dict[str, Any]) -> bool:
        if not self._checklist_path:
            return False
        try:
            self._checklist_path.parent.mkdir(parents=True, exist_ok=True)
            self._checklist_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return True
        except Exception as exc:
            logger.debug("[!] lite backend: checklist write error - %s", exc)
            return False
