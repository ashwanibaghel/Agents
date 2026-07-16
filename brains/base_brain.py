from abc import ABC, abstractmethod

class BaseBrain(ABC):
    @abstractmethod
    def think(self, context: dict) -> dict:
        """
        Think about the next action based on context.
        
        Args:
            context (dict): Contains:
                - task (dict): The task details (contains 'id', 'project', 'title', 'task_type').
                - project (str): The project name/ID.
                - autonomy_level (int): The agent's autonomy level.
                - workspace_info (dict): Workspace path and git details.
                - observations (list): Bounded list of recent ToolResult dicts.
                - iteration (int): Current iteration index (0-indexed).
                
        Returns:
            dict: Structured response with fields:
                - thought_summary (str): Concise operational summary of thought (no hidden chain-of-thought).
                - action (str): Selected action (e.g., LIST_FILES, READ_FILE, SEARCH_CODE, WRITE_FILE,
                                 RUN_COMMAND, GET_GIT_STATUS, COMPLETE_TASK, BLOCK_TASK).
                - action_input (dict): Action parameters (e.g. {"path": "..."} or {"command": "..."}).
                - reason (str): Reasoning behind selecting this action.
                - task_complete (bool): Indicates if the brain believes the task is complete.
        """
        pass
