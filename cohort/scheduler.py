"""Task scheduler -- asyncio tick loop that fires scheduled tasks.

Runs as an asyncio background task within the Starlette event loop.
Checks for due schedules every 60 seconds, creates task instances,
and hands them to the TaskExecutor for execution.

Design decisions (from roundtable):
    - Tick loop in asyncio, execution in threads (via TaskExecutor)
    - Schedules that hit max_failures auto-disable with UI notification
    - Heartbeat event so the UI can confirm the scheduler is alive
    - No external dependencies (no APScheduler, no celery)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from cohort.cron import compute_next_run
from cohort.task_store import TaskStore

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 60


class TaskScheduler:
    """Background scheduler that checks for due tasks and fires them.

    Parameters
    ----------
    task_store:
        Persistent store for schedules and tasks.
    task_executor:
        Executor that runs tasks (briefing/execution lifecycle).
    sio:
        Socket.IO server for broadcasting events.
    """

    def __init__(
        self,
        task_store: TaskStore,
        task_executor: Any,
        sio: Any = None,
    ) -> None:
        self.store = task_store
        self.executor = task_executor
        self.sio = sio
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_tick: Optional[str] = None
        self._tick_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def status(self) -> dict:
        """Return scheduler status for UI heartbeat."""
        return {
            "running": self._running,
            "last_tick": self._last_tick,
            "tick_count": self._tick_count,
            "active_schedules": len(self.store.list_schedules(enabled_only=True)),
            "total_schedules": len(self.store.list_schedules()),
        }

    def start(self) -> None:
        """Start the scheduler background task."""
        if self._running:
            logger.warning("[!] Scheduler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("[OK] Task scheduler started (tick every %ds)", TICK_INTERVAL_SECONDS)

    def stop(self) -> None:
        """Stop the scheduler background task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[OK] Task scheduler stopped")

    async def _tick_loop(self) -> None:
        """Main scheduler loop -- runs every TICK_INTERVAL_SECONDS."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[X] Scheduler tick error")

            try:
                await asyncio.sleep(TICK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """Single scheduler tick: check due schedules, fire tasks."""
        now = datetime.now(timezone.utc)
        self._last_tick = now.isoformat()
        self._tick_count += 1

        due = self.store.get_due_schedules(now)
        if due:
            logger.info("[>>] Scheduler tick: %d due schedule(s)", len(due))

        for schedule in due:
            try:
                await self._fire_schedule(schedule, now)
            except Exception:
                logger.exception("[X] Failed to fire schedule %s", schedule.id)
                # Record the failure on the schedule
                self.store.fail_task_for_schedule(schedule)

        # Broadcast heartbeat
        if self.sio:
            try:
                await self.sio.emit("cohort:scheduler_heartbeat", self.status)
            except Exception:
                pass  # Non-critical

    async def _fire_schedule(self, schedule: Any, now: datetime) -> None:
        """Create a task from a schedule and execute it."""
        logger.info("[>>] Firing schedule %s (%s) for agent %s",
                     schedule.id, schedule.schedule_type, schedule.agent_id)

        # Create the task instance (status = "assigned", skips briefing)
        task = self.store.create_scheduled_task(schedule)

        # Compute and store next run time
        next_run = compute_next_run(schedule.schedule_type, schedule.schedule_expr, now)
        self.store.update_schedule(schedule.id, next_run_at=next_run)

        # Prune old runs periodically (every 10th run)
        if schedule.run_count > 0 and schedule.run_count % 10 == 0:
            self.store.prune_old_runs(schedule.id)

        # Broadcast that a scheduled task is starting
        if self.sio:
            try:
                await self.sio.emit("cohort:schedule_run", {
                    "schedule_id": schedule.id,
                    "task": task,
                    "agent_id": schedule.agent_id,
                })
                await self.sio.emit("cohort:task_assigned", task)
            except Exception:
                pass

        # Execute via the TaskExecutor (skips briefing, goes to execution)
        try:
            # Build a minimal confirmed_brief for the executor
            confirmed_brief = {
                "goal": schedule.description,
                "approach": "Automated scheduled execution",
                "scope": f"Schedule: {schedule.schedule_type} ({schedule.schedule_expr})",
                "acceptance": "Complete the task as described",
            }
            await self.executor.execute_task(task, confirmed_brief)
        except Exception as exc:
            logger.exception("[X] Scheduled task execution failed for %s", task["task_id"])
            self.store.fail_task(task["task_id"], reason=str(exc))

            # Check if schedule should be auto-disabled
            updated_sched = self.store.get_schedule(schedule.id)
            if updated_sched and not updated_sched.enabled:
                await self._notify_schedule_disabled(updated_sched)

    async def _notify_schedule_disabled(self, schedule: Any) -> None:
        """Broadcast that a schedule was auto-disabled due to failures."""
        logger.warning(
            "[!] Schedule %s auto-disabled after %d failures",
            schedule.id, schedule.failure_streak,
        )
        if self.sio:
            try:
                await self.sio.emit("cohort:schedule_disabled", {
                    "schedule_id": schedule.id,
                    "agent_id": schedule.agent_id,
                    "description": schedule.description,
                    "failure_streak": schedule.failure_streak,
                })
                # Also update the full schedules list
                schedules = self.store.list_schedules()
                await self.sio.emit("cohort:schedules_update", {
                    "schedules": [s.to_dict() for s in schedules],
                })
            except Exception:
                pass

    async def force_run(self, schedule_id: str) -> Optional[dict]:
        """Manually trigger a schedule to run immediately (for UI "Run Now" button)."""
        schedule = self.store.get_schedule(schedule_id)
        if schedule is None:
            return None

        now = datetime.now(timezone.utc)
        await self._fire_schedule(schedule, now)

        return {"status": "ok", "schedule_id": schedule_id}
