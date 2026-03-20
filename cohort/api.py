"""Cohort public API -- stable interface for downstream consumers.

All external consumers (including the Cohort web app) should import
from this module rather than reaching into internal modules directly.
This is the stability contract: symbols exported here maintain backward
compatibility across minor versions.
"""

from __future__ import annotations

# -- Agent system --------------------------------------------------------

from cohort.agent import (
    AgentConfig,
    AgentMemory,
    LearnedFact,
    WorkingMemoryEntry,
)
from cohort.agent_store import AgentStore, set_global_store
from cohort.agent_registry import (
    get_all_agents,
    set_store as set_registry_store,
    _LEGACY_REGISTRY,
)
from cohort.agent_creator import AgentCreator, AgentSpec, AgentType

# -- Chat and messaging --------------------------------------------------

from cohort.chat import Channel, ChatManager, Message, parse_mentions
from cohort.registry import (
    AgentProfile,
    JsonFileStorage,
    StorageBackend,
    create_storage,
)
from cohort.file_transport import JsonlFileStorage, load_agents_from_file
from cohort.sqlite_storage import SqliteStorage

# -- Orchestration -------------------------------------------------------

from cohort.orchestrator import Orchestrator, Session
from cohort.meeting import (
    StakeholderStatus,
    should_agent_speak,
    calculate_contribution_score,
    calculate_composite_relevance,
    extract_keywords,
    initialize_meeting_context,
    STAKEHOLDER_THRESHOLDS,
)
from cohort.compiled_roundtable import (
    build_compiled_prompt,
    parse_compiled_response,
)
from cohort.context_window import truncate_context

# -- Personas and permissions --------------------------------------------

from cohort.personas import load_persona
from cohort.tool_permissions import (
    resolve_permissions,
    load_central_permissions,
    reload_central_permissions,
    get_central_permissions,
    ResolvedPermissions,
)

# -- Local LLM ----------------------------------------------------------

from cohort.local import LocalRouter, detect_hardware
from cohort.local.config import (
    DEFAULT_MODEL,
    MODEL_DESCRIPTIONS,
    get_model_for_vram,
    SMARTEST_CLAUDE_PROMPT,
)
from cohort.local.ollama import OllamaClient
from cohort.local.setup import (
    TOPIC_CATEGORIES,
    TOPIC_FEEDS,
    TOPIC_KEYWORDS,
    run_setup,
)
from cohort.local.tools import build_tool_schemas, execute_tool

# -- Tasks and scheduling ------------------------------------------------

from cohort.work_queue import WorkQueue
from cohort.task_store import TaskStore
from cohort.task_executor import TaskExecutor
from cohort.scheduler import TaskScheduler
from cohort.cron import resolve_preset, compute_next_run, PRESETS, preset_label

# -- Intelligence --------------------------------------------------------

from cohort.briefing import extract_triad_from_brief
from cohort.executive_briefing import ExecutiveBriefing
from cohort.intel_fetcher import IntelFetcher
from cohort.content_analyzer import score_article
from cohort.health_monitor import (
    configure_health_monitor,
    list_services,
    get_state,
    run_service_checks,
    stop_service,
    start_service,
    restart_service,
)
from cohort.benchmark import BENCHMARK_ENABLED, get_benchmark_runner
from cohort.memory_manager import MemoryManager

# -- Public API manifest -------------------------------------------------

__all__ = [
    # Agent system
    "AgentConfig",
    "AgentMemory",
    "LearnedFact",
    "WorkingMemoryEntry",
    "AgentStore",
    "set_global_store",
    "get_all_agents",
    "set_registry_store",
    "_LEGACY_REGISTRY",
    "AgentCreator",
    "AgentSpec",
    "AgentType",
    # Chat and messaging
    "Channel",
    "ChatManager",
    "Message",
    "parse_mentions",
    "AgentProfile",
    "JsonFileStorage",
    "StorageBackend",
    "create_storage",
    "JsonlFileStorage",
    "load_agents_from_file",
    "SqliteStorage",
    # Orchestration
    "Orchestrator",
    "Session",
    "StakeholderStatus",
    "should_agent_speak",
    "calculate_contribution_score",
    "calculate_composite_relevance",
    "extract_keywords",
    "initialize_meeting_context",
    "STAKEHOLDER_THRESHOLDS",
    "build_compiled_prompt",
    "parse_compiled_response",
    "truncate_context",
    # Personas and permissions
    "load_persona",
    "resolve_permissions",
    "load_central_permissions",
    "reload_central_permissions",
    "get_central_permissions",
    "ResolvedPermissions",
    # Local LLM
    "LocalRouter",
    "detect_hardware",
    "DEFAULT_MODEL",
    "MODEL_DESCRIPTIONS",
    "get_model_for_vram",
    "SMARTEST_CLAUDE_PROMPT",
    "OllamaClient",
    "TOPIC_CATEGORIES",
    "TOPIC_FEEDS",
    "TOPIC_KEYWORDS",
    "run_setup",
    "build_tool_schemas",
    "execute_tool",
    # Tasks and scheduling
    "WorkQueue",
    "TaskStore",
    "TaskExecutor",
    "TaskScheduler",
    "resolve_preset",
    "compute_next_run",
    "PRESETS",
    "preset_label",
    # Intelligence
    "ExecutiveBriefing",
    "IntelFetcher",
    "score_article",
    "list_services",
    "get_state",
    "run_service_checks",
    "stop_service",
    "start_service",
    "restart_service",
    "BENCHMARK_ENABLED",
    "get_benchmark_runner",
    "MemoryManager",
    "extract_triad_from_brief",
]
