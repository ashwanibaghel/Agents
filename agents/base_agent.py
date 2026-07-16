import os
from datetime import datetime
from enum import Enum
from tools.git_tool import GitTool


class AgentStatus(Enum):
    IDLE = "IDLE"
    WORKING = "WORKING"
    TESTING = "TESTING"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


class BaseAgent:
    def __init__(self, agent_id, name, project, autonomy_level):
        self.agent_id = agent_id
        self.name = name
        self.project = project
        self.autonomy_level = autonomy_level

        self.status = AgentStatus.IDLE
        self.current_task = None
        self.started_at = None
        self.completed_tasks = []
        self.workspace_info = None
        self.brain = None

    def set_workspace(self, workspace_info):
        """Assign workspace info to the agent after validation."""
        project_mapping = {
            "oi_labs": "OI Labs",
            "dkffj": "DKFFJ",
            "tehsil": "Tehsil Projects"
        }
        workspace_project = workspace_info.get("project")
        expected_project_name = project_mapping.get(workspace_project)
        if expected_project_name != self.project:
            raise RuntimeError(
                f"Workspace project '{workspace_project}' does not match "
                f"agent project '{self.project}'."
            )
        self.workspace_info = workspace_info

    def validate_workspace(self):
        """Validate workspace status, path, and git repository readiness."""
        if not self.workspace_info:
            raise RuntimeError(f"Workspace is not configured/set for agent '{self.name}'.")
        
        workspace_path = self.workspace_info.get("workspace")
        if not workspace_path or not os.path.exists(workspace_path):
            raise RuntimeError(f"Workspace directory '{workspace_path}' does not exist.")
            
        if not GitTool.is_git_repository(workspace_path):
            raise RuntimeError(f"Workspace directory '{workspace_path}' is not a valid Git repository.")

    def assign_task(self, task):
        if self.status == AgentStatus.WORKING:
            raise RuntimeError(
                f"{self.name} is already working."
            )

        self.current_task = task
        self.status = AgentStatus.WORKING
        self.started_at = datetime.now()

        print(
            f"\n🤖 {self.name} received task: "
            f"{task['title']}"
        )

    def start_work(self):
        if not self.current_task:
            raise RuntimeError(
                f"{self.name} has no assigned task."
            )
            
        self.validate_workspace()

        print(
            f"⚙️ {self.name} is working on "
            f"{self.project}..."
        )

    def mark_testing(self):
        self.status = AgentStatus.TESTING

        print(
            f"🧪 {self.name} is testing the work..."
        )

    def complete_task(self):
        if not self.current_task:
            raise RuntimeError(
                "No task available to complete."
            )

        self.completed_tasks.append(
            {
                "task": self.current_task,
                "completed_at": datetime.now().isoformat(),
            }
        )

        print(
            f"✅ {self.name} completed: "
            f"{self.current_task['title']}"
        )

        self.current_task = None
        self.started_at = None
        self.status = AgentStatus.DONE

    def block_task(self, reason):
        self.status = AgentStatus.BLOCKED

        print(
            f"🚨 {self.name} BLOCKED: {reason}"
        )

    def get_status(self):
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "project": self.project,
            "autonomy_level": self.autonomy_level,
            "status": self.status.value,
            "current_task": self.current_task,
            "completed_tasks": len(
                self.completed_tasks
            ),
        }

    def run_task(self, task, max_iterations=20, max_runtime_seconds=600, checkpoint_manager=None, task_source=None, worker_id=None) -> dict:
        """Runs the autonomous runtime execution loop using the assigned brain, supporting state resumption."""
        import time
        
        self.assign_task(task)
        task_id = task.get("id")
        
        observations = []
        iterations = 0
        actions_executed = []
        files_changed = []
        validation_commands = []
        validation_results = []
        
        # 1. Load checkpoint if available
        checkpoint = None
        if checkpoint_manager:
            checkpoint = checkpoint_manager.load_checkpoint(task_id)
            
        if checkpoint:
            iterations = checkpoint.get("iteration", 0)
            observations = checkpoint.get("observations", [])
            actions_executed = checkpoint.get("actions", [])
            cp_data = checkpoint.get("checkpoint_data", {})
            files_changed = cp_data.get("files_changed", [])
            validation_commands = cp_data.get("validation_commands", [])
            validation_results = cp_data.get("validation_results", [])
            print(f"⚙️ {self.name} is resuming task {task_id} from iteration {iterations}...")
            
        start_time = time.time()

        # 2. Validate Workspace
        try:
            self.validate_workspace()
        except Exception as e:
            self.block_task(str(e))
            result = self.create_task_result(
                status="BLOCKED",
                summary=f"Workspace validation failed: {str(e)}",
                actions_executed=actions_executed,
                files_changed=files_changed,
                validation_commands=validation_commands,
                validation_results=validation_results,
                iterations=iterations,
                started_at=self.started_at
            )
            # DO NOT delete checkpoint on BLOCKED or FAILED.
            return result

        # 3. Check task_type presence and validity
        task_type = task.get("task_type")
        if not task_type:
            self.block_task("task_type is required (missing task_type).")
            result = self.create_task_result(
                status="BLOCKED",
                summary="task_type is required (missing task_type).",
                actions_executed=actions_executed,
                files_changed=files_changed,
                validation_commands=validation_commands,
                validation_results=validation_results,
                iterations=iterations,
                started_at=self.started_at
            )
            return result
            
        if task_type not in ["audit", "code", "feature"]:
            self.block_task(f"Invalid task_type: '{task_type}'.")
            result = self.create_task_result(
                status="BLOCKED",
                summary=f"Invalid task_type: '{task_type}'.",
                actions_executed=actions_executed,
                files_changed=files_changed,
                validation_commands=validation_commands,
                validation_results=validation_results,
                iterations=iterations,
                started_at=self.started_at
            )
            return result

        # 4. Execution Loop
        while iterations < max_iterations:
            if time.time() - start_time > max_runtime_seconds:
                self.block_task("Max execution time reached.")
                result = self.create_task_result(
                    status="BLOCKED",
                    summary="Max execution time reached.",
                    actions_executed=actions_executed,
                    files_changed=files_changed,
                    validation_commands=validation_commands,
                    validation_results=validation_results,
                    iterations=iterations,
                    started_at=self.started_at
                )
                return result

            # Build brain context
            context = {
                "task": self.current_task,
                "project": self.workspace_info.get("project") if self.workspace_info else self.project,
                "autonomy_level": self.autonomy_level,
                "workspace_info": self.workspace_info,
                "observations": observations[-5:],  # bounded recent observations
                "iteration": iterations
            }

            # Brain thinks
            try:
                brain_response = self.brain.think(context)
            except Exception as e:
                self.block_task(f"Brain failure: {str(e)}")
                result = self.create_task_result(
                    status="FAILED",
                    summary=f"Brain think failed: {str(e)}",
                    actions_executed=actions_executed,
                    files_changed=files_changed,
                    validation_commands=validation_commands,
                    validation_results=validation_results,
                    iterations=iterations,
                    started_at=self.started_at
                )
                return result

            action = brain_response.get("action")
            action_input = brain_response.get("action_input", {})
            thought_summary = brain_response.get("thought_summary", "")

            actions_executed.append({
                "thought_summary": thought_summary,
                "action": action,
                "action_input": action_input
            })

            # Execute tool action
            tool_result = self.execute_action(action, action_input)
            observations.append(tool_result)

            # Track files changed
            if action == "WRITE_FILE" and tool_result.get("success"):
                path = action_input.get("path")
                if path and path not in files_changed:
                    files_changed.append(path)

            # Track validation commands
            if action == "RUN_COMMAND":
                cmd = action_input.get("command")
                if cmd and cmd not in validation_commands:
                    validation_commands.append(cmd)
                    validation_results.append({
                        "command": cmd,
                        "success": tool_result.get("success"),
                        "output": tool_result.get("output")
                    })

            iterations += 1

            # Save Checkpoint after completed agent iteration
            if checkpoint_manager:
                checkpoint_manager.save_checkpoint(
                    task_id=task_id,
                    project=self.project,
                    status=self.status.value,
                    worker_id=worker_id or "local-worker",
                    iteration=iterations,
                    observations=observations,
                    actions=actions_executed,
                    checkpoint_data={
                        "files_changed": files_changed,
                        "validation_commands": validation_commands,
                        "validation_results": validation_results
                    }
                )

            # Trigger heartbeat update
            if task_source and worker_id:
                task_source.heartbeat_task(task_id, worker_id)

            if action == "COMPLETE_TASK":
                proposed_summary = action_input.get("summary", "")
                gate_passed, gate_error = self.completion_gate(
                    task=task,
                    observations=observations,
                    files_changed=files_changed,
                    validation_results=validation_results,
                    proposed_summary=proposed_summary
                )

                if gate_passed:
                    self.completed_tasks.append(
                        {
                            "task": self.current_task,
                            "completed_at": datetime.now().isoformat(),
                        }
                    )
                    self.status = AgentStatus.DONE
                    
                    # Delete checkpoint on DONE
                    if checkpoint_manager:
                        checkpoint_manager.delete_checkpoint(task_id)
                        
                    res_dict = self.create_task_result(
                        status="DONE",
                        summary=proposed_summary,
                        actions_executed=actions_executed,
                        files_changed=files_changed,
                        validation_commands=validation_commands,
                        validation_results=validation_results,
                        iterations=iterations,
                        started_at=self.started_at
                    )
                    self.current_task = None
                    self.started_at = None
                    return res_dict
                else:
                    # Completion Gate rejected: feed error back to brain as observation
                    observations.append({
                        "success": False,
                        "action": "COMPLETE_TASK",
                        "output": f"Completion Gate Rejected: {gate_error}",
                        "error": gate_error,
                        "metadata": {}
                    })

            elif action == "BLOCK_TASK":
                reason = action_input.get("reason", "Blocked by brain request.")
                self.block_task(reason)
                # Keep checkpoint for BLOCKED. Do not delete.
                return self.create_task_result(
                    status="BLOCKED",
                    summary=reason,
                    actions_executed=actions_executed,
                    files_changed=files_changed,
                    validation_commands=validation_commands,
                    validation_results=validation_results,
                    iterations=iterations,
                    started_at=self.started_at
                )

        self.block_task("Max iterations reached without completion.")
        # Keep checkpoint on FAILED/BLOCKED. Do not delete.
        return self.create_task_result(
            status="BLOCKED",
            summary="Max iterations reached without completion.",
            actions_executed=actions_executed,
            files_changed=files_changed,
            validation_commands=validation_commands,
            validation_results=validation_results,
            iterations=iterations,
            started_at=self.started_at
        )

    def execute_action(self, action: str, action_input: dict) -> dict:
        """Helper to invoke FileTool, TerminalTool, and GitTool."""
        from tools.file_tool import FileTool
        from tools.terminal_tool import TerminalTool
        from tools.git_tool import GitTool

        workspace_path = self.workspace_info.get("workspace") if self.workspace_info else None
        if not workspace_path:
            return {
                "success": False,
                "action": action,
                "output": "",
                "error": "Workspace path is not set.",
                "metadata": {}
            }

        try:
            if action == "LIST_FILES":
                files = FileTool.list_files(workspace_path)
                return {
                    "success": True,
                    "action": action,
                    "output": f"Found {len(files)} files in workspace:\n" + "\n".join(files[:50]),
                    "error": None,
                    "metadata": {"file_count": len(files)}
                }
            elif action == "READ_FILE":
                path = action_input.get("path")
                content = FileTool.read_file(workspace_path, path)
                return {
                    "success": True,
                    "action": action,
                    "output": content,
                    "error": None,
                    "metadata": {"path": path}
                }
            elif action == "SEARCH_CODE":
                query = action_input.get("query")
                matches = FileTool.search_code(workspace_path, query)
                output_str = f"Found {len(matches)} matches:\n" + "\n".join([f"{m['file']}:L{m['line']}: {m['content']}" for m in matches])
                return {
                    "success": True,
                    "action": action,
                    "output": output_str,
                    "error": None,
                    "metadata": {"query": query}
                }
            elif action == "WRITE_FILE":
                path = action_input.get("path")
                content = action_input.get("content")
                FileTool.write_file(workspace_path, path, content)
                return {
                    "success": True,
                    "action": action,
                    "output": f"File '{path}' successfully written.",
                    "error": None,
                    "metadata": {"path": path}
                }
            elif action == "RUN_COMMAND":
                cmd = action_input.get("command")
                res = TerminalTool.run_command(workspace_path, cmd)
                return res
            elif action == "GET_GIT_STATUS":
                status = GitTool.get_repository_status(workspace_path)
                return {
                    "success": True,
                    "action": action,
                    "output": status if status else "Repository status: clean.",
                    "error": None,
                    "metadata": {}
                }
            elif action in ["COMPLETE_TASK", "BLOCK_TASK"]:
                return {
                    "success": True,
                    "action": action,
                    "output": "Stopping action received.",
                    "error": None,
                    "metadata": {}
                }
            else:
                return {
                    "success": False,
                    "action": action,
                    "output": "",
                    "error": f"Unknown action: '{action}'.",
                    "metadata": {}
                }
        except Exception as e:
            return {
                "success": False,
                "action": action,
                "output": "",
                "error": str(e),
                "metadata": {}
            }

    def completion_gate(self, task, observations, files_changed, validation_results, proposed_summary) -> tuple:
        """Gatekeeper validating task completion criteria before allowing DONE status."""
        task_type = task.get("task_type")
        
        if task_type == "audit":
            # 1. At least one repository inspection observation must exist
            inspection_actions = ["LIST_FILES", "READ_FILE", "SEARCH_CODE", "GET_GIT_STATUS", "RUN_COMMAND"]
            has_inspection = any(
                obs.get("action") in inspection_actions and obs.get("success") 
                for obs in observations
            )
            if not has_inspection:
                return False, "Audit task requires at least one successful repository inspection observation."
                
            # 2. Task result/report must exist in COMPLETE_TASK action input summary
            if not proposed_summary or len(proposed_summary.strip()) < 10:
                return False, "Audit task requires a descriptive report/summary of findings (at least 10 chars)."
                
            return True, None
            
        elif task_type in ["code", "feature"]:
            # 1. At least one actual file change must exist
            if not files_changed:
                return False, f"{task_type.capitalize()} modification task requires at least one file change."
                
            # 2. Git status must show expected changes (or we must be on a task branch with changes)
            workspace_path = self.workspace_info.get("workspace")
            if not workspace_path:
                return False, "Workspace path is missing."
            try:
                status = GitTool.get_repository_status(workspace_path)
                is_dirty = status and status.strip()
                active_branch = GitTool.get_current_branch(workspace_path)
                is_on_task_branch = active_branch and active_branch not in ["main", "master"]
                
                if not is_dirty and not is_on_task_branch:
                    return False, f"Git status shows no modified files and not on a task branch, but task is marked as {task_type}."
            except Exception as e:
                return False, f"Failed to verify Git status/branch: {str(e)}"
                
            # 3. Configured validation command must have been executed if validation_results is present
            if validation_results:
                last_validation = validation_results[-1]
                if not last_validation.get("success"):
                    return False, f"Validation command '{last_validation.get('command')}' failed."
                
            # 4. Report/summary must exist
            if not proposed_summary or len(proposed_summary.strip()) < 10:
                return False, f"{task_type.capitalize()} task requires a descriptive task summary."
                
            return True, None
            
        else:
            return False, f"Invalid or missing task_type: '{task_type}'."

    def create_task_result(self, status, summary, actions_executed, files_changed, validation_commands, validation_results, iterations, started_at):
        """Build structured TaskResult dictionary."""
        start_str = started_at.isoformat() if isinstance(started_at, datetime) else str(started_at)
        return {
            "task_id": self.current_task.get("id") if self.current_task else "",
            "project": self.project,
            "status": status,
            "summary": summary,
            "actions_executed": actions_executed,
            "files_changed": files_changed,
            "validation_commands": validation_commands,
            "validation_results": validation_results,
            "iterations": iterations,
            "started_at": start_str,
            "completed_at": datetime.now().isoformat()
        }