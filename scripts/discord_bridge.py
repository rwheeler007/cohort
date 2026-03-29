#!/usr/bin/env python3
"""
Discord Bridge — Posts Cohort agent discussions to Discord via webhooks.

Write-only bridge. No incoming message handling = no prompt injection surface.
Each agent gets its own Discord webhook (name + avatar).

Usage:
    # One-off discussion
    python scripts/discord_bridge.py --topic "Claude Code Channels vs direct API calls" \
        --channel ai-news --agents developer,security,strategist

    # Post from an existing Cohort channel
    python scripts/discord_bridge.py --from-channel api-review --discord-channel code-review

    # Dry run (print what would be posted, don't actually post)
    python scripts/discord_bridge.py --topic "Rate limiting strategies" --dry-run

Configuration:
    Set webhooks in config/discord_webhooks.json:
    {
        "channels": {
            "ai-news": "https://discord.com/api/webhooks/...",
            "code-review": "https://discord.com/api/webhooks/...",
            "cohort-features": "https://discord.com/api/webhooks/...",
            "strategy-room": "https://discord.com/api/webhooks/...",
            "security-briefs": "https://discord.com/api/webhooks/...",
            "the-watercooler": "https://discord.com/api/webhooks/..."
        },
        "agent_avatars": {
            "developer": "https://rwheeler007.github.io/cohort/assets/agents/developer.png",
            "security": "https://rwheeler007.github.io/cohort/assets/agents/security.png"
        }
    }
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Cohort imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from cohort import JsonFileStorage, Orchestrator
from cohort.chat import ChatManager

CONFIG_PATH = Path(__file__).parent.parent / "config" / "discord_webhooks.json"
DATA_DIR = Path(__file__).parent.parent / "data"

# Default agents for different channel topics
CHANNEL_AGENTS = {
    "ai-news": ["strategist", "developer", "researcher"],
    "cohort-features": ["developer", "technical_writer", "strategist"],
    "code-review": ["developer", "security", "qa_engineer"],
    "strategy-room": ["strategist", "marketing", "developer"],
    "security-briefs": ["security", "developer", "qa_engineer"],
    "the-watercooler": ["developer", "strategist", "creative_writer"],
}

# Message delay between posts (seconds) — makes it read naturally
POST_DELAY = 3.0


def load_config() -> dict:
    """Load Discord webhook configuration."""
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        print("Creating template config...")
        template = {
            "channels": {
                "ai-news": "PASTE_WEBHOOK_URL_HERE",
                "code-review": "PASTE_WEBHOOK_URL_HERE",
                "cohort-features": "PASTE_WEBHOOK_URL_HERE",
                "strategy-room": "PASTE_WEBHOOK_URL_HERE",
                "security-briefs": "PASTE_WEBHOOK_URL_HERE",
                "the-watercooler": "PASTE_WEBHOOK_URL_HERE",
            },
            "agent_avatars": {},
        }
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(template, indent=2))
        print(f"Template written to {CONFIG_PATH}")
        print("Fill in your webhook URLs and run again.")
        sys.exit(1)

    return json.loads(CONFIG_PATH.read_text())


def post_to_discord(webhook_url: str, username: str, content: str,
                    avatar_url: str | None = None, dry_run: bool = False) -> bool:
    """Post a message to Discord via webhook."""
    payload = {
        "username": f"{username} [Cohort Agent]",
        "content": content,
    }
    if avatar_url:
        payload["avatar_url"] = avatar_url

    if dry_run:
        print(f"  [{username}]: {content[:120]}{'...' if len(content) > 120 else ''}")
        return True

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            # Discord returns 204 No Content on success
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Rate limited — wait and retry
            retry_after = json.loads(e.read()).get("retry_after", 5)
            print(f"  Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            return post_to_discord(webhook_url, username, content, avatar_url)
        print(f"  Discord error {e.code}: {e.read().decode()}")
        return False


def run_discussion(topic: str, agents: list[str], rounds: int = 3) -> list[dict]:
    """Run a Cohort discussion and return the messages."""
    storage = JsonFileStorage(str(DATA_DIR))
    chat = ChatManager(storage)

    channel_name = f"discord-{int(time.time())}"
    chat.create_channel(channel_name, topic)

    # Build agent configs
    agent_configs = {}
    for name in agents:
        agent_configs[name] = {
            "triggers": [name],
            "capabilities": [name],
        }

    orch = Orchestrator(chat, agents=agent_configs)
    session = orch.start_session(
        channel_name, topic, initial_agents=agents
    )

    messages = []
    for _ in range(rounds * len(agents)):
        rec = orch.get_next_speaker(session.session_id)
        speaker = rec.get("recommended_speaker")
        if not speaker:
            break

        # Get the agent's contribution
        response = orch.get_agent_response(session.session_id, speaker)
        if response:
            messages.append({
                "agent": speaker,
                "content": response.get("content", ""),
            })

    return messages


def read_cohort_channel(channel_name: str, limit: int = 20) -> list[dict]:
    """Read recent messages from an existing Cohort channel."""
    storage = JsonFileStorage(str(DATA_DIR))
    chat = ChatManager(storage)

    raw_messages = chat.get_messages(channel_name, limit=limit)
    messages = []
    for msg in raw_messages:
        if msg.get("sender") and msg["sender"] != "system":
            messages.append({
                "agent": msg["sender"],
                "content": msg.get("content", ""),
            })
    return messages


def main():
    parser = argparse.ArgumentParser(
        description="Post Cohort agent discussions to Discord"
    )
    parser.add_argument("--topic", help="Topic for agents to discuss")
    parser.add_argument("--channel", default="the-watercooler",
                        help="Discord channel to post to (default: the-watercooler)")
    parser.add_argument("--agents", help="Comma-separated agent names")
    parser.add_argument("--from-channel", help="Read from existing Cohort channel instead of generating")
    parser.add_argument("--discord-channel", help="Override Discord channel name (with --from-channel)")
    parser.add_argument("--rounds", type=int, default=3, help="Discussion rounds (default: 3)")
    parser.add_argument("--dry-run", action="store_true", help="Print messages without posting")
    parser.add_argument("--delay", type=float, default=POST_DELAY,
                        help=f"Seconds between posts (default: {POST_DELAY})")
    args = parser.parse_args()

    config = load_config()
    avatars = config.get("agent_avatars", {})

    # Determine Discord channel
    discord_channel = args.discord_channel or args.channel
    webhook_url = config["channels"].get(discord_channel)

    if not webhook_url or webhook_url == "PASTE_WEBHOOK_URL_HERE":
        print(f"No webhook configured for #{discord_channel}")
        print(f"Add it to {CONFIG_PATH}")
        sys.exit(1)

    # Get messages
    if args.from_channel:
        print(f"Reading from Cohort channel: {args.from_channel}")
        messages = read_cohort_channel(args.from_channel)
    elif args.topic:
        agents = args.agents.split(",") if args.agents else CHANNEL_AGENTS.get(
            args.channel, ["developer", "strategist", "security"]
        )
        print(f"Running discussion: '{args.topic}'")
        print(f"Agents: {', '.join(agents)}")
        messages = run_discussion(args.topic, agents, args.rounds)
    else:
        parser.error("Provide --topic or --from-channel")

    if not messages:
        print("No messages to post.")
        sys.exit(0)

    # Post to Discord
    print(f"\nPosting {len(messages)} messages to #{discord_channel}"
          f"{' (dry run)' if args.dry_run else ''}:\n")

    for i, msg in enumerate(messages):
        success = post_to_discord(
            webhook_url,
            username=msg["agent"],
            content=msg["content"],
            avatar_url=avatars.get(msg["agent"]),
            dry_run=args.dry_run,
        )
        if not success:
            print(f"  Failed to post message {i+1}, stopping.")
            sys.exit(1)

        if not args.dry_run and i < len(messages) - 1:
            time.sleep(args.delay)

    print(f"\nDone. {len(messages)} messages posted to #{discord_channel}.")


if __name__ == "__main__":
    main()
