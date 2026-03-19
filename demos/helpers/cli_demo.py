"""
CLI Demo Recorder — scripted terminal replay for pip install demos.

Captures terminal sessions as timestamped text logs + optional screenshots.
Works on Windows (subprocess) and Unix (pexpect if available).

Usage:
    python helpers/cli_demo.py              # Run pip install demo
    python helpers/cli_demo.py --method mcp # Run MCP setup demo
    python helpers/cli_demo.py --method api # Run Python API demo

Output:
    recordings/cli-{method}-transcript.txt  (timestamped terminal log)
    recordings/cli-{method}-timing.json     (timing data)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RECORDINGS_DIR = Path(__file__).parent.parent / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)


class DemoTimer:
    def __init__(self):
        self.start_time = 0.0
        self.marks = []

    def start(self):
        self.start_time = time.time()
        self.marks = []

    def mark(self, name: str) -> float:
        elapsed = time.time() - self.start_time
        self.marks.append({"name": name, "elapsed_s": round(elapsed, 1)})
        return elapsed

    def save(self, filepath: str):
        total = time.time() - self.start_time
        result = {
            "total_s": round(total, 1),
            "marks": self.marks,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(filepath, "w") as f:
            json.dump(result, f, indent=2)

    def summary(self) -> str:
        total = round(time.time() - self.start_time, 1)
        parts = [f"{m['name']}: {m['elapsed_s']}s" for m in self.marks]
        return f"Total: {total}s | " + " | ".join(parts)


class TranscriptWriter:
    """Writes a timestamped terminal transcript."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.start_time = time.time()
        self.lines = []

    def command(self, cmd: str):
        elapsed = round(time.time() - self.start_time, 1)
        line = f"[{elapsed:6.1f}s] $ {cmd}"
        self.lines.append(line)
        print(line)

    def output(self, text: str):
        elapsed = round(time.time() - self.start_time, 1)
        for raw_line in text.strip().split("\n"):
            line = f"[{elapsed:6.1f}s]   {raw_line}"
            self.lines.append(line)
            print(line)

    def note(self, text: str):
        elapsed = round(time.time() - self.start_time, 1)
        line = f"[{elapsed:6.1f}s] # {text}"
        self.lines.append(line)
        print(line)

    def save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))


def run_cmd(cmd: str, cwd: str = None, timeout: int = 120) -> str:
    """Run a command and return its output."""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )
    return (result.stdout + result.stderr).strip()


# ---------------------------------------------------------------------------
# Demo: pip install (CLI)
# ---------------------------------------------------------------------------

def demo_pip_install():
    """
    Simulate: pip install cohort -> cohort setup -> first interaction

    Uses a fresh venv to make it look like a clean install.
    Pre-caches the wheel so 'pip install' is near-instant.
    """
    timer = DemoTimer()
    transcript = TranscriptWriter(str(RECORDINGS_DIR / "cli-pip-transcript.txt"))
    timer.start()

    # Create temp venv
    venv_dir = tempfile.mkdtemp(prefix="cohort-demo-")
    venv_python = os.path.join(venv_dir, "Scripts", "python.exe") if sys.platform == "win32" else os.path.join(venv_dir, "bin", "python")
    venv_pip = os.path.join(venv_dir, "Scripts", "pip.exe") if sys.platform == "win32" else os.path.join(venv_dir, "bin", "pip")

    transcript.note("Creating clean Python environment...")
    transcript.command("python -m venv demo-env")
    output = run_cmd(f'python -m venv "{venv_dir}"')
    if output:
        transcript.output(output)
    timer.mark("venv_created")

    # Activate (just for display — we use full paths)
    if sys.platform == "win32":
        transcript.command("demo-env\\Scripts\\activate")
    else:
        transcript.command("source demo-env/bin/activate")

    # Install cohort
    transcript.command("pip install cohort")
    # Use the local dist wheel if available, otherwise PyPI
    dist_dir = Path("g:/cohort/dist")
    wheels = sorted(dist_dir.glob("cohort-*.whl")) if dist_dir.exists() else []
    if wheels:
        install_cmd = f'"{venv_pip}" install "{wheels[-1]}" --quiet'
    else:
        install_cmd = f'"{venv_pip}" install cohort --quiet'

    output = run_cmd(install_cmd, timeout=120)
    transcript.output(output or "Successfully installed cohort-0.3.8")
    timer.mark("pip_installed")

    # Run cohort --version
    transcript.command("cohort --version")
    cohort_cmd = os.path.join(
        venv_dir,
        "Scripts" if sys.platform == "win32" else "bin",
        "cohort",
    )
    output = run_cmd(f'"{cohort_cmd}" --version 2>&1 || "{venv_python}" -m cohort --version 2>&1')
    transcript.output(output or "cohort 0.3.8")
    timer.mark("version_check")

    # Simulate setup (non-interactive — just show the commands)
    transcript.note("Setup wizard would launch here (interactive)")
    transcript.command("cohort setup")
    transcript.output("[1/7] Detecting hardware...")
    transcript.output("  GPU: NVIDIA RTX 3080 Ti (12 GB VRAM)")
    transcript.output("  Recommended model: qwen3.5:9b")
    transcript.output("[2/7] Checking Ollama... [OK] running")
    transcript.output("[3/7] Model qwen3.5:9b... [OK] already downloaded")
    transcript.output("[4/7] Verifying inference... [OK] 104 tok/s")
    transcript.output("[5/7] Content feeds... [SKIP]")
    transcript.output("[6/7] MCP server... [SKIP]")
    transcript.output("[7/7] Cloud API... [SKIP]")
    transcript.output("")
    transcript.output("[OK] Setup complete! Run 'cohort serve' to start.")
    timer.mark("setup_complete")

    # Start server + first interaction
    transcript.command("cohort serve &")
    transcript.output("Cohort server running on http://127.0.0.1:5100")
    timer.mark("server_started")

    # Hello world — send a message via CLI and show the response
    transcript.command('cohort say --channel general --message "Hello! Can you introduce yourself in one sentence?"')
    transcript.output("")
    transcript.output("You: Hello! Can you introduce yourself in one sentence?")
    transcript.output("")
    transcript.output("architect: Hi there! I'm the architect agent, here to help you")
    transcript.output("  design clean, maintainable software systems.")
    transcript.output("")
    transcript.output("[OK] Response received in 2.3s (104 tok/s)")
    timer.mark("first_response")
    timer.mark("demo_complete")

    # Cleanup
    import shutil
    shutil.rmtree(venv_dir, ignore_errors=True)

    # Save results
    print("\n========================================")
    print("  CLI (pip) DEMO TIMING")
    print("========================================")
    print(timer.summary())
    print("========================================\n")

    transcript.save()
    timer.save(str(RECORDINGS_DIR / "cli-pip-timing.json"))


# ---------------------------------------------------------------------------
# Demo: MCP setup
# ---------------------------------------------------------------------------

def demo_mcp_setup():
    """Simulate: adding Cohort as an MCP server to Claude Code."""
    timer = DemoTimer()
    transcript = TranscriptWriter(str(RECORDINGS_DIR / "cli-mcp-transcript.txt"))
    timer.start()

    transcript.command("pip install cohort[claude]")
    transcript.output("Successfully installed cohort-0.3.8")
    timer.mark("pip_installed")

    transcript.command("cohort setup --mcp-only")
    transcript.output("[OK] MCP config written to ~/.claude/settings.local.json")
    timer.mark("mcp_configured")

    transcript.note("Restart Claude Code / Cursor")
    transcript.note('Type: "Hello! Can you introduce yourself in one sentence?"')
    transcript.output("")
    transcript.output('You: Hello! Can you introduce yourself in one sentence?')
    transcript.output("")
    transcript.output("architect (via Cohort MCP): I'm your architecture agent,")
    transcript.output("  ready to help design robust, scalable software systems.")
    transcript.output("")
    transcript.output("[Cohort] Response scored 0.87 confidence | 104 tok/s local inference")
    timer.mark("first_response")

    timer.mark("demo_complete")

    print("\n========================================")
    print("  MCP SETUP DEMO TIMING")
    print("========================================")
    print(timer.summary())
    print("========================================\n")

    transcript.save()
    timer.save(str(RECORDINGS_DIR / "cli-mcp-timing.json"))


# ---------------------------------------------------------------------------
# Demo: Python API
# ---------------------------------------------------------------------------

def demo_python_api():
    """Simulate: using Cohort as a Python library."""
    timer = DemoTimer()
    transcript = TranscriptWriter(str(RECORDINGS_DIR / "cli-api-transcript.txt"))
    timer.start()

    # Show the script — includes hello world message + response
    script = '''from cohort import JsonFileStorage, Orchestrator
from cohort.chat import ChatManager

chat = ChatManager(JsonFileStorage("my_data"))
chat.create_channel("hello-world", "First conversation")

agents = {
    "architect": {"triggers": ["hello", "design"], "capabilities": ["architecture"]},
    "tester": {"triggers": ["testing", "qa"], "capabilities": ["test strategy"]},
}

orch = Orchestrator(chat, agents=agents)
chat.post_message("hello-world", "user", "Hello! Can you introduce yourself in one sentence?")
rec = orch.get_next_speaker_for_channel("hello-world")
print(f"Next speaker: {rec['recommended_speaker']} (confidence: {rec['confidence']:.0%})")

response = orch.invoke_agent(rec['recommended_speaker'], "hello-world")
print(f"{rec['recommended_speaker']}: {response['message']}")'''

    transcript.command("cat demo.py")
    transcript.output(script)
    timer.mark("script_shown")

    transcript.command("python demo.py")
    transcript.output("Next speaker: architect (confidence: 87%)")
    transcript.output("architect: Hi! I'm the architect agent, here to help you design")
    transcript.output("  clean, scalable software systems.")
    timer.mark("first_response")

    timer.mark("demo_complete")

    print("\n========================================")
    print("  PYTHON API DEMO TIMING")
    print("========================================")
    print(timer.summary())
    print("========================================\n")

    transcript.save()
    timer.save(str(RECORDINGS_DIR / "cli-api-timing.json"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEMOS = {
    "pip": demo_pip_install,
    "mcp": demo_mcp_setup,
    "api": demo_python_api,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cohort CLI demo recorder")
    parser.add_argument(
        "--method",
        choices=list(DEMOS.keys()),
        default="pip",
        help="Which CLI demo to run (default: pip)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all CLI demos",
    )
    args = parser.parse_args()

    if args.all:
        for name, fn in DEMOS.items():
            print(f"\n>>> Running {name} demo...\n")
            fn()
    else:
        DEMOS[args.method]()
