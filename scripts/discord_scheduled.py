#!/usr/bin/env python3
"""
Scheduled Discord discussions — runs agent conversations on a cadence.

Usage:
    # Run all scheduled discussions for today
    python scripts/discord_scheduled.py

    # Run a specific channel's discussion
    python scripts/discord_scheduled.py --only ai-news

    # Dry run
    python scripts/discord_scheduled.py --dry-run

    # Cron example (run daily at 10am ET):
    # 0 10 * * * cd /path/to/cohort && python scripts/discord_scheduled.py

Schedule is defined in SCHEDULE below. Each entry specifies:
    - days: which days of the week to run (0=Mon, 6=Sun)
    - channel: Discord channel to post to
    - agents: which agents participate
    - topic_source: how to pick the topic
"""

import argparse
import random
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for cohort imports
sys.path.insert(0, str(Path(__file__).parent.parent))

SCHEDULE = [
    {
        "channel": "ai-news",
        "days": [0, 2, 4],  # Mon, Wed, Fri
        "agents": ["strategist", "developer", "researcher"],
        "topics": [
            "What's the most significant AI paper or release this week and why?",
            "How are open-source models closing the gap with proprietary ones?",
            "What AI startup news matters for developers this week?",
            "MCP and tool-use protocols — where is the ecosystem heading?",
            "Local inference vs cloud APIs — what changed this week?",
            "AI coding assistants — what's working and what's hype?",
            "Multi-agent systems in production — who's actually shipping?",
            "AI regulation and policy — what should developers know?",
        ],
    },
    {
        "channel": "cohort-features",
        "days": [1, 4],  # Tue, Thu
        "agents": ["developer", "technical_writer", "strategist"],
        "topics": [
            "How does Cohort's 5-dimension contribution scoring actually work?",
            "Zero dependencies in core — why it matters and how we pulled it off",
            "The meeting system: 18 subcommands for structured agent discussions",
            "Context distillation: how we cut token usage by 70% on escalated calls",
            "Loop prevention — detecting when agents are talking in circles",
            "Response tiers: Smart, Smarter, Smartest — when to use each",
            "MCP-native architecture — what it means and why we chose it",
            "Claude Code Channels integration — the 3-hour story",
            "Session isolation and why every channel gets its own context",
            "The approval pipeline — human gates in autonomous workflows",
        ],
    },
    {
        "channel": "code-review",
        "days": [3],  # Wed
        "agents": ["developer", "security", "qa_engineer"],
        "topics": [
            "Review: Cohort's orchestrator session management code",
            "Review: How the agent router scores and selects specialists",
            "Review: The MCP tool registration pattern",
            "Review: Channel bridge architecture and message flow",
            "Review: Contribution scoring algorithm implementation",
            "Review: Loop detection and prevention mechanisms",
        ],
    },
    {
        "channel": "strategy-room",
        "days": [1],  # Tue
        "agents": ["strategist", "marketing", "developer"],
        "topics": [
            "Cohort's positioning vs CrewAI, LangGraph, and AutoGen",
            "The local-first economics pitch — is it resonating?",
            "Developer community building strategy",
            "What should Cohort's next major feature be?",
            "Enterprise vs indie developer — which audience first?",
            "Open source sustainability — how do we keep this funded?",
        ],
    },
    {
        "channel": "security-briefs",
        "days": [2],  # Wed
        "agents": ["security", "developer", "qa_engineer"],
        "topics": [
            "Prompt injection risks in multi-agent systems",
            "Session isolation — are we doing enough?",
            "Supply chain security for zero-dependency packages",
            "Agent permission models — principle of least privilege",
            "Audit logging and observability in agent workflows",
            "The case for human approval gates on all external actions",
        ],
    },
    {
        "channel": "the-watercooler",
        "days": [0, 3, 5],  # Mon, Thu, Sat
        "agents": ["developer", "strategist", "creative_writer"],
        "topics": [
            "If you could add one impossible feature to Cohort, what would it be?",
            "Best and worst developer tools of the month",
            "The future of IDEs — will they all have AI agents?",
            "Hot take: most AI demos are misleading. Discuss.",
            "What's your unpopular opinion about software engineering?",
            "If agents could dream, what would Cohort's agents dream about?",
            "The art of good error messages — who does it best?",
            "Tabs vs spaces, but for AI: structured output vs free-form?",
        ],
    },
]


def get_todays_discussions(day_of_week: int | None = None) -> list[dict]:
    """Get all discussions scheduled for today."""
    if day_of_week is None:
        day_of_week = datetime.now().weekday()

    discussions = []
    for entry in SCHEDULE:
        if day_of_week in entry["days"]:
            # Pick a random topic (could be made sequential with state file)
            topic = random.choice(entry["topics"])
            discussions.append({
                "channel": entry["channel"],
                "agents": entry["agents"],
                "topic": topic,
            })
    return discussions


def main():
    parser = argparse.ArgumentParser(description="Run scheduled Discord discussions")
    parser.add_argument("--only", help="Only run for this channel")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    parser.add_argument("--day", type=int, help="Override day of week (0=Mon, 6=Sun)")
    args = parser.parse_args()

    discussions = get_todays_discussions(args.day)

    if args.only:
        discussions = [d for d in discussions if d["channel"] == args.only]

    if not discussions:
        print(f"No discussions scheduled for today (day {args.day or datetime.now().weekday()}).")
        return

    print(f"Scheduled discussions for today ({datetime.now().strftime('%A %Y-%m-%d')}):\n")
    for d in discussions:
        print(f"  #{d['channel']}: \"{d['topic']}\"")
        print(f"    Agents: {', '.join(d['agents'])}")
        print()

    if args.dry_run:
        print("(dry run — not executing)")
        return

    # Run each discussion through the bridge
    import subprocess
    bridge = Path(__file__).parent / "discord_bridge.py"

    for d in discussions:
        print(f"Running #{d['channel']}...")
        cmd = [
            sys.executable, str(bridge),
            "--topic", d["topic"],
            "--channel", d["channel"],
            "--agents", ",".join(d["agents"]),
            "--rounds", "3",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Error: {result.stderr}")
        else:
            print(result.stdout)
        print()


if __name__ == "__main__":
    main()
