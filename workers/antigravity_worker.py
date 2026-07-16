import os
import datetime
from workers.antigravity_client import AntigravityClient

class AntigravityWorker:
    def __init__(self, checkpoint_manager=None, task_source=None, client=None):
        self.checkpoint_manager = checkpoint_manager
        self.task_source = task_source
        self.client = client or AntigravityClient()

    def validate_isolation(self, project: str, workspace_path: str):
        """Strict project/workspace path isolation check. Rejects mismatch."""
        if not workspace_path:
            raise ValueError(f"Workspace path is missing for project '{project}'.")
            
        abs_workspace = os.path.abspath(workspace_path).lower().replace("\\", "/")
        
        if project == "oi_labs":
            expected_suffix = "workspaces/oi-labs"
        elif project == "dkffj":
            expected_suffix = "workspaces/dkffj"
        else:
            raise ValueError(f"Project '{project}' is not authorized for Antigravity dispatch.")
            
        if not abs_workspace.endswith(expected_suffix):
            raise ValueError(
                f"Workspace isolation violation! Project '{project}' workspace path '{workspace_path}' "
                f"does not match expected suffix '{expected_suffix}'."
            )

    def build_prompt(self, task: dict, workspace_path: str) -> str:
        """Construct the full, structured task engineering prompt with constraints."""
        criteria = task.get("acceptance_criteria", [])
        constraints = task.get("constraints", [])
        commands = task.get("validation_commands", [])
        task_id = task.get("id")
        
        prompt = f"""[ANTIGRAVITY TASK INSTRUCTION]
TASK_ID: {task_id}
PROJECT: {task.get("project")}
TASK_TYPE: {task.get("task_type")}
OBJECTIVE: {task.get("title")}
CONTEXT: {task.get("context", "")}
ACCEPTANCE_CRITERIA:
{chr(10).join("- " + str(c) for c in criteria) if criteria else "None"}
CONSTRAINTS:
{chr(10).join("- " + str(c) for c in constraints) if constraints else "None"}
VALIDATION_COMMANDS:
{chr(10).join("- " + str(c) for c in commands) if commands else "None"}
AUTONOMY_LEVEL: {task.get("autonomy_level", 2)}
EXACT WORKSPACE PATH: {workspace_path}

OPERATIONAL CONSTRAINTS for Antigravity Agent:
1. Inspect the existing repository first before making modifications.
2. Work ONLY inside the exact assigned workspace directory: {workspace_path}.
3. Create and use a dedicated task branch for work.
4. Never modify another project workspace or any files outside this assigned workspace.
5. Never read, print, or expose .env or credentials files.
6. Implement the requested task according to acceptance criteria and constraints.
7. Run the configured validation commands: {", ".join(commands) if commands else "None"}.
8. Inspect, debug, and fix any test/validation failures.
9. Rerun validation to verify success.
10. Do not fake completion, do not deploy, and do not merge to the main branch.
11. After actual work and validation, write exactly one completion receipt to the absolute path:
    E:\\Projects\\ashwani-agent-company\\state\\receipts\\{task_id}.json
    The receipt must be a structured JSON object containing:
    {{
      "task_id": "{task_id}",
      "conversation_id": null,
      "status": "DONE | BLOCKED | FAILED",
      "summary": "Descriptive summary of what was done",
      "evidence_paths": ["relative/path/to/inspected/or/evidence/file"],
      "files_changed": ["relative/path/to/modified/file"],
      "validation_commands": {commands},
      "validation_results": [
        {{
          "command": "command string",
          "success": true
        }}
      ],
      "completed_at": "ISO-8601 timestamp"
    }}
12. Do not write DONE or complete the task until the work is finished and the completion receipt is written.
"""
        return prompt

    def dispatch_task(self, task: dict, workspace_info: dict, worker_id: str) -> dict:
        """Claim and dispatch the task to the Antigravity client, saving delegation state."""
        project = task.get("project")
        task_id = task.get("id")
        workspace_path = workspace_info.get("workspace")
        
        # 1. Project Isolation Check
        self.validate_isolation(project, workspace_path)
        
        # 2. Check if a conversation ID already exists in SQLite (resumption)
        if self.checkpoint_manager:
            checkpoint = self.checkpoint_manager.load_checkpoint(task_id)
            if checkpoint and checkpoint.get("provider") == "antigravity" and checkpoint.get("conversation_id"):
                conv_id = checkpoint.get("conversation_id")
                print(f"⚙️ Worker {worker_id} is resuming Antigravity task {task_id} with conversation {conv_id}...")
                return {
                    "task_id": task_id,
                    "project": project,
                    "status": "DELEGATED",
                    "conversation_id": conv_id,
                    "summary": f"Task is currently delegated to Antigravity conversation {conv_id}."
                }

        # 3. Compile prompt
        prompt = self.build_prompt(task, workspace_path)
        model = "pro" if task.get("autonomy_level", 2) >= 2 else "flash"
        
        # 4. Trigger agentapi command execution
        res = self.client.new_conversation(prompt, model=model)
        
        if not res["success"]:
            # Re-read or get error details
            error_reason = res.get("error") or "Unknown dispatch error"
            return {
                "task_id": task_id,
                "project": project,
                "status": "FAILED",
                "summary": f"Failed to dispatch to Antigravity: {error_reason}",
                "error": error_reason
            }
            
        # 5. Extract conversation ID defensively
        conv_id = AntigravityClient.extract_conversation_id(res.get("response", {}))
        if not conv_id:
            # Fallback search inside the command output text if JSON layout differs
            conv_id = AntigravityClient.extract_conversation_id(res)
            if not conv_id:
                conv_id = f"missing-conv-id-{task_id}"

        # 6. Save delegation state to SQLite
        now_str = datetime.datetime.now().isoformat()
        if self.checkpoint_manager:
            self.checkpoint_manager.save_delegation_state(
                task_id=task_id,
                project=project,
                status="delegated",
                worker_id=worker_id,
                provider="antigravity",
                conversation_id=conv_id,
                delegated_at=now_str,
                last_followup_at=now_str,
                worker_model=model,
                delegation_status="delegated"
            )

        # 7. Update status to delegated in task file
        result = {
            "task_id": task_id,
            "project": project,
            "status": "DELEGATED",
            "conversation_id": conv_id,
            "summary": f"Task is successfully delegated to Antigravity conversation {conv_id}."
        }
        if self.task_source:
            self.task_source.update_task_status(task_id, "delegated", result)
            
        return result
