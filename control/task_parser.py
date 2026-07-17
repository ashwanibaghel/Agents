import yaml
from control.task_models import Task

class TaskParser:
    @staticmethod
    def parse_yaml(yaml_content: str) -> Task:
        """Parse a YAML string into a Task model, validating required schema fields."""
        try:
            data = yaml.safe_load(yaml_content)
            if not isinstance(data, dict):
                raise ValueError("Parsed YAML is not a dictionary.")
            
            # Validation of required fields
            required_fields = ["task_id", "project", "task_type", "objective", "autonomy_level"]
            for field in required_fields:
                if field not in data or data[field] is None:
                    raise ValueError(f"Required field '{field}' is missing.")
            
            # Validate task_type
            if data["task_type"] not in ["audit", "code", "feature"]:
                raise ValueError(f"Invalid task_type: '{data['task_type']}'. Expected 'audit', 'code', or 'feature'.")
                
            return Task.from_dict(data)
        except Exception as e:
            raise ValueError(f"Failed to parse task: {str(e)}")

    @staticmethod
    def to_agent_format(task: Task) -> dict:
        """Convert Task model to dictionary format required by BaseAgent."""
        return {
            "id": task.task_id,
            "project": task.project,
            "title": task.objective,
            "task_type": task.task_type,
            "validation_commands": task.validation_commands
        }
