"""Hardware detection for local LLM routing.

Detects GPU presence, VRAM capacity, and platform info using only stdlib
(subprocess calls to nvidia-smi, sysctl). Graceful fallback to CPU-only mode
on any failure.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass, field


@dataclass
class GPUInfo:
    """Single GPU detection result."""

    index: int = 0
    name: str = "Unknown"
    vram_mb: int = 0
    vram_free_mb: int = 0


@dataclass
class HardwareInfo:
    """Hardware detection result."""

    gpu_name: str = "Unknown"  # Largest GPU name (backwards compat)
    vram_mb: int = 0  # Largest GPU VRAM in MB (backwards compat)
    cpu_only: bool = True
    platform: str = "unknown"
    gpus: list[GPUInfo] = field(default_factory=list)  # All detected GPUs
    total_vram_mb: int = 0  # Sum across all GPUs
    total_vram_free_mb: int = 0  # Sum of free VRAM across all GPUs


def detect_hardware() -> HardwareInfo:
    """Detect GPU and VRAM using subprocess calls.

    Returns:
        HardwareInfo with gpu_name, vram_mb, cpu_only flag, platform,
        and a list of all detected GPUs.  On any failure, returns
        cpu_only=True with an empty gpus list.

    Security:
        Uses hardcoded command strings only. No user input interpolated.
    """
    plat = platform.system().lower()
    info = HardwareInfo(platform=plat, cpu_only=True)

    # Try NVIDIA GPU detection (Linux/Windows)
    if plat in ("linux", "windows"):
        try:
            # Hardcoded command: nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                best_vram = 0
                for idx, line in enumerate(lines):
                    parts = line.split(",")
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        vram_str = parts[1].strip().split()[0]
                        try:
                            vram = int(vram_str)
                        except ValueError:
                            continue
                        vram_free = 0
                        if len(parts) >= 3:
                            try:
                                vram_free = int(parts[2].strip().split()[0])
                            except (ValueError, IndexError):
                                pass
                        gpu = GPUInfo(index=idx, name=name, vram_mb=vram, vram_free_mb=vram_free)
                        info.gpus.append(gpu)
                        info.total_vram_mb += vram
                        info.total_vram_free_mb += vram_free
                        if vram > best_vram:
                            best_vram = vram
                            info.gpu_name = name
                            info.vram_mb = vram

                if info.gpus:
                    info.cpu_only = False
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
