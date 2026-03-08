#!/usr/bin/env python3
"""Inject starter conversations into Cohort for testing.

Supports two modes:
  --mode live    POST to running Cohort server (default, http://localhost:5100)
  --mode offline Write directly to data files (no server needed)

Usage:
  python tools/conversation_injector.py                    # run all suites, live
  python tools/conversation_injector.py --suite single     # one suite only
  python tools/conversation_injector.py --mode offline     # no server needed
  python tools/conversation_injector.py --list             # list available suites
  python tools/conversation_injector.py --dry-run          # show what would be sent

Each conversation creates its own channel (test-<suite>-<timestamp>) so
results are isolated and easy to compare across runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# =====================================================================
# Metrics collection
# =====================================================================

@dataclass
class MessageResult:
    """Metrics captured for a single injected message."""

    suite: str
    channel_id: str
    message_id: str
    content_preview: str  # first 80 chars
    response_mode: str
    sent_at: float  # epoch
    status: str = "sent"  # sent, error
    error: str = ""


@dataclass
class SuiteResult:
    """Aggregated results for one test suite."""

    suite: str
    channel_id: str
    messages_sent: int = 0
    messages_failed: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    message_results: list[MessageResult] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.finished_at - self.started_at


@dataclass
class RunReport:
    """Full report for an injection run."""

    mode: str
    started_at: str
    suites: list[SuiteResult] = field(default_factory=list)

    @property
    def total_sent(self) -> int:
        return sum(s.messages_sent for s in self.suites)

    @property
    def total_failed(self) -> int:
        return sum(s.messages_failed for s in self.suites)

    def summary(self) -> str:
        lines = [
            f"\n{'=' * 60}",
            f"  Injection Report  ({self.mode} mode)",
            f"  {self.started_at}",
            f"{'=' * 60}",
        ]
        for s in self.suites:
            status = "[OK]" if s.messages_failed == 0 else "[!]"
            lines.append(
                f"  {status} {s.suite:<30} "
                f"sent={s.messages_sent} fail={s.messages_failed} "
                f"channel={s.channel_id} ({s.duration_s:.1f}s)"
            )
        lines.append(f"{'=' * 60}")
        lines.append(
            f"  Total: {self.total_sent} sent, {self.total_failed} failed"
        )
        lines.append(f"{'=' * 60}\n")
        return "\n".join(lines)


# =====================================================================
# Test conversation suites
# =====================================================================

@dataclass
class TestMessage:
    """A single message to inject."""

    sender: str
    content: str
    response_mode: str = "smarter"
    delay_after: float = 0.5  # seconds to wait after sending


@dataclass
class TestSuite:
    """A named collection of messages testing a specific behavior."""

    name: str
    description: str
    category: str  # single, reasoning, multi, routing, pipeline, loop, edge, shift
    messages: list[TestMessage] = field(default_factory=list)


def build_suites() -> list[TestSuite]:
    """Build all test conversation suites."""
    suites = []

    # -----------------------------------------------------------------
    # 1. Single-agent simple (Smart mode baseline)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="single-simple",
        description="Simple single-agent questions in smart mode",
        category="single",
        messages=[
            TestMessage(
                sender="user",
                content="@python_developer What's the difference between list.sort() and sorted()?",
                response_mode="smart",
            ),
            TestMessage(
                sender="user",
                content="@security_agent What is CORS in one sentence?",
                response_mode="smart",
            ),
            TestMessage(
                sender="user",
                content="@web_developer What does display: flex do?",
                response_mode="smart",
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 2. Single-agent reasoning (Smarter mode)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="single-reasoning",
        description="Reasoning questions requiring extended thinking",
        category="reasoning",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer Here's a function that leaks memory in a loop. "
                    "Find the bug:\n```python\n"
                    "def process(urls):\n"
                    "    data = []\n"
                    "    for url in urls:\n"
                    "        resp = requests.get(url)\n"
                    "        data.append(resp.json())\n"
                    "    return data\n```\n"
                    "What happens when urls has 10 million entries?"
                ),
                response_mode="smarter",
            ),
            TestMessage(
                sender="user",
                content=(
                    "@security_agent Review this auth flow: user sends password "
                    "in query string, server checks against DB with "
                    "`SELECT * FROM users WHERE pass='{input}'`. "
                    "List every vulnerability."
                ),
                response_mode="smarter",
            ),
            TestMessage(
                sender="user",
                content=(
                    "@coding_orchestrator We need to add WebSocket support to a "
                    "REST-only Flask app serving 50 concurrent users. "
                    "What's the migration plan? Consider backwards compatibility."
                ),
                response_mode="smarter",
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 3. Multi-agent mention (orchestrator priority + queue ordering)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="multi-agent",
        description="Multi-agent mentions testing queue priority",
        category="multi",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@coding_orchestrator @python_developer @security_agent "
                    "We need to add rate limiting to our API. "
                    "How should we approach this?"
                ),
                response_mode="smarter",
                delay_after=2.0,  # give agents time to queue
            ),
            TestMessage(
                sender="user",
                content=(
                    "@python_developer @web_developer "
                    "Should we use server-side rendering or client-side "
                    "rendering for our dashboard? Pros and cons."
                ),
                response_mode="smarter",
                delay_after=2.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 4. @all routing
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="all-routing",
        description="@all routes to orchestrator only",
        category="routing",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@all What's the biggest risk in deploying to "
                    "production on a Friday?"
                ),
                response_mode="smarter",
                delay_after=3.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 5. Smartest pipeline (3-phase)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="smartest-pipeline",
        description="Smartest mode: Qwen reason -> distill -> Claude",
        category="pipeline",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer Trace this code and tell me the exact "
                    "output, line by line:\n```python\n"
                    "def make_counter():\n"
                    "    count = 0\n"
                    "    def inc(n=1):\n"
                    "        nonlocal count\n"
                    "        count += n\n"
                    "        return count\n"
                    "    return inc\n\n"
                    "c1 = make_counter()\n"
                    "c2 = make_counter()\n"
                    "print(c1())      # ?\n"
                    "print(c1(5))     # ?\n"
                    "print(c2(10))    # ?\n"
                    "print(c1())      # ?\n"
                    "```"
                ),
                response_mode="smartest",
                delay_after=5.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "@security_agent Analyze this JWT implementation for "
                    "timing attacks and key management issues:\n"
                    "```python\n"
                    "import jwt, hmac\n"
                    "SECRET = 'mysecret123'\n"
                    "def verify(token):\n"
                    "    try:\n"
                    "        return jwt.decode(token, SECRET, algorithms=['HS256'])\n"
                    "    except:\n"
                    "        return None\n"
                    "def check_admin(token):\n"
                    "    data = verify(token)\n"
                    "    return data and data.get('role') == 'admin'\n"
                    "```"
                ),
                response_mode="smartest",
                delay_after=5.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 6. Smart vs Smarter comparison (same question, different modes)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="mode-comparison",
        description="Same question in smart vs smarter for A/B comparison",
        category="comparison",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer Explain Python's GIL. When does it "
                    "matter and when doesn't it? Give a concrete example "
                    "where threading beats asyncio and vice versa."
                ),
                response_mode="smart",
                delay_after=3.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "@python_developer Explain Python's GIL. When does it "
                    "matter and when doesn't it? Give a concrete example "
                    "where threading beats asyncio and vice versa."
                ),
                response_mode="smarter",
                delay_after=3.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 7. Chain/loop prevention (agents mentioning each other)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="chain-loop",
        description="Trigger agent-to-agent chaining, test depth limits",
        category="loop",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer @web_developer "
                    "Debate whether to use SSR or CSR for a real-time "
                    "analytics dashboard. Each of you should respond to "
                    "the other's points by @mentioning them."
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 8. Topic shift detection
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="topic-shift",
        description="Mid-conversation topic pivot to different agent",
        category="shift",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer How do I set up pytest fixtures "
                    "with async database connections?"
                ),
                response_mode="smarter",
                delay_after=3.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "Actually, forget that. @security_agent What are "
                    "the OWASP Top 10 for 2025? Just the list."
                ),
                response_mode="smart",
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 9. Edge cases
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="edge-cases",
        description="Empty mentions, unknown agents, long messages",
        category="edge",
        messages=[
            # Unknown agent
            TestMessage(
                sender="user",
                content="@nonexistent_agent Hello, do you exist?",
                response_mode="smart",
            ),
            # No mention at all (should not trigger routing)
            TestMessage(
                sender="user",
                content="This message has no mentions at all.",
                response_mode="smart",
            ),
            # Alias resolution
            TestMessage(
                sender="user",
                content="@py What's a list comprehension?",
                response_mode="smart",
            ),
            # Multiple aliases
            TestMessage(
                sender="user",
                content="@sec @pydev Review this: `eval(user_input)`",
                response_mode="smarter",
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 10. Collaboration: Handoff quality
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="collab-handoff",
        description="Agent A hands off to Agent B with sufficient context",
        category="collaboration",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@coding_orchestrator We have a Flask API that needs "
                    "input validation on all endpoints plus SQL injection "
                    "protection. Break this into tasks and delegate to the "
                    "right agents with @mentions."
                ),
                response_mode="smarter",
                delay_after=8.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 11. Collaboration: Contradiction detection
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="collab-contradiction",
        description="Two agents answer the same question -- check for contradictions",
        category="collaboration",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer Should we use SQLAlchemy ORM or "
                    "raw SQL queries for a high-throughput data pipeline "
                    "processing 100K rows/second?"
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "@coding_orchestrator Same question: SQLAlchemy ORM or "
                    "raw SQL for a high-throughput data pipeline at "
                    "100K rows/second? Don't ask others, give your own take."
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 12. Collaboration: Build-on-prior (sequential expertise)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="collab-build-on",
        description="Agent B should build on Agent A's response, not repeat it",
        category="collaboration",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer Design the data model for a "
                    "user authentication system with roles and permissions. "
                    "Show the classes/tables."
                ),
                response_mode="smarter",
                delay_after=8.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "@security_agent Review the data model that "
                    "@python_developer just proposed above. What security "
                    "gaps do you see? Don't redesign it from scratch -- "
                    "build on their work."
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 13. Collaboration: Role respect (stay in lane)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="collab-role-respect",
        description="Each agent should contribute from their expertise, not duplicate",
        category="collaboration",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer @security_agent @web_developer "
                    "We're building a file upload endpoint. Each of you: "
                    "give your TOP concern from YOUR domain only. "
                    "Don't overlap with each other."
                ),
                response_mode="smarter",
                delay_after=8.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 14. Collaboration: Orchestrator synthesis quality
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="collab-synthesis",
        description="Orchestrator synthesizes multiple agent inputs into a plan",
        category="collaboration",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer What's the fastest way to parse "
                    "10GB of JSON logs in Python?"
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "@security_agent What are the risks of processing "
                    "untrusted 10GB JSON log files?"
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "@coding_orchestrator Read the two responses above from "
                    "@python_developer and @security_agent. Synthesize them "
                    "into a single implementation plan that addresses both "
                    "performance and security. Reference their specific points."
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 15. Collaboration: Delegation accuracy
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="collab-delegation",
        description="Agent correctly identifies who to delegate to",
        category="collaboration",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@coding_orchestrator We need to: (1) write a Python "
                    "microservice, (2) add HTTPS and auth, (3) build a "
                    "React dashboard, (4) set up CI/CD. Who should handle "
                    "each part? @mention the right agents."
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 16. Collaboration: Consensus convergence
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="collab-consensus",
        description="Multi-turn discussion should converge, not spiral",
        category="collaboration",
        messages=[
            TestMessage(
                sender="user",
                content=(
                    "@python_developer @web_developer We need to choose "
                    "between REST and GraphQL for our internal API. "
                    "State your position clearly."
                ),
                response_mode="smarter",
                delay_after=8.0,
            ),
            TestMessage(
                sender="user",
                content=(
                    "@coding_orchestrator Based on the positions above, "
                    "what's the decision? Pick one and justify it. "
                    "Don't punt -- make the call."
                ),
                response_mode="smarter",
                delay_after=5.0,
            ),
        ],
    ))

    # -----------------------------------------------------------------
    # 17. DM channel auto-routing (no explicit mention needed)
    # -----------------------------------------------------------------
    suites.append(TestSuite(
        name="dm-auto-route",
        description="DM channels auto-inject agent as mention",
        category="routing",
        messages=[
            TestMessage(
                sender="user",
                content="What's the best way to structure a FastAPI project?",
                response_mode="smarter",
            ),
        ],
    ))

    return suites


# =====================================================================
# Injector backends
# =====================================================================

class LiveInjector:
    """Send messages via REST API to a running Cohort server."""

    def __init__(self, base_url: str = "http://localhost:5100"):
        self.base_url = base_url.rstrip("/")
        # Import lazily so offline mode works without requests
        import requests as _req
        self._session = _req.Session()

    def send(
        self,
        channel_id: str,
        sender: str,
        content: str,
        response_mode: str = "smarter",
    ) -> dict[str, Any]:
        """POST /api/send and return the JSON response."""
        resp = self._session.post(
            f"{self.base_url}/api/send",
            json={
                "channel": channel_id,
                "sender": sender,
                "message": content,
                "response_mode": response_mode,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def health_check(self) -> bool:
        """Check if the server is reachable."""
        try:
            resp = self._session.get(f"{self.base_url}/api/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False


class OfflineInjector:
    """Write messages directly to Cohort data files (no server needed)."""

    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        # Lazy import
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from cohort.chat import ChatManager
        from cohort.registry import JsonFileStorage

        self.storage = JsonFileStorage(self.data_dir)
        self.chat = ChatManager(self.storage)

    def send(
        self,
        channel_id: str,
        sender: str,
        content: str,
        response_mode: str = "smarter",
    ) -> dict[str, Any]:
        """Post message directly via ChatManager (no agent routing)."""
        # Auto-create channel
        if self.chat.get_channel(channel_id) is None:
            self.chat.create_channel(
                name=channel_id,
                description=f"Test injection: {channel_id}",
            )
        msg = self.chat.post_message(
            channel_id=channel_id,
            sender=sender,
            content=content,
        )
        return {"success": True, "message_id": msg.id}

    def health_check(self) -> bool:
        return self.data_dir.exists()


class DryRunInjector:
    """Print messages instead of sending them."""

    _counter = 0

    def send(
        self,
        channel_id: str,
        sender: str,
        content: str,
        response_mode: str = "smarter",
    ) -> dict[str, Any]:
        DryRunInjector._counter += 1
        preview = content[:100].replace("\n", " ")
        print(f"  [{self._counter}] #{channel_id} ({response_mode}) {sender}: {preview}...")
        return {"success": True, "message_id": f"dry-{self._counter}"}

    def health_check(self) -> bool:
        return True


# =====================================================================
# Response collector & collaboration scorer
# =====================================================================

# Collaboration dimensions scored per-channel after responses arrive
COLLAB_DIMENSIONS = {
    "handoff_quality": "Does the delegating agent provide enough context for the next agent?",
    "contradiction": "Do agents contradict each other on factual claims?",
    "build_on_prior": "Does a later agent reference/extend the earlier agent's work?",
    "role_respect": "Does each agent stay within its domain expertise?",
    "synthesis": "Does the orchestrator synthesize (not just summarize) inputs?",
    "delegation_accuracy": "Are tasks delegated to the correct specialist agent?",
    "convergence": "Does the discussion reach a decision vs. spiral endlessly?",
}


@dataclass
class CollabScore:
    """Score for one collaboration dimension in one channel."""

    dimension: str
    score: float  # 0.0 - 1.0
    evidence: str  # why this score
    agents_involved: list[str] = field(default_factory=list)


@dataclass
class ChannelAnalysis:
    """Full collaboration analysis for one test channel."""

    channel_id: str
    suite: str
    total_messages: int = 0
    user_messages: int = 0
    agent_messages: int = 0
    agents_responded: list[str] = field(default_factory=list)
    avg_response_length: float = 0.0
    collab_scores: list[CollabScore] = field(default_factory=list)
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


class ResponseCollector:
    """Fetch and analyze agent responses from a Cohort server."""

    def __init__(self, base_url: str = "http://localhost:5100"):
        self.base_url = base_url.rstrip("/")
        import requests as _req
        self._session = _req.Session()

    def collect_channel(self, channel_id: str, suite_name: str) -> ChannelAnalysis:
        """Fetch all messages from a channel and analyze collaboration."""
        analysis = ChannelAnalysis(channel_id=channel_id, suite=suite_name)

        try:
            resp = self._session.get(
                f"{self.base_url}/api/channels/{channel_id}/messages",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            messages = data.get("messages", [])
        except Exception as exc:
            logger.error("[X] Failed to collect %s: %s", channel_id, exc)
            return analysis

        analysis.total_messages = len(messages)
        analysis.raw_messages = messages

        agent_msgs = []
        for msg in messages:
            sender = msg.get("sender", "")
            if sender in ("user", "system", "admin", "human"):
                analysis.user_messages += 1
            else:
                analysis.agent_messages += 1
                agent_msgs.append(msg)
                if sender not in analysis.agents_responded:
                    analysis.agents_responded.append(sender)

        if agent_msgs:
            lengths = [len(m.get("content", "")) for m in agent_msgs]
            analysis.avg_response_length = sum(lengths) / len(lengths)

        # Score collaboration dimensions based on suite category
        analysis.collab_scores = self._score_collaboration(
            suite_name, messages, agent_msgs, analysis.agents_responded,
        )

        return analysis

    def _score_collaboration(
        self,
        suite_name: str,
        all_messages: list[dict],
        agent_messages: list[dict],
        agents: list[str],
    ) -> list[CollabScore]:
        """Score collaboration dimensions using heuristics.

        These are lightweight text-based checks. For deeper analysis,
        pipe the raw_messages through an LLM judge.
        """
        scores: list[CollabScore] = []

        if not agent_messages:
            return scores

        agent_contents = [m.get("content", "") for m in agent_messages]
        all_content = "\n".join(agent_contents).lower()

        # --- Handoff quality: does the delegator @mention others with context? ---
        if "handoff" in suite_name or "delegation" in suite_name:
            mention_count = sum(
                c.count("@") for c in agent_contents
            )
            # Good handoff = mentions + multi-sentence context around them
            has_mentions = mention_count >= 2
            avg_len = sum(len(c) for c in agent_contents) / max(len(agent_contents), 1)
            context_rich = avg_len > 200
            score = 0.0
            if has_mentions and context_rich:
                score = 1.0
            elif has_mentions:
                score = 0.6
            elif context_rich:
                score = 0.3
            scores.append(CollabScore(
                dimension="handoff_quality",
                score=score,
                evidence=f"{mention_count} @mentions, avg response {avg_len:.0f} chars",
                agents_involved=agents,
            ))

        # --- Contradiction: do agents give conflicting recommendations? ---
        if "contradiction" in suite_name:
            # Simple: check if one says "use X" and another says "avoid X"
            recommendations = []
            for content in agent_contents:
                cl = content.lower()
                if "recommend" in cl or "should use" in cl or "prefer" in cl:
                    recommendations.append(cl)
            if len(recommendations) >= 2:
                # Check for opposing signals
                conflict_pairs = [
                    ("orm", "raw sql"), ("sqlalchemy", "raw"),
                    ("yes", "no"), ("avoid", "recommend"),
                ]
                conflicts = 0
                for a, b in conflict_pairs:
                    if (a in recommendations[0] and b in recommendations[1]) or \
                       (b in recommendations[0] and a in recommendations[1]):
                        conflicts += 1
                score = 0.0 if conflicts > 0 else 1.0
                scores.append(CollabScore(
                    dimension="contradiction",
                    score=score,
                    evidence=f"{conflicts} conflicting recommendation pairs detected",
                    agents_involved=agents,
                ))
            else:
                scores.append(CollabScore(
                    dimension="contradiction",
                    score=0.5,
                    evidence="Could not extract clear recommendations to compare",
                    agents_involved=agents,
                ))

        # --- Build-on-prior: does agent B reference agent A's work? ---
        if "build" in suite_name:
            if len(agent_messages) >= 2:
                first_agent = agent_messages[0].get("sender", "")
                later_contents = " ".join(
                    m.get("content", "") for m in agent_messages[1:]
                ).lower()
                # Check for references to first agent or their concepts
                references_agent = first_agent.lower() in later_contents or \
                    f"@{first_agent}" in later_contents
                # Check for building language
                build_phrases = [
                    "building on", "as mentioned", "extending",
                    "in addition to", "complements", "above",
                    "proposed", "their design", "the model",
                ]
                builds = sum(1 for p in build_phrases if p in later_contents)
                score = min(1.0, (0.5 if references_agent else 0.0) + builds * 0.15)
                scores.append(CollabScore(
                    dimension="build_on_prior",
                    score=score,
                    evidence=f"References first agent: {references_agent}, "
                             f"{builds} build-on phrases found",
                    agents_involved=agents,
                ))

        # --- Role respect: check for domain-appropriate content ---
        if "role" in suite_name:
            domain_keywords = {
                "python_developer": ["python", "code", "function", "class", "module", "import"],
                "security_agent": ["security", "vulnerability", "auth", "injection", "xss", "cors"],
                "web_developer": ["html", "css", "javascript", "frontend", "react", "dom", "ui"],
                "coding_orchestrator": ["plan", "task", "delegate", "coordinate", "phase", "sprint"],
            }
            overlap_count = 0
            for i, msg_a in enumerate(agent_messages):
                sender_a = msg_a.get("sender", "")
                content_a = msg_a.get("content", "").lower()
                for msg_b in agent_messages[i + 1:]:
                    sender_b = msg_b.get("sender", "")
                    if sender_a == sender_b:
                        continue
                    content_b = msg_b.get("content", "").lower()
                    # Count shared domain keywords that belong to the other's domain
                    kw_a = domain_keywords.get(sender_a, [])
                    kw_b = domain_keywords.get(sender_b, [])
                    # B using A's keywords = stepping on A's role
                    overlap = sum(1 for kw in kw_a if kw in content_b and kw not in kw_b)
                    overlap_count += overlap
            score = max(0.0, 1.0 - overlap_count * 0.15)
            scores.append(CollabScore(
                dimension="role_respect",
                score=score,
                evidence=f"{overlap_count} cross-domain keyword overlaps",
                agents_involved=agents,
            ))

        # --- Synthesis: does orchestrator reference multiple inputs? ---
        if "synthesis" in suite_name or "consensus" in suite_name:
            orch_msgs = [
                m for m in agent_messages
                if "orchestrator" in m.get("sender", "")
            ]
            if orch_msgs:
                orch_content = " ".join(m.get("content", "") for m in orch_msgs).lower()
                other_agents = [a for a in agents if "orchestrator" not in a]
                refs = sum(1 for a in other_agents if a.lower() in orch_content or f"@{a}" in orch_content)
                synthesis_phrases = [
                    "combining", "both", "integrating", "taking into account",
                    "balancing", "considering", "synthesis", "decision",
                    "recommend", "conclusion", "plan",
                ]
                synth_hits = sum(1 for p in synthesis_phrases if p in orch_content)
                ref_score = min(1.0, refs / max(len(other_agents), 1))
                synth_score = min(1.0, synth_hits * 0.2)
                score = (ref_score + synth_score) / 2
                scores.append(CollabScore(
                    dimension="synthesis",
                    score=score,
                    evidence=f"References {refs}/{len(other_agents)} agents, "
                             f"{synth_hits} synthesis phrases",
                    agents_involved=agents,
                ))

        # --- Delegation accuracy: are the right agents picked? ---
        if "delegation" in suite_name:
            expected_mappings = {
                "python": ["python_developer", "py"],
                "security": ["security_agent", "sec"],
                "react": ["web_developer", "frontend"],
                "ci/cd": ["devops_agent", "devops"],
                "https": ["security_agent", "sec"],
                "auth": ["security_agent", "sec"],
                "dashboard": ["web_developer", "frontend"],
                "microservice": ["python_developer", "py"],
            }
            orch_content = " ".join(
                m.get("content", "") for m in agent_messages
                if "orchestrator" in m.get("sender", "")
            ).lower()
            correct = 0
            total = 0
            for task_kw, valid_agents in expected_mappings.items():
                if task_kw in orch_content:
                    total += 1
                    if any(a in orch_content for a in valid_agents):
                        correct += 1
            score = correct / max(total, 1)
            scores.append(CollabScore(
                dimension="delegation_accuracy",
                score=score,
                evidence=f"{correct}/{total} tasks matched to correct agents",
                agents_involved=agents,
            ))

        # --- Convergence: does discussion end with a decision? ---
        if "consensus" in suite_name:
            last_msg = agent_messages[-1].get("content", "").lower() if agent_messages else ""
            decision_signals = [
                "decision", "we'll go with", "recommend", "the answer is",
                "conclusion", "final", "pick", "choose", "going with",
                "selected", "our approach",
            ]
            hedging_signals = [
                "it depends", "hard to say", "either way", "both are valid",
                "up to you", "no clear winner", "more discussion needed",
            ]
            decision_hits = sum(1 for s in decision_signals if s in last_msg)
            hedge_hits = sum(1 for s in hedging_signals if s in last_msg)
            score = min(1.0, decision_hits * 0.25) - hedge_hits * 0.2
            score = max(0.0, min(1.0, score))
            scores.append(CollabScore(
                dimension="convergence",
                score=score,
                evidence=f"{decision_hits} decision signals, {hedge_hits} hedging signals",
                agents_involved=agents,
            ))

        return scores

    def print_analysis(self, analyses: list[ChannelAnalysis]) -> None:
        """Print a formatted collaboration report."""
        collab_analyses = [a for a in analyses if a.collab_scores]
        if not collab_analyses:
            print("\n  [*] No collaboration suites had agent responses to analyze.")
            print("      Wait for agents to respond, then run --collect again.\n")
            return

        print(f"\n{'=' * 65}")
        print("  Collaboration Analysis")
        print(f"{'=' * 65}")

        for analysis in analyses:
            print(f"\n  --- {analysis.suite} (#{analysis.channel_id}) ---")
            print(f"  Messages: {analysis.total_messages} total "
                  f"({analysis.user_messages} user, {analysis.agent_messages} agent)")
            print(f"  Agents responded: {', '.join(analysis.agents_responded) or 'none yet'}")
            if analysis.avg_response_length:
                print(f"  Avg response length: {analysis.avg_response_length:.0f} chars")

            if analysis.collab_scores:
                print()
                for cs in analysis.collab_scores:
                    bar = "#" * int(cs.score * 10) + "." * (10 - int(cs.score * 10))
                    label = "[OK]" if cs.score >= 0.7 else "[!]" if cs.score >= 0.4 else "[X]"
                    print(f"    {label} {cs.dimension:<25} [{bar}] {cs.score:.0%}")
                    print(f"        {cs.evidence}")
            elif analysis.agent_messages == 0:
                print("    [*] No agent responses yet -- waiting for processing")

        # Overall summary
        all_scores = [
            cs for a in collab_analyses for cs in a.collab_scores
        ]
        if all_scores:
            avg = sum(cs.score for cs in all_scores) / len(all_scores)
            by_dim: dict[str, list[float]] = {}
            for cs in all_scores:
                by_dim.setdefault(cs.dimension, []).append(cs.score)

            print(f"\n{'=' * 65}")
            print(f"  Overall Collaboration Score: {avg:.0%}")
            print(f"{'=' * 65}")
            for dim, vals in sorted(by_dim.items()):
                dim_avg = sum(vals) / len(vals)
                label = "[OK]" if dim_avg >= 0.7 else "[!]" if dim_avg >= 0.4 else "[X]"
                print(f"    {label} {dim:<25} {dim_avg:.0%}")
            print()


# =====================================================================
# Runner
# =====================================================================

def run_suite(
    suite: TestSuite,
    injector: Any,
    channel_prefix: str,
    dm_mode: bool = False,
) -> SuiteResult:
    """Run a single test suite and return results."""
    ts = datetime.now().strftime("%H%M%S")

    # DM suites get a dm- prefixed channel
    if dm_mode:
        channel_id = f"dm-python_developer-test-{ts}"
    else:
        channel_id = f"{channel_prefix}-{suite.name}-{ts}"

    result = SuiteResult(
        suite=suite.name,
        channel_id=channel_id,
        started_at=time.time(),
    )

    for msg in suite.messages:
        try:
            resp = injector.send(
                channel_id=channel_id,
                sender=msg.sender,
                content=msg.content,
                response_mode=msg.response_mode,
            )
            msg_result = MessageResult(
                suite=suite.name,
                channel_id=channel_id,
                message_id=resp.get("message_id", "unknown"),
                content_preview=msg.content[:80],
                response_mode=msg.response_mode,
                sent_at=time.time(),
                status="sent",
            )
            result.messages_sent += 1
        except Exception as exc:
            msg_result = MessageResult(
                suite=suite.name,
                channel_id=channel_id,
                message_id="",
                content_preview=msg.content[:80],
                response_mode=msg.response_mode,
                sent_at=time.time(),
                status="error",
                error=str(exc),
            )
            result.messages_failed += 1
            logger.error("[X] Failed to send: %s", exc)

        result.message_results.append(msg_result)

        if msg.delay_after > 0:
            time.sleep(msg.delay_after)

    result.finished_at = time.time()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inject starter conversations into Cohort for testing",
    )
    parser.add_argument(
        "--mode",
        choices=["live", "offline", "dry-run"],
        default="live",
        help="Injection mode (default: live)",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:5100",
        help="Cohort server URL (live mode only)",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Cohort data directory (offline mode only)",
    )
    parser.add_argument(
        "--suite",
        help="Run only this suite (by name). Use --list to see names.",
    )
    parser.add_argument(
        "--category",
        help="Run only suites in this category",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available suites and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent without sending",
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help="After injection, wait and collect agent responses for collaboration analysis",
    )
    parser.add_argument(
        "--collect-only",
        metavar="REPORT_JSON",
        help="Skip injection; analyze channels from a previous --output report",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=60,
        help="Seconds to wait for agent responses before collecting (default: 60)",
    )
    parser.add_argument(
        "--output",
        help="Write JSON report to this file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    all_suites = build_suites()

    # --collect-only: analyze a previous run's channels
    if args.collect_only:
        prev_report = json.loads(Path(args.collect_only).read_text(encoding="utf-8"))
        collector = ResponseCollector(args.url)
        analyses = []
        for suite_data in prev_report.get("suites", []):
            ch_id = suite_data["channel_id"]
            suite_name = suite_data["suite"]
            print(f"  [...] Collecting #{ch_id} ({suite_name})")
            analysis = collector.collect_channel(ch_id, suite_name)
            analyses.append(analysis)
        collector.print_analysis(analyses)
        return

    # --list
    if args.list:
        print(f"\nAvailable suites ({len(all_suites)}):\n")
        for s in all_suites:
            print(f"  {s.name:<25} [{s.category}]  {s.description}")
        print()
        return

    # Select suites
    suites = all_suites
    if args.suite:
        suites = [s for s in all_suites if s.name == args.suite]
        if not suites:
            print(f"[X] Unknown suite: {args.suite}")
            print(f"    Available: {', '.join(s.name for s in all_suites)}")
            sys.exit(1)
    elif args.category:
        suites = [s for s in all_suites if s.category == args.category]
        if not suites:
            print(f"[X] No suites in category: {args.category}")
            sys.exit(1)

    # Build injector
    if args.dry_run:
        injector = DryRunInjector()
        mode = "dry-run"
    elif args.mode == "live":
        injector = LiveInjector(args.url)
        mode = "live"
    elif args.mode == "offline":
        injector = OfflineInjector(args.data_dir)
        mode = "offline"
    else:
        injector = DryRunInjector()
        mode = "dry-run"

    # Health check
    if not injector.health_check():
        if mode == "live":
            print(f"[X] Cannot reach Cohort server at {args.url}")
            print("    Start it with: python -m cohort.server")
            print("    Or use --mode offline / --dry-run")
        else:
            print("[X] Health check failed")
        sys.exit(1)

    # Run
    report = RunReport(
        mode=mode,
        started_at=datetime.now().isoformat(),
    )

    channel_prefix = f"test-{datetime.now().strftime('%m%d')}"

    print(f"\n[>>] Injecting {sum(len(s.messages) for s in suites)} messages "
          f"across {len(suites)} suites ({mode} mode)\n")

    for suite in suites:
        dm_mode = suite.name == "dm-auto-route"
        print(f"  [...] {suite.name}: {suite.description}")

        result = run_suite(suite, injector, channel_prefix, dm_mode=dm_mode)
        report.suites.append(result)

        status = "[OK]" if result.messages_failed == 0 else "[!]"
        print(f"  {status} {result.messages_sent} sent, "
              f"{result.messages_failed} failed -> #{result.channel_id}")

    # Summary
    print(report.summary())

    # Save report
    if args.output:
        out_path = Path(args.output)
        report_data = {
            "mode": report.mode,
            "started_at": report.started_at,
            "total_sent": report.total_sent,
            "total_failed": report.total_failed,
            "suites": [
                {
                    "suite": s.suite,
                    "channel_id": s.channel_id,
                    "messages_sent": s.messages_sent,
                    "messages_failed": s.messages_failed,
                    "duration_s": s.duration_s,
                    "messages": [asdict(m) for m in s.message_results],
                }
                for s in report.suites
            ],
        }
        out_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
        print(f"[OK] Report saved to {out_path}")

    # Collect and analyze collaboration
    if args.collect and mode == "live":
        print(f"[...] Waiting {args.wait}s for agent responses...")
        time.sleep(args.wait)

        collector = ResponseCollector(args.url)
        analyses = []
        for suite_result in report.suites:
            print(f"  [...] Collecting #{suite_result.channel_id} ({suite_result.suite})")
            analysis = collector.collect_channel(
                suite_result.channel_id, suite_result.suite,
            )
            analyses.append(analysis)

        collector.print_analysis(analyses)

        # Append collaboration scores to the output report
        if args.output:
            out_path = Path(args.output)
            report_data = json.loads(out_path.read_text(encoding="utf-8"))
            report_data["collaboration"] = [
                {
                    "channel_id": a.channel_id,
                    "suite": a.suite,
                    "total_messages": a.total_messages,
                    "agent_messages": a.agent_messages,
                    "agents_responded": a.agents_responded,
                    "avg_response_length": a.avg_response_length,
                    "scores": [
                        {
                            "dimension": cs.dimension,
                            "score": cs.score,
                            "evidence": cs.evidence,
                        }
                        for cs in a.collab_scores
                    ],
                }
                for a in analyses
            ]
            out_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
            print(f"[OK] Collaboration analysis appended to {out_path}")

    if report.total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
