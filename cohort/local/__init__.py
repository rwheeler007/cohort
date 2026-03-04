"""Local LLM routing package for Cohort.

Zero-dependency local LLM routing with hardware detection and Ollama HTTP client.
Falls back gracefully to Claude CLI when local routing unavailable.
"""

from cohort.local.detect import detect_hardware, GPUInfo, HardwareInfo
from cohort.local.router import LocalRouter, RouteResult

__all__ = ["detect_hardware", "GPUInfo", "HardwareInfo", "LocalRouter", "RouteResult"]
