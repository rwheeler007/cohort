"""Interactive CLI tutorial for Cohort.

Walks new users through creating channels, registering agents,
posting messages, and seeing the scoring engine in action --
all with plain-English narration.  Zero dependencies beyond
the cohort package itself.

Usage::

    python -m cohort tutorial
    cohort tutorial
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

# =====================================================================
# Display helpers
# =====================================================================

_WIDTH = 60


def _banner(text: str) -> None:
    print()
    print("=" * _WIDTH)
    print(f"  {text}")
    print("=" * _WIDTH)
    print()


def _step(num: int, total: int, title: str) -> None:
    print()
    print(f"Step {num} of {total}: {title}")
    print("-" * _WIDTH)
    print()


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _info(msg: str) -> None:
    print(f"  [*] {msg}")


def _explain(text: str) -> None:
    for line in textwrap.wrap(text, width=_WIDTH - 4):
        print(f"  {line}")
    print()


def _bar(score: float, width: int = 20) -> str:
    filled = int(score * width)
    return "[" + "=" * filled + " " * (width - filled) + "]"


def _pause(prompt: str = "Press Enter to continue...") -> bool:
    """Wait for user input. Returns False if user wants to quit."""
    try:
        response = input(f"\n  {prompt} ")
        return response.strip().lower() not in ("q", "quit", "exit")
    except (KeyboardInterrupt, EOFError):
        print()
        return False


# =====================================================================
# Tutorial steps
# =====================================================================

TOTAL_STEPS = 7


def _step_1_create_channel(chat):
    """Create the tutorial channel."""
    _step(1, TOTAL_STEPS, "Create a Channel")

    _explain(
        "Channels are where conversations happen. Every message "
        "belongs to a channel -- think of them like Slack channels "
        "or Discord rooms."
    )

    _info("Creating channel 'tutorial'...")
    channel = chat.create_channel(
        name="tutorial",
        description="Learning how Cohort works",
    )
    _ok(f"Channel created: #{channel.name}")
    _explain(
        "You just created a conversation space. In a real project "
        "you might have channels like #api-design, #security-review, "
        "or #sprint-planning."
    )


def _step_2_register_agents(agents_path: Path):
    """Write agent configs to a JSON file."""
    import json

    _step(2, TOTAL_STEPS, "Register Two Agents")

    _explain(
        "Agents are defined by their triggers (topic keywords), "
        "capabilities, and domain expertise. These fields drive "
        "Cohort's scoring engine -- they decide who should speak."
    )

    agents = {
        "backend_dev": {
            "triggers": ["api", "database", "python", "backend", "server"],
            "capabilities": [
                "Python development",
                "REST API design",
                "database queries",
            ],
            "domain_expertise": ["python", "fastapi", "postgresql"],
        },
        "qa_engineer": {
            "triggers": ["test", "quality", "bug", "validation", "coverage"],
            "capabilities": [
                "test strategy",
                "integration testing",
                "bug triage",
            ],
            "domain_expertise": ["pytest", "test automation", "CI/CD"],
        },
    }

    agents_path.write_text(json.dumps(agents, indent=2), encoding="utf-8")

    for agent_id, config in agents.items():
        _ok(f"Registered: {agent_id}")
        triggers = ", ".join(config["triggers"][:4])
        print(f"       triggers: {triggers}")
        caps = ", ".join(config["capabilities"][:2])
        print(f"       capabilities: {caps}")
        print()

    _explain(
        "Notice how backend_dev has triggers like 'api' and "
        "'database', while qa_engineer has 'test' and 'quality'. "
        "When a message contains these keywords, the matching "
        "agent scores higher."
    )


def _step_3_conversation(chat):
    """Post three messages to show keyword matching."""
    _step(3, TOTAL_STEPS, "Start a Conversation")

    _explain(
        "Let's post three messages. Watch how the topic keywords "
        "shift from backend development to testing."
    )

    messages_data = [
        (
            "user",
            "We need to build a new REST API for user management. "
            "What's the best approach for the database schema?",
            "api, database, backend --> matches backend_dev",
        ),
        (
            "backend_dev",
            "I'd recommend PostgreSQL with SQLAlchemy ORM. Start "
            "with a users table, add an auth_tokens table for "
            "session management, and use alembic for migrations.",
            "(agent responds with domain expertise)",
        ),
        (
            "user",
            "Good. Now we need to make sure this is thoroughly "
            "tested before launch. What's the test strategy?",
            "test, quality --> topic shifts to qa_engineer",
        ),
    ]

    for sender, content, annotation in messages_data:
        msg = chat.post_message(
            channel_id="tutorial",
            sender=sender,
            content=content,
        )
        label = sender.replace("_", " ").title()
        # Truncate for display
        short = content[:70] + "..." if len(content) > 70 else content
        print(f"  [{label}]: \"{short}\"")
        print(f"    ^ {annotation}")
        print()

    _explain(
        "The first message mentions 'REST API' and 'database' -- "
        "backend_dev territory. The third message shifts to "
        "'tested' and 'test strategy' -- qa_engineer territory. "
        "Let's see how the scoring engine handles this."
    )


def _step_4_scoring(chat, agents_path: Path):
    """Show the 5-dimension scoring engine in action."""
    from cohort.file_transport import load_agents_from_file
    from cohort.meeting import (
        calculate_composite_relevance,
        extract_keywords,
        initialize_meeting_context,
    )

    _step(4, TOTAL_STEPS, "See the Scoring Engine")

    agents = load_agents_from_file(str(agents_path))
    agent_ids = list(agents.keys())

    print("  Cohort scores agents across 5 dimensions:")
    print()
    print("    Domain expertise  (30%) -- keyword match to triggers")
    print("    Complementary     (25%) -- are partners active?")
    print("    Historical        (20%) -- past performance")
    print("    Phase alignment   (15%) -- right agent for current phase")
    print("    Data ownership    (10%) -- owns relevant data?")
    print()

    # --- Topic 1: Backend ---
    print()
    _info('Topic: "REST API database schema design"')
    print()

    meeting_ctx = initialize_meeting_context(agent_ids)
    topic_kw = extract_keywords(
        "REST API database schema design backend server"
    )
    meeting_ctx["current_topic"]["keywords"] = topic_kw
    messages = chat.get_channel_messages("tutorial", limit=15)

    scores_1 = []
    for agent_id, agent_config in agents.items():
        relevance = calculate_composite_relevance(
            agent_id=agent_id,
            meeting_context=meeting_ctx,
            agent_config=agent_config,
            recent_messages=messages,
        )
        scores_1.append((agent_id, relevance["composite_total"], relevance))

    scores_1.sort(key=lambda x: x[1], reverse=True)
    for agent_id, total, breakdown in scores_1:
        bar = _bar(total)
        print(f"    {agent_id:20s}  {total:.2f}  {bar}")
        # Show top 2 dimensions
        dims = {
            k: v for k, v in breakdown.items()
            if k not in ("composite_total", "detected_phase")
        }
        top_dims = sorted(dims.items(), key=lambda x: x[1], reverse=True)[:2]
        dim_str = ", ".join(f"{k}={v:.2f}" for k, v in top_dims)
        print(f"    {'':20s}         ({dim_str})")

    print()

    # --- Topic 2: Testing ---
    _info('Topic: "testing strategy and quality assurance"')
    print()

    meeting_ctx_2 = initialize_meeting_context(agent_ids)
    topic_kw_2 = extract_keywords(
        "testing strategy quality assurance validation coverage"
    )
    meeting_ctx_2["current_topic"]["keywords"] = topic_kw_2

    scores_2 = []
    for agent_id, agent_config in agents.items():
        relevance = calculate_composite_relevance(
            agent_id=agent_id,
            meeting_context=meeting_ctx_2,
            agent_config=agent_config,
            recent_messages=messages,
        )
        scores_2.append((agent_id, relevance["composite_total"]))

    scores_2.sort(key=lambda x: x[1], reverse=True)
    for agent_id, total in scores_2:
        bar = _bar(total)
        print(f"    {agent_id:20s}  {total:.2f}  {bar}")

    print()
    _explain(
        "The topic shift changed who's most relevant. "
        "This is exactly how Cohort decides who should speak "
        "next in a multi-agent discussion -- the agent whose "
        "expertise best matches the current topic gets the turn."
    )


def _step_5_gating(chat, agents_path: Path):
    """Show the gating decision (should agent speak?)."""
    from cohort.chat import Channel
    from cohort.file_transport import load_agents_from_file
    from cohort.meeting import (
        STAKEHOLDER_THRESHOLDS,
        calculate_contribution_score,
        extract_keywords,
        initialize_meeting_context,
        should_agent_speak,
    )

    _step(5, TOTAL_STEPS, "The Gating Decision")

    _explain(
        "Scoring is only half the story. Cohort also has a gating "
        "system that prevents agents from talking too much. As an "
        "agent speaks more, their threshold rises:"
    )
    print("    ACTIVE           threshold: 0.30  (easy to speak)")
    print("    APPROVED_SILENT  threshold: 0.70  (spoke recently)")
    print("    OBSERVER         threshold: 0.80  (watching)")
    print("    DORMANT          threshold: 1.00  (muted)")
    print()
    _explain(
        "A topic shift can re-engage a dormant agent if the new "
        "topic matches their expertise. Let's see gating in action."
    )

    agents = load_agents_from_file(str(agents_path))
    agent_ids = list(agents.keys())
    messages = chat.get_channel_messages("tutorial", limit=15)
    last_msg = messages[-1]

    meeting_ctx = initialize_meeting_context(agent_ids)
    recent_text = " ".join(m.content for m in messages[-5:])
    topic_kw = extract_keywords(recent_text)
    meeting_ctx["current_topic"]["keywords"] = topic_kw

    channel = chat.get_channel("tutorial")
    if channel is None:
        channel = Channel(
            id="tutorial", name="tutorial",
            description="", created_at="",
            meeting_context=meeting_ctx,
        )
    else:
        channel.meeting_context = meeting_ctx

    _info("Should each agent speak right now?\n")

    for agent_id, agent_config in agents.items():
        speak = should_agent_speak(
            agent_id, last_msg, channel, chat, agent_config,
        )
        score = calculate_contribution_score(
            agent_id, "[considering response]",
            meeting_ctx, agent_config, messages,
        )
        threshold = STAKEHOLDER_THRESHOLDS["active_stakeholder"]
        decision = "SPEAK" if speak else "SILENT"
        marker = "[OK]" if speak else "[--]"

        print(f"    {marker} {agent_id:20s}  "
              f"score={score:.2f}  threshold={threshold:.2f}  "
              f"--> {decision}")

    print()
    _explain(
        "The last message was about testing -- so qa_engineer "
        "scores higher and gets the green light. backend_dev "
        "might stay silent since the topic moved away from "
        "their expertise."
    )


def _step_6_orchestrator(chat, agents_path: Path):
    """Show the full orchestrator flow."""
    from cohort.file_transport import load_agents_from_file
    from cohort.orchestrator import Orchestrator

    _step(6, TOTAL_STEPS, "The Orchestrator")

    _explain(
        "In production, you don't call scoring functions directly. "
        "The Orchestrator manages sessions, tracks turns, detects "
        "topic shifts, and recommends the next speaker."
    )

    agents = load_agents_from_file(str(agents_path))
    orch = Orchestrator(chat, agents=agents)

    session = orch.start_session(
        "tutorial",
        "REST API design and testing",
        initial_agents=list(agents.keys()),
    )
    _ok(f"Session started: {session.session_id[:12]}...")
    print(f"       topic: {session.topic}")
    print(f"       agents: {', '.join(session.initial_agents)}")
    print()

    rec = orch.get_next_speaker(session.session_id)
    if rec:
        print(f"    Recommended speaker: {rec['recommended_speaker']}")
        print(f"    Relevance score:     {rec['relevance_score']:.2f}")
        print(f"    Detected phase:      {rec.get('phase', 'unknown')}")
        print(f"    Reason:              {rec.get('reason', '')[:60]}")

        if rec.get("all_scores"):
            print()
            print("    Full ranking:")
            for entry in rec["all_scores"][:3]:
                score = entry.get("score", 0)
                bar = _bar(score, width=15)
                print(f"      {entry['agent_id']:20s}  {score:.2f}  {bar}")

    print()
    _explain(
        "The Orchestrator combines scoring, gating, turn history, "
        "and topic detection into a single get_next_speaker() call. "
        "This is what powers Cohort's multi-agent discussions."
    )


def _step_7_wrapup():
    """Summary and next steps."""
    _step(7, TOTAL_STEPS, "What You Just Did")

    print("  You just experienced Cohort's core engine:\n")
    print("    1. Created a channel (conversation space)")
    print("    2. Registered agents with triggers + capabilities")
    print("    3. Posted messages where topic keywords drive scoring")
    print("    4. Saw the 5-dimension scoring engine rank agents")
    print("    5. Watched gating decide who should speak")
    print("    6. Used the Orchestrator for session management")
    print()
    print("  Everything ran on flat files -- no server, no cloud,")
    print("  no database, no external dependencies.")
    print()
    print("-" * _WIDTH)
    print()
    print("  What's next:")
    print()
    print("    Use as a library:")
    print("      from cohort.chat import ChatManager")
    print("      from cohort.orchestrator import Orchestrator")
    print()
    print("    Use the CLI:")
    print("      python -m cohort say --help")
    print("      python -m cohort gate --help")
    print("      python -m cohort next-speaker --help")
    print()
    print("    Set up local LLM (optional):")
    print("      python -m cohort setup")
    print()
    print("    Use with Claude Code:")
    print("      Clone the repo, open in Claude Code, say \"get started\"")
    print("      CLAUDE.md pre-loads Claude with full project knowledge")
    print()


# =====================================================================
# Main entry point
# =====================================================================

def run_tutorial() -> int:
    """Run the interactive tutorial. Returns exit code."""
    from cohort.chat import ChatManager
    from cohort.file_transport import JsonlFileStorage

    _banner("Cohort Interactive Tutorial")

    print("  Learn how Cohort's multi-agent scoring engine works")
    print("  by building a conversation from scratch.\n")
    print("  This takes about 3 minutes. Press Enter to advance")
    print("  each step, or type 'q' to quit anytime.\n")
    print("  All data is temporary -- nothing is saved to your project.")

    if not _pause():
        return 0

    # Use a temp directory so we don't touch user's data
    with tempfile.TemporaryDirectory(prefix="cohort_tutorial_") as tmpdir:
        tmp = Path(tmpdir)
        jsonl_path = tmp / "tutorial.jsonl"
        agents_path = tmp / "agents.json"

        storage = JsonlFileStorage(str(jsonl_path))
        chat = ChatManager(storage)

        try:
            _step_1_create_channel(chat)
            if not _pause():
                return 0

            _step_2_register_agents(agents_path)
            if not _pause():
                return 0

            _step_3_conversation(chat)
            if not _pause():
                return 0

            _step_4_scoring(chat, agents_path)
            if not _pause():
                return 0

            _step_5_gating(chat, agents_path)
            if not _pause():
                return 0

            _step_6_orchestrator(chat, agents_path)
            if not _pause():
                return 0

            _step_7_wrapup()

        except Exception as e:
            print(f"\n  [X] Error: {e}", file=sys.stderr)
            print("  Please report this at:")
            print("  https://github.com/rwheeler007/cohort/issues")
            return 1

    _banner("Tutorial Complete!")
    print("  Run 'python -m cohort setup' to configure local LLM")
    print("  or clone the repo and try it with Claude Code.\n")

    return 0
