from agents.base_agent import BaseAgent


class OIAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="OI-AGENT-001",
            name="OI Labs Agent",
            project="OI Labs",
            autonomy_level=2,
        )