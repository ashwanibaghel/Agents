import sys
import yaml
import os
import time
import signal

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


def load_config():
    with open(
        "config/projects.yaml",
        "r",
        encoding="utf-8",
    ) as file:
        return yaml.safe_load(file)


def main():
    config = load_config()

    print("\n🤖 ASHWANI AGENT COMPANY")
    print("👑 BOSS: ASHWANI")

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
                print("☁️  Task source: Supabase")
        except Exception as _e:
            print(f"⚠️  Supabase config load failed: {_e} — falling back to local")

    if not use_supabase:
        task_source = LocalTaskSource(base_dir="tasks")
        print("📁 Task source: Local filesystem")

    checkpoint_manager = CheckpointManager(db_path="state/task_checkpoints.db")
    worker_id = "worker-main"
    worker_mode = config.get("company", {}).get("worker_mode", "scripted")

    # Polling config
    poll_interval = 15.0
    run_once = "--once" in sys.argv or not use_supabase

    # Setup signal handler for graceful shutdown
    shutdown = False

    def handle_shutdown(signum, frame):
        nonlocal shutdown
        print("\n👋 Shutdown signal received. Gracefully exiting loop after current cycle...")
        shutdown = True

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    print(f"🔄 Worker loop started (Mode: {'RUN_ONCE' if run_once else 'CONTINUOUS_POLLING'}). Press Ctrl+C to stop.")

    while not shutdown:
        # 1. Stale task recovery
        if use_supabase:
            try:
                recovered = task_source.recover_stale_tasks()
                if recovered > 0:
                    print(f"♻️  Recovered {recovered} stale task(s) back to inbox.")
            except Exception as e:
                print(f"⚠️  Stale task recovery failed: {e}")

        # 2. Fetch pending tasks from inbox
        try:
            pending_tasks = task_source.fetch_pending_tasks()
        except Exception as e:
            print(f"⚠️  Failed to fetch pending tasks: {e}")
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
                print("Inbox is empty. Generating default tasks in tasks/inbox/... ")
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
                claimed_tasks.append(task)
                mapped_agent_tasks.append(TaskParser.to_agent_format(task))

        # 4. Resume active tasks (delegated/claimed)
        if use_supabase:
            try:
                active_tasks = task_source.fetch_active_tasks(worker_id)
                for task in active_tasks:
                    if task.project in active_projects:
                        if not any(t.task_id == task.task_id for t in claimed_tasks):
                            claimed_tasks.append(task)
                            mapped_agent_tasks.append(TaskParser.to_agent_format(task))
            except Exception as e:
                print(f"⚠️  Failed to fetch active tasks: {e}")
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
                                claimed_tasks.append(task)
                                mapped_agent_tasks.append(TaskParser.to_agent_format(task))
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
                        
                        if receipt_status == "DONE":
                            workspace_info = agents[project].workspace_info
                            print(f"🧐 Running independent verification on workspace for task {task_id}...")
                            
                            original_agent_task = next((t for t in mapped_agent_tasks if t.get("id") == task_id), None)
                            verified, err_msg, verify_details = ResultVerifier.verify_result(
                                task=original_agent_task,
                                workspace_info=workspace_info,
                                receipt_data=receipt_data
                            )
                            
                            if verified:
                                result["status"] = "DONE"
                                result["summary"] = receipt_data.get("summary")
                                result["validation_results"] = verify_details.get("validation_results", [])
                                checkpoint_manager.delete_checkpoint(task_id)
                                try:
                                    os.remove(receipt_info["path"])
                                except Exception:
                                    pass
                            else:
                                print(f"❌ Independent verification failed: {err_msg}")
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
                            
                final_results.append(result)

            print("\n😎 DONE BOSS REPORT\n")

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

                print(f"{project} → {status}")
                if status == "DONE":
                    print(f"Summary: {summary}")
                    print(f"Actions: {actions_count}")
                    print(f"Files changed: {files_changed_count}")
                    print(f"Validation: {validation_info}\n")
                elif status == "DELEGATED":
                    conv_id = result.get("conversation_id", "Unknown")
                    print(f"Conversation ID: {conv_id}")
                    print(f"Summary: {summary}\n")
                elif status == "BLOCKED":
                    reason = result.get("reason") or summary or "Unknown reason"
                    print(f"Reason: {reason}\n")
                elif status == "FAILED":
                    error = result.get("error") or summary or "Unknown error"
                    print(f"Error: {error}\n")

        else:
            # Idle, show heartbeat dot
            if not run_once:
                sys.stdout.write(".")
                sys.stdout.flush()

        if run_once:
            break

        # Idle polling wait
        for _ in range(int(poll_interval)):
            if shutdown:
                break
            time.sleep(1.0)


if __name__ == "__main__":
    main()