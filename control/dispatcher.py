import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from control.workspace_manager import WorkspaceManager
from brains.scripted_brain import ScriptedBrain
from control.metrics_manager import metrics_manager
from control.telemetry import log_transition


class Dispatcher:
    def __init__(self, agents, max_parallel_agents=3, workspace_manager=None):
        self.agents = agents
        self.max_parallel_agents = max_parallel_agents
        self.workspace_manager = workspace_manager or WorkspaceManager()

    def find_agent(self, project):
        return self.agents.get(project)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _prepare_workspace(self, agent, task) -> dict:
        """Prepare workspace for task execution. Returns workspace_info dict.
        Raises on failure — caller handles the exception and blocks the task."""
        project_id = task["project"]
        project_config = self.workspace_manager.config.get("projects", {}).get(project_id, {})
        workspace_rel = project_config.get("workspace")
        workspace_reused = False

        if workspace_rel:
            workspace_path = os.path.abspath(
                os.path.join(self.workspace_manager.project_root, workspace_rel)
            )
            if os.path.exists(workspace_path) and os.path.exists(
                os.path.join(workspace_path, ".git")
            ):
                workspace_reused = True

        metrics_manager.record_workspace_reuse(task.get("trace_id"), workspace_reused)
        workspace_info = self.workspace_manager.prepare_workspace(project_id)
        agent.set_workspace(workspace_info)
        return workspace_info

    def _run_agent(self, agent, task, workspace_info, checkpoint_manager, task_source, worker_id, worker_mode):
        """Execute task via AntigravityWorker or ScriptedBrain. Returns result dict."""
        if worker_mode == "antigravity":
            from workers.antigravity_worker import AntigravityWorker
            worker = AntigravityWorker(
                checkpoint_manager=checkpoint_manager,
                task_source=task_source,
            )
            return worker.dispatch_task(task, workspace_info, worker_id or "local-worker")

        # Scripted mode
        agent.brain = ScriptedBrain()
        return agent.run_task(
            task,
            checkpoint_manager=checkpoint_manager,
            task_source=task_source,
            worker_id=worker_id,
        )

    # ── Public interface ─────────────────────────────────────────────────────

    def execute_task(self, agent, task, checkpoint_manager=None, task_source=None, worker_id=None, worker_mode="scripted"):
        """Orchestrate workspace prep + task execution for a single task."""
        project_id = task["project"]
        task_id = task["id"]
        trace_id = task.get("trace_id")

        try:
            # 1. Prepare Workspace (failure → BLOCKED)
            try:
                workspace_info = self._prepare_workspace(agent, task)
                log_transition("WORKSPACE_PREPARED", "PREPARED", task_id, project_id, trace_id)
            except Exception as prep_error:
                log_transition(
                    "WORKSPACE_PREPARED", "FAILED", task_id, project_id, trace_id,
                    error_code="CONFIG_002", message=str(prep_error),
                )
                agent.block_task(str(prep_error))
                return {
                    "task_id": task_id,
                    "project": agent.project,
                    "status": "BLOCKED",
                    "reason": str(prep_error),
                }

            # 2. Execute task
            try:
                return self._run_agent(
                    agent, task, workspace_info,
                    checkpoint_manager, task_source, worker_id, worker_mode,
                )
            except Exception as exec_error:
                agent.block_task(str(exec_error))
                return {
                    "task_id": task_id,
                    "project": agent.project,
                    "status": "FAILED",
                    "error": str(exec_error),
                }

        except Exception as error:
            return {
                "task_id": task_id,
                "project": agent.project if agent else project_id,
                "status": "FAILED",
                "error": str(error),
            }

    def dispatch(self, tasks, checkpoint_manager=None, task_source=None, worker_id=None, worker_mode="scripted"):
        """Dispatch all tasks in parallel up to max_parallel_agents."""
        print("\n🚀 DISPATCHING TASKS...\n")
        futures = []

        with ThreadPoolExecutor(max_workers=self.max_parallel_agents) as executor:
            for task in tasks:
                agent = self.find_agent(task["project"])
                if not agent:
                    print(f"❌ No agent found for {task['project']}")
                    continue

                future = executor.submit(
                    self.execute_task,
                    agent, task, checkpoint_manager, task_source, worker_id, worker_mode,
                )
                futures.append(future)

            results = [future.result() for future in as_completed(futures)]

        return results