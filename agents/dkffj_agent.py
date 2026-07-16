from agents.base_agent import BaseAgent


class DKFFJAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="DKFFJ-AGENT-001",
            name="DKFFJ Agent",
            project="DKFFJ",
            autonomy_level=2,
        )