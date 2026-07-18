import os
import sys
import sqlite3
import datetime
import shutil
import tempfile
import yaml
import codecs

# Force UTF-8 output on Windows to avoid UnicodeEncodeError for emojis
sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from control.project_runtime import ProjectRuntimeManager
from control.checkpoint_manager import CheckpointManager
from workers.antigravity_worker import AntigravityWorker

class MockAntigravityClient:
    def __init__(self):
        self.conv_counter = 0
        self.conversations = {}
        self.history = []

    def get_conversation_metadata(self, conversation_id):
        self.history.append(("get_conversation_metadata", conversation_id))
        if conversation_id in self.conversations:
            return {
                "success": True,
                "response": {
                    "conversationMetadata": {
                        "metadata": {
                            "lastActivityTime": self.conversations[conversation_id]["last_activity"]
                        }
                    }
                }
            }
        return {"success": False, "error": "Conversation not found"}

    def new_conversation(self, prompt, model=None):
        self.conv_counter += 1
        conv_id = f"mock-conv-{self.conv_counter}"
        self.conversations[conv_id] = {
            "last_activity": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        self.history.append(("new_conversation", conv_id))
        return {
            "success": True,
            "response": {"conversationId": conv_id}
        }

    def send_message(self, conversation_id, content):
        self.history.append(("send_message", conversation_id))
        if conversation_id in self.conversations:
            self.conversations[conversation_id]["last_activity"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return {
                "success": True,
                "response": {"conversationId": conversation_id}
            }
        return {"success": False, "error": "Conversation not found"}

def main():
    print("=" * 60)
    print("DEMONSTRATING E2E PERSISTENT SESSION LIFECYCLE (V3.1)")
    print("=" * 60)

    # 1. Create a temp directory for tests
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_checkpoints.db")
    workspace_dir = os.path.join(temp_dir, "workspaces", "dkffj")
    os.makedirs(workspace_dir, exist_ok=True)

    # Initialize a dummy git repo in workspace so git prepares correctly
    import subprocess
    subprocess.run(["git", "init"], cwd=workspace_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=workspace_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "demo@demo.com"], cwd=workspace_dir, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial commit"], cwd=workspace_dir, capture_output=True)
    # Add dummy remote origin to avoid remote not found error
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/dummy/repo.git"], cwd=workspace_dir, capture_output=True)

    # Initialize managers
    runtime = ProjectRuntimeManager(db_path=db_path)
    checkpoint_manager = CheckpointManager(db_path=db_path)
    client = MockAntigravityClient()
    
    worker = AntigravityWorker(
        checkpoint_manager=checkpoint_manager,
        task_source=None,
        client=client
    )
    # Inject temp runtime
    worker.runtime = runtime

    project = "dkffj"
    workspace_info = {"workspace": workspace_dir}

    # -------------------------------------------------------------
    # TASK A: Fresh dispatch (New conversation expected)
    # -------------------------------------------------------------
    print("\n--- [1] DISPATCHING TASK A ---")
    task_a = {"id": "TASK-A", "project": project, "task_type": "feature", "title": "Implement Feature A"}
    
    res_a = worker.dispatch_task(task_a, workspace_info, "worker-1")
    conv_a = res_a.get("conversation_id")
    print(f"Task A Result Status: {res_a['status']}")
    print(f"Conversation Created: {conv_a}")

    # Inspect project_sessions and checkpoints
    session_row = runtime.sessions.get_session(project)
    checkpoint_row = checkpoint_manager.load_checkpoint("TASK-A")
    print(f"Database session conv_id: {session_row['conversation_id']}")
    print(f"Database checkpoint conv_id: {checkpoint_row.get('conversation_id')}")

    # -------------------------------------------------------------
    # TASK B: Subsequent dispatch (Same conversation reused)
    # -------------------------------------------------------------
    print("\n--- [2] DISPATCHING TASK B ---")
    task_b = {"id": "TASK-B", "project": project, "task_type": "feature", "title": "Implement Feature B"}
    
    # Release previous lock for the demo
    runtime.sessions.release_lock(project, "worker-1")

    res_b = worker.dispatch_task(task_b, workspace_info, "worker-1")
    conv_b = res_b.get("conversation_id")
    print(f"Task B Result Status: {res_b['status']}")
    print(f"Conversation Used: {conv_b}")
    print(f"Is Same Conversation: {conv_a == conv_b}")

    # Inspect project_sessions and checkpoints
    session_row = runtime.sessions.get_session(project)
    checkpoint_row = checkpoint_manager.load_checkpoint("TASK-B")
    print(f"Database session conv_id: {session_row['conversation_id']}")
    print(f"Database checkpoint conv_id: {checkpoint_row.get('conversation_id')}")

    # -------------------------------------------------------------
    # TASK C: Forced Expiry (New conversation automatically created)
    # -------------------------------------------------------------
    print("\n--- [3] DISPATCHING TASK C AFTER FORCED EXPIRY (25 Hours Stale) ---")
    # Release previous lock
    runtime.sessions.release_lock(project, "worker-1")

    # Manually backdate the conversation activity in mock client
    stale_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=25)).isoformat()
    client.conversations[conv_b]["last_activity"] = stale_time
    print(f"Forced last activity for {conv_b} to: {stale_time}")

    task_c = {"id": "TASK-C", "project": project, "task_type": "feature", "title": "Implement Feature C"}
    res_c = worker.dispatch_task(task_c, workspace_info, "worker-1")
    conv_c = res_c.get("conversation_id")
    print(f"Task C Result Status: {res_c['status']}")
    print(f"Conversation Used: {conv_c}")
    print(f"Is Same Conversation: {conv_b == conv_c}")

    # Inspect project_sessions and checkpoints
    session_row = runtime.sessions.get_session(project)
    checkpoint_row = checkpoint_manager.load_checkpoint("TASK-C")
    print(f"Database session conv_id: {session_row['conversation_id']}")
    print(f"Database checkpoint conv_id: {checkpoint_row.get('conversation_id')}")

    # Clean up
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

    print("\n" + "=" * 60)
    print("DEMONSTRATION COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
