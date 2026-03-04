#!/usr/bin/env python3
"""Score Claude Code in-context assessment for python_developer."""
import json
import os
import time

with open("data/assessments/python_developer.json") as f:
    data = json.load(f)

questions = data["questions"]

# Answers from Claude Code (Opus 4.6) acting as python_developer agent
my_answers = {
    "PY001": "B", "PY002": "C", "PY003": "B", "PY004": "C", "PY005": "C",
    "PY006": "D", "PY007": "B", "PY008": "D", "PY009": "C", "PY010": "D",
    "PY011": "C", "PY012": "C", "PY013": "B", "PY014": "D", "PY015": "B",
    "PY016": "A", "PY017": "D", "PY018": "D", "PY019": "C", "PY020": "B",
    "PY021": "C", "PY022": "D", "PY023": "D", "PY024": "C", "PY025": "B",
    "PY026": "D", "PY027": "C", "PY028": "D", "PY029": "B", "PY030": "D",
    "PY031": "A", "PY032": "A", "PY033": "B", "PY034": "B", "PY035": "C",
    "PY036": "C", "PY037": "A", "PY038": "C", "PY039": "A", "PY040": "B",
    "PY041": "B", "PY042": "C", "PY043": "B", "PY044": "A", "PY045": "A",
    "PY046": "A", "PY047": "B", "PY048": "C", "PY049": "A", "PY050": "C",
    "PY051": "B", "PY052": "D", "PY053": "A", "PY054": "C", "PY055": "D",
    "PY056": "D", "PY057": "B", "PY058": "C", "PY059": "D", "PY060": "A",
    "PY061": "C", "PY062": "C", "PY063": "B", "PY064": "B", "PY065": "A",
    "PY066": "C", "PY067": "C", "PY068": "B", "PY069": "D", "PY070": "A",
    "PY071": "B", "PY072": "D", "PY073": "C", "PY074": "C", "PY075": "A",
    "PY076": "C", "PY077": "D", "PY078": "C", "PY079": "C", "PY080": "A",
    "PY081": "A", "PY082": "C", "PY083": "D", "PY084": "D", "PY085": "C",
    "PY086": "D", "PY087": "A", "PY088": "B", "PY089": "C", "PY090": "A",
    "PY091": "A", "PY092": "C", "PY093": "B", "PY094": "A", "PY095": "C",
    "PY096": "C", "PY097": "D", "PY098": "D", "PY099": "B", "PY100": "B",
}

correct = wrong = errors = 0
details = []

for q in questions:
    qid = q["id"]
    my_answer = my_answers.get(qid)
    expected = q["answer"]
    is_correct = my_answer == expected

    if my_answer is None:
        errors += 1
    elif is_correct:
        correct += 1
    else:
        wrong += 1

    details.append({
        "id": qid,
        "topic": q["topic"],
        "topic_category": q.get("topic_category", "unknown"),
        "difficulty": q.get("difficulty", "unknown"),
        "type": q.get("type", "standard"),
        "correct_answer": expected,
        "agent_answer": my_answer,
        "is_correct": is_correct,
        "elapsed_s": 0.1,
    })

total = len(questions)
score_pct = round(correct / total * 100, 1) if total else 0

# Breakdowns
by_difficulty = {}
by_category = {}
by_type = {}
for d in details:
    for key, field in [("by_difficulty", "difficulty"), ("by_category", "topic_category"), ("by_type", "type")]:
        store = {"by_difficulty": by_difficulty, "by_category": by_category, "by_type": by_type}[key]
        val = d[field]
        if val not in store:
            store[val] = {"correct": 0, "total": 0}
        store[val]["total"] += 1
        if d["is_correct"]:
            store[val]["correct"] += 1

results = {
    "agent_id": "python_developer",
    "backend": "claude_code_opus",
    "model": "claude-opus-4-6",
    "total": total,
    "correct": correct,
    "wrong": wrong,
    "errors": errors,
    "score_pct": score_pct,
    "passed": score_pct >= 70,
    "status": "complete",
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "by_difficulty": by_difficulty,
    "by_category": by_category,
    "by_type": by_type,
    "details": details,
}

out_dir = "data/assessment_results_claude"
os.makedirs(out_dir, exist_ok=True)
with open(f"{out_dir}/python_developer_results.json", "w") as f:
    json.dump(results, f, indent=2)

# Also save combined
with open(f"{out_dir}/combined_results.json", "w") as f:
    json.dump({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": "claude_code_opus",
        "model": "claude-opus-4-6",
        "passing_score": 70,
        "results": [results],
    }, f, indent=2, default=str)

# Print
print(f"{'=' * 60}")
print(f"  python_developer -- {score_pct}% -- {'PASSED' if score_pct >= 70 else 'FAILED'}")
print(f"  Backend: Claude Code (Opus 4.6, in-context)")
print(f"  {correct}/{total} correct, {wrong} wrong, {errors} errors")
print(f"{'=' * 60}")

print("\n  Difficulty breakdown:")
for diff in ["intermediate", "advanced", "expert"]:
    if diff in by_difficulty:
        d = by_difficulty[diff]
        pct = round(d["correct"] / d["total"] * 100)
        print(f"    {diff:<14} {d['correct']:>3}/{d['total']:<3} ({pct}%)")

print("\n  Type breakdown:")
for t in ["standard", "multi_step"]:
    if t in by_type:
        d = by_type[t]
        pct = round(d["correct"] / d["total"] * 100)
        label = "Multi-step" if t == "multi_step" else "Standard"
        print(f"    {label:<14} {d['correct']:>3}/{d['total']:<3} ({pct}%)")

print("\n  Category breakdown:")
for cat, d in sorted(by_category.items(), key=lambda x: x[1]["correct"]/max(x[1]["total"],1)):
    pct = round(d["correct"] / d["total"] * 100)
    bar = "#" * (pct // 5) + "." * (20 - pct // 5)
    print(f"    {cat:<22} {d['correct']:>3}/{d['total']:<3} ({pct:>3}%) [{bar}]")

missed = [d for d in details if not d["is_correct"]]
if missed:
    print(f"\n  Missed questions ({len(missed)}):")
    for d in missed:
        marker = "[MS]" if d["type"] == "multi_step" else "[  ]"
        print(f"    {marker} {d['id']} ({d['difficulty']}) {d['topic']}"
              f" -- answered {d['agent_answer']}, correct {d['correct_answer']}")
else:
    print("\n  Perfect score -- no missed questions!")

print(f"\n  Results saved to {out_dir}/python_developer_results.json")
