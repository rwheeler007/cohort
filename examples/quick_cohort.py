"""Quick start: two agents in a scored discussion session."""

from cohort import JsonFileStorage, Orchestrator
from cohort.chat import ChatManager


def main():
    # Storage and chat substrate
    chat = ChatManager(JsonFileStorage("demo_data"))
    chat.create_channel("design-review", "API design review")

    # Agent definitions -- triggers and capabilities drive scoring
    agents = {
        "architect": {"triggers": ["api", "design"], "capabilities": ["backend architecture"]},
        "tester": {"triggers": ["testing", "qa"], "capabilities": ["test strategy"]},
    }

    # Start a session -- Cohort selects participants by relevance
    orch = Orchestrator(chat, agents=agents)
    session = orch.start_session("design-review", "REST API design review", list(agents))
    print(f"Session started: {session.session_id} with {len(session.initial_agents)} agents")

    # Ask who should speak next
    rec = orch.get_next_speaker(session.session_id)
    if rec:
        print(f"Recommended speaker: {rec['recommended_speaker']}")
        print(f"Reason: {rec['reason']}")
    else:
        print("No speaker recommended (all below threshold)")


if __name__ == "__main__":
    main()
