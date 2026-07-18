import os
import shutil
import tempfile
import unittest
import sqlite3

from workers.antigravity_client import AntigravityClient
from workers.antigravity_worker import AntigravityWorker
from control.checkpoint_manager import CheckpointManager
from control.task_source import LocalTaskSource
from control.task_models import Task


class MockAntigravityClient:
    def __init__(self, exists_val=True, mock_response=None):
        self.exists_val = exists_val
        self.mock_response = mock_response or {
            "success": True,
            "output": '{"response": {"conversationId": "mock-conv-123"}}',
            "error": None,
            "response": {"conversationId": "mock-conv-123"}
        }
        self.history = []

    def exists(self):
        return self.exists_val

    def new_conversation(self, prompt, model=None):
        self.history.append(("new_conversation", prompt, model))
        return self.mock_response

    def send_message(self, recipient_id, content):
        self.history.append(("send_message", recipient_id, content))
        return self.mock_response

    def get_conversation_metadata(self, conversation_id):
        self.history.append(("get_conversation_metadata", conversation_id))
        return self.mock_response


class TestAntigravityBridge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "checkpoints.db")
        self.tasks_base_dir = os.path.join(self.temp_dir, "tasks")
        
        self.checkpoint_manager = CheckpointManager(self.db_path)
        self.task_source = LocalTaskSource(self.tasks_base_dir)
        
        self.mock_client = MockAntigravityClient()
        self.worker = AntigravityWorker(
            checkpoint_manager=self.checkpoint_manager,
            task_source=self.task_source,
            client=self.mock_client
        )

    def tearDown(self):
        try:
            # Clean read-only files on Windows
            def remove_readonly(func, p, excinfo):
                import stat
                try:
                    os.chmod(p, stat.S_IWRITE)
                    func(p)
                except Exception:
                    pass
            shutil.rmtree(self.temp_dir, onerror=remove_readonly)
        except Exception:
            pass

    def test_missing_agentapi(self):
        client = AntigravityClient(bat_path="nonexistent_file.bat")
        self.assertFalse(client.exists())
        
        res = client.new_conversation("Test prompt")
        self.assertFalse(res["success"])
        self.assertIn("not found", res["error"])

    def test_project_workspace_mismatch_rejection(self):
        # Project oi_labs expects workspaces/oi-labs suffix
        with self.assertRaises(ValueError):
            self.worker.validate_isolation("oi_labs", "E:\\some_other_folder\\workspaces\\dkffj")
            
        with self.assertRaises(ValueError):
            self.worker.validate_isolation("dkffj", "E:\\some_other_folder\\workspaces\\oi-labs")
            
        # Valid path formats
        self.worker.validate_isolation("oi_labs", "E:\\some_other_folder\\workspaces\\oi-labs")
        self.worker.validate_isolation("dkffj", "E:\\some_other_folder\\workspaces\\dkffj")

    def test_prompt_contains_complete_task_schema_and_constraints(self):
        task = {
            "id": "OI-BRIDGE-999",
            "project": "oi_labs",
            "task_type": "audit",
            "title": "Perform manual audit",
            "context": "Context description",
            "acceptance_criteria": ["Check code index"],
            "constraints": ["No edits"],
            "validation_commands": ["git status"],
            "autonomy_level": 1
        }
        prompt = self.worker.build_prompt(task, "E:/workspaces/oi-labs")
        
        # Verify schema elements
        self.assertIn("TASK_ID: OI-BRIDGE-999", prompt)
        self.assertIn("PROJECT: oi_labs", prompt)
        self.assertIn("TASK_TYPE: audit", prompt)
        self.assertIn("OBJECTIVE: Perform manual audit", prompt)
        self.assertIn("CONTEXT: Context description", prompt)
        self.assertIn("ACCEPTANCE_CRITERIA:\n- Check code index", prompt)
        self.assertIn("CONSTRAINTS:\n- No edits", prompt)
        self.assertIn("VALIDATION_COMMANDS:\n- git status", prompt)
        self.assertIn("AUTONOMY_LEVEL: 1", prompt)
        self.assertIn("EXACT WORKSPACE PATH: E:/workspaces/oi-labs", prompt)
        
        # Verify operational constraint instructions
        self.assertIn("Inspect the existing repository first", prompt)
        self.assertIn("Work ONLY inside the exact assigned workspace directory", prompt)
        self.assertIn("dedicated task branch", prompt)
        self.assertIn("Never modify another project workspace", prompt) # matches "Never modify any files or directories outside this assigned project workspace"
        self.assertIn("Never read, print, or expose .env", prompt)

    def test_new_conversation_invocation_and_defensive_parsing(self):
        task = {
            "id": "OI-BRIDGE-999",
            "project": "oi_labs",
            "task_type": "audit",
            "title": "Perform manual audit",
            "autonomy_level": 1
        }
        workspace_info = {"workspace": "E:\\workspaces\\oi-labs"}
        
        # Mock client to return nested JSON response
        self.mock_client.mock_response = {
            "success": True,
            "response": {
                "conversationMetadata": {
                  "metadata": {
                    "conversationId": "defensive-conv-id-789"
                  }
                }
            }
        }
        
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        self.assertEqual(res["status"], "DELEGATED")
        self.assertEqual(res["conversation_id"], "defensive-conv-id-789")

    def test_conversation_id_persistence_and_delegation_state(self):
        task = {
            "id": "OI-BRIDGE-999",
            "project": "oi_labs",
            "task_type": "audit",
            "title": "Perform manual audit",
            "autonomy_level": 1
        }
        workspace_info = {"workspace": "E:\\workspaces\\oi-labs"}
        self.mock_client.mock_response = {
            "success": True,
            "response": {"conversationId": "persisted-conv-123"}
        }
        
        # Dispatch the task
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        self.assertEqual(res["status"], "DELEGATED")
        self.assertEqual(res["conversation_id"], "persisted-conv-123")
        
        # Check SQLite db delegation columns
        checkpoint = self.checkpoint_manager.load_checkpoint("OI-BRIDGE-999")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["provider"], "antigravity")
        self.assertEqual(checkpoint["conversation_id"], "persisted-conv-123")
        self.assertEqual(checkpoint["delegation_status"], "delegated")
        self.assertEqual(checkpoint["worker_model"], "flash") # autonomy level 1 uses flash

    def test_task_remains_delegated_after_dispatch_no_fake_done(self):
        task = {
            "id": "OI-BRIDGE-999",
            "project": "oi_labs",
            "task_type": "audit",
            "title": "Perform manual audit",
            "autonomy_level": 1
        }
        # Prepare inbox file
        import yaml
        task_file = os.path.join(self.task_source.inbox_dir, "OI-BRIDGE-999.yaml")
        with open(task_file, "w") as f:
            yaml.dump(task, f)
            
        self.task_source.claim_task("OI-BRIDGE-999", "worker-1")
        
        # Dispatch task
        workspace_info = {"workspace": "E:\\workspaces\\oi-labs"}
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        self.assertEqual(res["status"], "DELEGATED")
        
        # Verify the file stays in working/ directory with status = "delegated"
        working_file = os.path.join(self.task_source.working_dir, "OI-BRIDGE-999.yaml")
        self.assertTrue(os.path.exists(working_file))
        with open(working_file, "r") as f:
            data = yaml.safe_load(f)
        self.assertEqual(data["status"], "delegated")

    def test_restart_restores_delegation_state(self):
        # Pretend the task was already delegated before restart
        task_id = "OI-BRIDGE-RESTART"
        self.checkpoint_manager.save_delegation_state(
            task_id=task_id,
            project="oi_labs",
            status="delegated",
            worker_id="worker-1",
            provider="antigravity",
            conversation_id="saved-conv-456",
            delegated_at="timestamp",
            last_followup_at="timestamp",
            worker_model="pro",
            delegation_status="delegated"
        )
        
        # Simulate worker picking up the task again after restart
        task = {
            "id": task_id,
            "project": "oi_labs",
            "title": "Audit dataset",
            "task_type": "audit"
        }
        workspace_info = {"workspace": "E:\\workspaces\\oi-labs"}
        
        # Should detect the saved conversation in SQLite and skip new conversation dispatch
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        self.assertEqual(res["status"], "DELEGATED")
        self.assertEqual(res["conversation_id"], "saved-conv-456")
        
        # Verify only a metadata check was made (no new_conversation or send_message calls)
        meta_calls = [h for h in self.mock_client.history if h[0] == "get_conversation_metadata"]
        new_conv_calls = [h for h in self.mock_client.history if h[0] == "new_conversation"]
        self.assertEqual(len(meta_calls), 1, "Expected exactly one metadata validation call")
        self.assertEqual(len(new_conv_calls), 0, "Expected no new_conversation calls when resuming")


if __name__ == "__main__":
    unittest.main()
