#!/usr/bin/env python3
"""Agent Assessor v2 -- comprehensive agent testing via Ollama.

Supports 100-question assessment banks per agent with multi-step challenges,
difficulty tiers, topic category analysis, and resume-on-interrupt.

Usage:
    python tools/agent_assessor.py                      # Test all agents
    python tools/agent_assessor.py python_developer      # Test one agent
    python tools/agent_assessor.py --dry-run             # Show questions without calling Ollama
    python tools/agent_assessor.py --resume              # Resume interrupted run
    python tools/agent_assessor.py --difficulty expert    # Only expert questions
    python tools/agent_assessor.py --category async       # Only async topic category
    python tools/agent_assessor.py --limit 30             # First N questions only
    python tools/agent_assessor.py --legacy               # Use old single-file format
    python tools/agent_assessor.py --report               # Show last results without running
"""

import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    import requests
except ImportError:
    print("[!] requests library required: pip install requests")
    sys.exit(1)

COHORT_ROOT = Path(__file__).parent.parent
ASSESSMENTS_DIR = COHORT_ROOT / "data" / "assessments"
LEGACY_ASSESSMENTS_FILE = COHORT_ROOT / "data" / "agent_assessments.json"
RESULTS_DIR = COHORT_ROOT / "data" / "assessment_results"
AGENTS_DIR = COHORT_ROOT / "agents"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen3:30b-a3b"
PASSING_SCORE = 70
REQUEST_TIMEOUT = 180  # 3 min for multi-step questions


def load_assessment(agent_id: str, use_legacy: bool = False) -> dict | None:
    """Load assessment questions for an agent."""
    if use_legacy:
        if not LEGACY_ASSESSMENTS_FILE.exists():
            return None
        with open(LEGACY_ASSESSMENTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if agent_id in data:
            return data[agent_id]
        return None

    assessment_file = ASSESSMENTS_DIR / f"{agent_id}.json"
    if assessment_file.exists():
        with open(assessment_file, encoding="utf-8") as f:
            return json.load(f)
    return None


def discover_agents(use_legacy: bool = False) -> list[str]:
    """Discover available agent assessments."""
    if use_legacy:
        if not LEGACY_ASSESSMENTS_FILE.exists():
            return []
        with open(LEGACY_ASSESSMENTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [k for k in data if k != "_meta"]

    if not ASSESSMENTS_DIR.exists():
        return []
    return [f.stem for f in sorted(ASSESSMENTS_DIR.glob("*.json"))]


def load_agent_prompt(agent_id: str) -> str:
    """Load the agent's system prompt from agent_prompt.md."""
    prompt_path = AGENTS_DIR / agent_id / "agent_prompt.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    persona_path = AGENTS_DIR / agent_id / "agent_persona.md"
    if persona_path.exists():
        return persona_path.read_text(encoding="utf-8")
    return f"You are the {agent_id} agent."


def load_agent_config(agent_id: str) -> dict:
    """Load agent config for temperature and metadata."""
    cfg_path = AGENTS_DIR / agent_id / "agent_config.json"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def ask_ollama(system_prompt: str, question_text: str, temperature: float = 0.15) -> str:
    """Send a question to Ollama and get the agent's answer."""
    prompt = (
        f"{question_text}\n\n"
        "After your analysis, state your final answer in this exact format on its own line:\n"
        "ANSWER: <letter>"
    )

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": 5000,
        },
        "keep_alive": "0",
        "think": False,
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()
    except requests.RequestException as e:
        return f"[ERROR] Ollama request failed: {e}"


def extract_answer(response: str) -> str | None:
    """Extract the answer letter from the model's response."""
    response_clean = response.strip()
    if not response_clean:
        return None

    # Pattern 1: "ANSWER: B" (our requested format)
    m = re.search(r"ANSWER\s*:\s*([A-Da-d])", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 2: "The answer is B" / "the correct answer is B"
    m = re.search(r"(?:the\s+)?(?:correct\s+)?answer\s+is\s+\**([A-Da-d])\**", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 3: "Answer: B" or "Choice: B"
    m = re.search(r"(?:answer|choice)[\s:]+\**([A-Da-d])\**", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 4: Starts with just a letter (A/B/C/D)
    if response_clean[0].upper() in "ABCD" and (len(response_clean) == 1 or response_clean[1] in " .\n\r\t)-:"):
        return response_clean[0].upper()

    # Pattern 5: "**B**" or "(B)" standalone on a line
    m = re.search(r"(?:^|\n)\s*(?:\*\*)?([A-D])(?:\*\*)?\s*(?:$|\n|[.)\-:])", response_clean)
    if m:
        return m.group(1).upper()

    # Pattern 6: last resort -- find last mention of "option X"
    m = re.search(r"option\s+([A-Da-d])", response_clean, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern 7: Look for the last "A)" "B)" etc. pattern
    matches = re.findall(r"\b([A-D])\)", response_clean)
    if matches:
        return matches[-1].upper()

    return None


def format_question(q: dict) -> str:
    """Format a question for the model."""
    q_type = q.get("type", "standard")
    difficulty = q.get("difficulty", "unknown")

    lines = []
    if q_type == "multi_step":
        lines.append("[Multi-step reasoning required]")
        lines.append("")
    lines.append(f"Question: {q['question']}")
    lines.append("")
    for letter in "ABCD":
        lines.append(f"  {letter}) {q['choices'][letter]}")
    return "\n".join(lines)


def get_results_path(agent_id: str) -> Path:
    """Get the path for saving agent results."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / f"{agent_id}_results.json"


def load_partial_results(agent_id: str) -> dict | None:
    """Load partial results from an interrupted run."""
    path = get_results_path(agent_id)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Only resume if it's from today and not complete
        if data.get("status") == "in_progress":
            return data
    return None


def save_results(agent_id: str, results: dict):
    """Save results (supports incremental saves during run)."""
    path = get_results_path(agent_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


def filter_questions(questions: list, difficulty: str | None = None,
                     category: str | None = None, limit: int | None = None) -> list:
    """Filter questions by difficulty, category, or count."""
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


def assess_agent(agent_id: str, questions: list, dry_run: bool = False,
                 resume: bool = False) -> dict:
    """Run assessment for one agent. Returns results dict."""
    config = load_agent_config(agent_id)
    system_prompt = load_agent_prompt(agent_id)
    temperature = config.get("model_params", {}).get("temperature", 0.15)

    # Check for resumable partial results
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
        "model": MODEL,
        "temperature": temperature,
        "total": len(questions),
        "correct": 0,
        "wrong": 0,
        "errors": 0,
        "score_pct": 0.0,
        "passed": False,
        "status": "in_progress",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "completed_at": None,
        "by_difficulty": defaultdict(lambda: {"correct": 0, "total": 0}),
        "by_category": defaultdict(lambda: {"correct": 0, "total": 0}),
        "by_type": {"standard": {"correct": 0, "total": 0}, "multi_step": {"correct": 0, "total": 0}},
        "details": list(existing_details),
    }

    # Re-count existing results
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

        # Skip already answered (resume mode)
        if q_id in answered_ids:
            continue

        q_text = format_question(q)

        if dry_run:
            marker = "[MS]" if q_type == "multi_step" else "[  ]"
            print(f"  {marker} [{q_id}] ({difficulty}) {q['question'][:75]}...")
            results["details"].append({
                "id": q_id,
                "topic": q["topic"],
                "topic_category": category,
                "difficulty": difficulty,
                "type": q_type,
                "correct_answer": q["answer"],
                "agent_answer": "DRY_RUN",
                "is_correct": None,
            })
            continue

        marker = "[MS]" if q_type == "multi_step" else "[  ]"
        print(f"  {marker} [{q_id}] ({difficulty}) Asking... ", end="", flush=True)
        start = time.time()
        response = ask_ollama(system_prompt, q_text, temperature)
        elapsed = time.time() - start

        agent_answer = extract_answer(response)
        is_correct = agent_answer == q["answer"]

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
            print(f"         Agent said: {response[:120]}")
            print(f"         Correct: {q['answer']} -- {q['explanation'][:80]}")

        detail = {
            "id": q_id,
            "topic": q["topic"],
            "topic_category": category,
            "difficulty": difficulty,
            "type": q_type,
            "correct_answer": q["answer"],
            "agent_answer": agent_answer,
            "agent_response": response[:300],
            "is_correct": is_correct,
            "elapsed_s": round(elapsed, 1),
        }
        results["details"].append(detail)

        # Save incrementally every 5 questions (for resume support)
        if len(results["details"]) % 5 == 0 and not dry_run:
            save_results(agent_id, results)

    # Compute final scores
    answered = results["correct"] + results["wrong"]
    if answered > 0:
        results["score_pct"] = round(results["correct"] / results["total"] * 100, 1)
    results["passed"] = results["score_pct"] >= PASSING_SCORE
    results["status"] = "complete"
    results["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Compute breakdowns
    by_difficulty = {}
    by_category = {}
    by_type = {}
    for d in results["details"]:
        if d.get("is_correct") is None:
            continue
        # By difficulty
        diff = d.get("difficulty", "unknown")
        if diff not in by_difficulty:
            by_difficulty[diff] = {"correct": 0, "total": 0}
        by_difficulty[diff]["total"] += 1
        if d["is_correct"]:
            by_difficulty[diff]["correct"] += 1
        # By category
        cat = d.get("topic_category", "unknown")
        if cat not in by_category:
            by_category[cat] = {"correct": 0, "total": 0}
        by_category[cat]["total"] += 1
        if d["is_correct"]:
            by_category[cat]["correct"] += 1
        # By type
        qtype = d.get("type", "standard")
        if qtype not in by_type:
            by_type[qtype] = {"correct": 0, "total": 0}
        by_type[qtype]["total"] += 1
        if d["is_correct"]:
            by_type[qtype]["correct"] += 1

    results["by_difficulty"] = by_difficulty
    results["by_category"] = by_category
    results["by_type"] = by_type

    return results


def print_agent_breakdown(results: dict):
    """Print detailed breakdown for one agent."""
    agent_id = results["agent_id"]

    # Difficulty breakdown
    by_diff = results.get("by_difficulty", {})
    if by_diff:
        print(f"\n  Difficulty breakdown:")
        for diff in ["intermediate", "advanced", "expert"]:
            if diff in by_diff:
                d = by_diff[diff]
                pct = round(d["correct"] / d["total"] * 100) if d["total"] else 0
                print(f"    {diff:<14} {d['correct']:>3}/{d['total']:<3} ({pct}%)")

    # Type breakdown
    by_type = results.get("by_type", {})
    if by_type:
        print(f"  Type breakdown:")
        for qtype in ["standard", "multi_step"]:
            if qtype in by_type:
                d = by_type[qtype]
                pct = round(d["correct"] / d["total"] * 100) if d["total"] else 0
                label = "Multi-step" if qtype == "multi_step" else "Standard"
                print(f"    {label:<14} {d['correct']:>3}/{d['total']:<3} ({pct}%)")

    # Category breakdown
    by_cat = results.get("by_category", {})
    if by_cat:
        print(f"  Category breakdown:")
        for cat, d in sorted(by_cat.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1)):
            pct = round(d["correct"] / d["total"] * 100) if d["total"] else 0
            bar = "#" * (pct // 5) + "." * (20 - pct // 5)
            print(f"    {cat:<22} {d['correct']:>3}/{d['total']:<3} ({pct:>3}%) [{bar}]")

    # Missed questions
    missed = [d for d in results.get("details", []) if d.get("is_correct") is False]
    if missed:
        print(f"\n  Missed questions ({len(missed)}):")
        for d in missed:
            marker = "[MS]" if d.get("type") == "multi_step" else "[  ]"
            print(f"    {marker} {d['id']} ({d.get('difficulty', '?')}) {d['topic']} "
                  f"-- answered {d['agent_answer']}, correct {d['correct_answer']}")


def print_summary(all_results: list[dict]):
    """Print a summary table of all results."""
    print("\n" + "=" * 90)
    print("ASSESSMENT RESULTS SUMMARY")
    print("=" * 90)
    print(f"{'Agent':<22} {'Score':>6} {'Pass':>5} {'Correct':>8} {'Wrong':>6} "
          f"{'Err':>4} {'Std%':>5} {'MS%':>5}")
    print("-" * 90)

    for r in all_results:
        status = "PASS" if r["passed"] else "FAIL"
        std = r.get("by_type", {}).get("standard", {})
        ms = r.get("by_type", {}).get("multi_step", {})
        std_pct = round(std["correct"] / std["total"] * 100) if std.get("total") else 0
        ms_pct = round(ms["correct"] / ms["total"] * 100) if ms.get("total") else 0
        print(
            f"{r['agent_id']:<22} {r['score_pct']:>5.0f}% {status:>5} "
            f"{r['correct']:>5}/{r['total']:<3} {r['wrong']:>5} {r['errors']:>4} "
            f"{std_pct:>4}% {ms_pct:>4}%"
        )

    print("-" * 90)
    total_correct = sum(r["correct"] for r in all_results)
    total_q = sum(r["total"] for r in all_results)
    overall = round(total_correct / total_q * 100, 1) if total_q else 0
    passed = sum(1 for r in all_results if r["passed"])
    print(f"{'OVERALL':<22} {overall:>5.0f}%       {total_correct:>5}/{total_q:<3}")
    print(f"Agents passed: {passed}/{len(all_results)}")
    print(f"Model: {MODEL} | Passing: {PASSING_SCORE}%")

    # Print per-agent breakdowns
    for r in all_results:
        print(f"\n{'=' * 50}")
        print(f"  {r['agent_id']} -- {r['score_pct']:.0f}%")
        print(f"{'=' * 50}")
        print_agent_breakdown(r)


def print_report():
    """Print the last saved results without running any tests."""
    if not RESULTS_DIR.exists():
        print("[!] No results directory found. Run assessments first.")
        return

    all_results = []
    for f in sorted(RESULTS_DIR.glob("*_results.json")):
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("status") == "complete":
            all_results.append(data)

    if not all_results:
        print("[!] No completed assessment results found.")
        return

    print_summary(all_results)


def parse_args():
    """Parse CLI arguments."""
    args = {
        "dry_run": False,
        "resume": False,
        "legacy": False,
        "report": False,
        "difficulty": None,
        "category": None,
        "limit": None,
        "agents": [],
    }

    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--resume":
            args["resume"] = True
        elif arg == "--legacy":
            args["legacy"] = True
        elif arg == "--report":
            args["report"] = True
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

    return args


def main():
    args = parse_args()

    if args["report"]:
        print_report()
        return

    agents = args["agents"] or discover_agents(use_legacy=args["legacy"])
    if not agents:
        print("[!] No assessments found. Check data/assessments/ directory.")
        return

    total_questions = 0
    for agent_id in agents:
        data = load_assessment(agent_id, use_legacy=args["legacy"])
        if data:
            qs = data.get("questions", [])
            qs = filter_questions(qs, args["difficulty"], args["category"], args["limit"])
            total_questions += len(qs)

    print(f"Agent Assessor v2 -- {len(agents)} agent(s), ~{total_questions} questions")
    print(f"Model: {MODEL} | Pass: {PASSING_SCORE}%")
    if args["difficulty"]:
        print(f"Filter: difficulty={args['difficulty']}")
    if args["category"]:
        print(f"Filter: category={args['category']}")
    if args["limit"]:
        print(f"Filter: limit={args['limit']}")
    if args["resume"]:
        print("[*] Resume mode: skipping already-answered questions")
    if args["dry_run"]:
        print("[DRY RUN -- questions shown but not sent to Ollama]")
    print()

    all_results = []
    for agent_id in agents:
        data = load_assessment(agent_id, use_legacy=args["legacy"])
        if not data:
            print(f"[!] No assessment found for '{agent_id}'")
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

        result = assess_agent(agent_id, questions, dry_run=args["dry_run"],
                              resume=args["resume"])
        all_results.append(result)

        if not args["dry_run"]:
            status = "PASSED" if result["passed"] else "FAILED"
            print(f"\n  >> {agent_id}: {result['score_pct']:.0f}% "
                  f"({result['correct']}/{result['total']}) -- {status}")
            save_results(agent_id, result)
        print()

    if not args["dry_run"] and all_results:
        print_summary(all_results)

        # Save combined results
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        combined_file = RESULTS_DIR / "combined_results.json"
        with open(combined_file, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "model": MODEL,
                "passing_score": PASSING_SCORE,
                "filters": {
                    "difficulty": args["difficulty"],
                    "category": args["category"],
                    "limit": args["limit"],
                },
                "results": all_results,
            }, f, indent=2, default=str)
        print(f"\nResults saved to {combined_file}")


if __name__ == "__main__":
    main()
