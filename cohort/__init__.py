"""cohort -- Multi-agent orchestration with loop prevention and contribution scoring."""

from cohort.agent import AgentConfig, AgentMemory
from cohort.agent_store import AgentStore
from cohort.chat import Channel, ChatManager, Message, parse_mentions
from cohort.file_transport import JsonlFileStorage
from cohort.meeting import StakeholderStatus, should_agent_speak
from cohort.orchestrator import Orchestrator, Session
from cohort.registry import AgentProfile, JsonFileStorage, StorageBackend

__version__ = "0.4.22"

__all__ = [
    "AgentConfig",
    "AgentMemory",
    "AgentProfile",
    "AgentStore",
    "StorageBackend",
    "JsonFileStorage",
    "JsonlFileStorage",
    "Orchestrator",
    "Session",
    "StakeholderStatus",
    "should_agent_speak",
    "Channel",
    "ChatManager",
    "Message",
    "parse_mentions",
]
