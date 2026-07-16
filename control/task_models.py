class Task:
    def __init__(self, task_id: str, project: str, task_type: str, objective: str,
                 context: str, acceptance_criteria: list, constraints: list,
                 validation_commands: list, autonomy_level: int, status: str = "inbox",
                 worker_id: str = None, claimed_at: str = None, last_heartbeat_at: str = None,
                 summary: str = None, evidence_paths: list = None,
                 files_changed: list = None, validation_results: list = None,
                 error_message: str = None):
        self.task_id = task_id
        self.project = project
        self.task_type = task_type
        self.objective = objective
        self.context = context
        self.acceptance_criteria = acceptance_criteria or []
        self.constraints = constraints or []
        self.validation_commands = validation_commands or []
        self.autonomy_level = autonomy_level
        self.status = status
        self.worker_id = worker_id
        self.claimed_at = claimed_at
        self.last_heartbeat_at = last_heartbeat_at
        self.summary = summary
        self.evidence_paths = evidence_paths or []
        self.files_changed = files_changed or []
        self.validation_results = validation_results or []
        self.error_message = error_message

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "project": self.project,
            "task_type": self.task_type,
            "objective": self.objective,
            "context": self.context,
            "acceptance_criteria": self.acceptance_criteria,
            "constraints": self.constraints,
            "validation_commands": self.validation_commands,
            "autonomy_level": self.autonomy_level,
            "status": self.status,
            "worker_id": self.worker_id,
            "claimed_at": self.claimed_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "summary": self.summary,
            "evidence_paths": self.evidence_paths,
            "files_changed": self.files_changed,
            "validation_results": self.validation_results,
            "error_message": self.error_message
        }

    @staticmethod
    def from_dict(data: dict) -> 'Task':
        return Task(
            task_id=data.get("task_id"),
            project=data.get("project"),
            task_type=data.get("task_type"),
            objective=data.get("objective"),
            context=data.get("context"),
            acceptance_criteria=data.get("acceptance_criteria", []),
            constraints=data.get("constraints", []),
            validation_commands=data.get("validation_commands", []),
            autonomy_level=data.get("autonomy_level", 2),
            status=data.get("status", "inbox"),
            worker_id=data.get("worker_id"),
            claimed_at=data.get("claimed_at"),
            last_heartbeat_at=data.get("last_heartbeat_at"),
            summary=data.get("summary"),
            evidence_paths=data.get("evidence_paths", []),
            files_changed=data.get("files_changed", []),
            validation_results=data.get("validation_results", []),
            error_message=data.get("error_message")
        )
