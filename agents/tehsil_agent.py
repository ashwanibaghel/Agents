from agents.base_agent import BaseAgent


class TehsilAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="TEHSIL-AGENT-001",
            name="Tehsil Agent",
            project="Tehsil Projects",
            autonomy_level=1,
        )