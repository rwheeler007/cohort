"""cohort -- Multi-agent orchestration with loop prevention and contribution scoring."""

from cohort.chat import Channel, Message
from cohort.meeting import StakeholderStatus, should_agent_speak
from cohort.orchestrator import Orchestrator, Session
from cohort.registry import AgentProfile, JsonFileStorage, StorageBackend

__version__ = "0.1.0"

__all__ = [
    "AgentProfile",
    "StorageBackend",
    "JsonFileStorage",
    "Orchestrator",
    "Session",
    "StakeholderStatus",
    "should_agent_speak",
    "Channel",
    "Message",
]
