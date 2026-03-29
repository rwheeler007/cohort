#!/usr/bin/env python3
"""Overnight Benchmark Assessment Runner.

Runs all agents through assessment banks sequentially using llama-server
(llama.cpp) as the inference backend. llama-server supports KV cache
offloading to system RAM, so even long benchmark prompts fit in memory.

Usage:
    python tools/run_benchmark_overnight.py                  # Run all 3 banks
    python tools/run_benchmark_overnight.py --bank benchmark # Only benchmark bank
    python tools/run_benchmark_overnight.py --bank linkedin  # Only LinkedIn bank
    python tools/run_benchmark_overnight.py --bank original  # Only original bank
    python tools/run_benchmark_overnight.py --limit 100      # Cap questions per agent
    python tools/run_benchmark_overnight.py --dry-run        # Preview without running

Prerequisites:
    llama-server running on port 8080 with the target model loaded.
    See config/llm_router.yaml for server config and model aliases.
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

COHORT_ROOT = Path(__file__).parent.parent
ASSESSOR = COHORT_ROOT / "tools" / "agent_assessor.py"
LOG_DIR = COHORT_ROOT / "data" / "benchmark_logs"

LLAMACPP_URL = "http://localhost:11435"
BACKEND = "llamacpp"  # "ollama" or "llamacpp"

MODEL = "qwen3.5:9b"

# Seconds per question estimate (based on observed GPU0 avg of ~40s)
SECS_PER_QUESTION = 40

# Agent workload estimates (question counts per bank)
AGENT_LOADS = {
    "benchmark": {
        "security_agent": 10226,
        "system_coder": 7057,
        "python_developer": 1393,
        "javascript_developer": 1009,
        "hardware_agent": 969,
        "ceo_agent": 789,
        "web_developer": 355,
        "database_developer": 271,
        "coding_orchestrator": 195,
        "ai_infrastructure_agent": 90,
    },
    "linkedin": {
        "coding_orchestrator": 905,
        "ceo_agent": 740,
        "web_developer": 467,
        "qa_agent": 349,
        "database_developer": 308,
        "python_developer": 222,
        "javascript_developer": 215,
        "system_coder": 206,
        "security_agent": 173,
        "ai_infrastructure_agent": 118,
        "hardware_agent": 59,
        "documentation_agent": 47,
    },
    "original": {
        "python_developer": 100,
        "ceo_agent": 100,
        "coding_orchestrator": 100,
        "database_developer": 100,
        "documentation_agent": 100,
        "hardware_agent": 100,
        "javascript_developer": 100,
        "qa_agent": 100,
        "security_agent": 100,
        "system_coder": 100,
        "web_developer": 100,
        "code_archaeologist": 100,
        "supervisor_agent": 100,
        "setup_guide": 100,
    },
}


def build_command(agents: list[str], bank: str,
                  limit: int | None = None, resume: bool = True) -> list[str]:
    """Build the agent_assessor.py command."""
    cmd = [sys.executable, str(ASSESSOR)]

    if bank == "benchmark":
        cmd.append("--benchmark")
    elif bank == "linkedin":
        cmd.append("--linkedin")
    # original = no flag

    cmd.extend(["--model", MODEL])
    cmd.extend(["--backend", BACKEND])
    if BACKEND == "llamacpp":
        cmd.extend(["--ollama-url", LLAMACPP_URL])  # reuses --ollama-url for base URL

    if limit:
        cmd.extend(["--limit", str(limit)])
    if resume:
        cmd.append("--resume")

    cmd.extend(agents)
    return cmd


def wait_for_worker(proc, fh, logfile, bank: str):
    """Wait for a single worker process to finish.

    Returns exit code.
    """
    try:
        while True:
            ret = proc.poll()
            if ret is not None:
                fh.close()
                lines = logfile.read_text(encoding="utf-8", errors="replace").splitlines()
                last_lines = lines[-5:] if len(lines) >= 5 else lines
                status = "OK" if ret == 0 else f"FAILED (exit {ret})"
                print(f"  [{status}] {bank} finished")
                for line in last_lines:
                    if "OVERALL" in line or "passed:" in line.lower() or ">>" in line:
                        print(f"    {line.strip()}")
                return ret

            # Show progress from log tail
            try:
                fh.flush()
                content = logfile.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                for line in reversed(lines):
                    if "Asking..." in line:
                        print(f"  {bank}: {line.strip()[:100]}")
                        break
            except Exception:
                pass

            time.sleep(30)

    except KeyboardInterrupt:
        print("\n\n  [!] Detached from monitoring. Worker continues in background.")
        print(f"  PID: {proc.pid}")
        print(f"  Log: {logfile}")
        try:
            fh.close()
        except Exception:
            pass
        raise


def run_overnight(banks: list[str], limit: int | None = None, dry_run: bool = False):
    """Run benchmark assessments on a single Ollama instance (both GPUs).

    Banks run sequentially. All agents within a bank are processed by one
    worker process hitting the single Ollama instance.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "started_at": datetime.now().isoformat(),
        "model": MODEL,
        "backend": BACKEND,
        "inference_url": LLAMACPP_URL if BACKEND == "llamacpp" else "ollama:11434",
        "banks": banks,
        "limit": limit,
        "workers": [],
        "bank_results": [],
    }

    # Print plan for all banks
    total_qs = 0
    for bank in banks:
        agents = AGENT_LOADS.get(bank, {})
        if limit:
            agents = {a: min(load, limit) for a, load in agents.items()}

        agent_list = sorted(agents.items(), key=lambda x: x[1], reverse=True)
        bank_total = sum(agents.values())
        total_qs += bank_total

        print(f"\n=== {bank.upper()} Bank === ({len(agents)} agents, ~{bank_total} questions)")
        for a, count in agent_list:
            print(f"    {a}: {count} qs")

        est_hours = bank_total * SECS_PER_QUESTION / 3600
        print(f"  Estimated time: ~{est_hours:.1f} hours")

    total_hours = total_qs * SECS_PER_QUESTION / 3600
    print(f"\n  Total: ~{total_qs} questions, ~{total_hours:.1f} hours")

    if dry_run:
        print(f"\n[DRY RUN] Would run {total_qs} questions across {len(banks)} banks")
        return

    # Save run manifest
    manifest_file = LOG_DIR / f"{timestamp}_manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\n[OK] Run manifest: {manifest_file}")

    # Run banks sequentially
    try:
        for bank_idx, bank in enumerate(banks):
            agents = AGENT_LOADS.get(bank, {})
            if limit:
                agents = {a: min(load, limit) for a, load in agents.items()}

            agent_list = list(agents.keys())

            print(f"\n{'='*50}")
            print(f"  Starting bank {bank_idx+1}/{len(banks)}: {bank.upper()}")
            print(f"  {len(agent_list)} agents")
            print(f"{'='*50}")

            cmd = build_command(agent_list, bank, limit)
            logfile = LOG_DIR / f"{timestamp}_{bank}.log"
            print(f"  [>>] Starting worker -> {logfile.name}")

            fh = open(logfile, "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                    cwd=str(COHORT_ROOT))

            summary["workers"].append({
                "bank": bank, "agents": agent_list,
                "questions": sum(agents.values()),
                "log": str(logfile), "pid": proc.pid,
            })

            print(f"  PID: {proc.pid}")
            print("  (Ctrl+C to detach -- worker continues in background)\n")

            exit_code = wait_for_worker(proc, fh, logfile, bank)
            summary["bank_results"].append({
                "bank": bank,
                "completed_at": datetime.now().isoformat(),
                "exit_code": exit_code,
            })

            # Update manifest after each bank
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)

            remaining = len(banks) - bank_idx - 1
            if remaining > 0:
                print(f"\n  [OK] {bank.upper()} complete. {remaining} bank(s) remaining.")

    except KeyboardInterrupt:
        print("\n  [!] Detached. Remaining banks will NOT start.")
        print(f"  Logs: {LOG_DIR}")
        summary["interrupted_at"] = datetime.now().isoformat()
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        return

    # Final summary
    summary["completed_at"] = datetime.now().isoformat()
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n=== All {len(banks)} banks complete ===")
    print(f"  Logs: {LOG_DIR}")
    print(f"  Results: {COHORT_ROOT / 'data' / 'assessment_results'}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Overnight Benchmark Assessment Runner")
    parser.add_argument("--bank", choices=["benchmark", "linkedin", "original", "all"],
                        default="all", help="Which assessment bank to run (default: all)")
    parser.add_argument("--limit", type=int, help="Cap questions per agent")
    parser.add_argument("--dry-run", action="store_true", help="Preview plan without running")
    args = parser.parse_args()

    if args.bank == "all":
        banks = ["benchmark", "linkedin", "original"]
    else:
        banks = [args.bank]

    run_overnight(banks, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
