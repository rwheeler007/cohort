"""
Overnight assessment retest for 5 underperforming agents.
Runs on GPU 0 via llama-server (primary, port 11435).
Scheduled via Windows Task Scheduler for 2:00 AM.

Before scores: linkedin 94%, hardware_agent 90%, javascript_developer 91%,
               web_developer 93%, code_archaeologist 94%
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

COHORT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = COHORT_ROOT / "data" / "assessment_results" / f"retest_{datetime.now().strftime('%Y-%m-%d')}.log"

AGENTS = ['linkedin', 'hardware_agent', 'javascript_developer', 'web_developer', 'code_archaeologist']
BEFORE = {
    'linkedin': 94.0,
    'hardware_agent': 90.0,
    'javascript_developer': 91.0,
    'web_developer': 93.0,
    'code_archaeologist': 94.0,
}

def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def main():
    log("=" * 50)
    log("OVERNIGHT ASSESSMENT RETEST")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"Agents: {', '.join(AGENTS)}")
    log("=" * 50)

    results = {}

    for agent in AGENTS:
        log(f"\n[*] Starting assessment: {agent}")
        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(COHORT_ROOT / 'tools' / 'agent_assessor.py'),
                 agent, '--backend', 'llamacpp', '--model', 'qwen3.5:9b'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=7200, cwd=str(COHORT_ROOT)
            )
            elapsed = time.time() - start
            log(f"  Elapsed: {elapsed:.0f}s")

            # Log last few lines of output
            output = (proc.stdout or '') + (proc.stderr or '')
            for l in output.strip().split('\n')[-5:]:
                log(f"  {l}")

        except subprocess.TimeoutExpired:
            log("  [X] TIMEOUT after 7200s")
            continue
        except Exception as e:
            log(f"  [X] ERROR: {e}")
            continue

        # Read results from combined_results.json
        result_dirs = [
            COHORT_ROOT / 'data' / 'assessment_results_Qwen_Qwen3.5-9B-Q4_K_M',
            COHORT_ROOT / 'data' / 'assessment_results',
        ]
        for rd in result_dirs:
            rf = rd / 'combined_results.json'
            if rf.exists():
                with open(rf, encoding='utf-8') as f:
                    data = json.load(f)
                for r in data.get('results', []):
                    if r.get('agent_id') == agent:
                        results[agent] = {
                            'score': r['score_pct'],
                            'correct': r['correct'],
                            'total': r['total'],
                        }
                        log(f"  >> Score: {r['score_pct']}% ({r['correct']}/{r['total']})")
                        break
                if agent in results:
                    break

        # Also check per-agent results file
        if agent not in results:
            rf = COHORT_ROOT / 'data' / 'assessment_results' / f'{agent}_results.json'
            if rf.exists():
                with open(rf, encoding='utf-8') as f:
                    r = json.load(f)
                if 'score_pct' in r:
                    results[agent] = {'score': r['score_pct'], 'correct': r['correct'], 'total': r['total']}
                    log(f"  >> Score: {r['score_pct']}% ({r['correct']}/{r['total']})")

    # Summary
    log("\n" + "=" * 56)
    log("BEFORE / AFTER COMPARISON")
    log("=" * 56)
    log(f"{'Agent':30s} {'Before':>8s} {'After':>8s} {'Delta':>8s}")
    log("-" * 56)
    for agent in AGENTS:
        if agent in results:
            b = BEFORE[agent]
            a = results[agent]['score']
            d = a - b
            sign = '+' if d > 0 else ''
            log(f"{agent:30s} {b:7.1f}% {a:7.1f}% {sign}{d:6.1f}%")
        else:
            log(f"{agent:30s} {BEFORE[agent]:7.1f}%   FAILED")

    log("\n[*] Retest complete. Results saved to assessment_results dirs.")
    log(f"[*] Log file: {LOG_FILE}")

if __name__ == '__main__':
    # Ensure stdout handles unicode
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    main()
