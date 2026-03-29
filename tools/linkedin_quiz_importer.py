#!/usr/bin/env python3
"""LinkedIn Skill Assessments Quiz Importer.

Imports quizzes from Ebazhanov/linkedin-skill-assessments-quizzes (GitHub)
into the BOSS agent assessment format compatible with agent_assessor.py.

Usage:
    python tools/linkedin_quiz_importer.py list                     # List available quizzes
    python tools/linkedin_quiz_importer.py preview python            # Preview parsed questions
    python tools/linkedin_quiz_importer.py import python_developer   # Import for one agent
    python tools/linkedin_quiz_importer.py import --all              # Import for all mapped agents
    python tools/linkedin_quiz_importer.py status                    # Show import status
    python tools/linkedin_quiz_importer.py update                    # Re-fetch and update all

Options:
    --merge          Merge with existing assessment (default: linkedin-only bank)
    --output-dir     Override output directory (default: data/assessments_linkedin/)
    --force          Overwrite existing imports
    --dry-run        Parse and show stats without writing files
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

COHORT_ROOT = Path(__file__).parent.parent
ASSESSMENTS_DIR = COHORT_ROOT / "data" / "assessments_linkedin"
CACHE_DIR = COHORT_ROOT / "data" / "linkedin_quiz_cache"
MAPPING_FILE = Path(__file__).parent / "linkedin_quiz_mapping.json"

REPO_BASE_URL = "https://raw.githubusercontent.com/Ebazhanov/linkedin-skill-assessments-quizzes/main"

# --------------------------------------------------------------------------- #
#  Agent <-> Quiz Mapping                                                      #
# --------------------------------------------------------------------------- #

DEFAULT_MAPPING = {
    "python_developer": {
        "quizzes": ["python/python-quiz.md"],
        "id_prefix": "LI_PY",
        "topic_category": "python",
    },
    "javascript_developer": {
        "quizzes": ["javascript/javascript-quiz.md"],
        "id_prefix": "LI_JS",
        "topic_category": "javascript",
    },
    "web_developer": {
        "quizzes": [
            "html/html-quiz.md",
            "css/css-quiz.md",
            "reactjs/reactjs-quiz.md",
            "node.js/node.js-quiz.md",
        ],
        "id_prefix": "LI_WEB",
        "topic_category": "web_development",
    },
    "security_agent": {
        "quizzes": ["cybersecurity/cybersecurity-quiz.md"],
        "id_prefix": "LI_SEC",
        "topic_category": "cybersecurity",
    },
    "database_developer": {
        "quizzes": ["mysql/mysql-quiz.md", "mongodb/mongodb-quiz.md", "nosql/nosql-quiz.md"],
        "id_prefix": "LI_DB",
        "topic_category": "databases",
    },
    "coding_orchestrator": {
        "quizzes": ["git/git-quiz.md", "agile-methodologies/agile-methodologies-quiz.md"],
        "id_prefix": "LI_CO",
        "topic_category": "engineering_process",
    },
    "system_coder": {
        "quizzes": ["linux/linux-quiz.md", "bash/bash-quiz.md"],
        "id_prefix": "LI_SYS",
        "topic_category": "systems",
    },
    "qa_agent": {
        "quizzes": ["json/json-quiz.md", "rest-api/rest-api-quiz.md"],
        "id_prefix": "LI_QA",
        "topic_category": "quality_assurance",
    },
    "documentation_agent": {
        "quizzes": ["xml/xml-quiz.md"],
        "id_prefix": "LI_DOC",
        "topic_category": "documentation",
    },
    "ai_infrastructure_agent": {
        "quizzes": ["machine-learning/machine-learning-quiz.md"],
        "id_prefix": "LI_ML",
        "topic_category": "machine_learning",
    },
    "hardware_agent": {
        "quizzes": ["it-operations/it-operations-quiz.md"],
        "id_prefix": "LI_HW",
        "topic_category": "it_operations",
    },
    "ceo_agent": {
        "quizzes": [
            "agile-methodologies/agile-methodologies-quiz.md",
        ],
        "id_prefix": "LI_CEO",
        "topic_category": "leadership",
    },
}


# --------------------------------------------------------------------------- #
#  Data classes                                                                #
# --------------------------------------------------------------------------- #

@dataclass
class ParsedQuestion:
    """A single parsed LinkedIn quiz question."""
    number: int
    question: str
    choices: dict  # {"A": "...", "B": "...", ...}
    correct_answer: str  # "A", "B", "C", or "D"
    explanation: str = ""
    references: list = field(default_factory=list)
    source_file: str = ""
    has_code: bool = False
    has_image: bool = False
    multi_correct: bool = False


# --------------------------------------------------------------------------- #
#  Quiz Fetcher                                                                #
# --------------------------------------------------------------------------- #

def fetch_quiz(quiz_path: str, use_cache: bool = True) -> str:
    """Fetch a quiz markdown file from GitHub, with optional caching."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.md5(quiz_path.encode()).hexdigest()
    cache_file = CACHE_DIR / f"{cache_key}.md"
    cache_meta = CACHE_DIR / f"{cache_key}.meta.json"

    # Check cache (24h TTL)
    if use_cache and cache_file.exists() and cache_meta.exists():
        meta = json.loads(cache_meta.read_text(encoding="utf-8"))
        if time.time() - meta.get("fetched_at", 0) < 86400:
            return cache_file.read_text(encoding="utf-8")

    url = f"{REPO_BASE_URL}/{quiz_path}"
    print(f"  [>>] Fetching {quiz_path}...")

    req = Request(url, headers={"User-Agent": "BOSS-AgentAssessor/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8")
    except (URLError, HTTPError) as e:
        print(f"  [X] Failed to fetch {url}: {e}")
        # Fall back to cache if available
        if cache_file.exists():
            print(f"  [!] Using stale cache for {quiz_path}")
            return cache_file.read_text(encoding="utf-8")
        return ""

    # Write cache
    cache_file.write_text(content, encoding="utf-8")
    cache_meta.write_text(json.dumps({
        "quiz_path": quiz_path,
        "fetched_at": time.time(),
        "size_bytes": len(content),
    }), encoding="utf-8")

    return content


# --------------------------------------------------------------------------- #
#  Markdown Parser                                                             #
# --------------------------------------------------------------------------- #

# Patterns
RE_QUESTION_HEADER = re.compile(r"^####\s+Q(\d+)\.\s+(.*)", re.MULTILINE)
RE_CHOICE = re.compile(r"^-\s+\[([ xX])\]\s+(.*)")
RE_REFERENCE = re.compile(r"\[(?:[Rr]eference|source)\]\((https?://[^)]+)\)")
RE_EXPLANATION = re.compile(r"\*\*(?:Explanation|Note|Example)\b[^*]*\*\*[:\s]*(.*)", re.IGNORECASE)
RE_CODE_BLOCK = re.compile(r"```")
RE_IMAGE_REF = re.compile(r"!\[.*?\]\(.*?\)|\.png|\.jpg|\.gif|\.svg", re.IGNORECASE)


def _collect_multiline_text(lines: list[str], start_idx: int, stop_patterns: list) -> tuple[str, int]:
    """Collect lines until we hit a stop pattern. Returns (text, next_index)."""
    collected = []
    i = start_idx
    while i < len(lines):
        line = lines[i]
        if any(p.match(line) for p in stop_patterns):
            break
        collected.append(line)
        i += 1
    return "\n".join(collected).strip(), i


def parse_quiz_markdown(content: str, source_file: str = "") -> list[ParsedQuestion]:
    """Parse a LinkedIn quiz markdown file into structured questions."""
    questions = []
    lines = content.split("\n")
    i = 0

    # Stop patterns for collecting question/explanation text
    choice_pattern = re.compile(r"^-\s+\[[ xX]\]")
    header_pattern = re.compile(r"^####\s+Q\d+\.")

    while i < len(lines):
        line = lines[i]

        # Match question header
        m = RE_QUESTION_HEADER.match(line)
        if not m:
            i += 1
            continue

        q_num = int(m.group(1))
        q_text_start = m.group(2).strip()

        # Collect remaining question text (may span multiple lines before choices)
        i += 1
        extra_lines = []
        while i < len(lines):
            if choice_pattern.match(lines[i]) or header_pattern.match(lines[i]):
                break
            extra_lines.append(lines[i])
            i += 1

        question_text = q_text_start
        if extra_lines:
            extra = "\n".join(extra_lines).strip()
            if extra:
                question_text += "\n" + extra

        # Parse answer choices
        choices = {}
        correct_letters = []
        choice_labels = "ABCDEFGH"
        choice_idx = 0

        while i < len(lines) and choice_idx < 8:
            cm = RE_CHOICE.match(lines[i])
            if not cm:
                # Check if this is a continuation of previous choice (code block)
                if choice_idx > 0 and (lines[i].startswith("```") or lines[i].startswith("  ") or lines[i].strip() == "" or lines[i].strip() == "<br>"):
                    # Accumulate into previous choice
                    prev_label = choice_labels[choice_idx - 1]
                    # Collect until next choice or question
                    cont_lines = [lines[i]]
                    i += 1
                    while i < len(lines):
                        if choice_pattern.match(lines[i]) or header_pattern.match(lines[i]):
                            break
                        cont_lines.append(lines[i])
                        i += 1
                    continuation = "\n".join(cont_lines).strip()
                    if continuation and continuation != "<br>":
                        choices[prev_label] = choices[prev_label] + "\n" + continuation if choices[prev_label] else continuation
                    continue
                else:
                    break

            is_correct = cm.group(1).lower() == "x"
            choice_text = cm.group(2).strip()
            label = choice_labels[choice_idx]

            choices[label] = choice_text
            if is_correct:
                correct_letters.append(label)
            choice_idx += 1
            i += 1

        # Skip questions with fewer than 2 choices (malformed)
        if len(choices) < 2:
            continue

        # Skip questions with no correct answer marked
        if not correct_letters:
            continue

        # Collect explanation and references from remaining lines until next question
        explanation_parts = []
        references = []
        while i < len(lines) and not header_pattern.match(lines[i]):
            line_s = lines[i].strip()

            # References
            for ref_match in RE_REFERENCE.finditer(line_s):
                references.append(ref_match.group(1))

            # Explanation text
            exp_match = RE_EXPLANATION.match(line_s)
            if exp_match:
                explanation_parts.append(exp_match.group(1).strip())
            elif line_s and not line_s.startswith("[") and not line_s.startswith("1.") and not RE_REFERENCE.match(line_s):
                # General explanatory text (not a reference link)
                if explanation_parts:  # Only append if we already started an explanation
                    explanation_parts.append(line_s)

            i += 1

        has_code = bool(RE_CODE_BLOCK.search(question_text) or
                       any(RE_CODE_BLOCK.search(v) for v in choices.values()))
        has_image = bool(RE_IMAGE_REF.search(question_text))

        questions.append(ParsedQuestion(
            number=q_num,
            question=question_text,
            choices=choices,
            correct_answer=correct_letters[0],  # Primary correct answer
            explanation=" ".join(explanation_parts) if explanation_parts else "",
            references=references,
            source_file=source_file,
            has_code=has_code,
            has_image=has_image,
            multi_correct=len(correct_letters) > 1,
        ))

    return questions


# --------------------------------------------------------------------------- #
#  Format Converter                                                            #
# --------------------------------------------------------------------------- #

def _infer_topic(question_text: str, source_file: str) -> str:
    """Infer a topic slug from question content and source file."""
    # Use source file directory as base topic
    parts = source_file.replace("\\", "/").split("/")
    if parts:
        base = parts[0].replace("-", "_")
    else:
        base = "general"

    # Try to detect specific sub-topics from keywords
    text_lower = question_text.lower()
    topic_keywords = {
        "decorator": "decorators",
        "generator": "generators",
        "lambda": "lambda_functions",
        "class": "oop",
        "inherit": "inheritance",
        "exception": "error_handling",
        "try": "error_handling",
        "async": "async_programming",
        "await": "async_programming",
        "list comprehension": "comprehensions",
        "dict comprehension": "comprehensions",
        "regex": "regular_expressions",
        "import": "modules_and_imports",
        "pip": "package_management",
        "virtualenv": "environments",
        "venv": "environments",
        "numpy": "numpy",
        "pandas": "pandas",
        "flask": "web_frameworks",
        "django": "web_frameworks",
        "sql": "sql",
        "join": "sql_joins",
        "index": "indexing",
        "select": "queries",
        "branch": "branching",
        "merge": "merging",
        "rebase": "rebasing",
        "commit": "commits",
        "stash": "stashing",
        "docker": "containers",
        "container": "containers",
        "kubernetes": "orchestration",
        "ssh": "remote_access",
        "permission": "permissions",
        "firewall": "network_security",
        "encrypt": "encryption",
        "hash": "hashing",
        "xss": "web_security",
        "injection": "injection_attacks",
        "css": "css",
        "html": "html",
        "react": "react",
        "component": "components",
        "hook": "hooks",
        "state": "state_management",
        "dom": "dom_manipulation",
        "api": "api_design",
        "rest": "rest_api",
        "json": "json",
        "xml": "xml",
        "test": "testing",
        "mock": "testing",
        "agile": "agile",
        "scrum": "scrum",
        "sprint": "sprint_planning",
        "kanban": "kanban",
        "machine learning": "ml_fundamentals",
        "neural": "neural_networks",
        "regression": "regression",
        "classification": "classification",
        "clustering": "clustering",
    }

    for keyword, topic in topic_keywords.items():
        if keyword in text_lower:
            return topic

    return base


def _estimate_difficulty(question: ParsedQuestion) -> str:
    """Estimate difficulty based on question characteristics."""
    text = question.question.lower()
    score = 0

    # Code blocks add complexity
    if question.has_code:
        score += 1

    # Longer questions tend to be harder
    if len(question.question) > 300:
        score += 1

    # Multi-step indicators
    multi_step_words = ["output", "result", "what happens", "which of the following",
                        "consider", "given the following", "what will be"]
    for w in multi_step_words:
        if w in text:
            score += 1
            break

    # Advanced concept indicators
    advanced_words = ["decorator", "metaclass", "generator", "coroutine", "mro",
                      "descriptor", "closure", "nonlocal", "rebase", "cherry-pick",
                      "injection", "vulnerability", "buffer overflow", "race condition"]
    for w in advanced_words:
        if w in text:
            score += 1
            break

    if score >= 3:
        return "expert"
    elif score >= 1:
        return "advanced"
    return "intermediate"


def convert_to_assessment_format(
    parsed_questions: list[ParsedQuestion],
    agent_id: str,
    id_prefix: str,
    topic_category: str,
) -> dict:
    """Convert parsed LinkedIn questions to agent_assessor.py format."""

    # Filter out image-dependent questions (model can't see images)
    valid_questions = [q for q in parsed_questions if not q.has_image]

    # Filter out multi-correct questions (our format supports single answer)
    valid_questions = [q for q in valid_questions if not q.multi_correct]

    # Ensure exactly 4 choices (pad or trim)
    final_questions = []
    for q in valid_questions:
        if len(q.choices) < 2:
            continue

        # Normalize to A/B/C/D
        normalized = {}
        labels = sorted(q.choices.keys())
        for j, label in enumerate(labels[:4]):
            new_label = "ABCD"[j]
            normalized[new_label] = q.choices[label]
            if label == q.correct_answer:
                q.correct_answer = new_label

        # If fewer than 4 choices, that's fine -- assessor handles it
        q.choices = normalized
        final_questions.append(q)

    # Build assessment JSON
    difficulty_counts = {"intermediate": 0, "advanced": 0, "expert": 0}
    questions_out = []

    for idx, q in enumerate(final_questions):
        difficulty = _estimate_difficulty(q)
        difficulty_counts[difficulty] += 1

        q_id = f"{id_prefix}{idx + 1:03d}"
        topic = _infer_topic(q.question, q.source_file)

        explanation = q.explanation
        if q.references:
            explanation += " " + " ".join(f"[ref]({r})" for r in q.references)

        questions_out.append({
            "id": q_id,
            "question": q.question,
            "choices": q.choices,
            "answer": q.correct_answer,
            "explanation": explanation.strip(),
            "topic": topic,
            "topic_category": topic_category,
            "difficulty": difficulty,
            "type": "standard",
            "source": "linkedin_skill_assessment",
        })

    return {
        "agent_id": agent_id,
        "source": "LinkedIn Skill Assessments (Ebazhanov/linkedin-skill-assessments-quizzes)",
        "total_questions": len(questions_out),
        "difficulty_distribution": difficulty_counts,
        "multi_step_count": 0,
        "questions": questions_out,
    }


# --------------------------------------------------------------------------- #
#  CLI Commands                                                                #
# --------------------------------------------------------------------------- #

def cmd_list():
    """List available quizzes from the repo (common ones)."""
    known_quizzes = [
        "python/python-quiz.md", "javascript/javascript-quiz.md",
        "html/html-quiz.md", "css/css-quiz.md", "react/reactjs-quiz.md",
        "git/git-quiz.md", "bash/bash-quiz.md", "linux/linux-quiz.md",
        "docker/docker-quiz.md", "mysql/mysql-quiz.md", "mongodb/mongodb-quiz.md",
        "nosql/nosql-quiz.md", "cybersecurity/cybersecurity-quiz.md",
        "machine-learning/machine-learning-quiz.md",
        "json/json-quiz.md", "xml/xml-quiz.md", "markdown/markdown-quiz.md",
        "rest-api/rest-api-quiz.md", "it-operations/it-operations-quiz.md",
        "agile-methodologies/agile-methodologies-quiz.md",
        "project-management/project-management-quiz.md",
        "node.js/node.js-quiz.md", "typescript/typescript-quiz.md",
        "django/django-quiz.md", "aws/aws-quiz.md",
        "google-cloud-platform/google-cloud-platform-quiz.md",
    ]

    print("\n=== Available LinkedIn Skill Assessment Quizzes ===\n")
    for qz in sorted(known_quizzes):
        # Check if cached
        cache_key = hashlib.md5(qz.encode()).hexdigest()
        cached = (CACHE_DIR / f"{cache_key}.md").exists()
        status = "[cached]" if cached else ""
        print(f"  {qz:<55s} {status}")

    print("\n=== Agent Mappings ===\n")
    mapping = _load_mapping()
    for agent_id, cfg in sorted(mapping.items()):
        quizzes = ", ".join(q.split("/")[0] for q in cfg["quizzes"])
        print(f"  {agent_id:<30s} <- {quizzes}")


def cmd_preview(quiz_name: str):
    """Preview parsed questions from a quiz."""
    # Resolve quiz path
    if "/" not in quiz_name:
        quiz_name = f"{quiz_name}/{quiz_name}-quiz.md"

    content = fetch_quiz(quiz_name)
    if not content:
        print(f"[X] Could not fetch {quiz_name}")
        return

    questions = parse_quiz_markdown(content, source_file=quiz_name)
    print(f"\n=== Preview: {quiz_name} ===")
    print(f"Total questions parsed: {len(questions)}")
    print(f"With code blocks: {sum(1 for q in questions if q.has_code)}")
    print(f"With images: {sum(1 for q in questions if q.has_image)}")
    print(f"Multi-correct: {sum(1 for q in questions if q.multi_correct)}")
    print(f"Usable (no image, single correct): {sum(1 for q in questions if not q.has_image and not q.multi_correct)}")

    print("\n--- First 5 Questions ---\n")
    for q in questions[:5]:
        print(f"Q{q.number}. {q.question[:120]}...")
        for label, text in q.choices.items():
            marker = "*" if label == q.correct_answer else " "
            print(f"  [{marker}] {label}: {text[:80]}")
        if q.explanation:
            print(f"  Explanation: {q.explanation[:100]}")
        print()


def cmd_import(agent_id: str, force: bool = False, dry_run: bool = False,
               merge: bool = False, output_dir: Optional[Path] = None):
    """Import LinkedIn quizzes for a specific agent."""
    mapping = _load_mapping()

    if agent_id == "--all":
        for aid in sorted(mapping.keys()):
            cmd_import(aid, force=force, dry_run=dry_run, merge=merge, output_dir=output_dir)
        return

    if agent_id not in mapping:
        print(f"[X] No quiz mapping for agent '{agent_id}'")
        print(f"    Available: {', '.join(sorted(mapping.keys()))}")
        return

    cfg = mapping[agent_id]
    out_dir = output_dir or ASSESSMENTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{agent_id}.json"

    if out_file.exists() and not force:
        print(f"[!] {out_file.name} already exists. Use --force to overwrite.")
        return

    # Fetch and parse all mapped quizzes
    all_questions = []
    for quiz_path in cfg["quizzes"]:
        content = fetch_quiz(quiz_path)
        if not content:
            continue
        parsed = parse_quiz_markdown(content, source_file=quiz_path)
        all_questions.extend(parsed)
        print(f"  [OK] {quiz_path}: {len(parsed)} questions parsed")

    if not all_questions:
        print(f"[X] No questions parsed for {agent_id}")
        return

    # Convert to assessment format
    assessment = convert_to_assessment_format(
        all_questions,
        agent_id=agent_id,
        id_prefix=cfg["id_prefix"],
        topic_category=cfg["topic_category"],
    )

    # Merge with existing assessment if requested
    if merge:
        existing_file = COHORT_ROOT / "data" / "assessments" / f"{agent_id}.json"
        if existing_file.exists():
            with open(existing_file, encoding="utf-8") as f:
                existing = json.load(f)
            existing_count = len(existing.get("questions", []))
            # Append LinkedIn questions
            existing["questions"].extend(assessment["questions"])
            existing["total_questions"] = len(existing["questions"])
            existing["source"] += " + LinkedIn Skill Assessments"
            assessment = existing
            print(f"  [OK] Merged: {existing_count} existing + {len(all_questions)} LinkedIn")

    usable = len(assessment["questions"])

    if dry_run:
        print(f"\n[DRY RUN] Would write {usable} questions to {out_file}")
        print(f"  Difficulty: {assessment['difficulty_distribution']}")
        topics = set(q["topic"] for q in assessment["questions"])
        print(f"  Topics ({len(topics)}): {', '.join(sorted(topics)[:10])}...")
        return

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(assessment, f, indent=2, ensure_ascii=False)

    print(f"  [OK] Wrote {usable} questions -> {out_file}")
    print(f"       Difficulty: {assessment['difficulty_distribution']}")


def cmd_status():
    """Show import status for all agents."""
    mapping = _load_mapping()

    print("\n=== LinkedIn Quiz Import Status ===\n")
    print(f"  {'Agent':<30s} {'LinkedIn':<12s} {'Original':<12s} {'Mapped Quizzes'}")
    print(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*30}")

    for agent_id in sorted(mapping.keys()):
        li_file = ASSESSMENTS_DIR / f"{agent_id}.json"
        orig_file = COHORT_ROOT / "data" / "assessments" / f"{agent_id}.json"

        li_count = "-"
        if li_file.exists():
            with open(li_file, encoding="utf-8") as f:
                li_count = str(json.load(f).get("total_questions", 0))

        orig_count = "-"
        if orig_file.exists():
            with open(orig_file, encoding="utf-8") as f:
                orig_count = str(json.load(f).get("total_questions", 0))

        quizzes = ", ".join(q.split("/")[0] for q in mapping[agent_id]["quizzes"])
        print(f"  {agent_id:<30s} {li_count:<12s} {orig_count:<12s} {quizzes}")


def _load_mapping() -> dict:
    """Load agent-to-quiz mapping from file or defaults."""
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_MAPPING


def _save_default_mapping():
    """Save the default mapping to disk for user customization."""
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_MAPPING, f, indent=2)
    print(f"[OK] Saved default mapping -> {MAPPING_FILE}")


# --------------------------------------------------------------------------- #
#  Agent Assessor Integration                                                  #
# --------------------------------------------------------------------------- #

def patch_assessor_discovery():
    """Print instructions for integrating with agent_assessor.py."""
    print("""
=== Integration with agent_assessor.py ===

Option 1: Run assessor against LinkedIn bank directly:
  python tools/agent_assessor.py python_developer --assessment-dir data/assessments_linkedin/

Option 2: Merge LinkedIn questions into existing assessment banks:
  python tools/linkedin_quiz_importer.py import --all --merge --output-dir data/assessments/

Option 3: Use the --linkedin flag (after patching agent_assessor.py):
  Add to agent_assessor.py CLI args:
    parser.add_argument("--linkedin", action="store_true", help="Use LinkedIn quiz bank")
  Then in load_assessment(), check for linkedin flag and swap ASSESSMENTS_DIR.
""")


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #

def main():
    import argparse
    parser = argparse.ArgumentParser(description="LinkedIn Skill Assessments Quiz Importer")
    sub = parser.add_subparsers(dest="command")

    # list
    sub.add_parser("list", help="List available quizzes and agent mappings")

    # preview
    p_preview = sub.add_parser("preview", help="Preview parsed questions from a quiz")
    p_preview.add_argument("quiz", help="Quiz name (e.g., 'python' or 'python/python-quiz.md')")

    # import
    p_import = sub.add_parser("import", help="Import quizzes for an agent")
    p_import.add_argument("agent", nargs="?", default="--all", help="Agent ID (default: all agents)")
    p_import.add_argument("--force", action="store_true", help="Overwrite existing imports")
    p_import.add_argument("--dry-run", action="store_true", help="Parse without writing")
    p_import.add_argument("--merge", action="store_true", help="Merge with existing assessment bank")
    p_import.add_argument("--output-dir", type=Path, help="Override output directory")

    # status
    sub.add_parser("status", help="Show import status for all agents")

    # update
    p_update = sub.add_parser("update", help="Re-fetch and update all mapped agents")
    p_update.add_argument("--merge", action="store_true")

    # save-mapping
    sub.add_parser("save-mapping", help="Save default mapping file for customization")

    # integration
    sub.add_parser("integration", help="Show integration instructions")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "preview":
        cmd_preview(args.quiz)
    elif args.command == "import":
        cmd_import(args.agent, force=args.force, dry_run=args.dry_run,
                   merge=args.merge, output_dir=args.output_dir)
    elif args.command == "status":
        cmd_status()
    elif args.command == "update":
        mapping = _load_mapping()
        for agent_id in sorted(mapping.keys()):
            # Clear cache for this agent's quizzes
            for quiz_path in mapping[agent_id]["quizzes"]:
                cache_key = hashlib.md5(quiz_path.encode()).hexdigest()
                cache_file = CACHE_DIR / f"{cache_key}.md"
                if cache_file.exists():
                    cache_file.unlink()
            cmd_import(agent_id, force=True, merge=args.merge)
    elif args.command == "save-mapping":
        _save_default_mapping()
    elif args.command == "integration":
        patch_assessor_discovery()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
