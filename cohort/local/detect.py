"""Hardware detection for local LLM routing.

Detects GPU presence, VRAM capacity, and platform info using only stdlib
(subprocess calls to nvidia-smi, sysctl). Graceful fallback to CPU-only mode
on any failure.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass


@dataclass
class HardwareInfo:
    """Hardware detection result."""

    gpu_name: str = "Unknown"
    vram_mb: int = 0
    cpu_only: bool = True
    platform: str = "unknown"


def detect_hardware() -> HardwareInfo:
    """Detect GPU and VRAM using subprocess calls.

    Returns:
        HardwareInfo with gpu_name, vram_mb, cpu_only flag, and platform.
        On any failure, returns cpu_only=True.

    Security:
        Uses hardcoded command strings only. No user input interpolated.
    """
    plat = platform.system().lower()
    info = HardwareInfo(platform=plat, cpu_only=True)

    # Try NVIDIA GPU detection (Linux/Windows)
    if plat in ("linux", "windows"):
        try:
            # Hardcoded command: nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Parse first GPU: "NVIDIA GeForce RTX 3080 Ti, 12288 MiB"
                lines = result.stdout.strip().split("\n")
                if lines:
                    parts = lines[0].split(",")
                    if len(parts) >= 2:
                        info.gpu_name = parts[0].strip()
                        # Parse VRAM: "12288 MiB" -> 12288
                        vram_str = parts[1].strip().split()[0]
                        try:
                            info.vram_mb = int(vram_str)
                            info.cpu_only = False
                        except ValueError:
                            pass
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            # nvidia-smi not found or failed -- graceful fallback to CPU-only
            pass

    # macOS: try Metal GPU detection via sysctl (if needed in future)
    elif plat == "darwin":
        try:
            # sysctl hw.memsize gives total RAM, not GPU VRAM
            # macOS doesn't expose VRAM easily via CLI -- skip for now
            # Metal detection would require platform-specific bindings
            pass
        except Exception:
            pass

    return info
