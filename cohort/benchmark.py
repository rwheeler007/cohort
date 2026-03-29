"""A/B benchmark runner for Cohort response modes.

Runs the same multi-agent task through different response pipelines
(local-only vs hybrid/smartest) and collects side-by-side results
for live comparison in the dashboard.

Design:
- Each benchmark "scenario" defines a prompt, participating agents, and
  evaluation criteria (structured rubric questions scored by the human).
- A "run" executes one scenario across two modes (A and B), posting
  progress to Socket.IO as it goes so the UI can render live.
- Results are persisted to SQLite for history/trending.

Kill switch:
    Set BENCHMARK_ENABLED = False to hide the benchmark UI and disable
    all benchmark API endpoints. Flip back to True when needed.
"""

from __future__ import annotations

# =====================================================================
# Kill switch -- set to False to disable benchmark UI and endpoints
# =====================================================================
BENCHMARK_ENABLED = True

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# =====================================================================
# SQLite persistence -- separate benchmark.db (dev tool, not user data)
# =====================================================================

class BenchmarkDB:
    """SQLite persistence for benchmark runs.

    Uses its own ``benchmark.db`` file, completely isolated from any
    user/application database.  Thread-safe (one connection per call).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id          TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    started_at  TEXT NOT NULL,
                    completed_at TEXT DEFAULT '',
                    status      TEXT DEFAULT 'pending',
                    notes       TEXT DEFAULT '',
                    data        TEXT NOT NULL  -- full JSON blob
                );
                CREATE INDEX IF NOT EXISTS idx_runs_started
                    ON runs(started_at DESC);
            """)
            conn.commit()
        finally:
            conn.close()

    def save_run(self, run: "BenchmarkRun") -> None:
        """Upsert a run (insert or replace)."""
        conn = self._connect()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO runs
                   (id, scenario_id, started_at, completed_at, status, notes, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.id,
                    run.scenario_id,
                    run.started_at,
                    run.completed_at,
                    run.status,
                    run.notes,
                    json.dumps(run.to_dict()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def load_all_runs(self) -> list[dict[str, Any]]:
        """Load all runs as dicts, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT data FROM runs ORDER BY started_at DESC"
            ).fetchall()
            results = []
            for row in rows:
                try:
                    results.append(json.loads(row["data"]))
                except (json.JSONDecodeError, KeyError):
                    pass
            return results
        finally:
            conn.close()

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        """Load a single run by ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT data FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["data"])
        except (json.JSONDecodeError, KeyError):
            return None
        finally:
            conn.close()

    def delete_run(self, run_id: str) -> bool:
        """Delete a run by ID."""
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


# =====================================================================
# Scenario definitions
# =====================================================================

@dataclass
class EvalCriterion:
    """A single evaluation dimension for human scoring."""
    id: str
    label: str
    description: str
    weight: float = 1.0  # relative weight in composite score


@dataclass
class BenchmarkScenario:
    """A benchmark task definition."""
    id: str
    name: str
    category: str  # "code_review", "architecture", "triage"
    description: str
    prompt: str
    agents: list[str]  # agent_ids to involve
    eval_criteria: list[EvalCriterion] = field(default_factory=list)
    context_files: list[str] = field(default_factory=list)  # optional file paths

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "prompt": self.prompt,
            "agents": self.agents,
            "eval_criteria": [asdict(c) for c in self.eval_criteria],
        }


# Built-in scenarios
SCENARIOS: dict[str, BenchmarkScenario] = {}


def _register_builtin_scenarios() -> None:
    """Register the three core benchmark scenarios."""
    # 1. Code Review -- subtle bug detection
    SCENARIOS["code_review"] = BenchmarkScenario(
        id="code_review",
        name="Code Review: Subtle Bug Detection",
        category="code_review",
        description=(
            "Review a Python function with 2 obvious issues and 1 subtle bug. "
            "Tests whether agents catch the subtle issue and build on each other's feedback."
        ),
        prompt="""Review this Python function. Identify ALL bugs, security issues, and quality problems.

```python
import os
import hashlib
import sqlite3

def authenticate_user(username: str, password: str, db_path: str = "users.db") -> dict | None:
    \"\"\"Authenticate a user and return their profile, or None if invalid.\"\"\"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Hash the password
    pw_hash = hashlib.md5(password.encode()).hexdigest()

    # Look up the user
    query = f"SELECT id, username, email, role FROM users WHERE username = '{username}' AND pw_hash = '{pw_hash}'"
    cursor.execute(query)
    row = cursor.fetchone()

    if row is None:
        return None

    profile = {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "role": row[3],
        "session_token": os.urandom(16).hex(),
    }

    # Update last login
    cursor.execute(
        f"UPDATE users SET last_login = datetime('now') WHERE id = {row[0]}"
    )
    conn.commit()

    return profile
```

For each issue found, explain:
1. What the issue is
2. Why it matters (severity: critical/high/medium/low)
3. How to fix it""",
        agents=["python_developer", "security_agent"],
        eval_criteria=[
            EvalCriterion("sql_injection", "SQL Injection Detection", "Caught the SQL injection vulnerability in the query", weight=2.0),
            EvalCriterion("weak_hash", "Weak Hash Detection", "Identified MD5 as insecure for password hashing", weight=1.5),
            EvalCriterion("conn_leak", "Connection Leak", "Noticed the connection is never closed (no context manager)", weight=1.0),
            EvalCriterion("update_injection", "Update SQL Injection", "Caught the f-string SQL injection in the UPDATE statement", weight=1.5),
            EvalCriterion("session_quality", "Session Token Weakness", "Noted that session token is returned but never persisted/validated", weight=0.5),
            EvalCriterion("builds_on_prior", "Builds on Prior Agent", "Second agent adds new insights rather than repeating the first", weight=1.0),
            EvalCriterion("actionable_fixes", "Actionable Fix Suggestions", "Provides concrete, correct fix code", weight=1.0),
        ],
    )

    # 2. Architecture Planning -- multi-agent convergence
    SCENARIOS["architecture"] = BenchmarkScenario(
        id="architecture",
        name="Architecture: Caching Layer Design",
        category="architecture",
        description=(
            "Design a caching layer for a high-traffic API. Tests whether agents "
            "hold context across rounds, challenge assumptions, and converge on a plan."
        ),
        prompt="""We need to add a caching layer to our API service. Current situation:

- Python FastAPI backend, ~50 endpoints
- PostgreSQL database, ~200ms avg query time
- 10K requests/minute peak traffic
- 12GB RAM available on the server
- Mix of user-specific data (profiles, dashboards) and shared data (product catalog, pricing)
- Some endpoints are real-time sensitive (inventory levels, order status)

Design the caching strategy. Address:
1. What to cache vs what to always fetch fresh
2. Cache invalidation strategy
3. Technology choice (Redis, in-memory, CDN, combination)
4. How to handle cache stampede / thundering herd
5. Monitoring and cache hit rate targets

Provide a concrete implementation plan with code examples where relevant.""",
        agents=["python_developer", "coding_orchestrator"],
        eval_criteria=[
            EvalCriterion("data_classification", "Data Classification", "Correctly separates cacheable vs real-time data", weight=1.5),
            EvalCriterion("invalidation", "Invalidation Strategy", "Proposes a concrete invalidation approach (TTL, event-driven, or hybrid)", weight=1.5),
            EvalCriterion("tech_choice", "Technology Rationale", "Justifies technology choice with specifics (not generic 'use Redis')", weight=1.0),
            EvalCriterion("stampede", "Stampede Protection", "Addresses thundering herd with a real pattern (locking, stale-while-revalidate, etc.)", weight=1.0),
            EvalCriterion("code_examples", "Concrete Code", "Provides working code examples, not pseudocode", weight=1.0),
            EvalCriterion("challenges_assumptions", "Challenges Assumptions", "At least one agent pushes back on another's suggestion", weight=1.5),
            EvalCriterion("convergence", "Coherent Plan", "Agents converge on a single plan, not competing proposals", weight=1.0),
        ],
    )

    # 3. Triage / Scoring -- structured classification
    SCENARIOS["triage"] = BenchmarkScenario(
        id="triage",
        name="Issue Triage: Priority Classification",
        category="triage",
        description=(
            "Classify and prioritize 8 incoming issues. Tests structured reasoning, "
            "rubric adherence, and whether agents agree on classification."
        ),
        prompt="""Triage these 8 incoming issues. For each, assign:
- Priority: P0 (critical), P1 (high), P2 (medium), P3 (low)
- Category: bug, feature, security, performance, documentation
- Recommended assignee from: python_developer, security_agent, web_developer, documentation_agent
- Brief justification (1-2 sentences)

Issues:

1. "Login page returns 500 error for all users since last deploy"
2. "Can we add dark mode to the settings page?"
3. "API response times increased 3x after upgrading to v2.1"
4. "Typo in the installation guide: 'pip instal' instead of 'pip install'"
5. "User reports they can access other users' profiles by changing the URL ID"
6. "Feature request: export dashboard data to CSV"
7. "Memory usage grows linearly over 24h, server needs daily restart"
8. "The /api/health endpoint returns 200 even when the database is down"

Format your response as a structured table.""",
        agents=["coding_orchestrator", "security_agent"],
        eval_criteria=[
            EvalCriterion("p0_correct", "P0 Identification", "Correctly identifies issue #1 (login 500) as P0", weight=1.0),
            EvalCriterion("security_priority", "Security Issue Priority", "Correctly flags #5 (IDOR) as P0/P1 security", weight=1.5),
            EvalCriterion("perf_vs_bug", "Performance Classification", "Distinguishes #3 (perf regression) from #7 (memory leak) appropriately", weight=1.0),
            EvalCriterion("assignee_match", "Correct Assignees", "Routes security issues to security_agent, code issues to python_developer", weight=1.0),
            EvalCriterion("structured_output", "Structured Format", "Produces a clear, scannable table format", weight=0.5),
            EvalCriterion("agent_agreement", "Agent Agreement", "Both agents agree on P0/P1 classifications (some P2/P3 disagreement is fine)", weight=1.0),
            EvalCriterion("health_check", "Health Endpoint Insight", "Recognizes #8 as a reliability issue, not just a bug", weight=0.5),
        ],
    )


_register_builtin_scenarios()


# =====================================================================
# Benchmark result types
# =====================================================================

@dataclass
class AgentResponse:
    """A single agent's response in one arm of the benchmark."""
    agent_id: str
    content: str
    model: str = ""
    pipeline: str = ""  # "local", "smartest", "smartest-degraded", "claude"
    tokens_in: int = 0          # local model tokens in
    tokens_out: int = 0         # local model tokens out
    claude_tokens_in: int = 0   # estimated Claude input tokens (smartest only)
    claude_tokens_out: int = 0  # estimated Claude output tokens (smartest only)
    elapsed_seconds: float = 0.0
    response_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkArm:
    """One side of the A/B comparison (e.g., local-only or hybrid)."""
    mode: str  # "smart", "smarter", "smartest"
    label: str  # Human-readable: "Local Only", "Hybrid (Claude)"
    responses: list[AgentResponse] = field(default_factory=list)
    total_elapsed: float = 0.0
    total_tokens_in: int = 0       # local model tokens
    total_tokens_out: int = 0      # local model tokens
    total_claude_in: int = 0       # estimated Claude tokens (smartest only)
    total_claude_out: int = 0      # estimated Claude tokens (smartest only)
    status: str = "pending"  # pending, running, complete, error
    error: str = ""
    scores: dict[str, float] = field(default_factory=dict)  # criterion_id -> 0-5 score

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "label": self.label,
            "responses": [r.to_dict() for r in self.responses],
            "total_elapsed": self.total_elapsed,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_claude_in": self.total_claude_in,
            "total_claude_out": self.total_claude_out,
            "status": self.status,
            "error": self.error,
            "scores": self.scores,
        }


@dataclass
class BenchmarkRun:
    """A complete A/B benchmark run."""
    id: str
    scenario_id: str
    started_at: str = ""
    completed_at: str = ""
    status: str = "pending"  # pending, running, complete, scored
    arm_a: BenchmarkArm = field(default_factory=lambda: BenchmarkArm(mode="smarter", label="Local Only (Smarter)"))
    arm_b: BenchmarkArm = field(default_factory=lambda: BenchmarkArm(mode="smartest", label="Hybrid (Smartest)"))
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        scenario = SCENARIOS.get(self.scenario_id)
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "scenario": scenario.to_dict() if scenario else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "arm_a": self.arm_a.to_dict(),
            "arm_b": self.arm_b.to_dict(),
            "notes": self.notes,
        }


# =====================================================================
# Benchmark runner
# =====================================================================

class BenchmarkRunner:
    """Executes A/B benchmark runs with live progress emission."""

    def __init__(self, db: BenchmarkDB | None = None) -> None:
        self._runs: dict[str, BenchmarkRun] = {}
        self._active_run: str | None = None
        self._emit_fn: Callable[..., Any] | None = None
        self._event_loop: Any = None
        self._chat: Any = None
        self._agent_store: Any = None
        self._lock = threading.Lock()
        self._db = db

        # Hydrate in-memory cache from SQLite
        if self._db is not None:
            self._load_from_db()

    def set_emit(self, emit_fn: Callable, loop: Any) -> None:
        """Wire the Socket.IO emit function and event loop."""
        self._emit_fn = emit_fn
        self._event_loop = loop

    def set_chat(self, chat: Any) -> None:
        """Wire the chat manager for posting benchmark messages."""
        self._chat = chat

    def set_agent_store(self, store: Any) -> None:
        """Wire the agent store for loading personas."""
        self._agent_store = store

    def _load_from_db(self) -> None:
        """Hydrate in-memory runs from SQLite on startup."""
        if self._db is None:
            return
        for run_dict in self._db.load_all_runs():
            run = _run_from_dict(run_dict)
            if run:
                self._runs[run.id] = run
        if self._runs:
            logger.info("[OK] Loaded %d benchmark runs from disk", len(self._runs))

    def _persist(self, run: BenchmarkRun) -> None:
        """Save a run to SQLite (no-op if no DB configured)."""
        if self._db is not None:
            try:
                self._db.save_run(run)
            except Exception:
                logger.warning("[!] Failed to persist benchmark run %s", run.id)

    @property
    def is_running(self) -> bool:
        return self._active_run is not None

    def list_scenarios(self) -> list[dict[str, Any]]:
        """Return all available scenarios."""
        return [s.to_dict() for s in SCENARIOS.values()]

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent benchmark runs."""
        runs = sorted(
            self._runs.values(),
            key=lambda r: r.started_at or "",
            reverse=True,
        )[:limit]
        return [r.to_dict() for r in runs]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a specific run."""
        run = self._runs.get(run_id)
        return run.to_dict() if run else None

    def start_run(self, scenario_id: str) -> dict[str, Any] | None:
        """Start a new A/B benchmark run for a scenario.

        Returns the run dict, or None if a run is already active.
        """
        if self._active_run is not None:
            return None

        scenario = SCENARIOS.get(scenario_id)
        if scenario is None:
            return None

        run_id = str(uuid.uuid4())[:8]
        run = BenchmarkRun(
            id=run_id,
            scenario_id=scenario_id,
            started_at=datetime.now().isoformat(),
            status="running",
        )
        self._runs[run_id] = run
        self._active_run = run_id
        self._persist(run)

        # Create a benchmark channel for live viewing
        channel_id = f"benchmark-{run_id}"
        if self._chat:
            self._chat.create_channel(
                name=channel_id,
                description=f"Benchmark: {scenario.name}",
            )
            # Post scenario description
            self._chat.post_message(
                channel_id=channel_id,
                sender="system",
                content=(
                    f"**Benchmark: {scenario.name}**\n\n"
                    f"{scenario.description}\n\n"
                    f"**Agents:** {', '.join(scenario.agents)}\n"
                    f"**Arms:** A = Local Only (Smarter) | B = Hybrid (Smartest)\n\n"
                    f"---"
                ),
            )

        self._emit_sync("benchmark:started", run.to_dict())

        # Run in background thread
        thread = threading.Thread(
            target=self._execute_run,
            args=(run,),
            daemon=True,
        )
        thread.start()

        return run.to_dict()

    def score_run(self, run_id: str, arm: str, scores: dict[str, float]) -> dict[str, Any] | None:
        """Record human scores for one arm of a run.

        Args:
            run_id: The benchmark run ID
            arm: "a" or "b"
            scores: Dict of criterion_id -> score (0-5)
        """
        run = self._runs.get(run_id)
        if run is None:
            return None

        target = run.arm_a if arm == "a" else run.arm_b
        target.scores.update(scores)

        # Check if both arms are scored
        if run.arm_a.scores and run.arm_b.scores:
            run.status = "scored"

        self._persist(run)
        self._emit_sync("benchmark:scored", run.to_dict())
        return run.to_dict()

    def trigger_auto_score(self, run_id: str) -> dict[str, Any] | None:
        """Trigger auto-scoring on an existing completed run.

        Returns the run dict, or None if the run doesn't exist or isn't scoreable.
        """
        run = self._runs.get(run_id)
        if run is None or run.status not in ("complete", "scored"):
            return None

        scenario = SCENARIOS.get(run.scenario_id)
        if scenario is None:
            return None

        channel_id = f"benchmark-{run.id}"

        self._emit_sync("benchmark:scoring", {"run_id": run.id})

        # Run in background thread to avoid blocking
        def _score():
            self._auto_score(run, scenario, channel_id)
            run.status = "scored" if (run.arm_a.scores and run.arm_b.scores) else run.status
            self._persist(run)
            self._emit_sync("benchmark:scored", run.to_dict())

        thread = threading.Thread(target=_score, daemon=True)
        thread.start()

        return run.to_dict()

    def _execute_run(self, run: BenchmarkRun) -> None:
        """Background thread: execute both arms of the benchmark."""
        scenario = SCENARIOS.get(run.scenario_id)
        if scenario is None:
            run.status = "error"
            self._active_run = None
            return

        channel_id = f"benchmark-{run.id}"

        try:
            # ARM A: Local Only (Smarter)
            self._execute_arm(run, run.arm_a, scenario, channel_id, "A")

            # ARM B: Hybrid (Smartest)
            self._execute_arm(run, run.arm_b, scenario, channel_id, "B")

            run.completed_at = datetime.now().isoformat()
            run.status = "complete"
            self._persist(run)  # Save as complete BEFORE scoring attempt

            # Auto-score both arms
            self._emit_sync("benchmark:scoring", {"run_id": run.id})
            self._auto_score(run, scenario, channel_id)

            run.status = "scored" if (run.arm_a.scores and run.arm_b.scores) else "complete"

            # Post summary
            if self._chat:
                summary = self._build_summary(run, scenario)
                self._chat.post_message(
                    channel_id=channel_id,
                    sender="system",
                    content=summary,
                )
                self._broadcast_message(channel_id)

        except Exception as exc:
            logger.exception("[X] Benchmark run %s failed", run.id)
            run.status = "error"
            run.arm_a.error = run.arm_a.error or str(exc)

        finally:
            self._active_run = None
            self._persist(run)
            self._emit_sync("benchmark:complete", run.to_dict())

    def _execute_arm(
        self,
        run: BenchmarkRun,
        arm: BenchmarkArm,
        scenario: BenchmarkScenario,
        channel_id: str,
        arm_label: str,
    ) -> None:
        """Execute one arm (A or B) of the benchmark."""
        arm.status = "running"
        self._emit_sync("benchmark:arm_started", {
            "run_id": run.id,
            "arm": arm_label.lower(),
            "mode": arm.mode,
            "label": arm.label,
        })

        # Post arm header to channel
        if self._chat:
            self._chat.post_message(
                channel_id=channel_id,
                sender="system",
                content=f"\n**--- ARM {arm_label}: {arm.label} ---**\n",
            )
            self._broadcast_message(channel_id)

        arm_start = time.time()

        for agent_id in scenario.agents:
            self._emit_sync("benchmark:agent_started", {
                "run_id": run.id,
                "arm": arm_label.lower(),
                "agent_id": agent_id,
            })

            # Post typing indicator to channel
            if self._chat:
                self._chat.post_message(
                    channel_id=channel_id,
                    sender="system",
                    content=f"*Waiting for @{agent_id} ({arm.mode} mode)...*",
                )
                self._broadcast_message(channel_id)

            response = self._invoke_agent(
                agent_id=agent_id,
                prompt=scenario.prompt,
                response_mode=arm.mode,
                prior_responses=[r.content for r in arm.responses],
            )

            arm.responses.append(response)
            arm.total_tokens_in += response.tokens_in
            arm.total_tokens_out += response.tokens_out
            arm.total_claude_in += response.claude_tokens_in
            arm.total_claude_out += response.claude_tokens_out

            # Post agent response to channel
            if self._chat:
                badge = f"[{arm.mode.upper()}]"
                meta_line = (
                    f"*{response.model} | {response.pipeline} | "
                    f"{response.tokens_in}+{response.tokens_out} tok | "
                    f"{response.elapsed_seconds:.1f}s*"
                )
                self._chat.post_message(
                    channel_id=channel_id,
                    sender=agent_id,
                    content=f"{badge} {meta_line}\n\n{response.content}",
                )
                self._broadcast_message(channel_id)

            self._emit_sync("benchmark:agent_complete", {
                "run_id": run.id,
                "arm": arm_label.lower(),
                "agent_id": agent_id,
                "response": response.to_dict(),
            })

        arm.total_elapsed = time.time() - arm_start
        arm.status = "complete"
        self._persist(run)
        self._emit_sync("benchmark:arm_complete", {
            "run_id": run.id,
            "arm": arm_label.lower(),
            "arm_data": arm.to_dict(),
        })

    def _invoke_agent(
        self,
        agent_id: str,
        prompt: str,
        response_mode: str,
        prior_responses: list[str] | None = None,
    ) -> AgentResponse:
        """Invoke an agent with the benchmark prompt using the specified mode.

        Builds context including prior agent responses (if any) to simulate
        a multi-agent review where later agents see earlier agents' output.
        """
        # Build full prompt with prior context
        full_prompt_parts = [prompt]
        if prior_responses:
            full_prompt_parts.append("\n\n---\n\n**Previous reviewers' findings:**\n")
            for i, prior in enumerate(prior_responses, 1):
                full_prompt_parts.append(f"\n**Reviewer {i}:**\n{prior}\n")
            full_prompt_parts.append(
                "\n---\n\nBuild on the previous reviewers' work. "
                "Confirm correct findings, challenge incorrect ones, "
                "and add anything they missed."
            )
        full_prompt = "\n".join(full_prompt_parts)

        # Load agent persona for system prompt
        system_prompt = self._load_persona(agent_id)

        start_time = time.time()

        try:
            from cohort.local import LocalRouter
            router = LocalRouter()

            # Load agent temperature
            temperature: float | None = None
            if self._agent_store:
                agent_cfg = self._agent_store.get(agent_id)
                if agent_cfg and agent_cfg.model_params:
                    temperature = agent_cfg.model_params.get("temperature")

            if response_mode == "smartest":
                # Phase 1: Qwen reasoning pass
                result = router.route(
                    full_prompt,
                    task_type="reasoning",
                    temperature=temperature,
                    response_mode="smarter",
                    system=system_prompt,
                )

                if result is None:
                    return AgentResponse(
                        agent_id=agent_id,
                        content="[Error] Local LLM unavailable",
                        elapsed_seconds=time.time() - start_time,
                        response_mode=response_mode,
                    )

                # Phase 2: Distill
                distilled = router.distill(result.text)
                if not distilled:
                    distilled = result.text[:2000]

                # Phase 3: Claude CLI
                claude_response = self._call_claude_cli(
                    agent_id, full_prompt, distilled, system_prompt,
                )

                elapsed = time.time() - start_time
                if claude_response:
                    # Estimate Claude tokens (~4 chars per token is a rough heuristic)
                    claude_prompt = self._build_claude_prompt(
                        agent_id, full_prompt, distilled, system_prompt,
                    )
                    est_claude_in = len(claude_prompt) // 4
                    est_claude_out = len(claude_response) // 4

                    return AgentResponse(
                        agent_id=agent_id,
                        content=claude_response,
                        model=f"{result.model}+claude",
                        pipeline="smartest",
                        tokens_in=result.tokens_in,
                        tokens_out=result.tokens_out,
                        claude_tokens_in=est_claude_in,
                        claude_tokens_out=est_claude_out,
                        elapsed_seconds=elapsed,
                        response_mode=response_mode,
                    )
                else:
                    # Smartest degraded -- return Qwen output
                    return AgentResponse(
                        agent_id=agent_id,
                        content=result.text,
                        model=result.model,
                        pipeline="smartest-degraded",
                        tokens_in=result.tokens_in,
                        tokens_out=result.tokens_out,
                        elapsed_seconds=elapsed,
                        response_mode=response_mode,
                    )
            else:
                # Smart or Smarter -- pure local
                result = router.route(
                    full_prompt,
                    task_type="reasoning",
                    temperature=temperature,
                    response_mode=response_mode,
                    system=system_prompt,
                )

                elapsed = time.time() - start_time
                if result is None:
                    return AgentResponse(
                        agent_id=agent_id,
                        content="[Error] Local LLM unavailable",
                        elapsed_seconds=elapsed,
                        response_mode=response_mode,
                    )

                return AgentResponse(
                    agent_id=agent_id,
                    content=result.text,
                    model=result.model,
                    pipeline="local",
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    elapsed_seconds=elapsed,
                    response_mode=response_mode,
                )

        except Exception as exc:
            logger.exception("[X] Benchmark agent invocation failed: %s", agent_id)
            return AgentResponse(
                agent_id=agent_id,
                content=f"[Error] {exc}",
                elapsed_seconds=time.time() - start_time,
                response_mode=response_mode,
            )

    def _auto_score(
        self,
        run: BenchmarkRun,
        scenario: BenchmarkScenario,
        channel_id: str,
    ) -> None:
        """Auto-score both arms using the local LLM as judge.

        Sends the original prompt, evaluation criteria, and each arm's
        combined responses to the LLM.  Asks for a JSON dict of
        criterion_id -> integer score (0-5) for each arm.
        """
        for arm_key, arm in [("a", run.arm_a), ("b", run.arm_b)]:
            if not arm.responses:
                continue

            combined_responses = "\n\n---\n\n".join(
                f"**@{r.agent_id}:**\n{r.content}" for r in arm.responses
            )

            criteria_lines = "\n".join(
                f"- {c.id}: {c.label} -- {c.description}" for c in scenario.eval_criteria
            )
            criteria_ids = [c.id for c in scenario.eval_criteria]

            judge_prompt = f"""You are an expert evaluator scoring AI agent responses.

**Original task given to agents:**
{scenario.prompt}

**Agent responses to evaluate:**
{combined_responses}

**Evaluation criteria:**
{criteria_lines}

**Scoring rubric (use the FULL range — most scores should be 2-4, not 4-5):**
- 0: Not mentioned at all
- 1: Mentioned but wrong or misleading (e.g., claims to fix an issue but the fix code has bugs)
- 2: Partially addressed — identifies the issue but explanation is shallow or fix is incomplete
- 3: Adequate — correctly identifies and explains the issue with a reasonable fix
- 4: Strong — thorough analysis with correct, production-quality fix code
- 5: Exceptional — only award 5 if the response demonstrates insight beyond the obvious (e.g., catches edge cases, explains WHY the fix works, considers operational impact). A score of 5 should be rare.

**Verification rules:**
- If code examples are provided, CHECK them for correctness. Code with syntax errors, undefined variables, or non-existent APIs scores at most 2 regardless of how well the concept is explained.
- If an agent claims to correct another agent's work, verify the correction is actually right. Wrong corrections score 1.
- "Challenges Assumptions" requires the challenge to be VALID. Challenging something that was already correct scores 0-1.
- "Convergence" requires a SINGLE unified plan, not two separate proposals restated.

Respond with ONLY a JSON object mapping criterion_id to integer score. No other text.
Example: {{"sql_injection": 3, "weak_hash": 2, ...}}

JSON:"""

            try:
                # Use Claude CLI as judge for accurate scoring
                import os
                import subprocess
                import sys

                claude_cmd = os.environ.get("COHORT_CLAUDE_CMD", "claude")
                cli_cmd = [claude_cmd, "-p", "-"]
                if sys.platform == "win32":
                    cli_cmd = ["cmd", "/c"] + cli_cmd

                proc = subprocess.run(
                    cli_cmd,
                    input=judge_prompt,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    shell=False,
                    encoding="utf-8",
                    errors="replace",
                )

                text = proc.stdout.strip()
                if proc.returncode != 0 and not text:
                    logger.warning("[!] Claude judge failed for arm %s: %s", arm_key, proc.stderr[:200])
                    continue

                if not text:
                    logger.warning("[!] Claude judge returned empty for arm %s", arm_key)
                    continue

                # Extract JSON from response (may have markdown fences)
                text = text.strip()
                if "```" in text:
                    # Pull content between first ``` pair
                    parts = text.split("```")
                    if len(parts) >= 3:
                        text = parts[1]
                        # Strip optional language tag (e.g. "json\n")
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()

                scores = json.loads(text)

                # Validate: must be dict with integer values 0-5
                valid_scores = {}
                for cid in criteria_ids:
                    val = scores.get(cid)
                    if isinstance(val, (int, float)) and 0 <= val <= 5:
                        valid_scores[cid] = int(val)
                    else:
                        valid_scores[cid] = 0
                        logger.warning("[!] Auto-score missing or invalid for %s.%s", arm_key, cid)

                arm.scores = valid_scores
                logger.info(
                    "[OK] Auto-scored arm %s: %s",
                    arm_key,
                    ", ".join(f"{k}={v}" for k, v in valid_scores.items()),
                )

            except subprocess.TimeoutExpired:
                logger.warning("[!] Claude judge timed out for arm %s (300s)", arm_key)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("[!] Auto-scorer JSON parse failed for arm %s: %s", arm_key, exc)
            except Exception:
                logger.exception("[!] Auto-scorer failed for arm %s", arm_key)

        # Post scoring results to channel
        if self._chat and (run.arm_a.scores or run.arm_b.scores):
            score_lines = ["## Auto-Scoring Results\n"]
            for arm_key, arm, label in [("a", run.arm_a, "Arm A (Local)"), ("b", run.arm_b, "Arm B (Hybrid)")]:
                if arm.scores:
                    score_lines.append(f"**{label}:**")
                    for c in scenario.eval_criteria:
                        s = arm.scores.get(c.id, "?")
                        score_lines.append(f"  - {c.label}: {s}/5")
                    score_lines.append("")
            self._chat.post_message(
                channel_id=channel_id,
                sender="system",
                content="\n".join(score_lines),
            )
            self._broadcast_message(channel_id)

        self._persist(run)

    def _build_claude_prompt(
        self,
        agent_id: str,
        original_prompt: str,
        distilled_briefing: str,
        persona: str | None = None,
    ) -> str:
        """Build the prompt sent to Claude CLI (reusable for token estimation)."""
        prompt_parts = []
        if persona:
            prompt_parts.append(f"You are {agent_id}. Follow this persona:\n{persona}\n\n")
        prompt_parts.append(f"**Briefing from local analysis:**\n{distilled_briefing}\n\n")
        prompt_parts.append(f"**Original task:**\n{original_prompt}\n\n")
        prompt_parts.append("Respond with your expert analysis. Be thorough and specific.")
        return "\n".join(prompt_parts)

    def _call_claude_cli(
        self,
        agent_id: str,
        original_prompt: str,
        distilled_briefing: str,
        persona: str | None = None,
    ) -> str | None:
        """Call Claude CLI for the smartest pipeline Phase 3."""
        import subprocess
        import sys

        full_prompt = self._build_claude_prompt(
            agent_id, original_prompt, distilled_briefing, persona,
        )

        try:
            import os
            claude_cmd = os.environ.get("COHORT_CLAUDE_CMD", "claude")
            cli_cmd = [claude_cmd, "-p", "-"]
            if sys.platform == "win32":
                cli_cmd = ["cmd", "/c"] + cli_cmd

            result = subprocess.run(
                cli_cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=300,
                shell=False,
                encoding="utf-8",
                errors="replace",
            )

            content = result.stdout.strip()
            if result.returncode != 0 and not content:
                logger.error("[X] Claude CLI error: %s", result.stderr[:200])
                return None
            return content if content else None

        except subprocess.TimeoutExpired:
            logger.error("[X] Claude CLI timeout in benchmark")
            return None
        except Exception:
            logger.exception("[X] Claude CLI failed in benchmark")
            return None

    def _load_persona(self, agent_id: str) -> str | None:
        """Load an agent's persona markdown.

        Fallback chain:
        1. cohort/personas/{agent_id}.md (lightweight persona)
        2. agent_store prompt path (full agent_prompt.md)
        """
        try:
            from cohort.personas import load_persona
            persona = load_persona(agent_id)
            if persona:
                return persona
        except Exception:
            pass

        # Fallback: try loading via agent_router's prompt path
        try:
            from cohort.agent_router import get_agent_prompt_path
            prompt_path = get_agent_prompt_path(agent_id)
            if prompt_path and prompt_path.exists():
                return prompt_path.read_text(encoding="utf-8")[:2000]
        except Exception:
            pass

        return None

    def _build_summary(self, run: BenchmarkRun, scenario: BenchmarkScenario) -> str:
        """Build a text summary comparing the two arms."""
        a, b = run.arm_a, run.arm_b
        lines = [
            f"## Benchmark Complete: {scenario.name}",
            "",
            "| Metric | Arm A (Local) | Arm B (Hybrid) |",
            "|--------|--------------|----------------|",
            f"| Mode | {a.mode} | {b.mode} |",
            f"| Total Time | {a.total_elapsed:.1f}s | {b.total_elapsed:.1f}s |",
            f"| Local Tokens (in/out) | {a.total_tokens_in:,} / {a.total_tokens_out:,} | {b.total_tokens_in:,} / {b.total_tokens_out:,} |",
        ]
        if b.total_claude_in or b.total_claude_out:
            lines.append(
                f"| Claude Tokens (in/out) | -- | ~{b.total_claude_in:,} / ~{b.total_claude_out:,} |"
            )
        lines.extend([
            f"| Agents | {len(a.responses)} | {len(b.responses)} |",
            "",
        ])

        # Auto-score results
        if a.scores and b.scores:
            lines.append("### Scores (Auto-scored by LLM Judge)")
            lines.append("")
            lines.append("| Criterion | Weight | Arm A | Arm B | Delta |")
            lines.append("|-----------|--------|-------|-------|-------|")
            total_a = total_b = total_w = 0.0
            for c in scenario.eval_criteria:
                sa = a.scores.get(c.id, 0)
                sb = b.scores.get(c.id, 0)
                delta = sb - sa
                delta_str = f"+{delta}" if delta > 0 else str(delta)
                lines.append(f"| {c.label} | x{c.weight} | {sa}/5 | {sb}/5 | {delta_str} |")
                total_a += sa * c.weight
                total_b += sb * c.weight
                total_w += c.weight * 5
            pct_a = (total_a / total_w * 100) if total_w else 0
            pct_b = (total_b / total_w * 100) if total_w else 0
            winner = "A (Local)" if pct_a > pct_b else "B (Hybrid)" if pct_b > pct_a else "Tie"
            lines.append("")
            lines.append(f"**Weighted: A={pct_a:.0f}% | B={pct_b:.0f}% | Winner: {winner}**")
        else:
            lines.append("### Evaluation Criteria")
            lines.append("")
            for c in scenario.eval_criteria:
                weight_label = f" (x{c.weight})" if c.weight != 1.0 else ""
                lines.append(f"- **{c.label}**{weight_label}: {c.description}")

        return "\n".join(lines)

    def _emit_sync(self, event: str, data: dict) -> None:
        """Emit a Socket.IO event from sync context."""
        if self._emit_fn is None or self._event_loop is None:
            return
        try:
            import asyncio
            if self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._emit_fn(event, data), self._event_loop,
                )
        except RuntimeError:
            logger.debug("No event loop for benchmark emit: %s", event)

    def _broadcast_message(self, channel_id: str) -> None:
        """Broadcast channel list update so new channel appears in sidebar."""
        if self._chat is None:
            return
        try:
            channels = self._chat.list_channels(include_archived=False)
            self._emit_sync("channels_list", {
                "channels": [ch.to_dict() for ch in channels],
            })
            # Also send messages for the benchmark channel
            messages = self._chat.get_channel_messages(channel_id, limit=100)
            self._emit_sync("channel_messages", {
                "channel_id": channel_id,
                "messages": [m.to_dict() for m in messages],
            })
        except Exception:
            pass


def _run_from_dict(d: dict[str, Any]) -> BenchmarkRun | None:
    """Reconstruct a BenchmarkRun from a JSON dict (loaded from SQLite)."""
    try:
        def _arm_from_dict(ad: dict) -> BenchmarkArm:
            responses = [
                AgentResponse(**r) for r in ad.get("responses", [])
            ]
            return BenchmarkArm(
                mode=ad.get("mode", ""),
                label=ad.get("label", ""),
                responses=responses,
                total_elapsed=ad.get("total_elapsed", 0.0),
                total_tokens_in=ad.get("total_tokens_in", 0),
                total_tokens_out=ad.get("total_tokens_out", 0),
                total_claude_in=ad.get("total_claude_in", 0),
                total_claude_out=ad.get("total_claude_out", 0),
                status=ad.get("status", "complete"),
                error=ad.get("error", ""),
                scores=ad.get("scores", {}),
            )

        return BenchmarkRun(
            id=d["id"],
            scenario_id=d["scenario_id"],
            started_at=d.get("started_at", ""),
            completed_at=d.get("completed_at", ""),
            status=d.get("status", "complete"),
            arm_a=_arm_from_dict(d.get("arm_a", {})),
            arm_b=_arm_from_dict(d.get("arm_b", {})),
            notes=d.get("notes", ""),
        )
    except Exception:
        logger.warning("[!] Failed to deserialize benchmark run: %s", d.get("id", "?"))
        return None


# Singleton
_benchmark_runner: BenchmarkRunner | None = None


def get_benchmark_runner(data_dir: str | Path | None = None) -> BenchmarkRunner:
    """Get or create the singleton benchmark runner.

    Args:
        data_dir: Cohort data directory. On first call, creates
                  ``{data_dir}/benchmark.db`` (separate from user DBs).
                  Ignored on subsequent calls.
    """
    global _benchmark_runner
    if _benchmark_runner is None:
        db: BenchmarkDB | None = None
        if data_dir is not None:
            db_path = Path(data_dir) / "benchmark.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db = BenchmarkDB(db_path)
            logger.info("[OK] Benchmark DB at %s", db_path)
        _benchmark_runner = BenchmarkRunner(db=db)
    return _benchmark_runner
