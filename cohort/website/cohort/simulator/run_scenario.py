#!/usr/bin/env python3
"""Run real Cohort conversations to generate simulator scenario data.

Creates channels, posts seed messages with @mentions, waits for real
agent responses, captures scoring snapshots, and exports everything.

Usage:
    python run_scenario.py                          # Run first branch as test
    python run_scenario.py --branch compat+security # Run one branch
    python run_scenario.py --all                    # Run all 4 branches
    python run_scenario.py --dry-run                # Show plan without executing
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL = "http://localhost:5100"
AGENTS = ["cohort_orchestrator", "python_developer", "database_developer", "qa_agent", "security_agent"]
SCENARIO_DIR = Path(__file__).parent
OUTPUT_DIR = SCENARIO_DIR / "captured"

MODE_STANDARD = "smarter"
MODE_ESCALATED = "smartest"

# Max time to wait for agent responses after a seed (seconds)
RESPONSE_WAIT_TIMEOUT = 120
RESPONSE_POLL_INTERVAL = 3


# =====================================================================
# Conversation Scripts
# =====================================================================
# Each step is a "seed" message. After posting, we wait for all
# triggered agent responses to land, capturing them with full metadata.

OPENING = [
    {
        "sender": "claude_code",
        "message": (
            "We need to add pagination to our /users REST API endpoint. "
            "Right now it returns all records at once -- no limit, no cursor, no offset. "
            "This is a real design discussion. I want each of you to contribute based on your expertise. "
            "@cohort_orchestrator please coordinate. "
            "@python_developer @database_developer @qa_agent @security_agent"
        ),
        "mode": MODE_STANDARD,
        "expect_from": ["cohort_orchestrator", "python_developer", "database_developer", "qa_agent", "security_agent"],
        "note": "Initial seed -- DISCOVER phase, all agents tagged"
    },
]

# Choice 1 branches
COMPAT_CHOICE = [
    {
        "sender": "claude_code",
        "message": (
            "We're going with backward compatibility. Existing callers must continue working "
            "without any changes. Pagination should be opt-in -- if a caller doesn't pass pagination "
            "parameters, they get the current behavior. No breaking changes allowed. "
            "How does this constraint affect your approach? "
            "@python_developer @database_developer @cohort_orchestrator"
        ),
        "mode": MODE_STANDARD,
        "expect_from": ["python_developer", "database_developer", "cohort_orchestrator"],
        "note": "Choice 1: Backward compatibility -- research phase extends"
    },
    {
        "sender": "claude_code",
        "message": (
            "Good analysis. @python_developer go ahead and describe your implementation approach "
            "in detail -- cursor encoding, query strategy, error handling, response format. "
            "Think about the backward-compat constraint from @database_developer's points. "
            "@qa_agent @security_agent hold for now, your turn is coming."
        ),
        "mode": MODE_ESCALATED,
        "expect_from": ["python_developer"],
        "note": "EXECUTE transition -- python_developer implements with constraints"
    },
]

CLEAN_CHOICE = [
    {
        "sender": "claude_code",
        "message": (
            "We're doing a clean break. Create a /v2/users endpoint with cursor-based pagination "
            "as the default. The v1 endpoint stays frozen with a deprecation header. "
            "No backward compatibility constraints -- design it right from scratch. "
            "@python_developer @database_developer what's your approach?"
        ),
        "mode": MODE_STANDARD,
        "expect_from": ["python_developer", "database_developer"],
        "note": "Choice 1: Clean break -- developer gets fast-tracked"
    },
    {
        "sender": "claude_code",
        "message": (
            "@python_developer implement this. Describe your full approach -- "
            "cursor encoding, query strategy, error handling, response envelope format. "
            "Clean slate, design it properly."
        ),
        "mode": MODE_ESCALATED,
        "expect_from": ["python_developer"],
        "note": "EXECUTE -- python_developer implements with freedom"
    },
]

VALIDATE_PHASE = [
    {
        "sender": "claude_code",
        "message": (
            "@qa_agent the implementation approach is described above. "
            "Run your full validation analysis -- what edge cases, failure modes, "
            "boundary conditions, and regression risks do you see? Be thorough. "
            "Test the cursor encoding, pagination boundaries, empty results, "
            "concurrent modifications, and anything else that could break."
        ),
        "mode": MODE_ESCALATED,
        "expect_from": ["qa_agent"],
        "note": "VALIDATE phase -- QA agent does deep analysis"
    },
]

# Choice 2 branches
SECURITY_FINDING = [
    {
        "sender": "claude_code",
        "message": (
            "CRITICAL: I just realized something about the cursor tokens described above. "
            "If the cursor is a base64-encoded database row ID, anyone can decode it. "
            "That means users can enumerate record IDs, infer data volume, and predict cursor values. "
            "This is an information disclosure vulnerability. "
            "@security_agent assess this vulnerability and recommend a fix. "
            "@python_developer you'll need to update the cursor encoding. "
            "We need to investigate encryption options and research how other APIs handle opaque cursor tokens."
        ),
        "mode": MODE_ESCALATED,
        "expect_from": ["security_agent", "python_developer"],
        "note": "Choice 2: Security vulnerability -- topic shift triggers security_agent"
    },
    {
        "sender": "claude_code",
        "message": (
            "Good analysis. @python_developer implement the fix that @security_agent recommended. "
            "@cohort_orchestrator summarize what we decided and the final approach."
        ),
        "mode": MODE_STANDARD,
        "expect_from": ["python_developer", "cohort_orchestrator"],
        "note": "Resolution -- fix implemented, orchestrator summarizes"
    },
]

PERFORMANCE_FINDING = [
    {
        "sender": "claude_code",
        "message": (
            "CRITICAL: The cursor-based pagination as described will hit a performance cliff. "
            "If the implementation uses OFFSET internally, response times degrade linearly -- "
            "by page 400+, we're looking at 12+ second queries on our dataset size. "
            "This is a database-level problem. "
            "@database_developer what's the proper query strategy to avoid this? "
            "@python_developer you'll need to adjust the cursor implementation based on the fix."
        ),
        "mode": MODE_STANDARD,
        "expect_from": ["database_developer", "python_developer"],
        "note": "Choice 2: Performance cliff -- stays in EXECUTE, database_developer leads"
    },
    {
        "sender": "claude_code",
        "message": (
            "@python_developer update the cursor implementation with @database_developer's approach. "
            "@cohort_orchestrator summarize the final design."
        ),
        "mode": MODE_STANDARD,
        "expect_from": ["python_developer", "cohort_orchestrator"],
        "note": "Resolution -- fix implemented, orchestrator summarizes"
    },
]

# Build the 4 branch scripts
BRANCHES = {
    "compat+security":     OPENING + COMPAT_CHOICE + VALIDATE_PHASE + SECURITY_FINDING,
    "compat+performance":  OPENING + COMPAT_CHOICE + VALIDATE_PHASE + PERFORMANCE_FINDING,
    "clean+security":      OPENING + CLEAN_CHOICE  + VALIDATE_PHASE + SECURITY_FINDING,
    "clean+performance":   OPENING + CLEAN_CHOICE  + VALIDATE_PHASE + PERFORMANCE_FINDING,
}


# =====================================================================
# Runner
# =====================================================================

class ConversationRunner:
    """Runs a scripted conversation through live Cohort agents."""

    def __init__(self, branch_name: str, client: httpx.AsyncClient):
        self.branch_name = branch_name
        self.client = client
        ts = datetime.now().strftime("%H%M%S")
        self.channel_name = f"sim-{branch_name.replace('+', '-')}-{ts}"
        self.session_id = None
        self.captured_events = []
        self.known_message_ids = set()

    async def run(self):
        """Execute the full branch conversation."""
        print(f"\n{'='*70}")
        print(f"  Branch: {self.branch_name}")
        print(f"  Channel: #{self.channel_name}")
        print(f"{'='*70}\n")

        # 1. Create channel
        await self._create_channel()

        # 2. Start session for scoring
        await self._start_session()

        # 3. Snapshot initial channel state
        await self._snapshot_messages()

        # 4. Execute each seed step
        steps = BRANCHES[self.branch_name]
        for i, step in enumerate(steps):
            print(f"\n  Step {i+1}/{len(steps)}: {step.get('note', '')}")
            print(f"    Sender: {step['sender']}, Mode: {step.get('mode', MODE_STANDARD)}")
            print(f"    Expecting responses from: {step.get('expect_from', [])}")

            await self._execute_step(step)

        # 5. End session
        await self._end_session()

        # 6. Final channel capture
        all_messages = await self._get_all_messages()

        # 7. Save everything
        self._save(all_messages)

        agent_msgs = [m for m in all_messages if m.get("sender") not in ("claude_code", "system")]
        print(f"\n  [OK] Branch {self.branch_name} complete.")
        print(f"       Total messages: {len(all_messages)}")
        print(f"       Agent responses: {len(agent_msgs)}")
        senders = {}
        for m in agent_msgs:
            senders[m["sender"]] = senders.get(m["sender"], 0) + 1
        for agent, count in sorted(senders.items()):
            print(f"         {agent}: {count}")

    async def _create_channel(self):
        resp = await self.client.post(
            f"{BASE_URL}/api/channels",
            json={
                "name": self.channel_name,
                "description": f"Simulator scenario: API pagination redesign ({self.branch_name})",
                "members": AGENTS,
                "topic": "API pagination redesign"
            }
        )
        data = resp.json()
        print(f"    [OK] Channel #{self.channel_name} ready")

    async def _start_session(self):
        resp = await self.client.post(
            f"{BASE_URL}/api/sessions/start",
            json={
                "channel_id": self.channel_name,
                "topic": "API pagination redesign",
                "initial_agents": AGENTS,
                "max_turns": 40
            }
        )
        data = resp.json()
        if data.get("success"):
            self.session_id = data["session"]["session_id"]
            print(f"    [OK] Session started: {self.session_id[:16]}...")

    async def _execute_step(self, step):
        """Post a seed message and wait for agent responses."""
        expect_from = set(step.get("expect_from", []))
        mode = step.get("mode", MODE_STANDARD)

        # Snapshot message count before posting
        pre_messages = await self._get_all_messages()
        pre_count = len(pre_messages)
        self.known_message_ids = {m["id"] for m in pre_messages}

        # Post the seed
        t0 = time.monotonic()
        resp = await self.client.post(
            f"{BASE_URL}/api/send",
            json={
                "channel": self.channel_name,
                "sender": step["sender"],
                "message": step["message"],
                "response_mode": mode
            },
            timeout=30
        )
        seed_elapsed = time.monotonic() - t0
        seed_data = resp.json()
        print(f"    -> Seed posted ({int(seed_elapsed*1000)}ms)")

        # Capture seed event
        self.captured_events.append({
            "type": "seed",
            "sender": step["sender"],
            "message": step["message"],
            "mode": mode,
            "message_id": seed_data.get("message_id"),
            "note": step.get("note", ""),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Wait for agent responses
        if not expect_from:
            print("    -> No responses expected, moving on")
            await asyncio.sleep(2)
            return

        print(f"    -> Waiting for responses from {expect_from}...")
        responded = set()
        wait_start = time.monotonic()
        last_activity = wait_start

        while time.monotonic() - wait_start < RESPONSE_WAIT_TIMEOUT:
            await asyncio.sleep(RESPONSE_POLL_INTERVAL)

            current_messages = await self._get_all_messages()
            new_messages = [m for m in current_messages if m["id"] not in self.known_message_ids]

            for msg in new_messages:
                sender = msg.get("sender", "")
                if sender not in ("claude_code", "system") and msg["id"] not in self.known_message_ids:
                    self.known_message_ids.add(msg["id"])
                    responded.add(sender)
                    content_preview = msg.get("content", "")[:100]
                    print(f"    -> {sender} responded: {content_preview}...")
                    last_activity = time.monotonic()

                    # Capture response event
                    self.captured_events.append({
                        "type": "agent_response",
                        "sender": sender,
                        "message_id": msg["id"],
                        "content_length": len(msg.get("content", "")),
                        "timestamp": msg.get("timestamp", ""),
                        "metadata": msg.get("metadata", {}),
                    })

            # Check if all expected agents have responded
            if expect_from and expect_from.issubset(responded):
                print(f"    -> All expected agents responded: {responded}")
                break

            # If no new activity for 30s, assume we're done
            if time.monotonic() - last_activity > 30:
                missing = expect_from - responded
                if missing:
                    print(f"    -> Timeout waiting for: {missing}")
                break

            elapsed = int(time.monotonic() - wait_start)
            waiting_for = expect_from - responded
            if waiting_for and elapsed % 15 == 0:
                print(f"    -> {elapsed}s elapsed, waiting for: {waiting_for}")

        # Give a moment for any chain responses
        await asyncio.sleep(3)

        # Capture scoring snapshot after responses
        await self._capture_scoring()

    async def _capture_scoring(self):
        """Capture current scoring state from the session."""
        if not self.session_id:
            return

        try:
            resp = await self.client.get(
                f"{BASE_URL}/api/sessions/{self.session_id}/next-speaker",
                timeout=10
            )
            data = resp.json()
            if data.get("success") and data.get("recommendation"):
                rec = data["recommendation"]
                print(f"    -> Scoring: top={rec.get('recommended_speaker')} "
                      f"({rec.get('relevance_score', 0):.2f}), phase={rec.get('phase', '?')}")
                for s in rec.get("all_scores", []):
                    print(f"       {s['agent_id']:25s} {s.get('score', 0):.2f}  [{s.get('status', '?')}]")

                self.captured_events.append({
                    "type": "scoring",
                    "recommendation": rec,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
        except Exception as e:
            print(f"    -> Scoring capture failed: {e}")

        # Also get session status for stakeholder states
        try:
            resp = await self.client.get(
                f"{BASE_URL}/api/sessions/{self.session_id}/status",
                timeout=10
            )
            data = resp.json()
            if data.get("success"):
                participants = data.get("status", {}).get("active_participants", {})
                non_active = {k: v for k, v in participants.items() if v != "active_stakeholder"}
                if non_active:
                    print(f"    -> Non-active agents: {non_active}")
                    self.captured_events.append({
                        "type": "stakeholder_status",
                        "participants": participants,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
        except Exception:
            pass

    async def _get_all_messages(self):
        """Read all messages from the channel."""
        resp = await self.client.get(
            f"{BASE_URL}/api/messages",
            params={"channel": self.channel_name, "limit": 200},
            timeout=10
        )
        data = resp.json()
        return data.get("messages", [])

    async def _snapshot_messages(self):
        """Record initial message IDs so we can detect new ones."""
        messages = await self._get_all_messages()
        self.known_message_ids = {m["id"] for m in messages}

    async def _end_session(self):
        if not self.session_id:
            return
        try:
            resp = await self.client.post(
                f"{BASE_URL}/api/sessions/{self.session_id}/end",
                timeout=10
            )
            data = resp.json()
            if data.get("success"):
                summary = data.get("summary", {})
                print(f"\n    [OK] Session ended. Turns: {summary.get('total_turns', '?')}")
                self.captured_events.append({
                    "type": "session_end",
                    "summary": summary,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
        except Exception as e:
            print(f"    [!] Session end failed: {e}")

    def _save(self, all_messages):
        """Save captured data and raw messages."""
        OUTPUT_DIR.mkdir(exist_ok=True)

        # Save captured events (structured)
        events_file = OUTPUT_DIR / f"{self.branch_name.replace('+', '_')}.json"
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump({
                "branch": self.branch_name,
                "channel": self.channel_name,
                "agents": AGENTS,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "events": self.captured_events
            }, f, indent=2, ensure_ascii=False)
        print(f"    [OK] Events saved to {events_file}")

        # Save raw messages (for "Show Your Work")
        raw_file = OUTPUT_DIR / f"{self.branch_name.replace('+', '_')}_raw.json"
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump({
                "channel": self.channel_name,
                "branch": self.branch_name,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "messages": all_messages
            }, f, indent=2, ensure_ascii=False)
        print(f"    [OK] Raw messages saved to {raw_file}")


# =====================================================================
# Main
# =====================================================================

async def run_branches(branches: list[str]):
    """Run specified branches sequentially."""
    async with httpx.AsyncClient() as client:
        # Verify server
        try:
            resp = await client.get(f"{BASE_URL}/api/agents", timeout=5)
            agents = resp.json().get("agents", [])
            agent_ids = {a["agent_id"] for a in agents}
            missing = set(AGENTS) - agent_ids
            if missing:
                print(f"[!] Missing agents: {missing}")
                return
            print(f"[OK] Cohort server at {BASE_URL} -- {len(agents)} agents available")
            for a in AGENTS:
                print(f"     {a}")
        except Exception as e:
            print(f"[X] Cannot reach Cohort server at {BASE_URL}: {e}")
            return

        for branch in branches:
            if branch not in BRANCHES:
                print(f"[!] Unknown branch: {branch}")
                continue
            runner = ConversationRunner(branch, client)
            await runner.run()

    print(f"\n{'='*70}")
    print(f"  All branches complete. Output in: {OUTPUT_DIR}")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Run Cohort simulator scenarios")
    parser.add_argument("--branch", type=str, help="Run a specific branch (e.g., compat+security)")
    parser.add_argument("--all", action="store_true", help="Run all 4 branches")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    args = parser.parse_args()

    if args.dry_run:
        for name, steps in BRANCHES.items():
            print(f"\nBranch: {name} ({len(steps)} steps)")
            for i, step in enumerate(steps):
                expect = step.get("expect_from", [])
                mode = step.get("mode", MODE_STANDARD)
                print(f"  {i+1}. [{mode:8s}] {step.get('note', '')}")
                print(f"     Sender: {step['sender']}, Expect: {expect}")
        return

    if args.branch:
        branches = [args.branch]
    elif args.all:
        branches = list(BRANCHES.keys())
    else:
        branches = ["compat+security"]
        print("[*] Running first branch as test. Use --all for all 4 branches.\n")

    asyncio.run(run_branches(branches))


if __name__ == "__main__":
    main()
