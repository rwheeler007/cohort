"""
Run All Demos — orchestrator for the full "Zero to Conversation" suite.

Runs each demo method, collects timing data, and generates a summary
page with timing results for the marketing website.

Usage:
    python run_all_demos.py              # Run everything
    python run_all_demos.py --browser    # Browser demos only (Playwright)
    python run_all_demos.py --cli        # CLI demos only (terminal)
    python run_all_demos.py --summary    # Just regenerate summary from existing timing files

Output:
    recordings/
      *.webm          — Playwright video recordings
      *.png           — Step screenshots
      *-timing.json   — Per-demo timing data
      *-transcript.txt — CLI session transcripts
      summary.json    — Combined timing summary
      summary.html    — Marketing-ready comparison page
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
RECORDINGS = ROOT / "recordings"
RECORDINGS.mkdir(exist_ok=True)


def run_playwright_demos():
    """Run all Playwright browser demos."""
    print("\n" + "=" * 60)
    print("  BROWSER DEMOS (Playwright)")
    print("=" * 60)

    projects = ["web-ui", "vscode-style", "docker"]
    for project in projects:
        print(f"\n>>> Running {project} demo...")
        result = subprocess.run(
            ["npx", "playwright", "test", f"--project={project}"],
            cwd=str(ROOT),
            timeout=300,
        )
        if result.returncode != 0:
            print(f"[!] {project} demo failed (exit {result.returncode})")
        else:
            print(f"[OK] {project} demo complete")


def run_cli_demos():
    """Run all CLI terminal demos."""
    print("\n" + "=" * 60)
    print("  CLI DEMOS (terminal)")
    print("=" * 60)

    result = subprocess.run(
        [sys.executable, str(ROOT / "helpers" / "cli_demo.py"), "--all"],
        cwd=str(ROOT),
        timeout=300,
    )
    if result.returncode != 0:
        print(f"[!] CLI demos failed (exit {result.returncode})")
    else:
        print("[OK] CLI demos complete")


def collect_timing_summary() -> dict:
    """Read all timing JSON files and build a combined summary."""
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "methods": {},
    }

    timing_files = sorted(RECORDINGS.glob("*-timing.json"))
    for tf in timing_files:
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            # Extract method name from filename: "web-ui-timing.json" -> "web-ui"
            method = tf.stem.replace("-timing", "")
            summary["methods"][method] = {
                "total_s": data.get("total_s", data.get("total_ms", 0) / 1000),
                "marks": data.get("marks", []),
            }
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[!] Could not parse {tf.name}: {e}")

    return summary


def generate_summary_html(summary: dict) -> str:
    """Generate a marketing-ready HTML comparison page."""
    methods_meta = {
        "web-ui": {
            "label": "Web Dashboard",
            "icon": "[W]",
            "audience": "Browser users",
            "steps": "pip install -> cohort serve -> browser",
        },
        "vscode": {
            "label": "VS Code Extension",
            "icon": "[V]",
            "audience": "VS Code users",
            "steps": "Marketplace install -> wizard -> chat",
        },
        "docker": {
            "label": "Docker",
            "icon": "[D]",
            "audience": "DevOps / self-host",
            "steps": "docker run -> browser -> chat",
        },
        "cli-pip": {
            "label": "pip install (CLI)",
            "icon": "[P]",
            "audience": "Python developers",
            "steps": "pip install cohort -> cohort setup -> serve",
        },
        "cli-mcp": {
            "label": "MCP Server",
            "icon": "[M]",
            "audience": "Claude Code / Cursor",
            "steps": "pip install -> MCP config -> restart",
        },
        "cli-api": {
            "label": "Python API",
            "icon": "[A]",
            "audience": "Builders / integrators",
            "steps": "pip install -> 6-line script -> run",
        },
    }

    cards_html = ""
    for method_id, data in summary["methods"].items():
        meta = methods_meta.get(method_id, {
            "label": method_id,
            "icon": "[?]",
            "audience": "",
            "steps": "",
        })
        total = data["total_s"]
        cards_html += f"""
        <div class="method-card">
            <div class="method-icon">{meta['icon']}</div>
            <div class="method-time">{total:.0f}s</div>
            <div class="method-label">{meta['label']}</div>
            <div class="method-audience">{meta['audience']}</div>
            <div class="method-steps">{meta['steps']}</div>
        </div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cohort - Zero to Conversation</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            min-height: 100vh;
        }}
        .hero {{
            text-align: center;
            padding: 80px 20px 40px;
        }}
        .hero h1 {{
            font-size: 3rem;
            font-weight: 700;
            margin-bottom: 16px;
            background: linear-gradient(135deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .hero p {{
            font-size: 1.2rem;
            color: #888;
            max-width: 600px;
            margin: 0 auto;
        }}
        .disclaimer {{
            text-align: center;
            padding: 12px 20px;
            color: #666;
            font-size: 0.85rem;
            border-top: 1px solid #1a1a1a;
            border-bottom: 1px solid #1a1a1a;
            background: #0f0f0f;
        }}
        .methods-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 24px;
            max-width: 1200px;
            margin: 48px auto;
            padding: 0 24px;
        }}
        .method-card {{
            background: #141414;
            border: 1px solid #222;
            border-radius: 12px;
            padding: 32px 24px;
            text-align: center;
            transition: border-color 0.2s, transform 0.2s;
        }}
        .method-card:hover {{
            border-color: #60a5fa;
            transform: translateY(-2px);
        }}
        .method-icon {{
            font-family: monospace;
            font-size: 1.4rem;
            color: #60a5fa;
            margin-bottom: 12px;
        }}
        .method-time {{
            font-size: 3rem;
            font-weight: 700;
            color: #fff;
            margin-bottom: 8px;
        }}
        .method-label {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        .method-audience {{
            font-size: 0.85rem;
            color: #888;
            margin-bottom: 12px;
        }}
        .method-steps {{
            font-size: 0.8rem;
            color: #555;
            font-family: monospace;
        }}
        .footer {{
            text-align: center;
            padding: 48px 20px;
            color: #444;
            font-size: 0.85rem;
        }}
        .footer a {{
            color: #60a5fa;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="hero">
        <h1>Every way to start. All under 90 seconds.</h1>
        <p>Pick your path. From install to your first AI-powered conversation,
           timed start to finish.</p>
    </div>

    <div class="disclaimer">
        Assumes Ollama is installed and model is downloaded (~5 min one-time setup).
        All times measured on commodity hardware.
    </div>

    <div class="methods-grid">
{cards_html}
    </div>

    <div class="footer">
        <p>Recorded with Playwright on {summary['generated_at'][:10]}</p>
        <p>Timing data: <a href="summary.json">summary.json</a></p>
    </div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Run all Cohort demos")
    parser.add_argument("--browser", action="store_true", help="Browser demos only")
    parser.add_argument("--cli", action="store_true", help="CLI demos only")
    parser.add_argument("--summary", action="store_true", help="Regenerate summary only")
    args = parser.parse_args()

    run_all = not args.browser and not args.cli and not args.summary

    if run_all or args.browser:
        run_playwright_demos()

    if run_all or args.cli:
        run_cli_demos()

    # Always generate summary
    print("\n" + "=" * 60)
    print("  GENERATING SUMMARY")
    print("=" * 60)

    summary = collect_timing_summary()

    # Save JSON
    summary_json = RECORDINGS / "summary.json"
    summary_json.write_text(json.dumps(summary, indent=2))
    print(f"[OK] {summary_json}")

    # Save HTML
    summary_html = RECORDINGS / "summary.html"
    summary_html.write_text(generate_summary_html(summary))
    print(f"[OK] {summary_html}")

    # Print final summary
    print("\n" + "=" * 60)
    print("  FINAL RESULTS")
    print("=" * 60)
    for method, data in summary["methods"].items():
        print(f"  {method:20s}  {data['total_s']:6.1f}s")
    print("=" * 60)

    print(f"\nOpen {summary_html} to see the marketing page.")


if __name__ == "__main__":
    main()
