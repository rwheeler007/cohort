"""Run 2 showcase roundtables through Claude Code CLI for model variety.

Uses the same prompt building as compiled_roundtable.py but sends to
`claude -p -` instead of Ollama. Posts results to Cohort channels.
"""

import json
import subprocess
import time
import sys
import requests

sys.path.insert(0, "G:/cohort")

from cohort.compiled_roundtable import (
    build_compiled_prompt,
    parse_compiled_response,
    CompiledResult,
)

COHORT_API = "http://localhost:5100"
METADATA_FILE = "G:/cohort/cohort/website_creator/output/cohort/conversation-meta.json"

SCENARIOS = [
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
        pass


def run_via_claude(scenario: dict) -> dict | None:
    ch = scenario["channel"]
    agents = scenario["agents"]
    topic = scenario["topic"]

    print(f"\n[>>] Running Claude CLI roundtable in #{ch}")
    print(f"     Agents: {', '.join(agents)}")

    clear_channel(ch)

    system_prompt, user_prompt, token_est = build_compiled_prompt(
        agents=agents, topic=topic, rounds=2,
    )

    # Combine system + user into a single prompt for claude -p -
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    print(f"  [*] Prompt: ~{token_est} tokens estimated")
    print(f"  [*] Calling claude -p - ...")

    start = time.time()
    try:
        claude_cmd = "C:/Users/rwhee/AppData/Roaming/npm/claude.cmd"
        result = subprocess.run(
            [claude_cmd, "-p", "-", "--model", "sonnet"],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
        elapsed_ms = int((time.time() - start) * 1000)

        if result.returncode != 0:
            print(f"  [X] Claude CLI error: {result.stderr[:200]}")
            return None

        raw = result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("  [X] Claude CLI timed out after 300s")
        return None
    except FileNotFoundError:
        print("  [X] claude CLI not found in PATH")
        return None

    if not raw:
        print("  [X] Empty response from Claude CLI")
        return None

    print(f"  [OK] Got response ({len(raw)} chars, {elapsed_ms}ms)")

    # Parse the response using the same parser
    agent_responses, synthesis = parse_compiled_response(raw, agents)

    if not agent_responses:
        print("  [X] Could not parse agent responses")
        print(f"  [*] First 500 chars: {raw[:500]}")
        return None

    print(f"  [OK] Parsed {len(agent_responses)} agent responses")

    # Post each response
    for agent_id, response in agent_responses.items():
        if response.strip():
            ok = post_message(ch, agent_id, response.strip())
            status = "[OK]" if ok else "[X]"
            preview = response.strip()[:80].replace("\n", " ")
            print(f"  {status} {agent_id}: {preview}...")

    if synthesis:
        post_message(ch, "cohort_orchestrator", f"**Synthesis:**\n\n{synthesis}")
        print("  [OK] Synthesis posted")

    # Estimate tokens from chars (rough: 1 token ~ 4 chars)
    tokens_in_est = token_est
    tokens_out_est = len(raw) // 4

    return {
        "channel": ch,
        "model": "claude-sonnet-4-20250514",
        "tokens_in": tokens_in_est,
        "tokens_out": tokens_out_est,
        "total_tokens": tokens_in_est + tokens_out_est,
        "latency_ms": elapsed_ms,
        "latency_s": round(elapsed_ms / 1000, 1),
        "agent_count": len(agent_responses),
    }


def main():
    # Verify Cohort
    try:
        r = requests.get(f"{COHORT_API}/health", timeout=3)
        if r.json().get("status") != "ok":
            print("[X] Cohort server not healthy")
            return
    except Exception:
        print("[X] Cohort server not reachable")
        return

    print("[OK] Cohort server is running")

    # Load existing metadata
    try:
        with open(METADATA_FILE) as f:
            all_meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        all_meta = {}

    for scenario in SCENARIOS:
        meta = run_via_claude(scenario)
        if meta:
            all_meta[meta["channel"]] = meta

    with open(METADATA_FILE, "w") as f:
        json.dump(all_meta, f, indent=2)

    print(f"\n[OK] Completed. Metadata saved to {METADATA_FILE}")


if __name__ == "__main__":
    main()
