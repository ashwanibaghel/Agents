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
        
        # 3. Missing conversation (Not found error)
        client = MockClient(success=False, error="Conversation not found")
        status = sessions.check_session_status("test_proj", client)
        self.assertEqual(status, "MISSING")
        
        # 4. Broken conversation (Other API errors)
        client = MockClient(success=False, error="Authentication error or internal server issue")
        status = sessions.check_session_status("test_proj", client)
        self.assertEqual(status, "BROKEN")

    def test_persistent_conversation_design_changes(self):
        sessions = self.runtime.sessions
        
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
        
        # 1. 7 days inactivity -> IDLE status (resumable)
        past_7_days = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat()
        client = MockClient(success=True, last_activity=past_7_days)
        sessions.save_session("proj_7d", "conv-7d", status="ACTIVE")
        status = sessions.check_session_status("proj_7d", client)
        self.assertEqual(status, "IDLE")
        
        # 2. 30 days inactivity -> IDLE status (resumable)
        past_30_days = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()
        client = MockClient(success=True, last_activity=past_30_days)
        sessions.save_session("proj_30d", "conv-30d", status="ACTIVE")
        status = sessions.check_session_status("proj_30d", client)
        self.assertEqual(status, "IDLE")
        
        # 3. SSL failure -> BROKEN status
        client = MockClient(success=False, error="SSL WRONG_VERSION_NUMBER")
        sessions.save_session("proj_ssl", "conv-ssl", status="ACTIVE")
        status = sessions.check_session_status("proj_ssl", client)
        self.assertEqual(status, "BROKEN")
        
        # 4. DNS failure -> BROKEN status
        client = MockClient(success=False, error="DNS resolution failed")
        sessions.save_session("proj_dns", "conv-dns", status="ACTIVE")
        status = sessions.check_session_status("proj_dns", client)
        self.assertEqual(status, "BROKEN")
        
        # 5. Timeout -> BROKEN status
        client = MockClient(success=False, error="Read timed out")
        sessions.save_session("proj_timeout", "conv-timeout", status="ACTIVE")
        status = sessions.check_session_status("proj_timeout", client)
        self.assertEqual(status, "BROKEN")
        
        # 6. 502 -> BROKEN status
        client = MockClient(success=False, error="HTTP Error 502: Bad Gateway")
        sessions.save_session("proj_502", "conv-502", status="ACTIVE")
        status = sessions.check_session_status("proj_502", client)
        self.assertEqual(status, "BROKEN")
        
        # 7. Conversation deleted -> MISSING status
        client = MockClient(success=False, error="conversation deleted")
        sessions.save_session("proj_del", "conv-del", status="ACTIVE")
        status = sessions.check_session_status("proj_del", client)
        self.assertEqual(status, "MISSING")
        
        # 8. Conversation not found -> MISSING status
        client = MockClient(success=False, error="could not find conversation")
        sessions.save_session("proj_nf", "conv-nf", status="ACTIVE")
        status = sessions.check_session_status("proj_nf", client)
        self.assertEqual(status, "MISSING")

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


class TestAntigravityWorkerResumption(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_checkpoints.db")
        
        # Init SQLite tables
        from control.checkpoint_manager import CheckpointManager
        self.checkpoint_manager = CheckpointManager(self.db_path)
        
        from control.project_runtime import ProjectRuntimeManager
        self.runtime = ProjectRuntimeManager(db_path=self.db_path)
        
        from workers.antigravity_worker import AntigravityWorker
        self.worker = AntigravityWorker(
            checkpoint_manager=self.checkpoint_manager,
            task_source=MagicMock()
        )
        self.worker.runtime = self.runtime
        self.worker.client = MagicMock()
        self.worker.validate_isolation = MagicMock()

    def tearDown(self):
        import gc
        gc.collect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_broken_does_not_create_new_conversation_and_preserves_id_and_increments_retry(self):
        # Seed an active session with an existing conversation_id
        self.runtime.sessions.save_session(
            project_id="test_proj",
            conversation_id="conv-123",
            status="ACTIVE"
        )
        
        # Mock metadata check to return a BROKEN error (e.g. temporary timeout)
        self.worker.client.get_conversation_metadata.return_value = {
            "success": False,
            "error": "Temporary timeout error"
        }
        
        # Dispatch a task
        task = {"id": "task-1", "project": "test_proj", "task_type": "audit"}
        workspace_info = {"workspace": "/dummy"}
        
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        
        # Verify BROKEN retry response returned
        self.assertEqual(res["status"], "DELEGATED")
        self.assertIn("Temporary infrastructure failure", res["summary"])
        
        # Verify next_retry_at is set in project_session and retry_count is incremented to 1
        session = self.runtime.sessions.get_session("test_proj")
        self.assertEqual(session["status"], "BROKEN")
        self.assertEqual(session["retry_count"], 1)
        self.assertEqual(session["conversation_id"], "conv-123")  # preserved!
        self.assertIsNotNone(session["next_retry_at"])
        
        # Verify new_conversation was NEVER called
        self.worker.client.new_conversation.assert_not_called()

    def test_missing_creates_new_conversation(self):
        # Seed a session that is missing / expired on server
        self.runtime.sessions.save_session(
            project_id="test_proj",
            conversation_id="conv-deleted",
            status="ACTIVE"
        )
        
        # Mock metadata check to return MISSING error (e.g. Conversation not found)
        self.worker.client.get_conversation_metadata.return_value = {
            "success": False,
            "error": "Conversation not found"
        }
        
        # Mock new_conversation success
        self.worker.client.new_conversation.return_value = {
            "success": True,
            "response": {"conversationId": "conv-new-789"}
        }
        
        task = {"id": "task-2", "project": "test_proj", "task_type": "audit"}
        workspace_info = {"workspace": "/dummy"}
        
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        
        # Verify new conversation was created
        self.assertEqual(res["status"], "DELEGATED")
        self.assertEqual(res["conversation_id"], "conv-new-789")
        self.worker.client.new_conversation.assert_called_once()
        
        # Verify SQLite saved the new conversation_id and reset retry count
        session = self.runtime.sessions.get_session("test_proj")
        self.assertEqual(session["conversation_id"], "conv-new-789")
        self.assertEqual(session["retry_count"], 0)

    def test_active_resumes_existing_conversation(self):
        self.runtime.sessions.save_session(
            project_id="test_proj",
            conversation_id="conv-active",
            status="ACTIVE"
        )
        
        # Mock metadata success (active conversation)
        self.worker.client.get_conversation_metadata.return_value = {
            "success": True,
            "response": {"metadata": {"lastActivityTime": datetime.datetime.now(datetime.timezone.utc).isoformat()}}
        }
        
        # Mock send_message success
        self.worker.client.send_message.return_value = {
            "success": True,
            "response": {"conversationId": "conv-active"}
        }
        
        task = {"id": "task-3", "project": "test_proj", "task_type": "audit"}
        workspace_info = {"workspace": "/dummy"}
        
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        
        self.assertEqual(res["status"], "DELEGATED")
        self.assertEqual(res["conversation_id"], "conv-active")
        self.worker.client.send_message.assert_called_once()
        self.worker.client.new_conversation.assert_not_called()

    def test_idle_resumes_existing_conversation(self):
        # Mock idle session
        self.runtime.sessions.save_session(
            project_id="test_proj",
            conversation_id="conv-idle",
            status="IDLE"
        )
        
        # Mock metadata check to indicate last activity 10 days ago (IDLE but resumable)
        past_10_days = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)).isoformat()
        self.worker.client.get_conversation_metadata.return_value = {
            "success": True,
            "response": {"metadata": {"lastActivityTime": past_10_days}}
        }
        
        # Mock send_message success
        self.worker.client.send_message.return_value = {
            "success": True,
            "response": {"conversationId": "conv-idle"}
        }
        
        task = {"id": "task-4", "project": "test_proj", "task_type": "audit"}
        workspace_info = {"workspace": "/dummy"}
        
        res = self.worker.dispatch_task(task, workspace_info, "worker-1")
        
        self.assertEqual(res["status"], "DELEGATED")
        self.assertEqual(res["conversation_id"], "conv-idle")
        self.worker.client.send_message.assert_called_once()
        self.worker.client.new_conversation.assert_not_called()
