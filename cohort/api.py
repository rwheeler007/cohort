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
from cohort.agent_creator import AgentCreator, AgentSpec, AgentType
from cohort.agent_registry import (
    _LEGACY_REGISTRY,
    get_all_agents,
)
from cohort.agent_registry import (
    set_store as set_registry_store,
)
from cohort.agent_store import AgentStore, set_global_store
from cohort.benchmark import BENCHMARK_ENABLED, get_benchmark_runner

# -- Intelligence --------------------------------------------------------
from cohort.briefing import extract_triad_from_brief

# -- Chat and messaging --------------------------------------------------
from cohort.chat import Channel, ChatManager, Message, parse_mentions
from cohort.compiled_roundtable import (
    build_compiled_prompt,
    parse_compiled_response,
)
from cohort.content_analyzer import score_article
from cohort.context_window import truncate_context
from cohort.cron import PRESETS, compute_next_run, preset_label, resolve_preset
from cohort.executive_briefing import ExecutiveBriefing
from cohort.file_transport import JsonlFileStorage, load_agents_from_file
from cohort.health_monitor import (
    get_state,
    list_services,
    restart_service,
    run_service_checks,
    start_service,
    stop_service,
)
from cohort.intel_fetcher import IntelFetcher

# -- Local LLM ----------------------------------------------------------
from cohort.local import LocalRouter, detect_hardware
from cohort.local.config import (
    DEFAULT_MODEL,
    MODEL_DESCRIPTIONS,
    SMARTEST_CLAUDE_PROMPT,
    get_model_for_vram,
)
from cohort.local.ollama import OllamaClient
from cohort.local.setup import (
    TOPIC_CATEGORIES,
    TOPIC_FEEDS,
    TOPIC_KEYWORDS,
    run_setup,
)
from cohort.local.tools import build_tool_schemas, execute_tool
from cohort.meeting import (
    STAKEHOLDER_THRESHOLDS,
    StakeholderStatus,
    calculate_composite_relevance,
    calculate_contribution_score,
    extract_keywords,
    initialize_meeting_context,
    should_agent_speak,
)
from cohort.memory_manager import MemoryManager

# -- Orchestration -------------------------------------------------------
from cohort.orchestrator import Orchestrator, Session

# -- Personas and permissions --------------------------------------------
from cohort.personas import load_persona
from cohort.registry import (
    AgentProfile,
    JsonFileStorage,
    StorageBackend,
    create_storage,
)
from cohort.scheduler import TaskScheduler
from cohort.sqlite_storage import SqliteStorage
from cohort.task_executor import TaskExecutor
from cohort.task_store import TaskStore
from cohort.tool_permissions import (
    ResolvedPermissions,
    get_central_permissions,
    load_central_permissions,
    reload_central_permissions,
    resolve_permissions,
)

# -- Tasks and scheduling ------------------------------------------------
from cohort.work_queue import WorkQueue

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
