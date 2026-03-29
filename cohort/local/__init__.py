"""Local LLM routing package for Cohort.

Zero-dependency local LLM routing with hardware detection and Ollama HTTP client.
Falls back gracefully to Claude CLI when local routing unavailable.
"""

from cohort.local.detect import GPUInfo, HardwareInfo, detect_hardware
from cohort.local.ollama import ChatResult
from cohort.local.router import LocalRouter, RouteResult

__all__ = [
    "detect_hardware", "GPUInfo", "HardwareInfo",
    "ChatResult", "LocalRouter", "RouteResult",
]
