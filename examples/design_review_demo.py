#!/usr/bin/env python3
"""design_review_demo.py -- Multi-agent design review via Cohort CLI.

Demonstrates a realistic 4-agent conversation where Cohort's scoring
engine decides who should speak, when agents should stay silent, and
how topic shifts re-engage different expertise.

Runs entirely via the CLI (no HTTP server). Any language that can shell
out to ``python -m cohort`` gets the same intelligence.

Usage::

    python examples/design_review_demo.py

What you'll see:

1. DISCOVER phase  -- architect + researcher dominate
2. EXECUTE phase   -- developer gets promoted, architect steps back
3. VALIDATE phase  -- tester gets called in, developer gated out
4. Topic shift     -- stakeholder re-evaluation mid-conversation
5. Direct question -- dormant agent pulled back in via @mention
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path


# =====================================================================
# Config
# =====================================================================

CONV_FILE = "demo_conversation.jsonl"
AGENTS_FILE = "demo_agents.json"
CHANNEL = "api-redesign"

AGENTS = {
    "architect": {
        "triggers": ["api", "design", "architecture", "endpoint", "schema", "rest"],
        "capabilities": ["backend architecture", "REST API design", "system design"],
        "domain_expertise": ["microservices", "api gateway"],
    },
    "developer": {
        "triggers": ["implement", "code", "python", "module", "function", "build"],
        "capabilities": ["python backend", "fastapi", "sqlalchemy"],
        "domain_expertise": ["python", "web frameworks"],
    },
    "tester": {
        "triggers": ["test", "qa", "validation", "coverage", "edge", "regression"],
        "capabilities": ["test strategy", "integration testing", "load testing"],
        "domain_expertise": ["pytest", "test automation"],
    },
    "researcher": {
        "triggers": ["research", "investigate", "existing", "history", "prior", "similar"],
        "capabilities": ["code archaeology", "prior art research"],
        "domain_expertise": ["documentation", "historical analysis"],
    },
}


# =====================================================================
# Helpers
# =====================================================================

def _cleanup() -> None:
    """Remove temp files from previous runs."""
    for f in [CONV_FILE, f"{Path(CONV_FILE).stem}_channels.json", AGENTS_FILE]:
        Path(f).unlink(missing_ok=True)


def _write_agents() -> None:
    Path(AGENTS_FILE).write_text(json.dumps(AGENTS, indent=2), encoding="utf-8")


def _cohort(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    """Run ``python -m cohort <args>``."""
    cmd = [sys.executable, "-m", "cohort", *args]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )


def say(sender: str, message: str) -> None:
    """Post a message and print it."""
    _cohort(
        "say",
        "--sender", sender,
        "--channel", CHANNEL,
        "--file", CONV_FILE,
        "--message", message,
        capture=True,
    )
    label = sender.ljust(20)
    print(f"  {label}| {message}")


def gate(agent: str) -> bool:
    """Check if an agent should speak. Returns True = speak."""
    result = _cohort(
        "gate",
        "--agent", agent,
        "--channel", CHANNEL,
        "--file", CONV_FILE,
        "--agents", AGENTS_FILE,
        "--format", "json",
        capture=True,
    )
    if result.returncode == 2:
        print(f"    [!] Agent '{agent}' not found")
        return False
    if result.returncode == 1 and not result.stdout.strip():
        # No messages yet
        return False
    try:
        data = json.loads(result.stdout)
        decision = "SPEAK" if data["speak"] else "SILENT"
        print(
            f"    gate({agent:20s}) -> {decision:6s}  "
            f"score={data['score']:.2f}  threshold={data['threshold']:.2f}"
        )
        return data["speak"]
    except (json.JSONDecodeError, KeyError):
        return result.returncode == 0


def next_speaker(top: int = 4) -> None:
    """Print ranked speaker recommendations."""
    result = _cohort(
        "next-speaker",
        "--channel", CHANNEL,
        "--file", CONV_FILE,
        "--agents", AGENTS_FILE,
        "--top", str(top),
        "--format", "json",
        capture=True,
    )
    if result.returncode != 0:
        print("    [!] No speaker data available")
        return
    rankings = json.loads(result.stdout)
    print("    Rank  Agent                  Score   Phase")
    print("    ----  --------------------   -----   --------")
    for i, entry in enumerate(rankings, 1):
        agent_id = entry["agent_id"]
        score = entry["score"]
        phase = entry.get("phase", "?")
        bar = "#" * int(score * 20)
        print(f"    {i}.    {agent_id:20s}   {score:.2f}    {phase:8s}  {bar}")


def divider(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


# =====================================================================
# Scenario
# =====================================================================

def main() -> None:
    _cleanup()
    _write_agents()

    print(textwrap.dedent("""\
    ===================================================================
      Cohort Design Review Demo
      4 agents, 1 JSONL file, zero infrastructure
    ===================================================================
    """))

    # -----------------------------------------------------------------
    # Phase 1: DISCOVER -- research the existing API
    # -----------------------------------------------------------------
    divider("PHASE 1: DISCOVER -- Research the existing API")

    say("architect",
        "Let's investigate the existing /users endpoint before we redesign it. "
        "We need to research what similar APIs do for pagination.")

    print("\n  -- Who should speak? --")
    next_speaker()

    print("\n  -- Gating each agent --")
    gate("researcher")
    gate("developer")
    gate("tester")

    print()
    say("researcher",
        "Investigated 5 similar REST APIs. All use cursor-based pagination "
        "for large datasets. The existing endpoint returns max 100 records "
        "with no pagination at all -- that's the gap.")

    print("\n  -- Updated rankings after research --")
    next_speaker()

    # -----------------------------------------------------------------
    # Phase 2: PLAN -- design the new API
    # -----------------------------------------------------------------
    divider("PHASE 2: PLAN -- Design the new pagination approach")

    say("architect",
        "Based on the research, cursor-based pagination is the way forward. "
        "Proposed design pattern: GET /users?cursor=<token>&limit=25. The schema "
        "needs a next_cursor field in the response envelope.")

    print("\n  -- Who should speak? --")
    next_speaker()

    print("\n  -- Gating: should the developer jump in yet? --")
    gate("developer")
    gate("tester")

    # -----------------------------------------------------------------
    # Phase 3: EXECUTE -- implement it
    # -----------------------------------------------------------------
    divider("PHASE 3: EXECUTE -- Build the implementation")

    say("architect",
        "The design is approved. @developer please implement the cursor-based "
        "pagination module in Python using FastAPI and SQLAlchemy.")

    print("\n  -- Direct mention + EXECUTE phase: developer should rank high --")
    next_speaker()

    print("\n  -- Gating with direct mention --")
    for agent in AGENTS:
        gate(agent)

    print()
    say("developer",
        "Implementing the pagination module now. Creating a cursor encoder, "
        "adding the query parameter to the FastAPI endpoint, and building the "
        "SQLAlchemy filter for cursor-based lookups.")

    say("developer",
        "Implementation complete. The module handles forward/backward cursors, "
        "empty result sets, and invalid cursor tokens with proper error codes.")

    print("\n  -- After developer speaks twice, novelty drops --")
    print("  -- Tester should now rank higher --")
    next_speaker()

    # -----------------------------------------------------------------
    # Phase 4: VALIDATE -- test it
    # -----------------------------------------------------------------
    divider("PHASE 4: VALIDATE -- Test and review")

    say("architect",
        "Time to test and validate the implementation. We need to check "
        "edge cases, verify the cursor encoding, and review quality.")

    print("\n  -- VALIDATE phase: tester should dominate --")
    next_speaker()

    print("\n  -- Gating: architect has contributed enough --")
    gate("architect")   # Should still pass (active stakeholder)
    gate("tester")      # Should pass strongly
    gate("researcher")  # Should be lower

    print()
    say("tester",
        "Validating edge cases: empty result sets, expired cursors, "
        "concurrent modifications, and load test with 10k records. "
        "Also need regression tests for the existing non-paginated callers.")

    # -----------------------------------------------------------------
    # Topic shift: security concern
    # -----------------------------------------------------------------
    divider("TOPIC SHIFT: Security concern surfaces")

    say("tester",
        "IMPORTANT: During testing found the cursor token is a base64-encoded "
        "database ID. This is an information disclosure vulnerability. We need "
        "to investigate the security implications and research encryption options.")

    print("\n  -- Topic shifted toward security + research --")
    print("  -- Researcher and architect should re-engage --")
    next_speaker()

    print("\n  -- Gating after topic shift --")
    for agent in AGENTS:
        gate(agent)

    print()
    say("researcher",
        "Good catch. Researched cursor encryption in similar APIs. "
        "Best practice is to use HMAC-signed opaque tokens. Found 3 "
        "libraries that handle this: itsdangerous, jwt, and fernet.")

    say("developer",
        "Will implement HMAC-signed cursors using itsdangerous. The existing "
        "cursor interface won't change -- just the encoding layer.")

    # -----------------------------------------------------------------
    # Wrap up
    # -----------------------------------------------------------------
    divider("FINAL: Conversation summary")

    print("  Messages exchanged:")
    result = _cohort(
        "next-speaker",
        "--channel", CHANNEL,
        "--file", CONV_FILE,
        "--agents", AGENTS_FILE,
        "--top", "4",
        "--format", "json",
        capture=True,
    )
    if result.returncode == 0:
        rankings = json.loads(result.stdout)
        print("\n  Final agent scores:")
        for entry in rankings:
            print(f"    {entry['agent_id']:20s}  score={entry['score']:.2f}")

    print(f"\n  Conversation file: {CONV_FILE}")
    print(f"  Agents file:       {AGENTS_FILE}")
    print(f"  Channel:           #{CHANNEL}")

    # Count messages in the file
    lines = Path(CONV_FILE).read_text(encoding="utf-8").strip().split("\n")
    user_msgs = [
        json.loads(line) for line in lines
        if json.loads(line).get("message_type") != "system"
    ]
    print(f"  Total messages:    {len(user_msgs)} (+ system messages)")

    print(textwrap.dedent(f"""
    ===================================================================
      Demo complete.

      To explore further:
        python -m cohort gate --agent developer --channel {CHANNEL} \\
            --file {CONV_FILE} --agents {AGENTS_FILE} --format json

        python -m cohort next-speaker --channel {CHANNEL} \\
            --file {CONV_FILE} --agents {AGENTS_FILE} --format json

      Clean up:
        rm -f {CONV_FILE} {Path(CONV_FILE).stem}_channels.json {AGENTS_FILE}
    ===================================================================
    """))


if __name__ == "__main__":
    main()
