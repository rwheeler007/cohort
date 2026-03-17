"""Interactive setup wizard for Cohort local LLM.

Guides non-technical users through hardware detection, Ollama
installation, model pulling, and content pipeline configuration.
Zero pip dependencies -- stdlib only.

Usage::

    python -m cohort setup
    cohort setup
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cohort.local.config import MODEL_DESCRIPTIONS, get_model_for_vram
from cohort.local.detect import HardwareInfo, detect_hardware
from cohort.local.ollama import OllamaClient

# =====================================================================
# Constants
# =====================================================================

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
OLLAMA_WINDOWS_INSTALLER = (
    "https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe"
)
OLLAMA_LINUX_SCRIPT = "https://ollama.com/install.sh"

TOTAL_STEPS = 8

# MCP server config snippet for Claude Code settings
MCP_SERVER_CONFIG: dict[str, Any] = {
    "mcpServers": {
        "local_llm": {
            "command": "python",
            "args": ["-m", "cohort.mcp.local_llm_server"],
        }
    }
}

# Curated RSS feeds by topic for Step 6
# Organized into categories for better browsability.
# TOPIC_CATEGORIES maps category -> list of topic keys.
# TOPIC_FEEDS maps topic key -> list of {name, url} dicts.

TOPIC_CATEGORIES: dict[str, list[str]] = {
    "Software Development": [
        "web development", "python", "javascript", "rust", "golang",
        "devops", "databases", "open source",
    ],
    "AI & Data": [
        "ai", "machine learning", "data science", "data engineering",
    ],
    "Infrastructure & Security": [
        "cloud", "cybersecurity", "networking", "hardware",
    ],
    "Product & Design": [
        "design", "mobile", "gaming", "product management",
    ],
    "News & World": [
        "world news", "us news", "science", "space",
    ],
    "Business & Industry": [
        "startup", "saas", "ecommerce", "marketing", "finance",
        "stocks & markets", "health", "energy & climate", "legal tech",
    ],
}

TOPIC_FEEDS: dict[str, list[dict[str, str]]] = {
    # -- Software Development --
    "web development": [
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
        {"name": "Dev.to", "url": "https://dev.to/feed"},
        {"name": "CSS-Tricks", "url": "https://css-tricks.com/feed/"},
    ],
    "python": [
        {"name": "Real Python", "url": "https://realpython.com/atom.xml"},
        {"name": "Planet Python", "url": "https://planetpython.org/rss20.xml"},
        {"name": "PyCoders Weekly", "url": "https://pycoders.com/feed"},
    ],
    "javascript": [
        {"name": "JavaScript Weekly", "url": "https://javascriptweekly.com/rss"},
        {"name": "Node Weekly", "url": "https://nodeweekly.com/rss"},
        {"name": "Dev.to #javascript", "url": "https://dev.to/feed/tag/javascript"},
    ],
    "rust": [
        {"name": "This Week in Rust", "url": "https://this-week-in-rust.org/atom.xml"},
        {"name": "Rust Blog", "url": "https://blog.rust-lang.org/feed.xml"},
        {"name": "Dev.to #rust", "url": "https://dev.to/feed/tag/rust"},
    ],
    "golang": [
        {"name": "Go Blog", "url": "https://go.dev/blog/feed.atom"},
        {"name": "Golang Weekly", "url": "https://golangweekly.com/rss"},
        {"name": "Dev.to #go", "url": "https://dev.to/feed/tag/go"},
    ],
    "devops": [
        {"name": "DevOps.com", "url": "https://devops.com/feed/"},
        {"name": "The New Stack", "url": "https://thenewstack.io/feed/"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
    ],
    "databases": [
        {"name": "Planet PostgreSQL", "url": "https://planet.postgresql.org/rss20.xml"},
        {"name": "Redis Blog", "url": "https://redis.io/blog/feed/"},
        {"name": "Dev.to #database", "url": "https://dev.to/feed/tag/database"},
    ],
    "open source": [
        {"name": "Open Source (Dev.to)", "url": "https://dev.to/feed/tag/opensource"},
        {"name": "GitHub Blog", "url": "https://github.blog/feed/"},
        {"name": "It's FOSS News", "url": "https://news.itsfoss.com/rss/"},
    ],

    # -- AI & Data --
    "ai": [
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
        {"name": "The Batch (deeplearning.ai)", "url": "https://www.deeplearning.ai/the-batch/feed/"},
        {"name": "MIT Technology Review AI", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed"},
    ],
    "machine learning": [
        {"name": "Towards Data Science", "url": "https://towardsdatascience.com/feed"},
        {"name": "ML Mastery", "url": "https://machinelearningmastery.com/feed/"},
        {"name": "Papers With Code", "url": "https://paperswithcode.com/latest/feed"},
    ],
    "data science": [
        {"name": "Towards Data Science", "url": "https://towardsdatascience.com/feed"},
        {"name": "KDnuggets", "url": "https://www.kdnuggets.com/feed"},
        {"name": "Data Science Central", "url": "https://www.datasciencecentral.com/feed/"},
    ],
    "data engineering": [
        {"name": "Data Engineering Weekly", "url": "https://www.dataengineeringweekly.com/feed"},
        {"name": "The New Stack", "url": "https://thenewstack.io/feed/"},
        {"name": "Dev.to #dataengineering", "url": "https://dev.to/feed/tag/dataengineering"},
    ],

    # -- Infrastructure & Security --
    "cloud": [
        {"name": "AWS News Blog", "url": "https://aws.amazon.com/blogs/aws/feed/"},
        {"name": "Google Cloud Blog", "url": "https://cloud.google.com/blog/rss"},
        {"name": "The New Stack", "url": "https://thenewstack.io/feed/"},
    ],
    "cybersecurity": [
        {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/"},
        {"name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews"},
        {"name": "Schneier on Security", "url": "https://www.schneier.com/feed/"},
    ],
    "networking": [
        {"name": "Packet Pushers", "url": "https://packetpushers.net/feed/"},
        {"name": "The Register Networking", "url": "https://www.theregister.com/data_centre/networks/headlines.atom"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
    ],
    "hardware": [
        {"name": "AnandTech", "url": "https://www.anandtech.com/rss/"},
        {"name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
    ],

    # -- Product & Design --
    "design": [
        {"name": "Smashing Magazine", "url": "https://www.smashingmagazine.com/feed/"},
        {"name": "A List Apart", "url": "https://alistapart.com/main/feed/"},
        {"name": "UX Collective", "url": "https://uxdesign.cc/feed"},
    ],
    "mobile": [
        {"name": "Android Developers Blog", "url": "https://android-developers.googleblog.com/atom.xml"},
        {"name": "Swift by Sundell", "url": "https://www.swiftbysundell.com/rss"},
        {"name": "React Native Blog", "url": "https://reactnative.dev/blog/rss.xml"},
    ],
    "gaming": [
        {"name": "Game Developer", "url": "https://www.gamedeveloper.com/rss.xml"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
        {"name": "Dev.to #gamedev", "url": "https://dev.to/feed/tag/gamedev"},
    ],
    "product management": [
        {"name": "Mind the Product", "url": "https://www.mindtheproduct.com/feed/"},
        {"name": "Lenny's Newsletter", "url": "https://www.lennysnewsletter.com/feed"},
        {"name": "First Round Review", "url": "https://review.firstround.com/feed.xml"},
    ],

    # -- News & World --
    "world news": [
        {"name": "Reuters Top News", "url": "https://feeds.reuters.com/reuters/topNews"},
        {"name": "BBC News", "url": "https://feeds.bbci.co.uk/news/rss.xml"},
        {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    ],
    "us news": [
        {"name": "NPR News", "url": "https://feeds.npr.org/1001/rss.xml"},
        {"name": "Associated Press", "url": "https://rsshub.app/apnews/topics/apf-topnews"},
        {"name": "Reuters US", "url": "https://feeds.reuters.com/reuters/domesticNews"},
    ],
    "science": [
        {"name": "Nature News", "url": "https://www.nature.com/nature.rss"},
        {"name": "Ars Technica Science", "url": "https://feeds.arstechnica.com/arstechnica/science"},
        {"name": "Phys.org", "url": "https://phys.org/rss-feed/"},
    ],
    "space": [
        {"name": "NASA Breaking News", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss"},
        {"name": "SpaceNews", "url": "https://spacenews.com/feed/"},
        {"name": "Ars Technica Space", "url": "https://feeds.arstechnica.com/arstechnica/space"},
    ],

    # -- Business & Industry --
    "startup": [
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
        {"name": "First Round Review", "url": "https://review.firstround.com/feed.xml"},
    ],
    "saas": [
        {"name": "SaaStr", "url": "https://www.saastr.com/feed/"},
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        {"name": "First Round Review", "url": "https://review.firstround.com/feed.xml"},
    ],
    "ecommerce": [
        {"name": "Shopify Engineering", "url": "https://shopify.engineering/blog.atom"},
        {"name": "Practical Ecommerce", "url": "https://www.practicalecommerce.com/feed"},
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    ],
    "marketing": [
        {"name": "HubSpot Blog", "url": "https://blog.hubspot.com/rss.xml"},
        {"name": "Content Marketing Institute", "url": "https://contentmarketinginstitute.com/feed/"},
        {"name": "Moz Blog", "url": "https://moz.com/blog/feed"},
    ],
    "finance": [
        {"name": "Finextra", "url": "https://www.finextra.com/rss/headlines.aspx"},
        {"name": "TechCrunch Fintech", "url": "https://techcrunch.com/category/fintech/feed/"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
    ],
    "stocks & markets": [
        {"name": "MarketWatch Top Stories", "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
        {"name": "Seeking Alpha Market News", "url": "https://seekingalpha.com/market_currents.xml"},
        {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex"},
    ],
    "health": [
        {"name": "Health IT News", "url": "https://www.healthcareitnews.com/feed"},
        {"name": "STAT News", "url": "https://www.statnews.com/feed/"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
    ],
    "energy & climate": [
        {"name": "Canary Media", "url": "https://www.canarymedia.com/feed"},
        {"name": "CleanTechnica", "url": "https://cleantechnica.com/feed/"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
    ],
    "legal tech": [
        {"name": "Artificial Lawyer", "url": "https://www.artificiallawyer.com/feed/"},
        {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        {"name": "Hacker News (best)", "url": "https://hnrss.org/best"},
    ],
}

# Suggested keywords per topic.  Auto-offered when a user selects a topic.
# These are starting points -- users can add/remove freely.
TOPIC_KEYWORDS: dict[str, list[str]] = {
    # Software Development
    "web development": ["html", "css", "frontend", "backend", "api", "web app"],
    "python": ["python", "pip", "django", "flask", "fastapi", "pytest"],
    "javascript": ["javascript", "typescript", "node", "react", "vue", "npm"],
    "rust": ["rust", "cargo", "memory safety", "wasm", "systems programming"],
    "golang": ["golang", "go", "goroutine", "concurrency", "microservice"],
    "devops": ["docker", "kubernetes", "ci/cd", "terraform", "deployment", "infrastructure"],
    "databases": ["sql", "postgres", "redis", "database", "query", "schema"],
    "open source": ["open source", "github", "oss", "license", "contributor"],
    # AI & Data
    "ai": ["ai", "llm", "gpt", "claude", "transformer", "neural network", "agent"],
    "machine learning": ["machine learning", "model", "training", "deep learning", "pytorch", "tensorflow"],
    "data science": ["data science", "analytics", "statistics", "visualization", "pandas", "jupyter"],
    "data engineering": ["data pipeline", "etl", "spark", "airflow", "data warehouse", "streaming"],
    # Infrastructure & Security
    "cloud": ["aws", "azure", "gcp", "cloud", "serverless", "lambda"],
    "cybersecurity": ["security", "vulnerability", "exploit", "ransomware", "encryption", "zero-day"],
    "networking": ["networking", "tcp", "dns", "routing", "firewall", "bandwidth"],
    "hardware": ["gpu", "cpu", "chip", "semiconductor", "motherboard", "benchmark"],
    # Product & Design
    "design": ["ux", "ui", "design system", "accessibility", "figma", "typography"],
    "mobile": ["ios", "android", "mobile app", "swift", "kotlin", "react native"],
    "gaming": ["game dev", "unity", "unreal", "indie game", "shader", "multiplayer"],
    "product management": ["product", "roadmap", "user research", "sprint", "backlog", "okr"],
    # News & World
    "world news": ["breaking", "geopolitics", "diplomacy", "conflict", "election", "policy"],
    "us news": ["congress", "policy", "election", "regulation", "supreme court"],
    "science": ["research", "study", "discovery", "physics", "biology", "climate"],
    "space": ["nasa", "spacex", "orbit", "satellite", "rocket", "mars", "telescope"],
    # Business & Industry
    "startup": ["startup", "funding", "venture capital", "seed round", "founder", "ipo"],
    "saas": ["saas", "subscription", "churn", "recurring revenue", "b2b", "onboarding"],
    "ecommerce": ["ecommerce", "shopify", "marketplace", "checkout", "conversion", "fulfillment"],
    "marketing": ["seo", "content marketing", "growth", "social media", "brand", "campaign"],
    "finance": ["fintech", "banking", "payments", "blockchain", "lending", "regulation"],
    "stocks & markets": ["stocks", "market", "earnings", "s&p 500", "trading", "fed", "nasdaq", "dividend"],
    "health": ["health tech", "telehealth", "clinical", "ehr", "biotech", "fda"],
    "energy & climate": ["solar", "wind", "battery", "ev", "carbon", "renewable", "grid"],
    "legal tech": ["legal tech", "contract", "compliance", "regulation", "court", "patent"],
}


# =====================================================================
# Display helpers
# =====================================================================

def _print_banner() -> None:
    print()
    print("=" * 64)
    print("  Cohort Setup Wizard")
    print("=" * 64)
    print()
    print("  Let's get your local AI up and running. This takes about")
    print("  5 minutes and you'll have AI agents running on your own")
    print("  machine -- no cloud, no subscription, no data leaving")
    print("  your computer.")
    print()
    print("=" * 64)
    print()


def _print_step(step_num: int, title: str) -> None:
    print()
    print(f"Step {step_num} of {TOTAL_STEPS}: {title}")
    print("-" * (20 + len(title)))
    print()


def _print_ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _print_info(msg: str) -> None:
    print(f"  [*] {msg}")


def _print_warn(msg: str) -> None:
    print(f"  [!] {msg}")


def _print_fail(msg: str) -> None:
    print(f"  [X] {msg}")


def _print_progress_bar(
    label: str, completed: int, total: int, width: int = 30,
) -> None:
    if total <= 0:
        return
    pct = min(completed / total, 1.0)
    filled = int(width * pct)
    bar = "=" * filled + ">" * (1 if filled < width else 0) + " " * (width - filled - 1)
    # Show size in MB/GB
    def _fmt(b: int) -> str:
        if b >= 1_073_741_824:
            return f"{b / 1_073_741_824:.1f} GB"
        return f"{b / 1_048_576:.0f} MB"

    sys.stdout.write(
        f"\r  {label:30s} [{bar}] {pct:3.0%}  {_fmt(completed)} / {_fmt(total)}"
    )
    sys.stdout.flush()


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input(f"  {prompt} {hint} ").strip().lower()
        except EOFError:
            return default
        if answer == "":
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please type y or n.")


def _ask_input(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{hint}: ").strip()
    except EOFError:
        return default
    return answer if answer else default


def _wait_for_enter(prompt: str = "Press Enter to continue...") -> None:
    try:
        input(f"  {prompt}")
    except EOFError:
        pass


def _format_vram(vram_mb: int) -> str:
    gb = vram_mb / 1024
    if gb >= 1:
        return f"{vram_mb:,} MB ({gb:.0f} GB)"
    return f"{vram_mb:,} MB"


def _vram_quality(vram_mb: int) -> str:
    if vram_mb >= 8192:
        return "that's excellent!"
    if vram_mb >= 6144:
        return "that's solid!"
    if vram_mb >= 4096:
        return "that'll work well!"
    return "we'll make it work!"


# =====================================================================
# Ollama helpers
# =====================================================================

def _is_ollama_on_path() -> bool:
    return shutil.which("ollama") is not None


def _is_ollama_running() -> bool:
    try:
        req = urllib.request.Request(OLLAMA_BASE_URL, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return "Ollama is running" in resp.read().decode()
    except (urllib.error.URLError, OSError, ConnectionRefusedError):
        return False


def _wait_for_ollama(max_retries: int = 5, interval: float = 3.0) -> bool:
    for i in range(max_retries):
        if _is_ollama_running():
            return True
        if i < max_retries - 1:
            _print_info(f"Waiting for Ollama to start... ({i + 1}/{max_retries})")
            time.sleep(interval)
    return False


def _model_is_installed(model: str) -> bool:
    client = OllamaClient(base_url=OLLAMA_BASE_URL, timeout=10)
    installed = client.list_models()
    # Check for exact match or base name match
    base = model.split(":")[0]
    for m in installed:
        if m == model or m.startswith(base + ":"):
            return True
    return False


def _pull_model_streaming(model: str) -> bool:
    url = f"{OLLAMA_BASE_URL}/api/pull"
    body = json.dumps({"model": model, "stream": True}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                status = data.get("status", "")
                total = data.get("total", 0)
                completed = data.get("completed", 0)

                if "error" in data:
                    print()
                    _print_fail(data["error"])
                    return False
                elif total > 0 and completed > 0:
                    _print_progress_bar(
                        f"Downloading {model}", completed, total,
                    )
                else:
                    # Status messages: pulling manifest, verifying, etc.
                    sys.stdout.write(f"\r  {status:60s}")
                    sys.stdout.flush()

            print()  # newline after progress bar
            return True

    except (urllib.error.URLError, OSError) as exc:
        print()
        _print_fail(f"Download failed: {exc}")
        _print_info("Run 'cohort setup' again to resume -- Ollama picks up where it left off.")
        return False


def _download_file(url: str, dest: str) -> bool:
    def _hook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            downloaded = block_num * block_size
            _print_progress_bar("Downloading installer", downloaded, total_size)

    try:
        urllib.request.urlretrieve(url, dest, reporthook=_hook)
        print()
        return True
    except (urllib.error.URLError, OSError) as exc:
        print()
        _print_fail(f"Download failed: {exc}")
        return False


# =====================================================================
# MCP helpers
# =====================================================================

def _check_mcp_deps() -> dict[str, bool]:
    """Check if MCP-related packages are importable.

    Returns dict with package name -> importable status.
    Does NOT import at module level -- keeps wizard dependency-free.
    """
    results: dict[str, bool] = {}
    for pkg in ("fastmcp", "mcp"):
        try:
            __import__(pkg)
            results[pkg] = True
        except ImportError:
            results[pkg] = False
    return results


def _write_mcp_settings() -> bool:
    """Write or merge MCP server config into .claude/settings.local.json.

    Locates .claude/ in the current working directory (project-level).
    If the file exists, merges mcpServers without clobbering other keys.
    If it doesn't exist, creates the directory and file.

    Returns True on success, False on any error.
    """
    claude_dir = Path.cwd() / ".claude"
    settings_path = claude_dir / "settings.local.json"

    try:
        existing: dict[str, Any] = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _print_warn("Existing settings.local.json is invalid, creating fresh.")
                existing = {}

        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["local_llm"] = MCP_SERVER_CONFIG["mcpServers"]["local_llm"]

        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(existing, indent=2) + "\n",
            encoding="utf-8",
        )
        return True

    except OSError as exc:
        _print_fail(f"Write failed: {exc}")
        return False


# =====================================================================
# Step 1: Hardware Detection
# =====================================================================

def _step_detect_hardware() -> HardwareInfo:
    _print_step(1, "Checking Your Hardware")

    hw = detect_hardware()
    plat_name = {
        "windows": "Windows PC",
        "darwin": "Mac",
        "linux": "Linux",
    }.get(hw.platform, hw.platform)

    if hw.cpu_only:
        if hw.platform == "darwin":
            _print_info("Mac detected -- Apple Silicon runs AI models efficiently")
            _print_info("through Metal, even without a traditional graphics card.")
        else:
            _print_info("No dedicated graphics card detected -- and that's perfectly fine!")
        print()
        print(f"      Computer:  {plat_name}")
        print(f"      Mode:      CPU-only")
        print()
        print("  Your computer will run AI using its main processor instead of")
        print("  a graphics card. It's like driving a reliable sedan instead of")
        print("  a sports car -- you'll get there, just at a steadier pace.")
        print()
        print("  We'll pick a lightweight model that runs great on CPU.")
    else:
        _print_ok("Detected your system:")
        print()
        print(f"      Computer:        {plat_name}")

        if len(hw.gpus) > 1:
            print(f"      Graphics cards:  {len(hw.gpus)} GPUs detected")
            for gpu in hw.gpus:
                marker = "  <-- recommendation based on this" if gpu.vram_mb == hw.vram_mb and gpu.name == hw.gpu_name else ""
                print(f"        GPU {gpu.index}: {gpu.name}  ({_format_vram(gpu.vram_mb)}){marker}")
            total_gb = hw.total_vram_mb / 1024
            print(f"      Total memory:    {_format_vram(hw.total_vram_mb)} ({total_gb:.1f} GB)")
        else:
            print(f"      Graphics card:   {hw.gpu_name}")
            print(f"      Graphics memory: {_format_vram(hw.vram_mb)} -- {_vram_quality(hw.vram_mb)}")

        print()
        print("  Your graphics card (GPU) is what runs AI models. Think of it")
        print("  as a turbo engine for AI -- 10-50x faster than your regular")
        print("  processor alone.")

    return hw


# =====================================================================
# Step 2: Check Ollama
# =====================================================================

def _step_check_ollama() -> bool:
    _print_step(2, "Checking for Ollama")

    print("  Ollama is a free tool that runs AI models right on your computer.")
    print()

    _print_info("Looking for Ollama...")

    # Check server first (may be running as a service even if not on PATH)
    if _is_ollama_running():
        _print_ok("Ollama is installed and running. Great!")
        return True

    # Check binary on PATH
    if _is_ollama_on_path():
        _print_info("Ollama is installed but the server isn't running.")
        print()

        plat = platform.system().lower()
        if plat == "windows":
            print("  Check your system tray -- Ollama may need to be started.")
            print("  Or open a terminal and run:  ollama serve")
        elif plat == "darwin":
            print("  Open the Ollama app from your Applications folder,")
            print("  or run in terminal:  ollama serve")
        else:
            print("  Start it with:  ollama serve &")

        print()
        _wait_for_enter("Start Ollama, then press Enter...")
        print()

        if _wait_for_ollama():
            _print_ok("Ollama is running now. Great!")
            return True
        else:
            _print_warn("Still can't reach Ollama. We'll try installing fresh.")
            return False

    _print_info("Ollama is not installed yet. No problem -- let's fix that.")
    return False


# =====================================================================
# Step 3: Install Ollama
# =====================================================================

def _step_install_ollama(plat: str) -> bool:
    _print_step(3, "Installing Ollama")

    if plat == "windows":
        return _install_ollama_windows()
    elif plat == "darwin":
        return _install_ollama_macos()
    else:
        return _install_ollama_linux()


def _install_ollama_windows() -> bool:
    if _ask_yes_no("Want me to download the Ollama installer for you?"):
        downloads = Path.home() / "Downloads"
        dest = str(downloads / "OllamaSetup.exe")
        print()
        if _download_file(OLLAMA_WINDOWS_INSTALLER, dest):
            _print_ok(f"Downloaded to {dest}")
            print()
            _print_info("Opening the installer now. Follow its prompts, then come")
            _print_info("back here when it's done.")
            print()
            try:
                os.startfile(dest)  # type: ignore[attr-defined]
            except OSError:
                _print_warn(f"Couldn't open automatically. Double-click: {dest}")
        else:
            _print_info(f"Download the installer from: {OLLAMA_DOWNLOAD_URL}")
    else:
        print()
        print(f"  No problem. Download the installer from:")
        print(f"    {OLLAMA_DOWNLOAD_URL}")
        print()
        print("  Install it, then come back here.")

    print()
    _wait_for_enter("Press Enter when the installer finishes...")
    return _verify_ollama_after_install()


def _install_ollama_macos() -> bool:
    if shutil.which("brew"):
        print("  Homebrew detected! Run this in your terminal:")
        print()
        print("    brew install ollama")
    else:
        print(f"  Download the installer from:")
        print(f"    {OLLAMA_DOWNLOAD_URL}")

    print()
    _wait_for_enter("Press Enter when you've installed Ollama...")
    return _verify_ollama_after_install()


def _install_ollama_linux() -> bool:
    print("  Run this in your terminal:")
    print()
    print(f"    curl -fsSL {OLLAMA_LINUX_SCRIPT} | sh")
    print()
    _wait_for_enter("Press Enter when the install finishes...")
    return _verify_ollama_after_install()


def _verify_ollama_after_install() -> bool:
    print()
    _print_info("Checking...")

    for attempt in range(3):
        if _is_ollama_running():
            _print_ok("Ollama is installed and running. Great job!")
            return True

        if _is_ollama_on_path():
            _print_info("Binary found, waiting for server to start...")
            if _wait_for_ollama(max_retries=3, interval=3.0):
                _print_ok("Ollama is installed and running. Great job!")
                return True

        if attempt == 0:
            _print_warn("Hmm, I can't find Ollama yet. Did the installer finish?")
            _wait_for_enter("Press Enter to try again...")
        elif attempt == 1:
            _print_warn("Still not finding it. You may need to restart your terminal.")
            _wait_for_enter("Press Enter to try again...")
        else:
            _print_fail("I'm still unable to detect Ollama.")
            print()
            print("  You can come back to this later by running 'cohort setup' again.")
            print(f"  Manual install: {OLLAMA_DOWNLOAD_URL}")
            return False

    return False


# =====================================================================
# Step 4: Pull Model
# =====================================================================

def _step_pull_model(model: str, hw: HardwareInfo) -> bool:
    _print_step(4, "Downloading Your AI Model")

    # Check if already installed
    if _model_is_installed(model):
        _print_ok(f"{model} is already installed. Nice!")
        return True

    info = MODEL_DESCRIPTIONS.get(model, {})
    size = info.get("size", "unknown size")
    summary = info.get("summary", "")

    if hw.cpu_only:
        print("  For CPU-only mode, I recommend a lightweight model:")
    else:
        print("  Based on your hardware, I recommend:")

    print()
    print(f"      Model:  {model}")
    print(f"      Size:   {size} download")
    if summary:
        print(f"      Why:    {summary}")
    print()

    if not _ask_yes_no("Ready to download?"):
        _print_info("Skipped. You can pull the model later with:")
        print(f"    ollama pull {model}")
        return False

    print()
    success = _pull_model_streaming(model)
    if success:
        _print_ok("Model downloaded and verified.")
    return success


# =====================================================================
# Step 5: Verify
# =====================================================================

def _step_verify(model: str) -> bool:
    _print_step(5, "Testing Everything")

    print("  Let's make sure it all works with a quick test...")
    print()

    client = OllamaClient(base_url=OLLAMA_BASE_URL, timeout=120)
    test_prompt = "What makes a good code review? Answer in two sentences."

    # Test 1: Basic generation (Smart mode)
    for attempt in range(2):
        _print_info("Asking the model a quick question...")
        result = client.generate(model=model, prompt=test_prompt, temperature=0.3, think=False)

        if result is not None and result.text.strip():
            print()
            print(f'      > "{test_prompt}"')
            print()
            text = result.text.strip()
            for line in text.split("\n"):
                print(f"      {line}")
            print()
            _print_ok(f"Response generated in {result.elapsed_seconds:.1f} seconds. Everything works!")
            break

        if attempt == 0:
            _print_info("The model loaded but didn't respond. First run can be slow.")
            _print_info("Trying once more...")
            print()
    else:
        _print_warn("Something's not quite right. Try running this in your terminal:")
        print(f"    ollama run {model}")
        print()
        print("  The model is downloaded -- it may just need a moment to warm up.")
        return False

    # Test 2: Thinking mode (Smarter mode)
    print()
    _print_info("Testing thinking mode for Smarter [S+] responses...")
    try:
        think_result = client.generate(
            model=model,
            prompt="Is 17 a prime number? Think step by step, then answer yes or no.",
            temperature=0.3,
            think=True,
        )
        if think_result and think_result.text.strip():
            _print_ok("Thinking mode works -- Smarter [S+] responses enabled!")
        else:
            _print_warn("Thinking mode returned empty. Smart [S] mode will be used as default.")
    except Exception:
        _print_warn("Thinking mode not available for this model. Smart [S] mode will be used.")

    return True


# =====================================================================
# Step 6: MCP Server Setup
# =====================================================================

def _step_mcp_setup(model: str) -> bool:
    """Step 6: Verify MCP server dependencies and show Claude Code config.

    Checks fastmcp/mcp packages, Ollama reachability, and offers to write
    the MCP server config into .claude/settings.local.json.

    Returns True always (graceful -- never fails the wizard).
    """
    _print_step(6, "MCP Server Setup (Claude Code Integration)")

    print("  The Cohort MCP server lets Claude Code use your local AI")
    print("  model as a tool -- draft code, transform data, and more,")
    print("  all running on your machine for free.")
    print()

    # --- Check 1: Package imports ---
    _print_info("Checking for MCP dependencies...")
    deps = _check_mcp_deps()

    if all(deps.values()):
        _print_ok("fastmcp and mcp packages found.")
    else:
        missing = [pkg for pkg, ok in deps.items() if not ok]
        _print_warn(f"Missing packages: {', '.join(missing)}")
        print()
        print("  Install them with:")
        print()
        print("    pip install cohort[mcp]")
        print()
        print("  This adds the MCP server capability. You can run")
        print("  'cohort setup' again after installing to configure it.")
        print()
        return True  # Graceful -- don't fail the wizard

    # --- Check 2: Ollama reachability ---
    _print_info("Verifying Ollama is reachable for MCP server...")

    if _is_ollama_running():
        _print_ok("Ollama is reachable. MCP server will work.")
    else:
        _print_warn("Ollama is not responding. The MCP server needs Ollama running.")
        _print_info("Make sure Ollama is running when you use Claude Code.")

    # --- Check 3: Model availability ---
    if _model_is_installed(model):
        _print_ok(f"Model {model} is available for MCP inference.")
    else:
        _print_info(f"Model {model} not found -- pull it with: ollama pull {model}")

    print()

    # --- Show config snippet ---
    print("  To use the MCP server with Claude Code, add this to your")
    print("  project's .claude/settings.local.json:")
    print()
    snippet = json.dumps(MCP_SERVER_CONFIG, indent=2)
    for line in snippet.split("\n"):
        print(f"    {line}")
    print()

    # --- Offer to write config ---
    if _ask_yes_no("Write this config to .claude/settings.local.json now?"):
        if _write_mcp_settings():
            _print_ok("MCP config written. Claude Code will detect it automatically.")
        else:
            _print_warn("Could not write config. Copy the snippet above manually.")
    else:
        _print_info("No problem -- paste the snippet into your settings when ready.")

    return True


# =====================================================================
# Step 7: Content Pipeline
# =====================================================================

def _step_content_pipeline(data_dir: str = "data") -> bool:
    _print_step(7, "Set Up Your Content Pipeline (Optional)")

    print("  Cohort can monitor RSS feeds and help you create content.")
    print("  Your Marketing Agent and Content Strategy Agent will use")
    print("  these feeds to find trends and draft posts for you.")
    print()

    if not _ask_yes_no("Want to set this up now?"):
        _print_info("Skipped. You can configure feeds later in data/content_config.json")
        return True

    # --- Topic Selection (grouped by category) ---
    print()
    print("  What topic or industry are you in?")
    print()

    # Build a flat numbered list, grouped by category
    numbered_topics: list[str] = []
    for category, topic_keys in TOPIC_CATEGORIES.items():
        print(f"  {category}:")
        for tk in topic_keys:
            if tk in TOPIC_FEEDS:
                numbered_topics.append(tk)
                print(f"    {len(numbered_topics):2d}. {tk}")
        print()

    choice = _ask_input("Pick a number, or type your own topic", "1")

    # Resolve topic
    topic_key = ""
    selected_feeds: list[dict[str, str]] = []
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(numbered_topics):
            topic_key = numbered_topics[idx]
            selected_feeds = TOPIC_FEEDS[topic_key]
            _print_ok(f"Great choice: {topic_key}")
        else:
            raise ValueError
    except ValueError:
        # Try fuzzy match on typed topic
        typed = choice.lower().strip()
        for key, feeds in TOPIC_FEEDS.items():
            if typed in key or key in typed:
                selected_feeds = feeds
                topic_key = key
                _print_ok(f"Matched: {topic_key}")
                break
        if not selected_feeds:
            selected_feeds = TOPIC_FEEDS.get("web development", [])
            topic_key = "web development"
            _print_info(f"No exact match -- using {topic_key} as a starting point.")

    if not selected_feeds:
        _print_info("No feeds available for that topic. You can add them manually later.")
        return True

    # --- Feed Selection ---
    print()
    print("  Here are some feeds I'd suggest:")
    print()
    for i, feed in enumerate(selected_feeds, 1):
        print(f"    {i}. {feed['name']:30s} {feed['url']}")
    print()

    pick = _ask_input("Which ones? (enter numbers like 1,3 or 'all')", "all")

    if pick.lower() == "all":
        chosen = selected_feeds
    else:
        chosen = []
        for part in pick.split(","):
            part = part.strip()
            try:
                idx = int(part) - 1
                if 0 <= idx < len(selected_feeds):
                    chosen.append(selected_feeds[idx])
            except ValueError:
                pass
        if not chosen:
            chosen = selected_feeds
            _print_info("Couldn't parse selection -- using all feeds.")

    # --- Interest Keywords ---
    suggested_kw = TOPIC_KEYWORDS.get(topic_key, [])
    keywords: list[str] = []
    if suggested_kw:
        print()
        print("  Suggested interest keywords for filtering articles:")
        print(f"    {', '.join(suggested_kw)}")
        print()
        kw_choice = _ask_input(
            "Keep these? (all / none / edit to add your own)", "all",
        )
        if kw_choice.lower() == "all":
            keywords = list(suggested_kw)
        elif kw_choice.lower() == "none":
            keywords = []
        else:
            # User typed custom keywords (comma-separated), merged with suggested
            keywords = list(suggested_kw)
            for kw in kw_choice.split(","):
                kw = kw.strip().lower()
                if kw and kw not in keywords:
                    keywords.append(kw)
            _print_ok(f"Keywords: {', '.join(keywords)}")
    else:
        print()
        kw_input = _ask_input(
            "Any interest keywords? (comma-separated, or leave blank)",
        )
        if kw_input:
            keywords = [k.strip().lower() for k in kw_input.split(",") if k.strip()]

    # Write config
    config: dict[str, object] = {
        "feeds": [{"name": f["name"], "url": f["url"]} for f in chosen],
        "topic": topic_key,
        "check_interval_minutes": 60,
        "max_articles_per_feed": 10,
    }
    if keywords:
        config["interest_keywords"] = keywords

    config_dir = Path(data_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "content_config.json"

    try:
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        _print_ok(f"Saved content config to {config_path}")
        print()
        print("  Your Marketing Agent and Content Strategy Agent will use")
        print("  these feeds to find trends and draft posts. Start the server")
        print("  with 'cohort serve' to see it in action.")
    except OSError as exc:
        _print_warn(f"Couldn't save config: {exc}")
        _print_info("You can create data/content_config.json manually later.")

    return True


# =====================================================================
# =====================================================================
# Global Agent Link Helper
# =====================================================================

def _create_global_agent_links(cohort_root: Path) -> None:
    """Junction/symlink ~/.claude/agents and ~/.claude/skills to Cohort.

    Makes Cohort agents available in any Claude Code project, not just
    the Cohort folder.  Non-blocking -- warns on failure, never crashes.
    Works on Windows (junction) and macOS/Linux (symlink).
    """
    global_claude = Path.home() / ".claude"
    global_claude.mkdir(exist_ok=True)

    targets = {
        "agents": cohort_root / ".claude" / "agents",
        "skills": cohort_root / ".claude" / "skills",
    }

    plat = platform.system()
    all_ok = True

    for name, source in targets.items():
        link = global_claude / name

        if not source.exists():
            _print_warn(f"  Source not found, skipping: {source}")
            all_ok = False
            continue

        if link.exists():
            # Real directory (not a link) -- don't clobber user files
            if not os.path.islink(str(link)):
                _print_warn(
                    f"  {link} already exists as a real directory. "
                    f"Remove it manually to enable global agents."
                )
                all_ok = False
                continue
            # Already linked -- nothing to do
            continue

        try:
            if plat == "Windows":
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(source)],
                    check=True, capture_output=True, text=True,
                )
            else:
                link.symlink_to(source)
        except Exception as exc:  # noqa: BLE001
            _print_warn(f"  Could not link {name}: {exc}")
            all_ok = False
            continue

    if all_ok:
        _print_ok("Agents available in all Claude Code projects.")
    else:
        _print_info(
            "Some agent links could not be created. "
            "See docs/setup.md for manual steps."
        )


# Step 8: Claude Code Connection
# =====================================================================

def _step_claude_connection() -> bool:
    """Step 8: Configure Claude Code connection settings.

    Detects Claude CLI, configures execution backend, response timeout,
    and force-to-Claude mode. Shows response mode availability.

    Returns True always (graceful -- never fails the wizard).
    """
    _print_step(8, "Connect Claude Code (Optional)")

    print("  Claude Code is an AI coding assistant by Anthropic. Connecting")
    print("  it lets your agents use advanced reasoning and unlocks the")
    print("  Smartest [S++] response mode.")
    print()

    if not _ask_yes_no("Want to set this up now?"):
        _print_info("Skipped. Configure Claude Code later in Settings.")
        return True

    # --- Detect Claude CLI ---
    _print_info("Looking for Claude CLI...")
    claude_path = shutil.which("claude")

    if claude_path:
        _print_ok(f"Found Claude CLI: {claude_path}")
    else:
        _print_warn("Claude CLI not found on your PATH.")
        print()
        print("  Install it from: https://docs.anthropic.com/en/docs/claude-code")
        print()
        claude_path = _ask_input("Or enter the full path to claude CLI (leave blank to skip)")
        if not claude_path:
            _print_info("Skipped. You can configure this later in Settings.")
            return True

    print()

    # --- Agents Root ---
    # Auto-detect: look for agents/ dir from the script's location
    script_root = Path(__file__).resolve().parent.parent.parent
    default_root = str(script_root) if (script_root / "agents").is_dir() else ""
    agents_root = _ask_input("Agents root directory", default_root)

    # --- Execution Backend ---
    print()
    print("  Task Execution Backend:")
    print("    1. Claude CLI (subprocess) -- recommended")
    print("    2. Anthropic API (direct)")
    print("    3. Chat-routed (@mention)")
    backend_choice = _ask_input("Pick a number", "1")
    backend_map = {"1": "cli", "2": "api", "3": "chat"}
    execution_backend = backend_map.get(backend_choice, "cli")

    # --- Response Timeout ---
    timeout_str = _ask_input("Response timeout in seconds", "300")
    try:
        response_timeout = max(30, min(600, int(timeout_str)))
    except ValueError:
        response_timeout = 300

    # --- Force to Claude Code ---
    print()
    force_claude = _ask_yes_no(
        "Force ALL responses through Claude Code (bypass local Ollama)?",
        default=False,
    )

    # --- Save settings ---
    settings_path = Path("data") / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    settings["claude_cmd"] = claude_path
    settings["agents_root"] = agents_root
    settings["execution_backend"] = execution_backend
    settings["response_timeout"] = response_timeout
    settings["force_to_claude_code"] = force_claude
    settings["claude_enabled"] = True

    # --- Default Permissions for new projects ---
    print()
    print("  Default agent permissions for new projects:")
    print("    1. developer  -- Read, Write, Edit, Bash, Glob, Grep  (recommended)")
    print("    2. readonly   -- Read, Glob, Grep only")
    print("    3. researcher -- Read + web search")
    print()
    profile_choice = _ask_input("Pick a profile for new projects", "1")
    profile_map = {"1": "developer", "2": "readonly", "3": "researcher"}
    default_profile = profile_map.get(profile_choice, "developer")

    # Drive-level deny list
    print()
    print("  Which drives/paths should agents NEVER edit?")
    print("  (comma-separated, e.g. D:/ or /mnt/backup -- leave blank for none)")
    deny_raw = _ask_input("Deny paths", "")
    deny_paths: list[str] = [p.strip() for p in deny_raw.split(",") if p.strip()] if deny_raw else []

    settings["default_permissions"] = {
        "profile": default_profile,
        "allow_paths": [],   # filled per-project at creation time
        "deny_paths": deny_paths,
        "allowed_tools": {
            "developer":  ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            "readonly":   ["Read", "Glob", "Grep"],
            "researcher": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
        }.get(default_profile, ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]),
        "max_turns": 15,
    }

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        _print_ok("Claude Code settings saved.")
    except OSError as exc:
        _print_warn(f"Could not save settings: {exc}")

    # --- Response Modes Summary ---
    print()
    print("  Response Modes:")
    print("    [S]  Smart    -- Fast local responses, no thinking (free)")
    print("    [S+] Smarter  -- Local with thinking enabled (free, default)")
    print("    [S++] Smartest -- Local reasoning + Claude refinement")
    print()
    if claude_path and Path(claude_path).exists():
        _print_ok("Smartest [S++] mode available -- Claude CLI detected!")
    else:
        _print_info("Smartest [S++] mode requires a working Claude CLI.")

    # --- Global Agent Availability ---
    print()
    _print_info("Making your agents available in all Claude Code projects...")
    _create_global_agent_links(script_root)

    return True


# =====================================================================
# Success Summary
# =====================================================================

def _print_success(hw: HardwareInfo, model: str) -> None:
    print()
    print("=" * 64)
    print("  Setup Complete!")
    print("=" * 64)
    print()
    print("  Here's what we set up:")
    print()

    if hw.cpu_only:
        print("    Hardware:  CPU-only mode")
    else:
        gb = hw.vram_mb / 1024
        print(f"    Hardware:  {hw.gpu_name} ({gb:.0f} GB VRAM)")

    print(f"    Engine:    Ollama (running on localhost:11434)")
    print(f"    Model:     {model}")
    print()

    # Load settings to show Claude status
    settings: dict = {}
    try:
        sp = Path("data") / "settings.json"
        if sp.exists():
            settings = json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass

    claude_cmd = settings.get("claude_cmd", "")
    if claude_cmd:
        print(f"    Claude:    Connected ({settings.get('execution_backend', 'cli')} mode)")
        print(f"    Timeout:   {settings.get('response_timeout', 300)}s")
        if settings.get("force_to_claude_code"):
            print(f"    Routing:   All responses forced through Claude Code")
    else:
        print(f"    Claude:    Not configured (local-only mode)")

    print()
    print("  Response Modes:")
    print("    [S]  Smart    -- fast, no thinking        (always available)")
    print("    [S+] Smarter  -- thinking enabled          (default)")
    if claude_cmd:
        print("    [S++] Smartest -- Qwen + Claude refinement (available!)")
    else:
        print("    [S++] Smartest -- requires Claude CLI       (not configured)")

    print()
    print("  What's next:")
    print()
    print("    1. Start the Cohort server:")
    print("       cohort serve")
    print()
    print("    2. Open your browser:")
    print("       http://localhost:5100")
    print()
    if claude_cmd:
        print("    3. Your agents can use both local AI and Claude Code!")
    else:
        print("    3. Claude Code integration (optional):")
        print("       Configure Claude in Settings to unlock Smartest mode.")
    print()
    print("    4. Meet the team -- your agents are ready to work with you!")
    print()
    print("  Run 'cohort setup' anytime to re-check your configuration.")
    print("=" * 64)
    print()


# =====================================================================
# Entry point
# =====================================================================

def run_setup() -> int:
    """Run the interactive setup wizard. Returns exit code."""
    try:
        _print_banner()

        # Step 1: Hardware
        hw = _step_detect_hardware()
        model = get_model_for_vram(hw.vram_mb)

        # Step 2: Check Ollama
        ollama_ok = _step_check_ollama()

        # Step 3: Install (if needed)
        if not ollama_ok:
            ollama_ok = _step_install_ollama(hw.platform)
            if not ollama_ok:
                return 1

        # Step 4: Pull model
        model_ok = _step_pull_model(model, hw)

        # Step 5: Verify
        if model_ok:
            _step_verify(model)

        # Step 6: MCP Server Setup
        _step_mcp_setup(model)

        # Step 7: Content pipeline
        _step_content_pipeline()

        # Step 8: Claude Code connection
        _step_claude_connection()

        # Success
        _print_success(hw, model)
        return 0

    except KeyboardInterrupt:
        print()
        print()
        _print_info("Setup interrupted. Run 'cohort setup' anytime to continue")
        _print_info("where you left off.")
        print()
        return 1
