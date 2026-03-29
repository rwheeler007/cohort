"""Cohort Installer Build Script.

Assembles the payload directory for the Inno Setup installer:

1. Downloads Python 3.13 embedded distribution (if not cached)
2. Installs Cohort + dependencies into the embedded Python's site-packages
3. Copies agents directory
4. Creates the launcher batch file

Usage::

    python build_installer.py              # full build
    python build_installer.py --skip-download  # reuse cached downloads
    python build_installer.py --clean      # clean payload dir first

Prerequisites:
    - Python 3.11+ (build host)
    - pip (build host)
    - Internet connection (for downloads)
    - Inno Setup 6.x (for final .exe -- not needed for payload assembly)

Output:
    installer/payload/           -- ready for Inno Setup
    installer/output/            -- .exe after running iscc
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# =====================================================================
# Configuration
# =====================================================================

PYTHON_VERSION = "3.13.2"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Packages to install into embedded Python
# These are the runtime dependencies for the installer (not dev deps)
INSTALL_PACKAGES = [
    "cohort",           # The cohort package itself (from local wheel or PyPI)
    "uvicorn",          # ASGI server
    "starlette",        # Web framework
    "python-socketio",  # Socket.IO for real-time
    "pystray",          # System tray icon
    "Pillow",           # Image generation for tray icon
    "httpx",            # HTTP client (for MCP)
    "aiofiles",         # Async file serving
]

SCRIPT_DIR = Path(__file__).parent
PAYLOAD_DIR = SCRIPT_DIR / "payload"
CACHE_DIR = SCRIPT_DIR / ".cache"
ASSETS_DIR = SCRIPT_DIR / "assets"

# Cohort source root (parent of installer/)
COHORT_ROOT = SCRIPT_DIR.parent


# =====================================================================
# Helpers
# =====================================================================

def _download(url: str, dest: Path, desc: str = "") -> None:
    """Download a file with progress indication."""
    if dest.exists():
        print(f"  [OK] {desc or dest.name} (cached)")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [>>] Downloading {desc or url}...")

    # Use urllib with progress
    response = urllib.request.urlopen(url)
    total = int(response.headers.get("content-length", 0))
    downloaded = 0
    block_size = 8192

    with open(dest, "wb") as f:
        while True:
            chunk = response.read(block_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f"\r  [...] {pct}% ({downloaded // 1024 // 1024}MB)", end="", flush=True)

    print(f"\r  [OK] {desc or dest.name} ({downloaded // 1024 // 1024}MB)")


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command, printing it first."""
    print(f"  [>>] {' '.join(cmd[:5])}{'...' if len(cmd) > 5 else ''}")
    return subprocess.run(cmd, check=True, text=True, **kwargs)


# =====================================================================
# Build steps
# =====================================================================

def step_clean() -> None:
    """Remove existing payload directory."""
    print("\n[1] Cleaning payload directory...")
    if PAYLOAD_DIR.exists():
        shutil.rmtree(PAYLOAD_DIR)
        print("  [OK] Cleaned")
    else:
        print("  [OK] Already clean")


def step_download_python() -> Path:
    """Download Python embedded distribution."""
    print("\n[2] Python embedded distribution...")
    zip_path = CACHE_DIR / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    _download(PYTHON_EMBED_URL, zip_path, f"Python {PYTHON_VERSION} embed")
    return zip_path


def step_extract_python(zip_path: Path) -> Path:
    """Extract Python to payload directory."""
    print("\n[3] Extracting Python...")
    python_dir = PAYLOAD_DIR / "python"
    python_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(python_dir)

    # Enable site-packages in embedded Python
    # The embedded distribution has a ._pth file that restricts imports.
    # We need to uncomment 'import site' to enable pip/site-packages.
    pth_files = list(python_dir.glob("python*._pth"))
    for pth_file in pth_files:
        content = pth_file.read_text(encoding="utf-8")
        content = content.replace("#import site", "import site")
        pth_file.write_text(content, encoding="utf-8")
        print(f"  [OK] Enabled site-packages in {pth_file.name}")

    print(f"  [OK] Python extracted to {python_dir}")
    return python_dir


def step_install_pip(python_dir: Path) -> None:
    """Install pip into embedded Python."""
    print("\n[4] Installing pip...")
    python_exe = python_dir / "python.exe"

    get_pip = CACHE_DIR / "get-pip.py"
    _download(GET_PIP_URL, get_pip, "get-pip.py")

    _run([str(python_exe), str(get_pip), "--no-warn-script-location"])
    print("  [OK] pip installed")


def step_install_packages(python_dir: Path, local_wheel: Path | None = None) -> None:
    """Install Cohort + dependencies into embedded Python."""
    print("\n[5] Installing packages...")
    python_exe = python_dir / "python.exe"

    packages = list(INSTALL_PACKAGES)

    # If we have a local wheel, use it instead of PyPI
    if local_wheel and local_wheel.exists():
        packages = [str(local_wheel)] + [p for p in packages if p != "cohort"]
        print(f"  [*] Using local wheel: {local_wheel.name}")
    else:
        print("  [*] Installing cohort from PyPI")

    _run([
        str(python_exe), "-m", "pip", "install",
        "--no-warn-script-location",
        "--disable-pip-version-check",
        *packages,
    ])
    print("  [OK] All packages installed")


def step_copy_agents() -> None:
    """Copy agent configurations to payload."""
    print("\n[6] Copying agents...")
    agents_src = COHORT_ROOT / "agents"
    agents_dst = PAYLOAD_DIR / "agents"

    if not agents_src.exists():
        print("  [!] No agents directory found -- skipping")
        return

    if agents_dst.exists():
        shutil.rmtree(agents_dst)

    # Copy only agent_config.json and agent_persona.md from each agent
    agent_count = 0
    for agent_dir in agents_src.iterdir():
        if not agent_dir.is_dir():
            continue

        dst_dir = agents_dst / agent_dir.name
        dst_dir.mkdir(parents=True, exist_ok=True)

        for filename in ["agent_config.json", "agent_persona.md"]:
            src_file = agent_dir / filename
            if src_file.exists():
                shutil.copy2(src_file, dst_dir / filename)

        # Also copy memory.json template (empty)
        memory_file = dst_dir / "memory.json"
        if not memory_file.exists():
            memory_file.write_text('{"learned_facts": [], "interaction_history": []}',
                                   encoding="utf-8")

        agent_count += 1

    print(f"  [OK] Copied {agent_count} agents")


def step_create_launcher() -> None:
    """Ensure launcher batch file is in payload."""
    print("\n[7] Launcher batch file...")
    bat_src = SCRIPT_DIR / "payload" / "cohort-launch.bat"
    if bat_src.exists():
        print("  [OK] cohort-launch.bat already in payload")
    else:
        print("  [X] cohort-launch.bat missing from payload/")


def step_create_assets() -> None:
    """Create installer assets (icon)."""
    print("\n[8] Creating assets...")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    ico_path = ASSETS_DIR / "cohort.ico"
    if ico_path.exists():
        print("  [OK] cohort.ico exists")
        return

    # Generate a simple .ico from the tray icon generator
    try:
        sys.path.insert(0, str(COHORT_ROOT))
        from cohort.tray import _create_icon_image

        img = _create_icon_image()
        # Save as .ico with multiple sizes
        img.resize((16, 16))
        img.resize((32, 32))
        img.resize((48, 48))
        img_256 = img.resize((256, 256))
        img_256.save(
            str(ico_path),
            format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
        )
        print(f"  [OK] Generated {ico_path}")
    except ImportError:
        print("  [!] Pillow not available -- create cohort.ico manually")
        # Create a placeholder
        ico_path.write_bytes(b"")


def step_find_wheel() -> Path | None:
    """Find a local Cohort wheel to bundle."""
    dist_dir = COHORT_ROOT / "dist"
    if not dist_dir.exists():
        return None

    wheels = sorted(dist_dir.glob("cohort-*.whl"), reverse=True)
    if wheels:
        print(f"  [*] Found local wheel: {wheels[0].name}")
        return wheels[0]

    return None


def step_summary() -> None:
    """Print build summary."""
    print("\n" + "=" * 60)
    print("  BUILD COMPLETE")
    print("=" * 60)

    # Calculate payload size
    total_size = 0
    file_count = 0
    for f in PAYLOAD_DIR.rglob("*"):
        if f.is_file():
            total_size += f.stat().st_size
            file_count += 1

    print(f"  Payload: {PAYLOAD_DIR}")
    print(f"  Files:   {file_count}")
    print(f"  Size:    {total_size // 1024 // 1024} MB")
    print()
    print("  Next steps:")
    print("    1. Install Inno Setup 6.x from https://jrsoftware.org/isinfo.php")
    print("    2. Run: iscc cohort_installer.iss")
    print("    3. Installer will be at: installer/output/CohortSetup-*.exe")
    print()


# =====================================================================
# Main
# =====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Build Cohort Windows installer payload")
    parser.add_argument("--clean", action="store_true", help="Clean payload dir before build")
    parser.add_argument("--skip-download", action="store_true", help="Skip downloads (use cache)")
    parser.add_argument("--from-pypi", action="store_true", help="Install cohort from PyPI instead of local wheel")
    args = parser.parse_args()

    print("=" * 60)
    print("  Cohort Installer Builder")
    print("=" * 60)

    if platform.system() != "Windows":
        print("[!] Warning: This script is designed for Windows.")
        print("    Cross-compilation is not supported.")
        print("    The embedded Python distribution is Windows-only.")

    if args.clean:
        step_clean()

    # Find local wheel
    local_wheel = None if args.from_pypi else step_find_wheel()

    # Download + extract Python
    zip_path = step_download_python()
    python_dir = step_extract_python(zip_path)

    # Install pip + packages
    step_install_pip(python_dir)
    step_install_packages(python_dir, local_wheel)

    # Copy supporting files
    step_copy_agents()
    step_create_launcher()
    step_create_assets()

    # Summary
    step_summary()

    return 0


if __name__ == "__main__":
    sys.exit(main())
