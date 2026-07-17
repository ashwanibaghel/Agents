import os
import datetime
from workers.antigravity_client import AntigravityClient
from control.project_runtime import ProjectRuntimeManager
from control.event_bus import event_bus, Event
from control.audit_trail import audit_trail
from control.structured_logger import logger

def log_transition(
    event_type: str,
    status: str,
    task_id: str,
    project_id: str,
    trace_id: str,
    conversation_id=None,
    branch=None,
    error_code=None,
    message=None,
    metadata=None
):
    evt_data = {
        "trace_id": trace_id,
        "worker_id": logger.worker_id,
        "task_id": task_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "branch": branch,
        "status": status,
        "error_code": error_code,
        "message": message,
        "metadata": metadata or {}
    }
    event_bus.publish(Event(event_type, evt_data))
    audit_trail.append(
        event_type=event_type,
        status=status,
        trace_id=trace_id,
        worker_id=logger.worker_id,
        task_id=task_id,
        project_id=project_id,
        conversation_id=conversation_id,
        branch=branch,
        error_code=error_code,
        message=message,
        metadata=metadata
    )

class AntigravityWorker:
    def __init__(self, checkpoint_manager=None, task_source=None, client=None, db_path=None):
        self.checkpoint_manager = checkpoint_manager
        self.task_source = task_source
        self.client = client or AntigravityClient()
        
        if db_path is None:
            if checkpoint_manager and hasattr(checkpoint_manager, "db_path"):
                db_path = checkpoint_manager.db_path
            else:
                db_path = "state/task_checkpoints.db"
                
        self.runtime = ProjectRuntimeManager(db_path=db_path)

    def _load_proj_config(self, project: str) -> dict:
        """Safely load project config from config/projects.yaml. Returns {} if missing."""
        try:
            import yaml
            config_path = "config/projects.yaml"
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                return cfg.get("projects", {}).get(project, {})
        except Exception:
            pass
        return {}

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
3. A dedicated task branch `task-{task_id}` has already been created and checked out for you in the workspace. DO NOT create any new branch. Commit ALL your changes to the existing branch `task-{task_id}`.
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
        """Claim and dispatch the task using ProjectRuntimeManager persistent sessions."""
        project = task.get("project")
        task_id = task.get("id")
        task_type = task.get("task_type")
        workspace_path = workspace_info.get("workspace")
        
        # 1. Project Isolation Check
        self.validate_isolation(project, workspace_path)
        
        # 2. Acquire database-backed lock on workspace
        if not self.runtime.sessions.acquire_lock(project, worker_id):
            print(f"🔒 Workspace lock acquisition failed for project '{project}' (already locked by another worker).")
            return {
                "task_id": task_id,
                "project": project,
                "status": "BLOCKED",
                "reason": "Workspace is currently locked by another worker."
            }

        # 3. Check if a task checkpoint already exists (e.g. for resumption across restarts)
        if self.checkpoint_manager:
            checkpoint = self.checkpoint_manager.load_checkpoint(task_id)
            if checkpoint and checkpoint.get("provider") == "antigravity" and checkpoint.get("conversation_id"):
                conv_id = checkpoint.get("conversation_id")
                # Validate the conversation is still alive by checking metadata directly
                meta_res = self.client.get_conversation_metadata(conv_id)
                conv_alive = False
                if meta_res.get("success"):
                    # Check if last activity is within 24 hours
                    try:
                        import datetime as _dt
                        resp = meta_res.get("response", {})
                        inner = resp.get("conversationMetadata", {}).get("metadata", resp)
                        ts = inner.get("lastActivityTime") or inner.get("updatedAt") or inner.get("createdAt")
                        if ts:
                            last = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            diff = (_dt.datetime.now(_dt.timezone.utc) - last).total_seconds()
                            conv_alive = diff < 86400.0  # alive if < 24 hours old
                        else:
                            conv_alive = True  # no timestamp → assume alive (mock/test scenario)
                    except Exception:
                        conv_alive = True  # if we can't parse, assume alive
                if conv_alive:
                    print(f"⚙️ Worker {worker_id} is resuming Antigravity task {task_id} with conversation {conv_id}...")
                    return {
                        "task_id": task_id,
                        "project": project,
                        "status": "DELEGATED",
                        "conversation_id": conv_id,
                        "summary": f"Task is currently delegated to Antigravity conversation {conv_id}."
                    }
                else:
                    print(f"⚠️ Checkpoint conversation {conv_id} is EXPIRED — clearing and re-dispatching fresh for task {task_id}...")
                    self.checkpoint_manager.save_checkpoint(task_id, project, "redispatching", "antigravity", 0, [], [])

        # 4. Git feature workflow (fetch, dirty check, branch creation)
        if task_type == "feature":
            print(f"🧹 Preparing feature branch task-{task_id} inside {workspace_path}...")
            # Verify/update workspace remote URL safely first
            repo_config = self.runtime.sessions.get_session(project)
            repo_url = repo_config.get("repository_url") if repo_config else None
            # If not in session, load from projects.yaml config
            if not repo_url:
                proj_config = self._load_proj_config(project)
                repo_url = proj_config.get("repository")
                
            if repo_url:
                self.runtime.workspaces.verify_or_update_workspace(project, repo_url, workspace_path)
                
            res_git = self.runtime.git.prepare_feature_branch(workspace_path, task_id)
            if res_git["success"]:
                log_transition("GIT_CHECKOUT", "CHECKOUT", task_id, project, task.get("trace_id"), branch=f"task-{task_id}")
            else:
                # Release lock on failure
                self.runtime.sessions.release_lock(project, worker_id)
                print(f"❌ Git prepare branch failed: {res_git['error']}")
                return {
                    "task_id": task_id,
                    "project": project,
                    "status": "BLOCKED",
                    "reason": res_git["error"]
                }

        # 5. Load or create persistent conversation session
        session = self.runtime.sessions.get_session(project)
        session_status = "EXPIRED"
        if session:
            session_status = self.runtime.sessions.check_session_status(project, self.client)
            
        conv_id = None
        if session and session_status in ["ACTIVE", "IDLE"]:
            conv_id = session["conversation_id"]
            
        # Compile prompt (pre-inject context and memory)
        task_context = self.runtime.tasks.inject_task_context(task)
        project_memory = self.runtime.memories.get_memory_prompt(project)
        base_instructions = self.build_prompt(task, workspace_path)
        full_prompt = f"{task_context}{project_memory}{base_instructions}"
        
        model = "pro" if task.get("autonomy_level", 2) >= 2 else "flash"
        
        if conv_id:
            # Resume existing persistent conversation
            print(f"🔄 Resuming persistent conversation {conv_id} for project {project}...")
            log_transition("SESSION_REUSED", "REUSED", task_id, project, task.get("trace_id"), conversation_id=conv_id, branch=f"task-{task_id}" if task_type == "feature" else "main")
            res = self.client.send_message(conv_id, full_prompt)
        else:
            # Create a replacement conversation
            print(f"✨ Creating new persistent conversation session for project {project}...")
            res = self.client.new_conversation(full_prompt, model=model)
            
        if not res["success"]:
            # Release lock on failure
            self.runtime.sessions.release_lock(project, worker_id)
            error_reason = res.get("error") or "Unknown dispatch error"
            return {
                "task_id": task_id,
                "project": project,
                "status": "FAILED",
                "summary": f"Failed to dispatch to Antigravity: {error_reason}",
                "error": error_reason
            }
            
        # Extract and register new conversation ID if needed
        new_conv_id = AntigravityClient.extract_conversation_id(res.get("response", {}))
        if not new_conv_id:
            new_conv_id = AntigravityClient.extract_conversation_id(res)
            
        if new_conv_id:
            conv_id = new_conv_id
            if not session or session_status == "EXPIRED":
                log_transition("SESSION_CREATED", "CREATED", task_id, project, task.get("trace_id"), conversation_id=conv_id, branch=f"task-{task_id}" if task_type == "feature" else "main")
        elif not conv_id:
            conv_id = f"missing-conv-id-{task_id}"
            
        # Save persistent session metadata
        proj_config = self._load_proj_config(project)
        repo_url = proj_config.get("repository")
        default_branch = proj_config.get("default_branch") or "main"
        
        self.runtime.sessions.save_session(
            project_id=project,
            conversation_id=conv_id,
            workspace_path=workspace_path,
            repository_url=repo_url,
            default_branch=default_branch,
            current_branch=f"task-{task_id}" if task_type == "feature" else default_branch,
            status="ACTIVE"
        )
        
        # Save delegation state to SQLite (task checkpoint tracking)
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

        # Update status to delegated in task file/Supabase
        result = {
            "task_id": task_id,
            "project": project,
            "status": "DELEGATED",
            "conversation_id": conv_id,
            "summary": f"Task is successfully delegated to Antigravity conversation {conv_id}."
        }
        log_transition("ANTIGRAVITY_STARTED", "DELEGATED", task_id, project, task.get("trace_id"), conversation_id=conv_id, branch=f"task-{task_id}" if task_type == "feature" else "main")
        if self.task_source:
            self.task_source.update_task_status(task_id, "delegated", result)
            
        return result
