from concurrent.futures import ThreadPoolExecutor, as_completed
from control.workspace_manager import WorkspaceManager
from brains.scripted_brain import ScriptedBrain


class Dispatcher:
    def __init__(self, agents, max_parallel_agents=3, workspace_manager=None):
        self.agents = agents
        self.max_parallel_agents = max_parallel_agents
        self.workspace_manager = workspace_manager or WorkspaceManager()

    def find_agent(self, project):
        return self.agents.get(project)

    def execute_task(self, agent, task, checkpoint_manager=None, task_source=None, worker_id=None, worker_mode="scripted"):
        project_id = task["project"]
        try:
            # 1. Prepare Workspace (if this fails, task is BLOCKED)
            try:
                workspace_info = self.workspace_manager.prepare_workspace(project_id)
                agent.set_workspace(workspace_info)
            except Exception as prep_error:
                agent.block_task(str(prep_error))
                return {
                    "task_id": task["id"],
                    "project": agent.project,
                    "status": "BLOCKED",
                    "reason": str(prep_error),
                }

            # 2. Execute Task using ScriptedBrain or AntigravityWorker
            try:
                if worker_mode == "antigravity":
                    from workers.antigravity_worker import AntigravityWorker
                    worker = AntigravityWorker(checkpoint_manager=checkpoint_manager, task_source=task_source)
                    task_result = worker.dispatch_task(task, workspace_info, worker_id or "local-worker")
                    return task_result
                else:
                    # Assign brain dynamically (independent of provider)
                    agent.brain = ScriptedBrain()
                    
                    # Execute autonomous task run loop
                    task_result = agent.run_task(
                        task, 
                        checkpoint_manager=checkpoint_manager, 
                        task_source=task_source, 
                        worker_id=worker_id
                    )
                    return task_result
            except Exception as exec_error:
                agent.block_task(str(exec_error))
                return {
                    "task_id": task["id"],
                    "project": agent.project,
                    "status": "FAILED",
                    "error": str(exec_error),
                }

        except Exception as error:
            # General fallback
            return {
                "task_id": task["id"],
                "project": agent.project if agent else project_id,
                "status": "FAILED",
                "error": str(error),
            }

    def dispatch(self, tasks, checkpoint_manager=None, task_source=None, worker_id=None, worker_mode="scripted"):
        futures = []

        print("\n🚀 DISPATCHING TASKS...\n")

        with ThreadPoolExecutor(
            max_workers=self.max_parallel_agents
        ) as executor:

            for task in tasks:
                agent = self.find_agent(task["project"])

                if not agent:
                    print(
                        f"❌ No agent found for "
                        f"{task['project']}"
                    )
                    continue

                future = executor.submit(
                    self.execute_task,
                    agent,
                    task,
                    checkpoint_manager,
                    task_source,
                    worker_id,
                    worker_mode
                )

                futures.append(future)

            results = []

            for future in as_completed(futures):
                results.append(future.result())

        return results