"""cohort hardware -- hardware detection CLI."""

from __future__ import annotations

import argparse

from cohort.cli._base import format_output


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

def _format_hardware(info) -> str:
    """Pretty-print a HardwareInfo object."""
    lines: list[str] = ["\n  Hardware Detection", "  " + "-" * 45]

    lines.append(f"  Platform:   {info.platform}")
    lines.append(f"  CPU only:   {info.cpu_only}")

    gpus = getattr(info, "gpus", [])
    if gpus:
        lines.append(f"\n  GPUs ({len(gpus)}):")
        for gpu in gpus:
            free = getattr(gpu, "vram_free_mb", 0)
            total = gpu.vram_mb
            used = total - free if free else 0
            pct = (used / total * 100) if total else 0
            bar_len = 20
            filled = int(pct / 100 * bar_len)
            bar = "#" * filled + "." * (bar_len - filled)
            lines.append(f"    [{gpu.index}] {gpu.name}")
            lines.append(f"        VRAM: [{bar}] {used:,}MB / {total:,}MB ({pct:.0f}% used)")
    else:
        lines.append("\n  No GPUs detected.")

    total_vram = getattr(info, "total_vram_mb", 0)
    total_free = getattr(info, "total_vram_free_mb", 0)
    if total_vram:
        lines.append(f"\n  Total VRAM:      {total_vram:,} MB")
        lines.append(f"  Total VRAM free: {total_free:,} MB")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def _cmd_hardware(args: argparse.Namespace) -> int:
    """Detect and display hardware info."""
    from cohort.local.detect import detect_hardware

    info = detect_hardware()

    json_flag = getattr(args, "json", False)
    if json_flag:
        format_output(info, json_flag=True)
    else:
        print(_format_hardware(info))

    return 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``cohort hardware`` command."""

    hw_parser = subparsers.add_parser("hardware", help="Detect GPU and hardware info")
    hw_parser.add_argument("--json", action="store_true", help="Output as JSON")


def handle(args: argparse.Namespace) -> int:
    """Dispatch hardware command."""
    return _cmd_hardware(args)
