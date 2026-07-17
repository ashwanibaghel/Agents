import sys
import yaml
import os
import time
import signal
from dotenv import load_dotenv

load_dotenv()

os.environ["GIT_TERMINAL_PROMPT"] = "0"
os.environ["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from agents.oi_agent import OIAgent
from agents.dkffj_agent import DKFFJAgent
from agents.tehsil_agent import TehsilAgent
from control.dispatcher import Dispatcher
from control.task_source import LocalTaskSource
from control.checkpoint_manager import CheckpointManager
from control.task_parser import TaskParser
from control.receipt_monitor import ReceiptMonitor
from control.result_verifier import ResultVerifier
from control.structured_logger import logger
from control import error_codes
from control.event_bus import event_bus, Event
from control.audit_trail import audit_trail
from control.metrics_manager import metrics_manager
import uuid


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
    
    # Record metrics for transitions
    if trace_id:
        if event_type == "TASK_CLAIMED":
            metrics_manager.start_task_metric(trace_id, task_id, project_id)
        elif event_type == "ANTIGRAVITY_STARTED":
            metrics_manager.start_timer(trace_id, "execution")
        elif event_type == "ANTIGRAVITY_COMPLETED":
            metrics_manager.stop_timer(trace_id, "execution")
        elif event_type == "VERIFICATION_STARTED":
            metrics_manager.start_timer(trace_id, "verification")
        elif event_type == "VERIFICATION_PASSED":
            metrics_manager.stop_timer(trace_id, "verification")
            metrics_manager.record_verifier_result(trace_id, True)
        elif event_type == "VERIFICATION_FAILED":
            metrics_manager.stop_timer(trace_id, "verification")
            metrics_manager.record_verifier_result(trace_id, False)
            metrics_manager.increment_counter("verifier_failures")
        elif event_type == "GIT_PUSH" and status == "PUSHED":
            metrics_manager.record_git_result(trace_id, True)
        elif event_type == "TASK_COMPLETED":
            metrics_manager.complete_task_metric(trace_id, "DONE")
        elif event_type == "TASK_FAILED":
            metrics_manager.complete_task_metric(trace_id, "FAILED")
        elif event_type == "TASK_BLOCKED":
            metrics_manager.complete_task_metric(trace_id, "BLOCKED")


def load_config():
    with open(
        "config/projects.yaml",
        "r",
        encoding="utf-8",
    ) as file:
        return yaml.safe_load(file)


def main():
    from control.config_manager import ConfigManager
    config_mgr = ConfigManager()
    is_valid, errors = config_mgr.validate_startup()
    if not is_valid:
        for err in errors:
            parts = err.split(":", 1)
            err_code = parts[0].strip()
            msg = parts[1].strip() if len(parts) > 1 else err
            logger.critical(f"Startup config validation failed: {msg}", error_code=err_code, step="STARTUP")
        sys.exit(1)

    config = config_mgr.projects_config

    logger.info("ASHWANI AGENT COMPANY - Worker Booted", step="STARTUP")
    logger.info(f"Configuration Version: {config_mgr.get_version()}", step="STARTUP")
    for flag in ["persistent_sessions", "structured_logging", "metrics", "auto_push", "chaos_testing", "backup"]:
        status = "ENABLED" if config_mgr.get_feature_flag(flag) else "DISABLED"
        logger.info(f"Feature Flag - {flag}: {status}", step="STARTUP")

    # Get active projects from config
    active_projects = {
        proj_id: proj_info
        for proj_id, proj_info in config.get("projects", {}).items()
        if proj_info.get("active", True)
    }

    # Instantiate agents for active projects only
    all_agents = {
        "oi_labs": OIAgent(),
        "dkffj": DKFFJAgent(),
        "tehsil": TehsilAgent(),
    }
    agents = {
        proj_id: all_agents[proj_id]
        for proj_id in active_projects
        if proj_id in all_agents
    }

    # Detect Supabase config
    supabase_cfg_path = "config/supabase.yaml"
    use_supabase = False
    task_source = None

    if os.path.exists(supabase_cfg_path):
        try:
            with open(supabase_cfg_path, "r", encoding="utf-8") as _f:
                _sb = yaml.safe_load(_f)
            if _sb and _sb.get("enabled", False):
                from control.supabase_task_source import SupabaseTaskSource
                task_source = SupabaseTaskSource(
                    supabase_url=_sb["supabase_url"],
                    supabase_key=_sb["supabase_key"],
                )
                use_supabase = True
                logger.info("Task source initialized: Supabase", step="STARTUP")
        except Exception as _e:
            logger.warning(f"Supabase config load failed: {_e} — falling back to local", error_code=error_codes.SUPABASE_001, step="STARTUP")

    if not use_supabase:
        task_source = LocalTaskSource(base_dir="tasks")
        logger.info("Task source initialized: Local filesystem", step="STARTUP")

    checkpoint_manager = CheckpointManager(db_path="state/task_checkpoints.db")

    worker_id = logger.worker_id
    metrics_manager.record_worker_boot(worker_id)
    worker_mode = config.get("company", {}).get("worker_mode", "scripted")

    # Polling config
    poll_interval = 5.0
    run_once = "--once" in sys.argv or not use_supabase

    # Setup signal handler for graceful shutdown
    shutdown = False

    def handle_shutdown(signum, frame):
        nonlocal shutdown
        logger.info("Shutdown signal received. Gracefully exiting loop after current cycle...", step="SHUTDOWN")
        shutdown = True

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    last_worker_heartbeat = 0.0
    trace_ids = {}

    while not shutdown:
        # Update worker heartbeat every 15 seconds
        now_time = time.time()
        if now_time - last_worker_heartbeat >= 15.0:
            metrics_manager.record_worker_heartbeat(worker_id)
            if use_supabase:
                try:
                    task_source.update_worker_heartbeat(worker_id)
                except Exception as _e:
                    logger.warning(f"Supabase worker heartbeat update failed: {_e}", error_code=error_codes.SUPABASE_003, step="HEARTBEAT")
            last_worker_heartbeat = now_time

        # 1. Stale task recovery
        if use_supabase:
            try:
                recovered = task_source.recover_stale_tasks()
                if recovered > 0:
                    logger.info(f"Recovered {recovered} stale task(s) back to inbox.", step="RECOVERY")
            except Exception as e:
                logger.error(f"Stale task recovery failed: {e}", error_code=error_codes.SUPABASE_002, step="RECOVERY")

        # 2. Fetch pending tasks from inbox
        try:
            pending_tasks = task_source.fetch_pending_tasks()
        except Exception as e:
            logger.error(f"Failed to fetch pending tasks: {e}", error_code=error_codes.SUPABASE_002, step="FETCH")
            pending_tasks = []

        # Generate default tasks only for local filesystem when empty
        if not pending_tasks and not use_supabase:
            has_active_working = False
            if os.path.exists(task_source.working_dir):
                for file in os.listdir(task_source.working_dir):
                    if file.endswith((".yaml", ".yml")):
                        has_active_working = True
                        break

            if not has_active_working:
                logger.info("Inbox is empty. Generating default tasks in tasks/inbox/...", step="FETCH")
                default_oi = {
                    "task_id": "OI-001",
                    "project": "oi_labs",
                    "task_type": "audit",
                    "objective": "Audit dataset training readiness",
                    "context": "Auditing backend readiness",
                    "acceptance_criteria": ["Run LIST_FILES"],
                    "constraints": ["Read-only"],
                    "validation_commands": ["git status --short"],
                    "autonomy_level": 2,
                    "status": "inbox"
                }
                default_dk = {
                    "task_id": "DKFFJ-001",
                    "project": "dkffj",
                    "task_type": "audit",
                    "objective": "Test membership workflow",
                    "context": "Auditing membership frontend/backend",
                    "acceptance_criteria": ["Run LIST_FILES"],
                    "constraints": ["Read-only"],
                    "validation_commands": ["git status --short"],
                    "autonomy_level": 2,
                    "status": "inbox"
                }

                with open(os.path.join(task_source.inbox_dir, "OI-001.yaml"), "w", encoding="utf-8") as f:
                    yaml.dump(default_oi, f)
                with open(os.path.join(task_source.inbox_dir, "DKFFJ-001.yaml"), "w", encoding="utf-8") as f:
                    yaml.dump(default_dk, f)

                pending_tasks = task_source.fetch_pending_tasks()

        # 3. Claim pending tasks
        claimed_tasks = []
        mapped_agent_tasks = []

        for task in pending_tasks:
            if task.project not in active_projects:
                continue
            if task_source.claim_task(task.task_id, worker_id):
                # Generate unique trace ID for this task execution
                t_id = f"trace-{str(uuid.uuid4())[:8]}"
                trace_ids[task.task_id] = t_id
                logger.info(f"Claimed task: {task.task_id}", trace_id=t_id, task_id=task.task_id, project_id=task.project, step="CLAIMED")
                log_transition("TASK_CLAIMED", "CLAIMED", task.task_id, task.project, t_id)

                # Clear any stale checkpoint from a previous run of this task.
                checkpoint_manager.delete_checkpoint(task.task_id)
                claimed_tasks.append(task)
                agent_task = TaskParser.to_agent_format(task)
                agent_task["trace_id"] = t_id
                mapped_agent_tasks.append(agent_task)

        # 4. Resume active tasks (delegated/claimed)
        if use_supabase:
            try:
                active_tasks = task_source.fetch_active_tasks(worker_id)
                for task in active_tasks:
                    if task.project in active_projects:
                        if not any(t.task_id == task.task_id for t in claimed_tasks):
                            t_id = trace_ids.get(task.task_id) or f"trace-{str(uuid.uuid4())[:8]}"
                            trace_ids[task.task_id] = t_id
                            logger.info(f"Resumed active task: {task.task_id}", trace_id=t_id, task_id=task.task_id, project_id=task.project, step="CLAIMED")
                            log_transition("TASK_CLAIMED", "RESUMED", task.task_id, task.project, t_id)
                            claimed_tasks.append(task)
                            agent_task = TaskParser.to_agent_format(task)
                            agent_task["trace_id"] = t_id
                            mapped_agent_tasks.append(agent_task)
            except Exception as e:
                logger.error(f"Failed to fetch active tasks: {e}", error_code=error_codes.SUPABASE_002, step="FETCH")
        else:
            if os.path.exists(task_source.working_dir):
                for file in os.listdir(task_source.working_dir):
                    if file.endswith((".yaml", ".yml")):
                        file_path = os.path.join(task_source.working_dir, file)
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                data = yaml.safe_load(f)
                            task = TaskParser.from_dict(data)
                            if task.status == "delegated" and task.project in active_projects:
                                t_id = trace_ids.get(task.task_id) or f"trace-{str(uuid.uuid4())[:8]}"
                                trace_ids[task.task_id] = t_id
                                log_transition("TASK_CLAIMED", "RESUMED", task.task_id, task.project, t_id)
                                claimed_tasks.append(task)
                                agent_task = TaskParser.to_agent_format(task)
                                agent_task["trace_id"] = t_id
                                mapped_agent_tasks.append(agent_task)
                        except Exception:
                            pass

        # 5. Process claimed tasks
        if mapped_agent_tasks:
            # Clean stale receipts
            receipt_dir = "state/receipts"
            if os.path.exists(receipt_dir):
                for task in mapped_agent_tasks:
                    t_id = task.get("id")
                    r_path = os.path.join(receipt_dir, f"{t_id}.json")
                    if os.path.exists(r_path):
                        try:
                            os.remove(r_path)
                        except Exception:
                            pass

            dispatcher = Dispatcher(
                agents=agents,
                max_parallel_agents=config["company"]["max_parallel_agents"],
            )

            # Run tasks through dispatcher
            results = dispatcher.dispatch(
                mapped_agent_tasks,
                checkpoint_manager=checkpoint_manager,
                task_source=task_source,
                worker_id=worker_id,
                worker_mode=worker_mode
            )

            monitor = ReceiptMonitor(receipt_dir="state/receipts", poll_interval=3.0, timeout=300.0)
            final_results = []

            for result in results:
                status = result["status"]
                task_id = result["task_id"]
                project = result["project"]
                
                if status == "DELEGATED" and worker_mode == "antigravity":
                    conv_id = result.get("conversation_id")
                    print(f"\n🔍 Monitoring Antigravity task {task_id} completion (Conv: {conv_id})...")
                    
                    # Wait for completion receipt, triggering periodic heartbeats
                    receipt_info = monitor.wait_for_receipt(
                        task_id=task_id,
                        conversation_id=conv_id,
                        heartbeat_callback=lambda tid: task_source.heartbeat_task(tid, worker_id),
                        heartbeat_interval=15.0
                    )
                    
                    if receipt_info and receipt_info.get("success"):
                        receipt_status = receipt_info.get("status")
                        receipt_data = receipt_info.get("receipt_data", {})
                        log_transition("ANTIGRAVITY_COMPLETED", receipt_status, task_id, project, trace_ids.get(task_id), conversation_id=conv_id)
                        
                        if receipt_status == "DONE":
                            workspace_info = agents[project].workspace_info
                            print(f"🧐 Running independent verification on workspace for task {task_id}...")
                            log_transition("VERIFICATION_STARTED", "RUNNING", task_id, project, trace_ids.get(task_id), conversation_id=conv_id)
                            
                            original_agent_task = next((t for t in mapped_agent_tasks if t.get("id") == task_id), None)
                            verified, err_msg, verify_details = ResultVerifier.verify_result(
                                task=original_agent_task,
                                workspace_info=workspace_info,
                                receipt_data=receipt_data
                            )
                            
                            if verified:
                                log_transition("VERIFICATION_PASSED", "PASSED", task_id, project, trace_ids.get(task_id), conversation_id=conv_id)
                                result["status"] = "DONE"
                                summary_text = receipt_data.get("summary") or ""
                                task_type = original_agent_task.get("task_type") if original_agent_task else "code"
                                if task_type == "feature":
                                    print(f"🚀 Publishing verified changes to Git for task {task_id}...")
                                    log_transition("GIT_CHECKOUT", "CHECKOUT", task_id, project, trace_ids.get(task_id), conversation_id=conv_id, branch=f"task-{task_id}")
                                    try:
                                        from control.project_runtime import ProjectRuntimeManager
                                        runtime = ProjectRuntimeManager()
                                        metrics_manager.start_timer(trace_ids.get(task_id), "push")
                                        git_res = runtime.git.publish_feature_branch(workspace_info.get("workspace"), task_id)
                                        metrics_manager.stop_timer(trace_ids.get(task_id), "push")
                                        if git_res["success"]:
                                            log_transition("GIT_COMMIT", "COMMITTED", task_id, project, trace_ids.get(task_id), conversation_id=conv_id, branch=git_res['branch'], metadata={"commit_sha": git_res['commit_sha']})
                                            log_transition("GIT_PUSH", "PUSHED", task_id, project, trace_ids.get(task_id), conversation_id=conv_id, branch=git_res['branch'], metadata={"github_url": git_res['github_url']})
                                            summary_text += f"\n\n### 🛡️ VERIFIED FEATURE PROOF (V3.1)\n"
                                            summary_text += f"- **Current Branch**: `{git_res['branch']}`\n"
                                            summary_text += f"- **Commit Hash**: `{git_res['commit_sha']}`\n"
                                            summary_text += f"- **GitHub URL**: [{git_res['github_url']}]({git_res['github_url']})\n"
                                            logger.info(
                                                f"Git lifecycle completed successfully: branch={git_res['branch']}, commit={git_res['commit_sha']}, url={git_res['github_url']}",
                                                trace_id=trace_ids.get(task_id),
                                                task_id=task_id,
                                                project_id=project,
                                                conversation_id=conv_id,
                                                branch=git_res['branch'],
                                                step="PUSHING",
                                                status="DONE"
                                            )
                                            
                                            try:
                                                summary_text += ResultVerifier.generate_feature_proofs(workspace_info.get("workspace"))
                                            except Exception as e:
                                                logger.warning(f"Failed to generate feature proofs: {str(e)}", trace_id=trace_ids.get(task_id), task_id=task_id, project_id=project, step="VERIFYING")
                                        else:
                                            metrics_manager.record_git_result(trace_ids.get(task_id), False)
                                            metrics_manager.increment_counter("git_failures")
                                            logger.error(f"Git publish failed for task {task_id}: {git_res['error']}", error_code=error_codes.GIT_004, trace_id=trace_ids.get(task_id), task_id=task_id, project_id=project, step="PUSHING")
                                            result["status"] = "BLOCKED"
                                            summary_text = f"Git publish failed: {git_res['error']}"
                                    except Exception as git_err:
                                        metrics_manager.stop_timer(trace_ids.get(task_id), "push")
                                        metrics_manager.record_git_result(trace_ids.get(task_id), False)
                                        metrics_manager.increment_counter("git_failures")
                                        logger.error(f"Git publish exception: {str(git_err)}", error_code=error_codes.GIT_004, trace_id=trace_ids.get(task_id), task_id=task_id, project_id=project, step="PUSHING")
                                        result["status"] = "BLOCKED"
                                        summary_text = f"Git publish exception: {str(git_err)}"
                                        
                                result["summary"] = summary_text
                                result["validation_results"] = verify_details.get("validation_results", [])
                                checkpoint_manager.delete_checkpoint(task_id)
                                try:
                                    os.remove(receipt_info["path"])
                                except Exception:
                                    pass
                            else:
                                logger.error(f"Independent verification failed: {err_msg}", error_code=error_codes.VERIFIER_002, trace_id=trace_ids.get(task_id), task_id=task_id, project_id=project, step="VERIFYING")
                                log_transition("VERIFICATION_FAILED", "FAILED", task_id, project, trace_ids.get(task_id), conversation_id=conv_id, error_code=error_codes.VERIFIER_002, message=err_msg)
                                result["status"] = "BLOCKED"
                                result["summary"] = f"Independent Verification Failed: {err_msg}"
                        elif receipt_status == "BLOCKED":
                            result["status"] = "BLOCKED"
                            result["summary"] = receipt_data.get("summary", "Blocked by Antigravity worker.")
                        elif receipt_status == "FAILED":
                            result["status"] = "FAILED"
                            result["summary"] = receipt_data.get("summary", "Failed by Antigravity worker.")
                    else:
                        err = receipt_info.get("error", "Unknown error")
                        if receipt_info.get("timeout"):
                            result["status"] = "DELEGATED"
                            result["summary"] = f"Delegation Timeout: {err}"
                        else:
                            result["status"] = "FAILED"
                            result["summary"] = f"Malformed Receipt Error: {err}"
                            
                    # Always release the lock for this project/worker on completion
                    try:
                        from control.project_runtime import ProjectRuntimeManager
                        runtime = ProjectRuntimeManager()
                        runtime.sessions.release_lock(project, worker_id)
                    except Exception as lock_err:
                        logger.warning(f"Failed to release lock for {project}: {str(lock_err)}", error_code=error_codes.SESSION_002, trace_id=trace_ids.get(task_id), task_id=task_id, project_id=project, step="CLEANUP")
                            
                final_results.append(result)

            logger.info("Dispatching cycle completed - generating final status report", step="DISPATCH")

            for result in final_results:
                status = result["status"]
                project = result["project"]
                task_id = result["task_id"]
                summary = result.get("summary", "No summary provided.")
                actions_count = len(result.get("actions_executed", []))
                files_changed_count = len(result.get("files_changed", []))
                
                # Update task file status and archive evidence
                task_source.update_task_status(task_id, status, result)

                # Print console report
                validation_results = result.get("validation_results", [])
                if validation_results:
                    last_val = validation_results[-1]
                    val_cmd = last_val.get("command", "")
                    val_status = "PASS" if last_val.get("success") else "FAIL"
                    validation_info = f"{val_cmd} {val_status}"
                else:
                    validation_info = "None"

                t_id = trace_ids.get(task_id)
                if status == "DONE":
                    log_transition("TASK_COMPLETED", "DONE", task_id, project, t_id, message=summary)
                    logger.info(
                        f"Task completed successfully: {project} -> DONE. Summary: {summary} | Actions: {actions_count} | Files changed: {files_changed_count} | Validation: {validation_info}",
                        trace_id=t_id,
                        task_id=task_id,
                        project_id=project,
                        step="DISPATCH",
                        status="DONE"
                    )
                elif status == "DELEGATED":
                    logger.info(
                        f"Task delegated to runtime: {project} -> DELEGATED. Summary: {summary}",
                        trace_id=t_id,
                        task_id=task_id,
                        project_id=project,
                        step="DISPATCH",
                        status="DELEGATED"
                    )
                elif status == "BLOCKED":
                    log_transition("TASK_BLOCKED", "BLOCKED", task_id, project, t_id, error_code=error_codes.VERIFIER_002, message=summary)
                    logger.warning(
                        f"Task blocked during run: {project} -> BLOCKED. Reason: {summary}",
                        error_code=error_codes.VERIFIER_002,
                        trace_id=t_id,
                        task_id=task_id,
                        project_id=project,
                        step="DISPATCH",
                        status="BLOCKED"
                    )
                elif status == "FAILED":
                    log_transition("TASK_FAILED", "FAILED", task_id, project, t_id, error_code=error_codes.WORKER_002, message=summary)
                    logger.error(
                        f"Task failed during execution: {project} -> FAILED. Error: {summary}",
                        error_code=error_codes.WORKER_002,
                        trace_id=t_id,
                        task_id=task_id,
                        project_id=project,
                        step="DISPATCH",
                        status="FAILED"
                    )

        else:
            # Idle, show heartbeat debug log
            if not run_once:
                logger.debug("Worker idle - polling for tasks", step="POLL")

        if run_once:
            break

        # Idle polling wait
        for _ in range(int(poll_interval)):
            if shutdown:
                break
            time.sleep(1.0)


if __name__ == "__main__":
    main()