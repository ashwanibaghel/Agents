"""
cloud_worker.py — Ashwani Agent Company Cloud Worker
=====================================================
Platform-independent polling worker that:
  - Polls Supabase for inbox tasks every POLL_INTERVAL seconds
  - Recovers stale tasks whose heartbeats have expired
  - Claims tasks and executes them using ScriptedBrain (pure Python, Linux-compatible)
  - Writes results directly to Supabase (no local receipt files required)
  - Designed to run on GitHub Actions, Render, Railway, or any Linux container

Environment variables required:
  SUPABASE_URL            — e.g. https://xxx.supabase.co
  SUPABASE_SERVICE_KEY    — service_role JWT (never commit this)
  GITHUB_TOKEN            — PAT with repo read access for private repos
  WORKER_ID               — unique worker identity (default: cloud-worker-1)
  POLL_INTERVAL           — seconds between Supabase polls (default: 30)
  MAX_TASK_RUNTIME        — max seconds per task (default: 480)
  MAX_ITERATIONS          — max brain iterations per task (default: 20)
  RUN_ONCE                — if "true", run one poll cycle then exit (for GitHub Actions)

Usage:
  python workers/cloud_worker.py
"""

import os
import sys
import time
import signal
import datetime
import traceback
import logging

# ── Make sure project root is on the import path ─────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("cloud_worker")


# ── Config from environment ───────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
WORKER_ID = os.environ.get("WORKER_ID", "cloud-worker-1")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "30"))
MAX_TASK_RUNTIME = int(os.environ.get("MAX_TASK_RUNTIME", "480"))
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "20"))
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() == "true"

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_SHUTDOWN = False

def _handle_signal(signum, frame):
    global _SHUTDOWN
    log.info("Shutdown signal received — finishing current task then exiting.")
    _SHUTDOWN = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── GitHub token injection for private repos ──────────────────────────────────
def configure_git_credentials():
    """
    Inject GITHUB_TOKEN into git credential store so WorkspaceManager
    can clone private repos without prompting.
    """
    if not GITHUB_TOKEN:
        log.warning("GITHUB_TOKEN not set — private repositories may fail to clone.")
        return
    # Write a .netrc-style credential or configure git to use token via URL rewrite
    # Use git config to store credentials in memory for this process lifetime
    os.system(f'git config --global credential.helper store')
    # Write to ~/.git-credentials
    cred_path = os.path.expanduser("~/.git-credentials")
    cred_line = f"https://x-token:{GITHUB_TOKEN}@github.com\n"
    existing = ""
    if os.path.exists(cred_path):
        with open(cred_path, "r") as f:
            existing = f.read()
    if "github.com" not in existing:
        with open(cred_path, "a") as f:
            f.write(cred_line)
        os.chmod(cred_path, 0o600)
    log.info("Git credentials configured for github.com.")


# ── Result writer — writes result directly to Supabase ────────────────────────
def write_result_to_supabase(task_source, task_id: str, result: dict):
    """
    Update Supabase task row with final execution result.
    Maps ScriptedBrain result dict → Supabase columns.
    """
    status_map = {
        "DONE":    "done",
        "BLOCKED": "blocked",
        "FAILED":  "failed",
    }
    status = status_map.get(result.get("status", "FAILED").upper(), "failed")
    
    summary = result.get("summary", "No summary provided.")
    files_changed = result.get("files_changed", [])
    validation_results = result.get("validation_results", [])

    task_source.update_task_status(
        task_id=task_id,
        status=status,
        evidence={
            "summary": summary,
            "files_changed": files_changed,
            "validation_results": validation_results,
            "worker_id": WORKER_ID,
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    log.info(f"Task {task_id} → Supabase status: {status}")


# ── Single poll cycle ─────────────────────────────────────────────────────────
def run_poll_cycle(task_source, workspace_manager, dispatcher, agents):
    """
    1. Recover any stale tasks.
    2. Fetch inbox tasks.
    3. Claim + execute one task (scripted mode).
    4. Write result to Supabase.
    """
    # Step 1: Stale task recovery
    recovered = task_source.recover_stale_tasks()
    if recovered:
        log.info(f"Recovered {recovered} stale task(s) → back to inbox.")

    # Step 2: Fetch pending tasks
    pending = task_source.fetch_pending_tasks()
    if not pending:
        log.info("No pending tasks in Supabase inbox.")
        return

    log.info(f"Found {len(pending)} pending task(s).")

    for raw_task in pending:
        if _SHUTDOWN:
            break

        task_id = raw_task.get("task_id") or raw_task.get("id")
        project = raw_task.get("project")
        log.info(f"Attempting to claim task {task_id} ({project})...")

        # Step 3: Claim task
        claimed = task_source.claim_task(task_id, WORKER_ID)
        if not claimed:
            log.info(f"Task {task_id} already claimed by another worker — skipping.")
            continue

        log.info(f"✅ Claimed task {task_id}. Starting execution...")
        task_source.update_task_status(task_id, "working", {})

        try:
            # Step 4: Prepare workspace
            workspace_info = workspace_manager.prepare_workspace(project)
            log.info(f"Workspace ready: {workspace_info['workspace']} @ {workspace_info['branch']}")

            # Step 5: Find agent
            agent = agents.get(project)
            if not agent:
                raise ValueError(f"No agent configured for project: {project}")

            agent.set_workspace(workspace_info)

            # Step 6: Convert raw task to task dict (BaseAgent format)
            task_dict = {
                "id": task_id,
                "task_id": task_id,
                "project": project,
                "task_type": raw_task.get("task_type", "audit"),
                "objective": raw_task.get("objective", ""),
                "context": raw_task.get("context", ""),
                "acceptance_criteria": raw_task.get("acceptance_criteria") or [],
                "constraints": raw_task.get("constraints") or [],
                "validation_commands": raw_task.get("validation_commands") or ["git status --short"],
                "autonomy_level": raw_task.get("autonomy_level", 2),
            }

            # Step 7: Execute with ScriptedBrain
            from brains.scripted_brain import ScriptedBrain
            agent.brain = ScriptedBrain()

            result = agent.run_task(
                task=task_dict,
                max_iterations=MAX_ITERATIONS,
                max_runtime_seconds=MAX_TASK_RUNTIME,
                task_source=task_source,
                worker_id=WORKER_ID,
            )

            log.info(f"Task {task_id} execution result: {result.get('status')}")

        except Exception as exc:
            log.error(f"Task {task_id} execution failed: {exc}")
            traceback.print_exc()
            result = {
                "task_id": task_id,
                "project": project,
                "status": "FAILED",
                "summary": f"Worker execution error: {str(exc)}",
                "files_changed": [],
                "validation_results": [],
            }

        # Step 8: Write result to Supabase
        write_result_to_supabase(task_source, task_id, result)


# ── Main entry point ──────────────────────────────────────────────────────────
def main():
    # Validate required config
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. Exiting.")
        sys.exit(1)

    log.info("=" * 60)
    log.info("🤖 ASHWANI AGENT COMPANY — CLOUD WORKER")
    log.info(f"   Worker ID : {WORKER_ID}")
    log.info(f"   Supabase  : {SUPABASE_URL}")
    log.info(f"   Mode      : {'RUN_ONCE (GitHub Actions)' if RUN_ONCE else 'CONTINUOUS'}")
    log.info(f"   Interval  : {POLL_INTERVAL}s")
    log.info("=" * 60)

    # Configure git credentials for private repos
    configure_git_credentials()

    # Import project components
    from control.supabase_task_source import SupabaseTaskSource
    from control.workspace_manager import WorkspaceManager
    from control.dispatcher import Dispatcher
    from agents.oi_agent import OIAgent
    from agents.dkffj_agent import DKFFJAgent

    task_source = SupabaseTaskSource(
        supabase_url=SUPABASE_URL,
        service_key=SUPABASE_SERVICE_KEY,
    )

    workspace_manager = WorkspaceManager()

    agents = {
        "oi_labs": OIAgent(),
        "dkffj":   DKFFJAgent(),
    }

    dispatcher = Dispatcher(agents=agents, workspace_manager=workspace_manager)

    if RUN_ONCE:
        log.info("RUN_ONCE mode — executing one poll cycle then exiting.")
        run_poll_cycle(task_source, workspace_manager, dispatcher, agents)
        log.info("RUN_ONCE complete.")
        return

    # Continuous polling loop
    log.info("Starting continuous polling loop...")
    while not _SHUTDOWN:
        try:
            run_poll_cycle(task_source, workspace_manager, dispatcher, agents)
        except Exception as exc:
            log.error(f"Poll cycle error: {exc}")
            traceback.print_exc()

        if _SHUTDOWN:
            break
        log.info(f"Sleeping {POLL_INTERVAL}s before next poll...")
        time.sleep(POLL_INTERVAL)

    log.info("Cloud worker shut down cleanly.")


if __name__ == "__main__":
    main()
