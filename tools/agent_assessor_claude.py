#!/usr/bin/env python3
"""Agent Assessor (Claude Code backend) -- tests agents using Claude CLI.

Same assessment bank, same scoring, same output format as agent_assessor.py
but uses `claude --print` instead of Ollama. This tests agents as they'd
perform when a Cohort user picks Claude as their LLM backend.

Usage:
    python tools/agent_assessor_claude.py python_developer
    python tools/agent_assessor_claude.py python_developer --limit 10
    python tools/agent_assessor_claude.py python_developer --model haiku
    python tools/agent_assessor_claude.py --resume
    python tools/agent_assessor_claude.py --report
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

COHORT_ROOT = Path(__file__).parent.parent
ASSESSMENTS_DIR = COHORT_ROOT / "data" / "assessments"
RESULTS_DIR = COHORT_ROOT / "data" / "assessment_results_claude"
AGENTS_DIR = COHORT_ROOT / "agents"
PASSING_SCORE = 70

# Claude CLI model shortcuts
MODELS = {
    "haiku": "haiku",
    "sonnet": "sonnet",
    "opus": "opus",
}
DEFAULT_MODEL = "haiku"

CLAUDE_TIMEOUT = 60  # seconds per question -- Claude is fast

# Find claude CLI
import shutil
CLAUDE_CMD = shutil.which("claude")
if not CLAUDE_CMD:
    # Common Windows locations
    for candidate in [
        Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
        Path.home() / "AppData" / "Roaming" / "npm" / "claude",
        Path.home() / ".claude" / "local" / "claude.exe",
    ]:
        if candidate.exists():
            CLAUDE_CMD = str(candidate)
            break
if not CLAUDE_CMD:
    CLAUDE_CMD = "claude"  # hope for the best


def load_assessment(agent_id: str) -> dict | None:
    assessment_file = ASSESSMENTS_DIR / f"{agent_id}.json"
    if assessment_file.exists():
        with open(assessment_file, encoding="utf-8") as f:
            return json.load(f)
    return None


def discover_agents() -> list[str]:
    if not ASSESSMENTS_DIR.exists():
        return []
    return [f.stem for f in sorted(ASSESSMENTS_DIR.glob("*.json"))]


def load_agent_persona(agent_id: str) -> str:
    for name in ("agent_persona.md", "agent_prompt.md"):
        path = AGENTS_DIR / agent_id / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    return f"You are the {agent_id} agent."


def load_agent_config(agent_id: str) -> dict:
    cfg_path = AGENTS_DIR / agent_id / "agent_config.json"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def ask_claude(persona: str, question_text: str, model: str = DEFAULT_MODEL) -> str:
    """Send a question to Claude CLI and get the agent's answer."""
    prompt = (
        f"You are role-playing as the following agent. Stay in character.\n\n"
        f"---AGENT PERSONA---\n{persona}\n---END PERSONA---\n\n"
        f"{question_text}\n\n"
        "After your analysis, state your final answer in this exact format on its own line:\n"
        "ANSWER: <letter>"
    )

    cmd = [
        CLAUDE_CMD,
        "--print",
        "--model", model,
        "--max-turns", "1",
        "--setting-sources", "user",  # skip project CLAUDE.md context
    ]

    # Strip CLAUDECODE env var so nested claude CLI doesn't refuse to launch
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    # Run from temp dir to avoid project CLAUDE.md context drowning the prompt
    neutral_cwd = tempfile.gettempdir()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=neutral_cwd,
            input=prompt,  # pipe prompt via stdin instead of -p flag
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()[:200] if result.stderr else "unknown error"
            return f"[ERROR] Claude CLI failed: {stderr}"
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "[ERROR] Claude CLI timed out"
    except FileNotFoundError:
        return "[ERROR] claude CLI not found in PATH"
    except Exception as e:
        return f"[ERROR] {e}"


def extract_answer(response: str) -> str | None:
    """Extract the answer letter from the model's response."""
    response_clean = response.strip()
    if not response_clean:
        return None

    # Pattern 1: "ANSWER: B"
    m = re.search(r"ANSWER\s*:\s*([A-Da-d])", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 2: "The answer is B"
    m = re.search(r"(?:the\s+)?(?:correct\s+)?answer\s+is\s+\**([A-Da-d])\**", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 3: "Answer: B" or "Choice: B"
    m = re.search(r"(?:answer|choice)[\s:]+\**([A-Da-d])\**", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 4: Starts with just a letter
    if response_clean[0].upper() in "ABCD" and (len(response_clean) == 1 or response_clean[1] in " .\n\r\t)-:"):
        return response_clean[0].upper()

    # Pattern 5: "**B**" standalone
    m = re.search(r"(?:^|\n)\s*(?:\*\*)?([A-D])(?:\*\*)?\s*(?:$|\n|[.)\-:])", response_clean)
    if m:
        return m.group(1).upper()

    # Pattern 6: "option X"
    m = re.search(r"option\s+([A-Da-d])", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 7: last "A)" pattern
    matches = re.findall(r"\b([A-D])\)", response_clean)
    if matches:
        return matches[-1].upper()

    return None


def format_question(q: dict) -> str:
    lines = []
    if q.get("type") == "multi_step":
        lines.append("[Multi-step reasoning required]")
        lines.append("")
    lines.append(f"Question: {q['question']}")
    lines.append("")
    for letter in "ABCD":
        lines.append(f"  {letter}) {q['choices'][letter]}")
    return "\n".join(lines)


def filter_questions(questions: list, difficulty: str | None = None,
                     category: str | None = None, limit: int | None = None) -> list:
    filtered = questions
    if difficulty:
        filtered = [q for q in filtered if q.get("difficulty") == difficulty]
    if category:
        filtered = [q for q in filtered
                    if category.lower() in q.get("topic_category", "").lower()
                    or category.lower() in q.get("topic", "").lower()]
    if limit:
        filtered = filtered[:limit]
    return filtered


def get_results_path(agent_id: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / f"{agent_id}_results.json"


def load_partial_results(agent_id: str) -> dict | None:
    path = get_results_path(agent_id)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("status") == "in_progress":
            return data
    return None


def save_results(agent_id: str, results: dict):
    path = get_results_path(agent_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


def assess_agent(agent_id: str, questions: list, model: str = DEFAULT_MODEL,
                 resume: bool = False, dry_run: bool = False) -> dict:
    """Run assessment for one agent via Claude CLI."""
    persona = load_agent_persona(agent_id)

    answered_ids = set()
    existing_details = []
    if resume:
        partial = load_partial_results(agent_id)
        if partial:
            existing_details = partial.get("details", [])
            answered_ids = {d["id"] for d in existing_details if d.get("agent_answer") != "DRY_RUN"}
            if answered_ids:
                print(f"  [*] Resuming: {len(answered_ids)} questions already answered")

    results = {
        "agent_id": agent_id,
        "backend": "claude",
        "model": model,
        "total": len(questions),
        "correct": 0,
        "wrong": 0,
        "errors": 0,
        "score_pct": 0.0,
        "passed": False,
        "status": "in_progress",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "completed_at": None,
        "by_difficulty": {},
        "by_category": {},
        "by_type": {},
        "details": list(existing_details),
    }

    # Re-count existing
    for d in existing_details:
        if d.get("is_correct") is True:
            results["correct"] += 1
        elif d.get("is_correct") is False:
            results["wrong"] += 1
        elif d.get("agent_answer") is None:
            results["errors"] += 1

    for i, q in enumerate(questions, 1):
        q_id = q["id"]
        q_type = q.get("type", "standard")
        difficulty = q.get("difficulty", "unknown")
        category = q.get("topic_category", "unknown")

        if q_id in answered_ids:
            continue

        q_text = format_question(q)

        if dry_run:
            marker = "[MS]" if q_type == "multi_step" else "[  ]"
            print(f"  {marker} [{q_id}] ({difficulty}) {q['question'][:75]}...")
            continue

        marker = "[MS]" if q_type == "multi_step" else "[  ]"
        answered_so_far = len(results["details"]) + 1
        print(f"  {marker} [{q_id}] ({difficulty}) {answered_so_far}/{results['total']} Asking... ", end="", flush=True)
        start = time.time()
        response = ask_claude(persona, q_text, model)
        elapsed = time.time() - start

        agent_answer = extract_answer(response)
        is_correct = agent_answer == q["answer"] if agent_answer else False

        if agent_answer is None:
            results["errors"] += 1
            status = "ERR"
        elif is_correct:
            results["correct"] += 1
            status = "OK"
        else:
            results["wrong"] += 1
            status = "WRONG"

        print(f"{status} ({agent_answer or '?'} vs {q['answer']}) [{elapsed:.1f}s] {q['topic']}")

        if not is_correct and agent_answer is not None:
            print(f"         Agent said: {response[:150]}")
            print(f"         Correct: {q['answer']} -- {q['explanation'][:80]}")

        detail = {
            "id": q_id,
            "topic": q["topic"],
            "topic_category": category,
            "difficulty": difficulty,
            "type": q_type,
            "correct_answer": q["answer"],
            "agent_answer": agent_answer,
            "agent_response": response[:500],
            "is_correct": is_correct,
            "elapsed_s": round(elapsed, 1),
        }
        results["details"].append(detail)

        # Save every 5 questions
        if len(results["details"]) % 5 == 0:
            save_results(agent_id, results)

    # Final scoring
    answered = results["correct"] + results["wrong"]
    if answered > 0:
        results["score_pct"] = round(results["correct"] / results["total"] * 100, 1)
    results["passed"] = results["score_pct"] >= PASSING_SCORE
    results["status"] = "complete"
    results["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Breakdowns
    for d in results["details"]:
        if d.get("is_correct") is None:
            continue
        for key, val in [("by_difficulty", "difficulty"), ("by_category", "topic_category"), ("by_type", "type")]:
            bucket = d.get(val, "unknown")
            if bucket not in results[key]:
                results[key][bucket] = {"correct": 0, "total": 0}
            results[key][bucket]["total"] += 1
            if d["is_correct"]:
                results[key][bucket]["correct"] += 1

    return results


def print_results(results: dict):
    agent_id = results["agent_id"]
    status = "PASSED" if results["passed"] else "FAILED"
    print(f"\n{'=' * 60}")
    print(f"  {agent_id} -- {results['score_pct']:.1f}% -- {status}")
    print(f"  Backend: Claude ({results['model']})")
    print(f"  {results['correct']}/{results['total']} correct, "
          f"{results['wrong']} wrong, {results['errors']} errors")
    print(f"{'=' * 60}")

    for label, key in [("Difficulty", "by_difficulty"), ("Type", "by_type"), ("Category", "by_category")]:
        data = results.get(key, {})
        if not data:
            continue
        print(f"\n  {label} breakdown:")
        for bucket, d in sorted(data.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1)):
            pct = round(d["correct"] / d["total"] * 100) if d["total"] else 0
            bar = "#" * (pct // 5) + "." * (20 - pct // 5)
            print(f"    {bucket:<22} {d['correct']:>3}/{d['total']:<3} ({pct:>3}%) [{bar}]")

    missed = [d for d in results.get("details", []) if d.get("is_correct") is False]
    if missed:
        print(f"\n  Missed questions ({len(missed)}):")
        for d in missed[:20]:
            marker = "[MS]" if d.get("type") == "multi_step" else "[  ]"
            print(f"    {marker} {d['id']} ({d.get('difficulty', '?')}) {d['topic']} "
                  f"-- answered {d['agent_answer']}, correct {d['correct_answer']}")
        if len(missed) > 20:
            print(f"    ... and {len(missed) - 20} more")

    # Timing
    times = [d["elapsed_s"] for d in results.get("details", []) if "elapsed_s" in d]
    if times:
        print(f"\n  Timing: avg {sum(times)/len(times):.1f}s, "
              f"min {min(times):.1f}s, max {max(times):.1f}s, "
              f"total {sum(times):.0f}s ({sum(times)/60:.1f} min)")


def print_report():
    if not RESULTS_DIR.exists():
        print("[!] No Claude results directory found.")
        return
    all_results = []
    for f in sorted(RESULTS_DIR.glob("*_results.json")):
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("status") == "complete":
            all_results.append(data)
    if not all_results:
        print("[!] No completed Claude assessment results found.")
        return
    for r in all_results:
        print_results(r)


def main():
    args = {
        "dry_run": False, "resume": False, "report": False,
        "model": DEFAULT_MODEL, "difficulty": None, "category": None,
        "limit": None, "agents": [],
    }

    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--resume":
            args["resume"] = True
        elif arg == "--report":
            args["report"] = True
        elif arg == "--model" and i + 1 < len(argv):
            i += 1
            args["model"] = argv[i]
        elif arg == "--difficulty" and i + 1 < len(argv):
            i += 1
            args["difficulty"] = argv[i]
        elif arg == "--category" and i + 1 < len(argv):
            i += 1
            args["category"] = argv[i]
        elif arg == "--limit" and i + 1 < len(argv):
            i += 1
            args["limit"] = int(argv[i])
        elif not arg.startswith("-"):
            args["agents"].append(arg)
        i += 1

    if args["report"]:
        print_report()
        return

    agents = args["agents"] or discover_agents()
    if not agents:
        print("[!] No assessments found.")
        return

    print(f"Agent Assessor (Claude backend) -- {len(agents)} agent(s)")
    print(f"Model: {args['model']} | Pass: {PASSING_SCORE}%")
    if args["limit"]:
        print(f"Limit: {args['limit']} questions")
    print()

    all_results = []
    for agent_id in agents:
        data = load_assessment(agent_id)
        if not data:
            print(f"[!] No assessment for '{agent_id}'")
            continue

        questions = data.get("questions", [])
        questions = filter_questions(questions, args["difficulty"], args["category"], args["limit"])
        if not questions:
            print(f"[!] No questions match filters for '{agent_id}'")
            continue

        source = data.get("source", "unknown")
        multi_step = sum(1 for q in questions if q.get("type") == "multi_step")
        print(f"--- {agent_id} ({source}) ---")
        print(f"    {len(questions)} questions ({multi_step} multi-step)")

        result = assess_agent(agent_id, questions, model=args["model"],
                              resume=args["resume"], dry_run=args["dry_run"])
        all_results.append(result)

        if not args["dry_run"]:
            print_results(result)
            save_results(agent_id, result)
        print()

    if not args["dry_run"] and all_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        combined = RESULTS_DIR / "combined_results.json"
        with open(combined, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "backend": "claude",
                "model": args["model"],
                "passing_score": PASSING_SCORE,
                "results": all_results,
            }, f, indent=2, default=str)
        print(f"\nResults saved to {combined}")


if __name__ == "__main__":
    main()
