import os
import tempfile
import shutil
import unittest
import sqlite3
import datetime
from unittest.mock import MagicMock, patch

from control.checkpoint_manager import CheckpointManager
from control.project_runtime import ProjectRuntimeManager, SessionManager, MemoryManager, GitManager, TaskManager

class TestProjectRuntime(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_checkpoints.db")
        
        # This will trigger table creation inside checkpoints manager
        self.checkpoint_manager = CheckpointManager(self.db_path)
        self.runtime = ProjectRuntimeManager(db_path=self.db_path)

    def tearDown(self):
        import gc
        gc.collect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_save_and_get(self):
        sessions = self.runtime.sessions
        sessions.save_session(
            project_id="test_proj",
            conversation_id="conv-123",
            workspace_path="/some/path",
            repository_url="https://github.com/user/repo",
            default_branch="main",
            current_branch="main",
            last_commit="abc1234",
            status="ACTIVE"
        )
        
        session = sessions.get_session("test_proj")
        self.assertIsNotNone(session)
        self.assertEqual(session["conversation_id"], "conv-123")
        self.assertEqual(session["workspace_path"], "/some/path")
        self.assertEqual(session["repository_url"], "https://github.com/user/repo")
        self.assertEqual(session["default_branch"], "main")
        self.assertEqual(session["current_branch"], "main")
        self.assertEqual(session["last_commit"], "abc1234")
        self.assertEqual(session["status"], "ACTIVE")

    def test_session_lock_acquire_and_release(self):
        sessions = self.runtime.sessions
        sessions.save_session("test_proj", "conv-123", status="ACTIVE")
        
        # Acquire lock first time
        acquired = sessions.acquire_lock("test_proj", "worker-A")
        self.assertTrue(acquired)
        
        session = sessions.get_session("test_proj")
        self.assertEqual(session["locked_by"], "worker-A")
        self.assertIsNotNone(session["locked_at"])
        
        # Try to acquire by another worker - should fail
        acquired2 = sessions.acquire_lock("test_proj", "worker-B")
        self.assertFalse(acquired2)
        
        # Release lock by incorrect worker - should return False
        released = sessions.release_lock("test_proj", "worker-B")
        self.assertFalse(released)
        
        # Release lock by correct worker - should succeed
        released2 = sessions.release_lock("test_proj", "worker-A")
        self.assertTrue(released2)
        
        session = sessions.get_session("test_proj")
        self.assertNull = self.assertIsNone(session["locked_by"])

    def test_session_lock_lease_expiration(self):
        sessions = self.runtime.sessions
        sessions.save_session("test_proj", "conv-123", status="ACTIVE")
        
        # Lock acquired 15 minutes ago
        past_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=15)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE project_sessions 
                SET locked_by = 'worker-A', locked_at = ?
                WHERE project_id = 'test_proj'
            """, (past_time,))
            conn.commit()
            
        # Try to acquire by worker-B - should succeed since 15 mins > 10 mins lease limit
        acquired = sessions.acquire_lock("test_proj", "worker-B")
        self.assertTrue(acquired)
        
        session = sessions.get_session("test_proj")
        self.assertEqual(session["locked_by"], "worker-B")

    def test_session_status_transitions(self):
        sessions = self.runtime.sessions
        sessions.save_session("test_proj", "conv-123", status="ACTIVE")
        
        class MockClient:
            def __init__(self, success=True, error=None, last_activity=None):
                self.success = success
                self.error = error
                self.last_activity = last_activity

            def get_conversation_metadata(self, conversation_id):
                if not self.success:
                    return {"success": False, "error": self.error}
                return {
                    "success": True,
                    "response": {
                        "conversationMetadata": {
                            "lastActivityTime": self.last_activity
                        }
                    }
                }
                
        # 1. Active conversation
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        client = MockClient(success=True, last_activity=now_str)
        status = sessions.check_session_status("test_proj", client)
        self.assertEqual(status, "ACTIVE")
        
        # 2. Idle conversation (older than 5 minutes)
        past_str = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
        client = MockClient(success=True, last_activity=past_str)
        status = sessions.check_session_status("test_proj", client)
        self.assertEqual(status, "IDLE")
        
        # 3. Expired conversation (Not found error)
        client = MockClient(success=False, error="Conversation not found")
        status = sessions.check_session_status("test_proj", client)
        self.assertEqual(status, "EXPIRED")
        
        # 4. Broken conversation (Other API errors)
        client = MockClient(success=False, error="Authentication error or internal server issue")
        status = sessions.check_session_status("test_proj", client)
        self.assertEqual(status, "BROKEN")

    def test_memory_management(self):
        memories = self.runtime.memories
        
        memories.save_memory("test_proj", {
            "architecture": "Clean Architecture / MVC",
            "coding_style": "PEP8 rules",
            "owner_instructions": "Never push to main directly"
        })
        
        mem = memories.get_memory("test_proj")
        self.assertIsNotNone(mem)
        self.assertEqual(mem["architecture"], "Clean Architecture / MVC")
        self.assertEqual(mem["coding_style"], "PEP8 rules")
        self.assertEqual(mem["owner_instructions"], "Never push to main directly")
        self.assertIsNone(mem["framework"])
        
        # Test prompt generation
        prompt = memories.get_memory_prompt("test_proj")
        self.assertIn("ARCHITECTURE:\nClean Architecture / MVC", prompt)
        self.assertIn("CODING STYLE:\nPEP8 rules", prompt)
        self.assertIn("OWNER INSTRUCTIONS:\nNever push to main directly", prompt)
        self.assertNotIn("FRAMEWORK", prompt)

    @patch("subprocess.run")
    def test_git_dirty_check_blocks_checkout(self, mock_run):
        git = self.runtime.git
        
        # Mock git status --short showing dirty workspace
        mock_run.return_value = MagicMock(returncode=0, stdout=" M file.py\n")
        
        clean = git.is_clean("/dummy/workspace")
        self.assertFalse(clean)
        
        res = git.prepare_feature_branch("/dummy/workspace", "OI-123")
        self.assertFalse(res["success"])
        self.assertIn("dirty", res["error"])
        
    @patch("subprocess.run")
    def test_git_clean_workflow(self, mock_run):
        git = self.runtime.git
        
        # Mock clean status
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        
        clean = git.is_clean("/dummy/workspace")
        self.assertTrue(clean)
        
        # Test prepare_feature_branch
        res = git.prepare_feature_branch("/dummy/workspace", "OI-123")
        self.assertTrue(res["success"])
        self.assertEqual(res["branch"], "task-OI-123")

    def test_task_context_prompt(self):
        task_mgr = self.runtime.tasks
        task = {
            "id": "OI-999",
            "project": "oi_labs",
            "title": "Fix index page loading latency",
            "acceptance_criteria": [
                "Page load time under 100ms",
                "No console errors"
            ]
        }
        prompt = task_mgr.inject_task_context(task)
        self.assertIn("Task ID: OI-999", prompt)
        self.assertIn("Project: oi_labs", prompt)
        self.assertIn("Page load time under 100ms", prompt)
