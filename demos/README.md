# Cohort Demo Recorder

Playwright-automated "Zero to Conversation" demo recordings for every Cohort install method.

## Quick Start

```bash
cd g:/cohort/demos
npm install
```

## Run Demos

```bash
# All demos (browser + CLI)
python run_all_demos.py

# Individual browser demos
npx playwright test specs/web-ui-demo.spec.ts     # Web dashboard
npx playwright test specs/vscode-demo.spec.ts      # VS Code style
npx playwright test specs/docker-demo.spec.ts      # Docker

# Individual CLI demos
python helpers/cli_demo.py --method pip            # pip install
python helpers/cli_demo.py --method mcp            # MCP setup
python helpers/cli_demo.py --method api            # Python API
python helpers/cli_demo.py --all                   # All CLI demos

# Just regenerate summary page from existing timing data
python run_all_demos.py --summary
```

## Prerequisites

- **Ollama running** with model already downloaded (the demos skip the download)
- **Cohort server** running on `localhost:5100` (or set `COHORT_URL` env var)
- **Node.js** for Playwright
- **Python 3.11+** for CLI demos

## What Gets Recorded

| Demo | Type | Output |
|------|------|--------|
| Web Dashboard | Playwright video + screenshots | `recordings/web-ui-*.webm`, `XX-*.png` |
| VS Code | Playwright video (1280x800) | `recordings/vscode-*.webm` |
| Docker | Playwright video | `recordings/docker-*.webm` |
| pip install | Terminal transcript | `recordings/cli-pip-transcript.txt` |
| MCP setup | Terminal transcript | `recordings/cli-mcp-transcript.txt` |
| Python API | Terminal transcript | `recordings/cli-api-transcript.txt` |

## Clean Environment

The browser demos automatically:
1. Back up your existing `settings.json`
2. Delete it so the wizard triggers fresh
3. Restore the backup when done

For a truly clean VS Code Extension recording:
```bash
code --profile "Demo"
```

## Output

After running, open `recordings/summary.html` for the marketing page with
all timing results displayed as cards.
