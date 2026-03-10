#!/usr/bin/env python3
"""Benchmark Dataset Importer for Agent Assessment.

Imports high-quality LLM benchmark datasets into the BOSS agent assessment
format compatible with agent_assessor.py.

Supported sources:
  - CodeMMLU     (Fsoft-AIC/CodeMMLU)       -- 19,878 programming MCQs
  - CyberMetric  (cybermetric/CyberMetric)  -- 10,000 cybersecurity MCQs
  - MMLU-Pro     (TIGER-Lab/MMLU-Pro)       -- 12,032 multi-domain MCQs (10-option)

Usage:
    python tools/benchmark_importer.py list                        # Show available sources
    python tools/benchmark_importer.py import codemmlu             # Import CodeMMLU
    python tools/benchmark_importer.py import cybermetric          # Import CyberMetric
    python tools/benchmark_importer.py import mmlu-pro             # Import MMLU-Pro
    python tools/benchmark_importer.py import all                  # Import everything
    python tools/benchmark_importer.py status                      # Show import status
    python tools/benchmark_importer.py import codemmlu --dry-run   # Preview without writing

Options:
    --force          Overwrite existing imports
    --dry-run        Parse and show stats without writing
    --output-dir     Override output directory
"""

import json
import re
import sys
import time
import hashlib
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

COHORT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = COHORT_ROOT / "data" / "assessments_benchmark"
CACHE_DIR = COHORT_ROOT / "data" / "benchmark_cache"

LETTERS = "ABCDEFGHIJ"

# --------------------------------------------------------------------------- #
#  Agent <-> Benchmark Mapping                                                 #
# --------------------------------------------------------------------------- #

# Which benchmark subsets map to which agents
AGENT_MAPPING = {
    # CodeMMLU subsets -> agents
    "codemmlu": {
        "python_developer": {
            "filter": {"language_hint": ["python"]},
            "id_prefix": "CM_PY",
        },
        "javascript_developer": {
            "filter": {"language_hint": ["javascript", "js", "typescript", "node"]},
            "id_prefix": "CM_JS",
        },
        "web_developer": {
            "filter": {"language_hint": ["html", "css", "react", "bootstrap", "django", "flask"]},
            "id_prefix": "CM_WEB",
        },
        "database_developer": {
            "filter": {"language_hint": ["sql", "database", "mysql", "postgres", "mongodb"]},
            "id_prefix": "CM_DB",
        },
        "system_coder": {
            "filter": {"language_hint": ["bash", "linux", "shell", "c++", "c ", "rust", "go "]},
            "id_prefix": "CM_SYS",
        },
        "coding_orchestrator": {
            "filter": {"language_hint": ["git", "agile", "design pattern", "software engineer"]},
            "id_prefix": "CM_CO",
        },
        # Catch-all: questions not matched to specific agents go to a general pool
        "_general": {
            "filter": {},
            "id_prefix": "CM_GEN",
        },
    },
    # CyberMetric -> agents
    "cybermetric": {
        "security_agent": {
            "filter": {},  # All questions go to security
            "id_prefix": "CY",
        },
    },
    # MMLU-Pro subsets -> agents
    "mmlu_pro": {
        "ai_infrastructure_agent": {
            "filter": {"src_contains": ["machine_learning"]},
            "id_prefix": "MP_ML",
        },
        "security_agent": {
            "filter": {"src_contains": ["computer_security"]},
            "id_prefix": "MP_SEC",
        },
        "python_developer": {
            "filter": {"category": ["computer science"]},
            "id_prefix": "MP_CS",
        },
        "ceo_agent": {
            "filter": {"category": ["business"]},
            "id_prefix": "MP_BIZ",
        },
        "hardware_agent": {
            "filter": {"category": ["engineering"]},
            "id_prefix": "MP_ENG",
        },
    },
}


# --------------------------------------------------------------------------- #
#  Shared Utilities                                                            #
# --------------------------------------------------------------------------- #

def _fetch_url(url: str, cache_key: str = "", cache_ttl: int = 86400 * 7) -> bytes:
    """Fetch URL with caching. Returns raw bytes."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not cache_key:
        cache_key = hashlib.md5(url.encode()).hexdigest()
    cache_file = CACHE_DIR / cache_key

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < cache_ttl:
            return cache_file.read_bytes()

    print(f"  [>>] Fetching {url[:100]}...")
    req = Request(url, headers={"User-Agent": "BOSS-BenchmarkImporter/1.0"})
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urlopen(req, timeout=120) as resp:
                data = resp.read()
            break
        except HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"  [!] Rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            print(f"  [X] Failed: {e}")
            if cache_file.exists():
                print(f"  [!] Using stale cache")
                return cache_file.read_bytes()
            return b""
        except URLError as e:
            print(f"  [X] Failed: {e}")
            if cache_file.exists():
                print(f"  [!] Using stale cache")
                return cache_file.read_bytes()
            return b""

    cache_file.write_bytes(data)
    return data


def _fetch_json(url: str, cache_key: str = "") -> dict | list:
    """Fetch and parse JSON from URL."""
    data = _fetch_url(url, cache_key)
    if not data:
        return {}
    return json.loads(data)


def _choices_list_to_dict(choices: list) -> dict:
    """Convert ordered list of choices to {A: ..., B: ..., ...} dict."""
    return {LETTERS[i]: str(c) for i, c in enumerate(choices) if i < 10}


def _infer_language(text: str) -> str:
    """Infer programming language from question text."""
    text_lower = text.lower()
    patterns = [
        (["def ", "import ", "self.", "python", "__init__", "pip ", ">>> "], "python"),
        (["function ", "const ", "let ", "var ", "javascript", "console.log", "=>"], "javascript"),
        (["<html", "<div", "css", "bootstrap", "<!doctype"], "html_css"),
        (["select ", "insert ", "where ", "join ", "sql", "table "], "sql"),
        (["#include", "cout", "std::", "c++", "nullptr"], "cpp"),
        (["func ", "goroutine", "go ", "chan "], "go"),
        (["fn ", "rust", "impl ", "let mut"], "rust"),
        (["public static", "java", "system.out", "class "], "java"),
        (["#!/bin/bash", "bash", "#!/bin/sh", "grep ", "awk ", "sed "], "bash"),
        (["git ", "commit", "branch", "merge"], "git"),
        (["react", "usestate", "useeffect", "jsx", "component"], "react"),
        (["node", "require(", "express", "npm "], "nodejs"),
        (["docker", "container", "dockerfile"], "docker"),
        (["django", "flask", "fastapi"], "web_framework"),
    ]
    for keywords, lang in patterns:
        if any(k in text_lower for k in keywords):
            return lang
    return "general"


def _classify_cyber_topic(text: str) -> str:
    """Classify cybersecurity question into sub-topic."""
    text_lower = text.lower()
    topics = [
        (["encrypt", "cipher", "aes", "rsa", "tls", "ssl", "certificate", "pki", "hash"], "cryptography"),
        (["firewall", "ids", "ips", "packet", "tcp", "udp", "dns", "routing", "network", "port", "vlan", "subnet"], "network_security"),
        (["xss", "sql injection", "csrf", "owasp", "web application", "waf", "cookie"], "web_security"),
        (["malware", "virus", "trojan", "ransomware", "worm", "botnet", "rootkit"], "malware"),
        (["phishing", "social engineering", "pretexting", "baiting"], "social_engineering"),
        (["access control", "authentication", "authorization", "rbac", "mfa", "password", "identity"], "access_control"),
        (["risk", "audit", "compliance", "governance", "policy", "nist", "iso 27", "gdpr"], "governance_risk"),
        (["incident", "forensic", "breach", "response", "siem", "log"], "incident_response"),
        (["vulnerability", "exploit", "cve", "patch", "penetration", "pentest"], "vulnerability_management"),
        (["backup", "disaster", "recovery", "continuity", "availability"], "business_continuity"),
    ]
    for keywords, topic in topics:
        if any(k in text_lower for k in keywords):
            return topic
    return "general_security"


def _estimate_difficulty_from_options(num_options: int, question_len: int, has_code: bool) -> str:
    """Estimate difficulty based on structural cues."""
    score = 0
    if num_options > 4:
        score += 1
    if num_options >= 8:
        score += 1
    if question_len > 300:
        score += 1
    if has_code:
        score += 1
    if score >= 3:
        return "expert"
    if score >= 1:
        return "advanced"
    return "intermediate"


# --------------------------------------------------------------------------- #
#  CodeMMLU Importer                                                           #
# --------------------------------------------------------------------------- #

CODEMMLU_CONFIGS = [
    "api_frameworks", "programming_syntax", "software_principles",
    "dbms_sql", "others", "code_completion", "fill_in_the_middle",
    "code_repair", "execution_prediction",
]

CODEMMLU_PARQUET = "https://huggingface.co/api/datasets/Fsoft-AIC/CodeMMLU/parquet/{config}/test/0.parquet"


def _load_parquet(path_or_url: str) -> list[dict]:
    """Load a parquet file into list of dicts using pandas."""
    try:
        import pandas as pd
        df = pd.read_parquet(path_or_url)
        return df.to_dict(orient="records")
    except ImportError:
        print("  [X] pandas + pyarrow required: pip install pandas pyarrow")
        return []


def fetch_codemmlu() -> list[dict]:
    """Fetch all CodeMMLU questions via Parquet download (1 request per config)."""
    all_rows = []

    for config in CODEMMLU_CONFIGS:
        cache_file = CACHE_DIR / f"codemmlu_{config}.json"

        # Check JSON cache
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < 86400 * 7:
                rows = json.loads(cache_file.read_text(encoding="utf-8"))
                if rows:  # Skip empty caches from failed prior runs
                    print(f"  [cached] {config}: {len(rows)} questions")
                    all_rows.extend(rows)
                    continue

        # Download parquet
        parquet_cache = CACHE_DIR / f"codemmlu_{config}.parquet"
        parquet_url = CODEMMLU_PARQUET.format(config=config)
        raw = _fetch_url(parquet_url, cache_key=f"codemmlu_{config}.parquet")
        if not raw:
            print(f"  [X] Failed to download {config} parquet")
            continue

        # Ensure parquet file is on disk for pandas
        parquet_cache.write_bytes(raw)
        config_rows = _load_parquet(str(parquet_cache))

        # Add config tag and normalize
        for row in config_rows:
            row["_config"] = config
            # Parquet may store choices as numpy array; convert to list
            if hasattr(row.get("choices"), "tolist"):
                row["choices"] = row["choices"].tolist()

        # Cache as JSON
        cache_file.write_text(json.dumps(config_rows, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"  [OK] {config}: {len(config_rows)} questions")
        all_rows.extend(config_rows)
        time.sleep(1)  # Rate limit courtesy between configs

    return all_rows


def convert_codemmlu(rows: list[dict]) -> dict[str, list[dict]]:
    """Convert CodeMMLU rows to assessment format, grouped by agent."""
    agent_questions: dict[str, list[dict]] = {}
    mapping = AGENT_MAPPING["codemmlu"]

    for row in rows:
        question_text = row.get("question", "")
        choices_list = row.get("choices", [])
        answer = row.get("answer", "")
        config = row.get("_config", "")
        task_id = row.get("task_id", "")

        if not question_text or not choices_list or not answer:
            continue

        # For fill_in_the_middle, prepend problem description
        if config == "fill_in_the_middle" and row.get("problem_description"):
            question_text = row["problem_description"] + "\n\n" + question_text

        language = _infer_language(question_text)
        has_code = bool(re.search(r"```|def |function |class |#include|SELECT ", question_text))

        choices = _choices_list_to_dict(choices_list)
        difficulty = _estimate_difficulty_from_options(len(choices_list), len(question_text), has_code)

        # Determine which agent this maps to
        matched_agent = None
        for agent_id, cfg in mapping.items():
            if agent_id == "_general":
                continue
            hints = cfg["filter"].get("language_hint", [])
            if hints and any(h in question_text.lower() for h in hints):
                matched_agent = agent_id
                break

        if not matched_agent:
            matched_agent = "_general"

        if matched_agent not in agent_questions:
            agent_questions[matched_agent] = []

        prefix = mapping[matched_agent]["id_prefix"]
        idx = len(agent_questions[matched_agent]) + 1

        agent_questions[matched_agent].append({
            "id": f"{prefix}{idx:04d}",
            "question": question_text,
            "choices": choices,
            "answer": answer,
            "explanation": "",
            "topic": language,
            "topic_category": config,
            "difficulty": difficulty,
            "type": "multi_step" if config in ("execution_prediction", "code_repair", "fill_in_the_middle") else "standard",
            "source": "codemmlu",
            "source_id": task_id,
        })

    return agent_questions


# --------------------------------------------------------------------------- #
#  CyberMetric Importer                                                        #
# --------------------------------------------------------------------------- #

CYBERMETRIC_URL = "https://raw.githubusercontent.com/cybermetric/CyberMetric/main/CyberMetric-{size}-v1.json"
CYBERMETRIC_SIZES = [80, 500, 2000, 10000]


def fetch_cybermetric(size: int = 10000) -> list[dict]:
    """Fetch CyberMetric questions from GitHub."""
    if size not in CYBERMETRIC_SIZES:
        print(f"  [!] Invalid size {size}. Using 10000.")
        size = 10000

    url = CYBERMETRIC_URL.format(size=size)
    data = _fetch_json(url, cache_key=f"cybermetric_{size}")

    if isinstance(data, dict) and "questions" in data:
        questions = data["questions"]
    elif isinstance(data, list):
        questions = data
    else:
        print(f"  [X] Unexpected CyberMetric format")
        return []

    print(f"  [OK] CyberMetric-{size}: {len(questions)} questions")
    return questions


def convert_cybermetric(rows: list[dict]) -> list[dict]:
    """Convert CyberMetric questions to assessment format."""
    questions = []

    for i, row in enumerate(rows):
        q_text = row.get("question", "")
        answers = row.get("answers", {})
        solution = row.get("solution", "")

        if not q_text or not answers or not solution:
            continue

        topic = _classify_cyber_topic(q_text)
        has_code = bool(re.search(r"```|code|script|command", q_text.lower()))
        difficulty = _estimate_difficulty_from_options(len(answers), len(q_text), has_code)

        questions.append({
            "id": f"CY{i + 1:05d}",
            "question": q_text,
            "choices": answers,  # Already in {A, B, C, D} format
            "answer": solution,
            "explanation": "",
            "topic": topic,
            "topic_category": "cybersecurity",
            "difficulty": difficulty,
            "type": "standard",
            "source": "cybermetric",
        })

    return questions


# --------------------------------------------------------------------------- #
#  MMLU-Pro Importer                                                           #
# --------------------------------------------------------------------------- #

MMLU_PRO_PARQUET = "https://huggingface.co/api/datasets/TIGER-Lab/MMLU-Pro/parquet/default/test/0.parquet"

# Which categories to import
MMLU_PRO_CATEGORIES = [
    "computer science", "engineering", "math", "business", "other",
]


def fetch_mmlu_pro() -> list[dict]:
    """Fetch MMLU-Pro questions via Parquet download (single file)."""
    cache_file = CACHE_DIR / "mmlu_pro_all.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < 86400 * 7:
            rows = json.loads(cache_file.read_text(encoding="utf-8"))
            if rows:
                print(f"  [cached] MMLU-Pro: {len(rows)} questions")
                return rows

    # Download parquet
    parquet_cache = CACHE_DIR / "mmlu_pro.parquet"
    raw = _fetch_url(MMLU_PRO_PARQUET, cache_key="mmlu_pro.parquet")
    if not raw:
        print("  [X] Failed to download MMLU-Pro parquet")
        return []

    parquet_cache.write_bytes(raw)
    all_data = _load_parquet(str(parquet_cache))

    # Filter to relevant categories
    all_rows = []
    for row in all_data:
        category = row.get("category", "")
        if category in MMLU_PRO_CATEGORIES:
            # Normalize options from numpy if needed
            if hasattr(row.get("options"), "tolist"):
                row["options"] = row["options"].tolist()
            all_rows.append(row)

    print(f"  [OK] MMLU-Pro: {len(all_rows)} relevant questions (of {len(all_data)} total)")

    cache_file.write_text(json.dumps(all_rows, ensure_ascii=False, default=str), encoding="utf-8")
    return all_rows


def convert_mmlu_pro(rows: list[dict]) -> dict[str, list[dict]]:
    """Convert MMLU-Pro rows to assessment format, grouped by agent."""
    agent_questions: dict[str, list[dict]] = {}
    mapping = AGENT_MAPPING["mmlu_pro"]

    for row in rows:
        question_text = row.get("question", "")
        options = row.get("options", [])
        answer = row.get("answer", "")
        category = row.get("category", "")
        src = row.get("src", "")
        cot = row.get("cot_content", "")
        q_id = row.get("question_id", 0)

        if not question_text or not options or not answer:
            continue

        choices = _choices_list_to_dict(options)
        has_code = bool(re.search(r"```|def |function |class |SELECT ", question_text))
        difficulty = _estimate_difficulty_from_options(len(options), len(question_text), has_code)

        # Match to agent based on filters
        matched_agent = None
        for agent_id, cfg in mapping.items():
            filt = cfg["filter"]

            if "src_contains" in filt:
                if any(s in src for s in filt["src_contains"]):
                    matched_agent = agent_id
                    break

            if "category" in filt:
                if category in filt["category"]:
                    matched_agent = agent_id
                    break

        if not matched_agent:
            continue  # Skip unmapped categories

        if matched_agent not in agent_questions:
            agent_questions[matched_agent] = []

        prefix = mapping[matched_agent]["id_prefix"]
        idx = len(agent_questions[matched_agent]) + 1

        # Use src field for more specific topic
        topic = src.replace("ori_mmlu-", "").replace("stemez-", "").replace("theoremQA-", "").lower()

        agent_questions[matched_agent].append({
            "id": f"{prefix}{idx:04d}",
            "question": question_text,
            "choices": choices,
            "answer": answer,
            "explanation": cot if cot else "",
            "topic": topic,
            "topic_category": category,
            "difficulty": difficulty,
            "type": "standard",
            "source": "mmlu_pro",
            "source_id": str(q_id),
        })

    return agent_questions


# --------------------------------------------------------------------------- #
#  Output Writer                                                               #
# --------------------------------------------------------------------------- #

def write_assessment(agent_id: str, questions: list[dict], source_name: str,
                     output_dir: Path, force: bool = False, dry_run: bool = False) -> bool:
    """Write assessment file for an agent. Returns True if written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # File name includes source to avoid collisions
    out_file = output_dir / f"{agent_id}.json"

    if out_file.exists() and not force:
        # Merge: load existing and append new questions
        with open(out_file, encoding="utf-8") as f:
            existing = json.load(f)
        existing_ids = {q["id"] for q in existing.get("questions", [])}
        new_qs = [q for q in questions if q["id"] not in existing_ids]
        if not new_qs:
            print(f"  [=] {agent_id}: no new questions to add")
            return False
        existing["questions"].extend(new_qs)
        existing["total_questions"] = len(existing["questions"])
        assessment = existing
        print(f"  [+] {agent_id}: merging {len(new_qs)} new questions (total: {assessment['total_questions']})")
    else:
        # Compute difficulty distribution
        diff_dist = {"intermediate": 0, "advanced": 0, "expert": 0}
        for q in questions:
            d = q.get("difficulty", "intermediate")
            if d in diff_dist:
                diff_dist[d] += 1

        multi_step = sum(1 for q in questions if q.get("type") == "multi_step")

        assessment = {
            "agent_id": agent_id,
            "source": source_name,
            "total_questions": len(questions),
            "difficulty_distribution": diff_dist,
            "multi_step_count": multi_step,
            "questions": questions,
        }

    if dry_run:
        topics = set(q.get("topic", "") for q in questions)
        print(f"  [DRY] {agent_id}: {len(questions)} questions, {len(topics)} topics")
        return False

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(assessment, f, indent=2, ensure_ascii=False)

    print(f"  [OK] {agent_id}: {len(assessment['questions'])} questions -> {out_file.name}")
    return True


# --------------------------------------------------------------------------- #
#  CLI Commands                                                                #
# --------------------------------------------------------------------------- #

def cmd_list():
    """List available benchmark sources."""
    print("\n=== Available Benchmark Sources ===\n")
    print("  codemmlu     CodeMMLU (Fsoft-AIC)     19,878 programming MCQs")
    print("               Subsets: api_frameworks, programming_syntax, software_principles,")
    print("               dbms_sql, others, code_completion, fill_in_the_middle,")
    print("               code_repair, execution_prediction")
    print()
    print("  cybermetric  CyberMetric              10,000 cybersecurity MCQs (expert-verified)")
    print("               Sizes: 80, 500, 2000, 10000")
    print()
    print("  mmlu-pro     MMLU-Pro (TIGER-Lab)     12,032 multi-domain MCQs (10-option)")
    print("               Relevant: computer_science (410), engineering (969),")
    print("               business (789), math (1351), + src-level filtering")
    print()
    print("=== Agent Mappings ===\n")
    for source, agents in AGENT_MAPPING.items():
        for agent_id, cfg in agents.items():
            if agent_id.startswith("_"):
                continue
            filt = cfg.get("filter", {})
            filt_str = str(filt) if filt else "all"
            print(f"  {source:<14s} -> {agent_id:<30s} (prefix: {cfg['id_prefix']}, filter: {filt_str})")


def cmd_import(source: str, force: bool = False, dry_run: bool = False,
               output_dir: Optional[Path] = None):
    """Import a benchmark source."""
    out = output_dir or OUTPUT_DIR

    if source in ("codemmlu", "all"):
        print("\n--- CodeMMLU ---")
        rows = fetch_codemmlu()
        if rows:
            agent_groups = convert_codemmlu(rows)
            for agent_id, questions in sorted(agent_groups.items()):
                if agent_id.startswith("_"):
                    # Write general pool separately
                    write_assessment("_codemmlu_general", questions,
                                   "CodeMMLU (general pool)", out, force, dry_run)
                else:
                    write_assessment(agent_id, questions,
                                   "CodeMMLU (Fsoft-AIC/CodeMMLU)", out, force, dry_run)

    if source in ("cybermetric", "all"):
        print("\n--- CyberMetric ---")
        rows = fetch_cybermetric(10000)
        if rows:
            questions = convert_cybermetric(rows)
            write_assessment("security_agent", questions,
                           "CyberMetric (cybermetric/CyberMetric)", out, force, dry_run)

    if source in ("mmlu-pro", "mmlu_pro", "all"):
        print("\n--- MMLU-Pro ---")
        rows = fetch_mmlu_pro()
        if rows:
            agent_groups = convert_mmlu_pro(rows)
            for agent_id, questions in sorted(agent_groups.items()):
                write_assessment(agent_id, questions,
                               "MMLU-Pro (TIGER-Lab/MMLU-Pro)", out, force, dry_run)

    if source not in ("codemmlu", "cybermetric", "mmlu-pro", "mmlu_pro", "all"):
        print(f"[X] Unknown source: {source}")
        print("    Available: codemmlu, cybermetric, mmlu-pro, all")


def cmd_status():
    """Show import status."""
    print("\n=== Benchmark Import Status ===\n")
    print(f"  {'Agent':<30s} {'Benchmark Qs':<14s} {'LinkedIn Qs':<14s} {'Original Qs':<14s}")
    print(f"  {'-'*30} {'-'*14} {'-'*14} {'-'*14}")

    # All agents that could have assessments
    agents = set()
    for source_agents in AGENT_MAPPING.values():
        for a in source_agents:
            if not a.startswith("_"):
                agents.add(a)

    for agent_id in sorted(agents):
        bench_file = OUTPUT_DIR / f"{agent_id}.json"
        li_file = COHORT_ROOT / "data" / "assessments_linkedin" / f"{agent_id}.json"
        orig_file = COHORT_ROOT / "data" / "assessments" / f"{agent_id}.json"

        def _count(f):
            if f.exists():
                with open(f, encoding="utf-8") as fh:
                    return str(json.load(fh).get("total_questions", 0))
            return "-"

        print(f"  {agent_id:<30s} {_count(bench_file):<14s} {_count(li_file):<14s} {_count(orig_file):<14s}")


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Benchmark Dataset Importer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List available sources and mappings")

    p_imp = sub.add_parser("import", help="Import a benchmark source")
    p_imp.add_argument("source", help="Source name: codemmlu, cybermetric, mmlu-pro, all")
    p_imp.add_argument("--force", action="store_true", help="Overwrite existing")
    p_imp.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p_imp.add_argument("--output-dir", type=Path, help="Override output directory")

    sub.add_parser("status", help="Show import status")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "import":
        cmd_import(args.source, force=args.force, dry_run=args.dry_run,
                   output_dir=args.output_dir)
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
