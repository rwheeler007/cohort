"""File-backed persistence for tasks and schedules.

Replaces the in-memory ``_tasks`` dict in ``CohortDataLayer`` with a
thread-safe, JSON-persisted store.  Also manages schedule definitions
(recurring task templates).

Storage files::

    {data_dir}/tasks.json       -- task instances (one-shot + scheduled runs)
    {data_dir}/task_schedules.json -- schedule definitions

Design decisions (from roundtable):
    - Flat schedule model (no template/instance split in Phase 1)
    - Hard caps: 50 active schedules, 5-minute minimum interval
    - Schedule runs create real task entries with schedule_id foreign key
    - Audit logging on all schedule CRUD
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# -- Constants ---------------------------------------------------------------

MAX_ACTIVE_SCHEDULES = 50
MIN_INTERVAL_SECONDS = 300  # 5 minutes
MAX_DESCRIPTION_LENGTH = 500
VALID_PRIORITIES = frozenset({"low", "medium", "high", "critical"})
VALID_SCHEDULE_TYPES = frozenset({"once", "interval", "cron"})
VALID_TASK_STATUSES = frozenset({
    "briefing", "assigned", "in_progress", "complete",
    "approved", "needs_work", "rejected", "failed",
})
MAX_RUNS_PER_SCHEDULE = 100  # Keep last N runs, prune older

VALID_TRIGGER_TYPES = frozenset({"manual", "scheduled", "event", "mcp"})
VALID_OUTCOME_TYPES = frozenset({
    "report", "artifact", "state_change", "notification", "analysis",
})

# -- Helpers -----------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# -- Secret scanning ---------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("anthropic_key", re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?!ant-)[a-zA-Z0-9]{20,}")),
    ("github_pat", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("github_pat_fine", re.compile(r"github_pat_[a-zA-Z0-9_]{80,}")),
    ("private_key", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")),
    ("generic_secret", re.compile(r"(?:api[_-]?key|secret[_-]?key|password)\s*[=:]\s*['\"][^\s'\"]{12,}['\"]", re.IGNORECASE)),
]


def _scan_for_secrets(text: str) -> str:
    """Scan text for leaked secrets and replace with [REDACTED:type].

    Returns the (possibly redacted) text.
    """
    if not text:
        return text

    redacted = False
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(f"[REDACTED:{label}]", text)
            redacted = True

    if redacted:
        logger.warning("[!] Secret(s) redacted from task output")

    return text


def _scan_output(output: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """Scan an output dict for secrets in its 'content' field."""
    if output is None:
        return None
    content = output.get("content")
    if isinstance(content, str):
        output["content"] = _scan_for_secrets(content)
    return output


# -- Schedule dataclass ------------------------------------------------------

@dataclass
class TaskSchedule:
    """A recurring task definition."""

    id: str
    agent_id: str
    description: str
    priority: str
    schedule_type: str          # "once" | "interval" | "cron"
    schedule_expr: str          # seconds for interval, cron expr for cron, ISO for once
    enabled: bool = True
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None   # "success" | "failed"
    run_count: int = 0
    failure_streak: int = 0
    max_failures: int = 3       # consecutive failures before auto-disable
    created_at: str = ""
    updated_at: str = ""
    created_by: str = "user"
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Triad templates -- pre-filled into task instances on scheduled runs
    action_template: Dict[str, Any] = field(default_factory=dict)
    outcome_template: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskSchedule:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


# -- TaskStore ---------------------------------------------------------------

class TaskStore:
    """Thread-safe, file-backed store for tasks and schedules.

    Parameters
    ----------
    data_dir:
        Directory for persistence files.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._tasks_path = self._data_dir / "tasks.json"
        self._schedules_path = self._data_dir / "task_schedules.json"
        self._lock = threading.Lock()

        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._schedules: Dict[str, TaskSchedule] = {}
        self._loaded = False

    # == Persistence ==========================================================

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        self._tasks = {}
        self._schedules = {}

        # Load tasks
        if self._tasks_path.exists():
            try:
                raw = json.loads(self._tasks_path.read_text(encoding="utf-8"))
                for task_data in raw.get("tasks", []):
                    tid = task_data.get("task_id")
                    if tid:
                        self._tasks[tid] = task_data
                logger.info("[OK] TaskStore loaded %d tasks", len(self._tasks))
            except Exception as exc:
                logger.warning("[!] Task load error: %s", exc)

        # Load schedules
        if self._schedules_path.exists():
            try:
                raw = json.loads(self._schedules_path.read_text(encoding="utf-8"))
                for sched_data in raw.get("schedules", []):
                    sched = TaskSchedule.from_dict(sched_data)
                    self._schedules[sched.id] = sched
                logger.info("[OK] TaskStore loaded %d schedules", len(self._schedules))
            except Exception as exc:
                logger.warning("[!] Schedule load error: %s", exc)

        self._loaded = True

    def _save_tasks(self) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "last_updated": _now_iso(),
                "tasks": list(self._tasks.values()),
            }
            self._tasks_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[!] Task save error: %s", exc)

    def _save_schedules(self) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "last_updated": _now_iso(),
                "schedules": [s.to_dict() for s in self._schedules.values()],
            }
            self._schedules_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[!] Schedule save error: %s", exc)

    # == Task CRUD ============================================================

    def create_task(
        self,
        agent_id: str,
        description: str,
        priority: str = "medium",
        schedule_id: Optional[str] = None,
        trigger: Optional[Dict[str, Any]] = None,
        action: Optional[Dict[str, Any]] = None,
        outcome: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new task. Returns the task dict.

        Parameters
        ----------
        trigger:
            How the task was initiated. Required for new tasks.
            ``{"type": "manual|scheduled|event|mcp", "source": "...", "fired_at": "..."}``
        action:
            What tool/script the task will execute. Can be set later during briefing.
            ``{"tool": "...", "tool_ref": "...", "parameters": {}}``
        outcome:
            Expected result of the task. Can be set later during briefing.
            ``{"type": "report|artifact|...", "success_criteria": "...", "artifact_ref": null, "verified": false}``
        """
        task_id = _gen_id("task")
        now = _now_iso()

        # Build trigger -- default to manual if not provided
        if trigger is None:
            trigger = {"type": "manual", "source": "user", "fired_at": now}
        else:
            trigger.setdefault("fired_at", now)
            if trigger.get("type") not in VALID_TRIGGER_TYPES:
                trigger["type"] = "manual"

        # Build action skeleton
        if action is None:
            action = {"tool": None, "tool_ref": None, "parameters": {}}

        # Build outcome skeleton
        if outcome is None:
            outcome = {
                "type": None,
                "success_criteria": None,
                "artifact_ref": None,
                "verified": False,
            }
        else:
            outcome.setdefault("artifact_ref", None)
            outcome.setdefault("verified", False)

        task = {
            "task_id": task_id,
            "agent_id": agent_id,
            "description": description[:MAX_DESCRIPTION_LENGTH],
            "priority": priority if priority in VALID_PRIORITIES else "medium",
            "status": "briefing",
            "created_at": now,
            "updated_at": now,
            "schedule_id": schedule_id,
            "channel_id": None,
            "brief": None,
            "output": None,
            "review": None,
            "completed_at": None,
            "trigger": trigger,
            "action": action,
            "outcome": outcome,
        }
        with self._lock:
            self._ensure_loaded()
            self._tasks[task_id] = task
            self._save_tasks()

        logger.info("[+] Task %s created for %s%s",
                     task_id, agent_id,
                     f" (schedule {schedule_id})" if schedule_id else "")
        return task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return self._tasks.get(task_id)

    def update_task(self, task_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        """Update task fields. Returns updated task or None."""
        with self._lock:
            self._ensure_loaded()
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for key, val in updates.items():
                if key != "task_id":  # Never overwrite ID
                    task[key] = val
            task["updated_at"] = _now_iso()
            self._save_tasks()
        return task

    def complete_task(
        self,
        task_id: str,
        output: Optional[Dict[str, Any]] = None,
        artifact_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Mark a task as complete with optional output.

        Parameters
        ----------
        artifact_ref:
            Path, URL, or identifier for the produced artifact.
            Stored in ``task["outcome"]["artifact_ref"]`` and used
            for outcome verification.
        """
        now = _now_iso()
        with self._lock:
            self._ensure_loaded()
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task["status"] = "complete"
            task["updated_at"] = now
            task["completed_at"] = now
            if output:
                task["output"] = _scan_output(output)

            # Update outcome with artifact reference
            outcome = task.get("outcome") or {}
            if artifact_ref:
                outcome["artifact_ref"] = artifact_ref
            outcome["verified"] = bool(
                outcome.get("success_criteria")
                and (artifact_ref or (output and output.get("content")))
            )
            task["outcome"] = outcome

            # Update parent schedule stats if this is a scheduled run
            schedule_id = task.get("schedule_id")
            if schedule_id and schedule_id in self._schedules:
                sched = self._schedules[schedule_id]
                sched.last_run_at = now
                sched.last_status = "success"
                sched.run_count += 1
                sched.failure_streak = 0
                sched.updated_at = now
                self._save_schedules()

            self._save_tasks()

        logger.info("[OK] Task %s completed", task_id)
        return task

    def fail_task(self, task_id: str, reason: str = "") -> Optional[Dict[str, Any]]:
        """Mark a task as failed."""
        now = _now_iso()
        with self._lock:
            self._ensure_loaded()
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task["status"] = "failed"
            task["updated_at"] = now
            task["completed_at"] = now
            task["output"] = _scan_output({"content": reason, "backend": "error", "completed_at": now})

            # Update parent schedule failure tracking
            schedule_id = task.get("schedule_id")
            if schedule_id and schedule_id in self._schedules:
                sched = self._schedules[schedule_id]
                sched.last_run_at = now
                sched.last_status = "failed"
                sched.run_count += 1
                sched.failure_streak += 1
                sched.updated_at = now
                if sched.failure_streak >= sched.max_failures:
                    sched.enabled = False
                    logger.warning(
                        "[!] Schedule %s auto-disabled after %d consecutive failures",
                        schedule_id, sched.failure_streak,
                    )
                self._save_schedules()

            self._save_tasks()

        logger.info("[X] Task %s failed: %s", task_id, reason[:100])
        return task

    def list_tasks(
        self,
        status_filter: Optional[str] = None,
        schedule_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return tasks, newest first. Optionally filter by status or schedule."""
        with self._lock:
            self._ensure_loaded()
            tasks = list(self._tasks.values())

        if status_filter:
            tasks = [t for t in tasks if t.get("status") == status_filter]
        if schedule_id:
            tasks = [t for t in tasks if t.get("schedule_id") == schedule_id]

        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return tasks[:limit]

    def get_outputs_for_review(self) -> List[Dict[str, Any]]:
        """Return completed tasks without a review."""
        with self._lock:
            self._ensure_loaded()
            return [
                t for t in self._tasks.values()
                if t.get("status") == "complete" and t.get("review") is None
            ]

    def record_review(
        self,
        task_id: str,
        verdict: str,
        notes: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Record a human review for a task."""
        review = {
            "task_id": task_id,
            "verdict": verdict,
            "notes": notes,
            "reviewed_at": _now_iso(),
        }
        with self._lock:
            self._ensure_loaded()
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task["review"] = review
            task["updated_at"] = _now_iso()
            self._save_tasks()
        logger.info("[*] Review for %s: %s", task_id, verdict)
        return review

    def prune_old_runs(self, schedule_id: str) -> int:
        """Keep only the last MAX_RUNS_PER_SCHEDULE runs for a schedule."""
        with self._lock:
            self._ensure_loaded()
            runs = [
                t for t in self._tasks.values()
                if t.get("schedule_id") == schedule_id
            ]
            runs.sort(key=lambda t: t.get("created_at", ""), reverse=True)

            to_remove = runs[MAX_RUNS_PER_SCHEDULE:]
            for task in to_remove:
                del self._tasks[task["task_id"]]

            if to_remove:
                self._save_tasks()
                logger.info("[*] Pruned %d old runs for schedule %s",
                            len(to_remove), schedule_id)

        return len(to_remove)

    # == Schedule CRUD ========================================================

    def create_schedule(
        self,
        agent_id: str,
        description: str,
        schedule_type: str,
        schedule_expr: str,
        priority: str = "medium",
        next_run_at: Optional[str] = None,
        created_by: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskSchedule:
        """Create a new schedule definition.

        Raises
        ------
        ValueError
            If caps are exceeded or parameters are invalid.
        """
        # Validation
        if schedule_type not in VALID_SCHEDULE_TYPES:
            raise ValueError(f"Invalid schedule_type: {schedule_type}")
        if priority not in VALID_PRIORITIES:
            priority = "medium"
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH]

        # Interval minimum check
        if schedule_type == "interval":
            try:
                interval_secs = int(schedule_expr)
                if interval_secs < MIN_INTERVAL_SECONDS:
                    raise ValueError(
                        f"Minimum interval is {MIN_INTERVAL_SECONDS}s "
                        f"({MIN_INTERVAL_SECONDS // 60} minutes), "
                        f"got {interval_secs}s"
                    )
            except (TypeError, ValueError) as exc:
                if "Minimum interval" in str(exc):
                    raise
                raise ValueError(f"Interval schedule_expr must be integer seconds: {schedule_expr}") from exc

        now = _now_iso()
        sched = TaskSchedule(
            id=_gen_id("sched"),
            agent_id=agent_id,
            description=description,
            priority=priority,
            schedule_type=schedule_type,
            schedule_expr=schedule_expr,
            enabled=True,
            next_run_at=next_run_at or now,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            metadata=metadata or {},
        )

        with self._lock:
            self._ensure_loaded()

            # Cap check
            active_count = sum(1 for s in self._schedules.values() if s.enabled)
            if active_count >= MAX_ACTIVE_SCHEDULES:
                raise ValueError(
                    f"Maximum {MAX_ACTIVE_SCHEDULES} active schedules reached. "
                    f"Disable or delete existing schedules first."
                )

            self._schedules[sched.id] = sched
            self._save_schedules()

        logger.info("[+] Schedule %s created: %s %s %s (by %s)",
                     sched.id, agent_id, schedule_type, schedule_expr, created_by)
        return sched

    def get_schedule(self, schedule_id: str) -> Optional[TaskSchedule]:
        with self._lock:
            self._ensure_loaded()
            return self._schedules.get(schedule_id)

    def update_schedule(self, schedule_id: str, **updates: Any) -> Optional[TaskSchedule]:
        """Update schedule fields. Returns updated schedule or None."""
        with self._lock:
            self._ensure_loaded()
            sched = self._schedules.get(schedule_id)
            if sched is None:
                return None

            for key, val in updates.items():
                if key in ("id", "created_at"):  # Immutable fields
                    continue
                if hasattr(sched, key):
                    setattr(sched, key, val)

            # Re-validate interval minimum if changed
            if "schedule_expr" in updates and sched.schedule_type == "interval":
                try:
                    if int(sched.schedule_expr) < MIN_INTERVAL_SECONDS:
                        sched.schedule_expr = str(MIN_INTERVAL_SECONDS)
                except (TypeError, ValueError):
                    pass

            sched.updated_at = _now_iso()
            self._save_schedules()

        logger.info("[*] Schedule %s updated: %s", schedule_id, list(updates.keys()))
        return sched

    def toggle_schedule(self, schedule_id: str) -> Optional[TaskSchedule]:
        """Toggle a schedule's enabled state."""
        with self._lock:
            self._ensure_loaded()
            sched = self._schedules.get(schedule_id)
            if sched is None:
                return None

            # Cap check when enabling
            if not sched.enabled:
                active_count = sum(1 for s in self._schedules.values() if s.enabled)
                if active_count >= MAX_ACTIVE_SCHEDULES:
                    logger.warning("[!] Cannot enable schedule %s: cap reached", schedule_id)
                    return sched  # Return without toggling

            sched.enabled = not sched.enabled
            if sched.enabled:
                sched.failure_streak = 0  # Reset on re-enable
            sched.updated_at = _now_iso()
            self._save_schedules()

        state = "enabled" if sched.enabled else "disabled"
        logger.info("[*] Schedule %s %s", schedule_id, state)
        return sched

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule definition. Does NOT delete its task runs."""
        with self._lock:
            self._ensure_loaded()
            if schedule_id not in self._schedules:
                return False
            del self._schedules[schedule_id]
            self._save_schedules()

        logger.info("[-] Schedule %s deleted", schedule_id)
        return True

    def list_schedules(self, enabled_only: bool = False) -> List[TaskSchedule]:
        """Return all schedules, sorted by created_at descending."""
        with self._lock:
            self._ensure_loaded()
            schedules = list(self._schedules.values())

        if enabled_only:
            schedules = [s for s in schedules if s.enabled]

        schedules.sort(key=lambda s: s.created_at, reverse=True)
        return schedules

    def get_due_schedules(self, now: datetime) -> List[TaskSchedule]:
        """Return enabled schedules whose next_run_at <= now."""
        now_iso = now.isoformat()
        with self._lock:
            self._ensure_loaded()
            due = [
                s for s in self._schedules.values()
                if s.enabled
                and s.next_run_at is not None
                and s.next_run_at <= now_iso
            ]
        return due

    def create_scheduled_task(self, schedule: TaskSchedule) -> Dict[str, Any]:
        """Create a task instance from a schedule (skips briefing).

        Populates trigger from schedule metadata, and action/outcome from
        the schedule's templates if defined.
        """
        now = _now_iso()
        trigger = {
            "type": "scheduled",
            "source": schedule.id,
            "fired_at": now,
        }

        # Use schedule templates if defined, otherwise leave as skeleton
        action = dict(schedule.action_template) if schedule.action_template else None
        outcome = dict(schedule.outcome_template) if schedule.outcome_template else None

        task = self.create_task(
            agent_id=schedule.agent_id,
            description=schedule.description,
            priority=schedule.priority,
            schedule_id=schedule.id,
            trigger=trigger,
            action=action,
            outcome=outcome,
        )
        # Scheduled tasks skip briefing -- go straight to assigned
        self.update_task(task["task_id"], status="assigned")
        return self.get_task(task["task_id"]) or task
