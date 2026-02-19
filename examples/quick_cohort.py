"""10-line quickstart: two agents in a discussion session."""

from cohort import JsonFileStorage, Orchestrator
from cohort.chat import ChatManager

storage = ChatManager(JsonFileStorage("demo_data"))
storage.create_channel("design-review", "API design review")

agents = {
    "alice": {"triggers": ["api", "design"], "capabilities": ["backend architecture"]},
    "perry": {"triggers": ["testing", "qa"], "capabilities": ["test strategy"]},
}

orch = Orchestrator(storage, agents=agents)
session = orch.start_session("design-review", "REST API design review", list(agents))
print(f"Session started: {session.session_id} with {len(session.initial_agents)} agents")
