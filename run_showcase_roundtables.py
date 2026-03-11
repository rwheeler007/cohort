"""Generate real Cohort roundtable conversations for the website showcase.

Runs 5 compiled roundtables through Cohort's native engine, then posts
the agent responses as individual messages to the channels via the API.
Metadata (model, tokens, latency) is stored per-channel for display.
"""

import sys
import json
import time
import requests

sys.path.insert(0, "G:/cohort")

from cohort.compiled_roundtable import run_compiled_roundtable

COHORT_API = "http://localhost:5100"
METADATA_FILE = "G:/cohort/cohort/website_creator/output/cohort/conversation-meta.json"

SCENARIOS = [
    {
        "channel": "oauth2-security-review",
        "agents": ["security_agent", "python_developer", "qa_agent"],
        "topic": (
            "Review the authentication middleware for the new API endpoints. "
            "We are adding OAuth2 support and need to make sure token validation "
            "is solid before shipping. Specific concerns: audience claim validation, "
            "refresh token rate limiting, error response opacity vs debugging, "
            "and test coverage for all rejection paths. "
            "Tag each other with @mentions when handing off or requesting input."
        ),
    },
    {
        "channel": "cohort-launch-post",
        "agents": ["content_strategy_agent", "marketing_agent", "cohort_orchestrator"],
        "topic": (
            "We need a launch blog post for Cohort. Zero-dep Python multi-agent "
            "framework, MIT license, runs on consumer hardware with local models. "
            "What angle do we lead with? Who is the primary audience? What tone? "
            "Tag each other with @mentions when building on or pushing back on a point."
        ),
    },
    {
        "channel": "agent-list-performance",
        "agents": ["python_developer", "web_developer", "database_developer"],
        "topic": (
            "The AgentStore.list_agents() endpoint returns all agents as full JSON configs. "
            "Users with 50+ agents report slow dashboard loads. Should we add pagination, "
            "field filtering, server-side search, or restructure the response? "
            "Each of you bring your layer's perspective. "
            "Tag each other with @mentions when referencing someone's point."
        ),
    },
    {
        "channel": "self-review-test-coverage",
        "agents": ["qa_agent", "python_developer", "security_agent"],
        "topic": (
            "The code queue worker's self-review loop has 0% test coverage. "
            "It is the most critical path -- if self-review silently passes bad code, "
            "it ships with a green checkmark. The core judgment is an LLM call. "
            "How do we test it without mocking everything away? "
            "Tag each other with @mentions when you disagree or want input."
        ),
    },
    {
        "channel": "first-run-experience",
        "agents": ["web_developer", "setup_guide", "documentation_agent"],
        "topic": (
            "First-time users drop off after pip install. They get a working system "
            "but don't know what to do next. The setup_guide agent exists but users "
            "aren't discovering it. How do we fix the first-5-minutes experience? "
            "Tag each other with @mentions when assigning ownership or asking questions."
        ),
    },
]


def post_message(channel: str, sender: str, content: str) -> bool:
    """Post a message to a Cohort channel via the API."""
    try:
        r = requests.post(
            f"{COHORT_API}/api/send",
            json={"channel": channel, "sender": sender, "message": content},
            timeout=5,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  [!] Failed to post: {e}")
        return False


def clear_channel(channel: str):
    """Delete existing messages so we get a fresh conversation."""
    try:
        r = requests.get(
            f"{COHORT_API}/api/messages",
            params={"channel": channel, "limit": 100},
            timeout=5,
        )
        data = r.json()
        msgs = data.get("messages", data) if isinstance(data, dict) else data
        for m in msgs:
            mid = m.get("id")
            if mid:
                requests.delete(
                    f"{COHORT_API}/api/messages/{mid}",
                    params={"channel": channel},
                    timeout=5,
                )
    except Exception:
        pass  # Channel may not exist yet


def run_scenario(scenario: dict) -> dict | None:
    """Run one roundtable and post results. Returns metadata."""
    ch = scenario["channel"]
    agents = scenario["agents"]
    topic = scenario["topic"]

    print(f"\n[>>] Running roundtable in #{ch}")
    print(f"     Agents: {', '.join(agents)}")

    # Clear old messages
    clear_channel(ch)

    result = run_compiled_roundtable(
        agents=agents,
        topic=topic,
        rounds=2,
        temperature=0.30,
    )

    if result.error:
        print(f"  [X] Error: {result.error}")
        return None

    if not result.agent_responses:
        print("  [X] No agent responses returned")
        return None

    meta = result.metadata or {}
    model = meta.get("model", "unknown")
    tokens_in = meta.get("tokens_in") or meta.get("input_token_estimate", 0)
    tokens_out = meta.get("tokens_out", 0)
    latency = meta.get("latency_ms", 0)

    print(f"  [OK] Got {len(result.agent_responses)} agent responses")
    print(f"  [*] Model: {model} | Tokens: {tokens_in}+{tokens_out} | {latency}ms")

    # Post each agent's response as a separate message
    for agent_id, response in result.agent_responses.items():
        if response.strip():
            ok = post_message(ch, agent_id, response.strip())
            status = "[OK]" if ok else "[X]"
            preview = response.strip()[:80].replace("\n", " ")
            print(f"  {status} {agent_id}: {preview}...")

    # Post synthesis if available
    if result.synthesis:
        post_message(ch, "cohort_orchestrator", f"**Synthesis:**\n\n{result.synthesis}")
        print(f"  [OK] Synthesis posted")

    return {
        "channel": ch,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "latency_ms": latency,
        "latency_s": round(latency / 1000, 1),
        "agent_count": len(result.agent_responses),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--channels", nargs="*", help="Only run specific channels")
    args = parser.parse_args()

    # Verify Cohort is running
    try:
        r = requests.get(f"{COHORT_API}/health", timeout=3)
        if r.json().get("status") != "ok":
            print("[X] Cohort server not healthy")
            return
    except Exception:
        print("[X] Cohort server not reachable at", COHORT_API)
        return

    print("[OK] Cohort server is running")

    # Load existing metadata
    try:
        with open(METADATA_FILE) as f:
            all_meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        all_meta = {}

    scenarios = SCENARIOS
    if args.channels:
        scenarios = [s for s in SCENARIOS if s["channel"] in args.channels]

    for scenario in scenarios:
        meta = run_scenario(scenario)
        if meta:
            all_meta[meta["channel"]] = meta

    # Save metadata
    with open(METADATA_FILE, "w") as f:
        json.dump(all_meta, f, indent=2)

    print(f"\n[OK] Completed. Metadata saved to {METADATA_FILE}")
    print("[*] Run build_website_conversations.py to update the website")


if __name__ == "__main__":
    main()
