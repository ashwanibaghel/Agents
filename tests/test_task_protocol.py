import os
import shutil
import tempfile
import time
import unittest
import sqlite3
import yaml
from concurrent.futures import ThreadPoolExecutor

from control.task_models import Task
from control.task_parser import TaskParser
from control.task_source import LocalTaskSource, file_lock
from control.checkpoint_manager import CheckpointManager
from agents.base_agent import BaseAgent, AgentStatus
from brains.base_brain import BaseBrain


class MockSimpleBrain(BaseBrain):
    def __init__(self, steps):
        self.steps = steps

    def think(self, context: dict) -> dict:
        it = context.get("iteration", 0)
        if it < len(self.steps):
            return self.steps[it]
        return {
            "thought_summary": "Default done",
            "action": "COMPLETE_TASK",
            "action_input": {"summary": "Done"},
            "reason": "Complete",
            "task_complete": True
        }


class TestTaskProtocol(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.tasks_base_dir = os.path.join(self.temp_dir, "tasks")
        self.db_path = os.path.join(self.temp_dir, "checkpoints.db")
        
        # Initialize LocalTaskSource and CheckpointManager
        self.task_source = LocalTaskSource(self.tasks_base_dir, lease_timeout_seconds=1.0)
        self.checkpoint_manager = CheckpointManager(self.db_path)
        
        # Setup agent
        self.agent = BaseAgent("TEST-002", "Runtime Agent", "OI Labs", 2)
        # Mock workspace validation to avoid git dependency in protocol tests
        self.agent.validate_workspace = lambda: None
        self.agent.workspace_info = {"project": "oi_labs", "workspace": "dummy"}
        
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

    def test_structured_task_parsing_and_validation(self):
        valid_yaml = """
task_id: "OI-101"
project: "oi_labs"
task_type: "audit"
objective: "Audit the training pipelines"
context: "Inspecting codebase folders"
acceptance_criteria:
  - "Run LIST_FILES"
constraints:
  - "Read-only"
validation_commands:
  - "git status --short"
autonomy_level: 2
status: "inbox"
"""
        task = TaskParser.parse_yaml(valid_yaml)
        self.assertEqual(task.task_id, "OI-101")
        self.assertEqual(task.project, "oi_labs")
        self.assertEqual(task.task_type, "audit")
        self.assertEqual(task.objective, "Audit the training pipelines")
        self.assertEqual(task.autonomy_level, 2)
        self.assertEqual(task.validation_commands, ["git status --short"])
        
        # Test agent mapping format
        agent_task = TaskParser.to_agent_format(task)
        self.assertEqual(agent_task["id"], "OI-101")
        self.assertEqual(agent_task["title"], "Audit the training pipelines")
        self.assertEqual(agent_task["task_type"], "audit")
        
    def test_invalid_task_rejection(self):
        # Missing objective
        invalid_yaml = """
task_id: "OI-102"
project: "oi_labs"
task_type: "audit"
autonomy_level: 1
"""
        with self.assertRaises(ValueError):
            TaskParser.parse_yaml(invalid_yaml)
            
        # Invalid task_type
        invalid_type_yaml = """
task_id: "OI-102"
project: "oi_labs"
task_type: "code-modify"
objective: "Some objective"
autonomy_level: 1
"""
        with self.assertRaises(ValueError):
            TaskParser.parse_yaml(invalid_type_yaml)

    def test_atomic_task_claim(self):
        # Write task to inbox
        task_id = "OI-CLAIM"
        task_file = os.path.join(self.task_source.inbox_dir, f"{task_id}.yaml")
        task_data = {
            "task_id": task_id,
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Audit objective",
            "autonomy_level": 2
        }
        with open(task_file, "w") as f:
            yaml.dump(task_data, f)
            
        # Concurrently try to claim the task using 5 threads
        claims = []
        def claim_worker(worker_name):
            success = self.task_source.claim_task(task_id, worker_name)
            claims.append((worker_name, success))
            
        with ThreadPoolExecutor(max_workers=5) as executor:
            for i in range(5):
                executor.submit(claim_worker, f"worker-{i}")
                
        # Exactly one worker must succeed
        success_count = sum(1 for worker, success in claims if success)
        self.assertEqual(success_count, 1)
        
        # Verify the file is in working directory and claimed
        working_file = os.path.join(self.task_source.working_dir, f"{task_id}.yaml")
        self.assertTrue(os.path.exists(working_file))
        with open(working_file, "r") as f:
            data = yaml.safe_load(f)
        self.assertEqual(data["status"], "working")
        self.assertIsNotNone(data.get("worker_id"))

    def test_duplicate_worker_claim_rejection(self):
        task_id = "OI-DUP"
        task_file = os.path.join(self.task_source.inbox_dir, f"{task_id}.yaml")
        task_data = {
            "task_id": task_id,
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Audit objective",
            "autonomy_level": 2
        }
        with open(task_file, "w") as f:
            yaml.dump(task_data, f)
            
        # First claim succeeds
        res1 = self.task_source.claim_task(task_id, "worker-A")
        self.assertTrue(res1)
        
        # Second claim fails
        res2 = self.task_source.claim_task(task_id, "worker-B")
        self.assertFalse(res2)

    def test_task_state_checkpoint_saved(self):
        task_id = "OI-CP"
        self.checkpoint_manager.save_checkpoint(
            task_id=task_id,
            project="oi_labs",
            status="working",
            worker_id="worker-X",
            iteration=3,
            observations=[{"success": True, "action": "LIST_FILES", "output": "file1"}],
            actions=[{"action": "LIST_FILES"}],
            checkpoint_data={"files_changed": []}
        )
        
        checkpoint = self.checkpoint_manager.load_checkpoint(task_id)
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["iteration"], 3)
        self.assertEqual(checkpoint["worker_id"], "worker-X")
        self.assertEqual(len(checkpoint["observations"]), 1)
        self.assertEqual(checkpoint["observations"][0]["action"], "LIST_FILES")

    def test_interrupt_and_resume_from_next_iteration(self):
        task_id = "OI-RESUME"
        task = {
            "id": task_id,
            "project": "oi_labs",
            "title": "Audit task",
            "task_type": "audit"
        }
        
        # Steps sequence
        steps = [
            # Iteration 0
            {"thought_summary": "inspecting", "action": "LIST_FILES", "action_input": {}, "reason": "view", "task_complete": False},
            # Iteration 1
            {"thought_summary": "reading", "action": "READ_FILE", "action_input": {"path": "README.md"}, "reason": "view", "task_complete": False},
            # Iteration 2 (This is where we want to stop and verify checkpoints are saved)
            {"thought_summary": "completing", "action": "COMPLETE_TASK", "action_input": {"summary": "Task complete summary!"}, "reason": "done", "task_complete": True}
        ]
        
        # Run agent up to iteration 2 (2 iterations run: 0 and 1)
        self.agent.brain = MockSimpleBrain(steps)
        res1 = self.agent.run_task(task, max_iterations=2, checkpoint_manager=self.checkpoint_manager)
        
        # Task should be blocked / max iterations reached
        self.assertEqual(res1["status"], "BLOCKED")
        self.assertEqual(res1["iterations"], 2)
        
        # Checkpoint must contain iteration = 2
        checkpoint = self.checkpoint_manager.load_checkpoint(task_id)
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["iteration"], 2)
        self.assertEqual(len(checkpoint["observations"]), 2)
        
        # Resume task: run next iterations (starting from 2)
        res2 = self.agent.run_task(task, max_iterations=5, checkpoint_manager=self.checkpoint_manager)
        self.assertEqual(res2["status"], "DONE")
        # Total iterations executed in the resumed run should show up
        self.assertEqual(res2["iterations"], 3)
        self.assertEqual(res2["summary"], "Task complete summary!")
        
        # On DONE, active checkpoint must be deleted
        checkpoint_after = self.checkpoint_manager.load_checkpoint(task_id)
        self.assertIsNone(checkpoint_after)

    def test_heartbeat_and_stale_worker_recovery(self):
        # Configure LocalTaskSource with 0.1 lease timeout for testing recovery
        stale_source = LocalTaskSource(self.tasks_base_dir, lease_timeout_seconds=0.1)
        task_id = "OI-STALE"
        task_file = os.path.join(stale_source.inbox_dir, f"{task_id}.yaml")
        task_data = {
            "task_id": task_id,
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Stale audit test",
            "autonomy_level": 2
        }
        with open(task_file, "w") as f:
            yaml.dump(task_data, f)
            
        # Worker 1 claims task
        res1 = stale_source.claim_task(task_id, "worker-1")
        self.assertTrue(res1)
        
        # Check task file details
        working_file = os.path.join(stale_source.working_dir, f"{task_id}.yaml")
        with open(working_file, "r") as f:
            data = yaml.safe_load(f)
        self.assertEqual(data["worker_id"], "worker-1")
        hb1 = data["last_heartbeat_at"]
        
        # Sleep for timeout period (0.2s)
        time.sleep(0.2)
        
        # Worker 2 tries to claim stale task
        res2 = stale_source.claim_stale_task(task_id, "worker-2")
        self.assertTrue(res2)
        
        # Verify Worker 2 now owns the task
        with open(working_file, "r") as f:
            data2 = yaml.safe_load(f)
        self.assertEqual(data2["worker_id"], "worker-2")
        self.assertNotEqual(data2["last_heartbeat_at"], hb1)

    def test_duplicate_live_worker_prevention(self):
        # Fresh task claimed by worker-1
        task_id = "OI-LIVE"
        task_file = os.path.join(self.task_source.inbox_dir, f"{task_id}.yaml")
        task_data = {
            "task_id": task_id,
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Live test task",
            "autonomy_level": 2
        }
        with open(task_file, "w") as f:
            yaml.dump(task_data, f)
            
        res1 = self.task_source.claim_task(task_id, "worker-1")
        self.assertTrue(res1)
        
        # Worker 2 tries to claim stale - but lease_timeout is 1.0s and we haven't slept
        # So task is fresh/live! Claim stale must fail.
        res2 = self.task_source.claim_stale_task(task_id, "worker-2")
        self.assertFalse(res2)

    def test_checkpoint_preservation_blocked_and_failed(self):
        task_id = "OI-PRESERVED"
        task = {
            "id": task_id,
            "project": "oi_labs",
            "title": "Preservation test task",
            "task_type": "audit"
        }
        
        # Brain requests BLOCK
        brain_actions = [
            {"thought_summary": "inspecting", "action": "LIST_FILES", "action_input": {}, "reason": "list", "task_complete": False},
            {"thought_summary": "blocking task", "action": "BLOCK_TASK", "action_input": {"reason": "Requires human review"}, "reason": "block", "task_complete": False}
        ]
        
        self.agent.brain = MockSimpleBrain(brain_actions)
        res = self.agent.run_task(task, max_iterations=5, checkpoint_manager=self.checkpoint_manager)
        
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["summary"], "Requires human review")
        
        # Verify checkpoint is preserved in database
        checkpoint = self.checkpoint_manager.load_checkpoint(task_id)
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["iteration"], 2)
        
    def test_task_file_lifecycle_movement(self):
        task_id = "OI-LIFECYCLE"
        task_file_inbox = os.path.join(self.task_source.inbox_dir, f"{task_id}.yaml")
        task_data = {
            "task_id": task_id,
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Lifecycle test",
            "autonomy_level": 2
        }
        with open(task_file_inbox, "w") as f:
            yaml.dump(task_data, f)
            
        # 1. Claim task
        self.task_source.claim_task(task_id, "worker-1")
        self.assertFalse(os.path.exists(task_file_inbox))
        
        working_file = os.path.join(self.task_source.working_dir, f"{task_id}.yaml")
        self.assertTrue(os.path.exists(working_file))
        
        # 2. Update to done
        evidence = {"total_iterations": 3, "summary": "Task complete summary!"}
        self.task_source.update_task_status(task_id, "DONE", evidence)
        self.assertFalse(os.path.exists(working_file))
        
        done_file = os.path.join(self.task_source.done_dir, f"{task_id}.yaml")
        self.assertTrue(os.path.exists(done_file))
        
        with open(done_file, "r") as f:
            finished_data = yaml.safe_load(f)
        self.assertEqual(finished_data["status"], "done")
        self.assertEqual(finished_data["evidence"]["summary"], "Task complete summary!")


if __name__ == "__main__":
    unittest.main()
