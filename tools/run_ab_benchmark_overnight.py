#!/usr/bin/env python3
"""Overnight A/B Benchmark Runner.

Runs all three response-mode benchmark scenarios (code_review, architecture,
triage) repeatedly through the live Cohort server's HTTP API to build a
statistically meaningful token/quality dataset for the Smart/Smarter/Smartest
comparison.

This script talks to the running Cohort server -- it does NOT import cohort
modules directly. Start the server before running this.

Usage:
    python tools/run_ab_benchmark_overnight.py                     # 3 passes of all 3 scenarios
    python tools/run_ab_benchmark_overnight.py --passes 5          # 5 passes
    python tools/run_ab_benchmark_overnight.py --scenarios code_review architecture  # subset
    python tools/run_ab_benchmark_overnight.py --port 5100         # non-default port
    python tools/run_ab_benchmark_overnight.py --dry-run           # preview plan, no execution

Each run polls the server every 30s until completion, then sleeps a brief
cooldown before starting the next run. Results accumulate in the server's
benchmark.db automatically.

Output:
    data/ab_benchmark_logs/YYYYMMDD_HHMMSS_summary.json   -- run manifest + token stats
    Console table printed at the end with per-scenario token savings summary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("[X] requests is required: pip install requests")
    sys.exit(1)

COHORT_ROOT = Path(__file__).parent.parent
LOG_DIR = COHORT_ROOT / "data" / "ab_benchmark_logs"

ALL_SCENARIOS = ["code_review", "architecture", "triage"]

# Seconds between the end of one run and the start of the next.
# Gives the server time to flush logs and free any held model context.
COOLDOWN_SECS = 15

# How often to poll for run completion (seconds)
POLL_INTERVAL = 30

# Rough upper bound per run (both arms) -- if a run exceeds this, skip and move on
RUN_TIMEOUT_SECS = 30 * 60  # 30 minutes


def base_url(port: int) -> str:
    return f"http://localhost:{port}"


def check_server(port: int) -> bool:
    """Return True if the Cohort server is reachable."""
    try:
        r = requests.get(f"{base_url(port)}/api/benchmark/status", timeout=5)
        data = r.json()
        if not data.get("enabled", False):
            print("[!] Benchmark is disabled on this server (BENCHMARK_ENABLED=False)")
            return False
        return True
    except Exception as exc:
        print(f"[X] Cannot reach Cohort server on port {port}: {exc}")
        return False


def get_latest_run(port: int) -> dict | None:
    """Return the most recent run from the server."""
    try:
        r = requests.get(f"{base_url(port)}/api/benchmark/runs", timeout=10)
        runs = r.json().get("runs", [])
        return runs[0] if runs else None
    except Exception:
        return None


def start_run(port: int, scenario_id: str) -> dict | None:
    """POST /api/benchmark/start. Returns run dict or None on failure."""
    try:
        r = requests.post(
            f"{base_url(port)}/api/benchmark/start",
            json={"scenario_id": scenario_id},
            timeout=15,
        )
        if r.status_code == 409:
            print(f"  [!] Server says a run is already active -- waiting for it to finish")
            return None
        if r.status_code != 200:
            print(f"  [X] start_run HTTP {r.status_code}: {r.text[:200]}")
            return None
        return r.json().get("run")
    except Exception as exc:
        print(f"  [X] start_run failed: {exc}")
        return None


def poll_until_done(port: int, run_id: str, timeout: int = RUN_TIMEOUT_SECS) -> dict | None:
    """Poll until the run reaches status 'scored' or 'complete'. Returns final run dict."""
    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url(port)}/api/benchmark/runs/{run_id}", timeout=10)
            if r.status_code == 200:
                run = r.json()
                status = run.get("status", "")
                if status != last_status:
                    print(f"  [*] Run {run_id}: {status}")
                    last_status = status
                if status in ("scored", "complete", "error"):
                    return run
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)

    print(f"  [!] Run {run_id} timed out after {timeout}s -- skipping")
    return None


def extract_token_stats(run: dict) -> dict:
    """Pull token counts out of a completed run dict."""
    arm_a = run.get("arm_a", {})
    arm_b = run.get("arm_b", {})
    return {
        "run_id": run.get("id", "?"),
        "scenario_id": run.get("scenario_id", "?"),
        "status": run.get("status", "?"),
        "arm_a_mode": arm_a.get("mode", "?"),
        "arm_b_mode": arm_b.get("mode", "?"),
        # Local tokens (Arm A -- smarter baseline)
        "a_local_in": arm_a.get("total_tokens_in", 0),
        "a_local_out": arm_a.get("total_tokens_out", 0),
        # Local tokens (Arm B -- smartest, phase 1+2 Qwen)
        "b_local_in": arm_b.get("total_tokens_in", 0),
        "b_local_out": arm_b.get("total_tokens_out", 0),
        # Claude tokens (Arm B -- phase 3 only)
        "b_claude_in": arm_b.get("total_claude_in", 0),
        "b_claude_out": arm_b.get("total_claude_out", 0),
        # Timing
        "a_elapsed": arm_a.get("total_elapsed", 0),
        "b_elapsed": arm_b.get("total_elapsed", 0),
        # Scores (weighted totals if present)
        "a_scores": arm_a.get("scores", {}),
        "b_scores": arm_b.get("scores", {}),
    }


def print_summary_table(all_stats: list[dict]) -> None:
    """Print a summary table of token savings across all completed runs."""
    if not all_stats:
        print("\n[!] No completed runs to summarise.")
        return

    # Group by scenario
    by_scenario: dict[str, list[dict]] = {}
    for s in all_stats:
        sid = s["scenario_id"]
        by_scenario.setdefault(sid, []).append(s)

    print("\n" + "=" * 70)
    print("  OVERNIGHT A/B BENCHMARK SUMMARY")
    print("=" * 70)
    print(f"  {'Scenario':<20} {'Runs':>5} {'Avg Claude-In':>14} {'Avg A Local-In':>15} {'Savings %':>10}")
    print("-" * 70)

    total_savings = []
    for scenario_id, stats in sorted(by_scenario.items()):
        completed = [s for s in stats if s["status"] in ("scored", "complete")]
        if not completed:
            continue
        avg_claude_in = sum(s["b_claude_in"] for s in completed) / len(completed)
        avg_a_in = sum(s["a_local_in"] for s in completed) / len(completed)
        # Savings: what Claude WOULD have seen (all local context = arm_a_in)
        # vs what it DID see (distilled briefing = b_claude_in)
        if avg_a_in > 0:
            savings_pct = (1 - avg_claude_in / avg_a_in) * 100
            total_savings.append(savings_pct)
        else:
            savings_pct = 0.0
        print(f"  {scenario_id:<20} {len(completed):>5} {avg_claude_in:>14,.0f} {avg_a_in:>15,.0f} {savings_pct:>9.1f}%")

    if total_savings:
        overall = sum(total_savings) / len(total_savings)
        print("-" * 70)
        print(f"  {'OVERALL AVERAGE':<20} {'':>5} {'':>14} {'':>15} {overall:>9.1f}%")
    print("=" * 70)
    print()
    print("  Interpretation: 'Savings %' = how much less context Claude received")
    print("  in Smartest mode vs what a direct (Smarter) call would have sent.")
    print("  Higher = more API cost reduction via preprocessing.\n")


def run_overnight(
    scenarios: list[str],
    passes: int,
    port: int,
    dry_run: bool,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    plan = [(scenario, pass_num) for pass_num in range(1, passes + 1) for scenario in scenarios]
    total = len(plan)

    print(f"\n[*] Overnight A/B Benchmark Plan")
    print(f"    Scenarios : {', '.join(scenarios)}")
    print(f"    Passes    : {passes}")
    print(f"    Total runs: {total}")
    print(f"    Port      : {port}")
    print(f"    Cooldown  : {COOLDOWN_SECS}s between runs")
    print(f"    Timeout   : {RUN_TIMEOUT_SECS // 60}m per run")
    print()

    if dry_run:
        for i, (scenario, pass_num) in enumerate(plan, 1):
            print(f"    [{i:>2}/{total}] {scenario}  (pass {pass_num})")
        print(f"\n[DRY RUN] Would execute {total} runs. Exiting.\n")
        return

    if not check_server(port):
        sys.exit(1)

    manifest = {
        "started_at": datetime.now().isoformat(),
        "scenarios": scenarios,
        "passes": passes,
        "port": port,
        "runs": [],
    }
    manifest_file = LOG_DIR / f"{timestamp}_summary.json"

    all_stats: list[dict] = []

    for run_num, (scenario_id, pass_num) in enumerate(plan, 1):
        print(f"\n[{run_num}/{total}] {scenario_id}  pass {pass_num}/{passes}  --  {datetime.now().strftime('%H:%M:%S')}")

        run_info = start_run(port, scenario_id)
        if run_info is None:
            # Might already be running (e.g. stale active run). Wait and retry once.
            print(f"  [!] Could not start -- waiting 60s and retrying once")
            time.sleep(60)
            run_info = start_run(port, scenario_id)
            if run_info is None:
                print(f"  [X] Skipping {scenario_id} pass {pass_num}")
                manifest["runs"].append({"scenario_id": scenario_id, "pass": pass_num, "status": "skipped"})
                continue

        run_id = run_info.get("id", "?")
        print(f"  [OK] Run started: {run_id}")

        completed_run = poll_until_done(port, run_id)
        if completed_run is None:
            manifest["runs"].append({"scenario_id": scenario_id, "pass": pass_num, "run_id": run_id, "status": "timeout"})
        else:
            stats = extract_token_stats(completed_run)
            all_stats.append(stats)
            manifest["runs"].append({
                "scenario_id": scenario_id,
                "pass": pass_num,
                "run_id": run_id,
                "status": completed_run.get("status"),
                "token_stats": stats,
            })
            # Brief inline token report
            a_in = stats["a_local_in"]
            c_in = stats["b_claude_in"]
            if a_in > 0:
                savings = (1 - c_in / a_in) * 100
                print(f"  [>>] Smarter context: {a_in:,} tokens  |  Claude saw: {c_in:,} tokens  |  Savings: {savings:.1f}%")
            else:
                print(f"  [>>] Claude tokens: {c_in:,} (baseline context not available)")

        # Persist manifest after each run
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        if run_num < total:
            print(f"  [*] Cooling down {COOLDOWN_SECS}s ...")
            time.sleep(COOLDOWN_SECS)

    manifest["completed_at"] = datetime.now().isoformat()
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print_summary_table(all_stats)
    print(f"[OK] Manifest saved: {manifest_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Overnight A/B response-mode benchmark runner")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=ALL_SCENARIOS,
        default=ALL_SCENARIOS,
        metavar="SCENARIO",
        help=f"Scenarios to run (default: all). Choices: {', '.join(ALL_SCENARIOS)}",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=3,
        help="How many times to run each scenario (default: 3)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5100,
        help="Cohort server port (default: 5100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the run plan without executing anything",
    )
    args = parser.parse_args()

    run_overnight(
        scenarios=args.scenarios,
        passes=args.passes,
        port=args.port,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
