import os
import unittest
import tempfile
import time
import shutil
import datetime
import yaml

from control.task_models import Task
from control.task_parser import TaskParser
from control.task_source import LocalTaskSource
from control.checkpoint_manager import CheckpointManager

class TestStoreAndForward(unittest.TestCase):
    def setUp(self):
        # Create temp environment
        self.temp_dir = tempfile.mkdtemp()
        self.tasks_base_dir = os.path.join(self.temp_dir, "tasks")
        
        # Initialize LocalTaskSource (lease timeout 2 seconds for test)
        self.task_source = LocalTaskSource(self.tasks_base_dir, lease_timeout_seconds=2.0)
        
    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_queued_tasks_while_worker_offline(self):
        """Prove that tasks remain in the inbox (queued) while the worker is offline."""
        task_data = {
            "task_id": "TEST-QUEUED-1",
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Audit task when worker offline",
            "autonomy_level": 2,
            "status": "inbox"
        }
        
        # Write directly to inbox directory (simulating remote insert)
        inbox_file = os.path.join(self.task_source.inbox_dir, "TEST-QUEUED-1.yaml")
        with open(inbox_file, "w", encoding="utf-8") as f:
            yaml.dump(task_data, f)
            
        # Verify tasks are in inbox and status is "inbox"
        pending = self.task_source.fetch_pending_tasks()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].task_id, "TEST-QUEUED-1")
        self.assertEqual(pending[0].status, "inbox")
        self.assertIsNone(pending[0].worker_id)

    def test_worker_startup_backlog_processing(self):
        """Prove that when a worker starts up, it processes all pending backlog tasks."""
        # Add multiple tasks to inbox
        for i in range(3):
            t_id = f"BACKLOG-{i}"
            task_data = {
                "task_id": t_id,
                "project": "oi_labs",
                "task_type": "audit",
                "objective": f"Backlog audit {i}",
                "autonomy_level": 2,
                "status": "inbox"
            }
            with open(os.path.join(self.task_source.inbox_dir, f"{t_id}.yaml"), "w", encoding="utf-8") as f:
                yaml.dump(task_data, f)

        # Worker starts up
        worker_id = "worker-startup-test"
        pending = self.task_source.fetch_pending_tasks()
        self.assertEqual(len(pending), 3)

        # Claim backlog tasks
        claimed_count = 0
        for task in pending:
            if self.task_source.claim_task(task.task_id, worker_id):
                claimed_count += 1

        self.assertEqual(claimed_count, 3)
        self.assertEqual(len(self.task_source.fetch_pending_tasks()), 0)

    def test_heartbeat_updates_lease_timestamp(self):
        """Prove that sending a heartbeat updates the last_heartbeat_at timestamp."""
        task_data = {
            "task_id": "HB-1",
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Heartbeat test",
            "autonomy_level": 2,
            "status": "inbox"
        }
        
        inbox_file = os.path.join(self.task_source.inbox_dir, "HB-1.yaml")
        with open(inbox_file, "w", encoding="utf-8") as f:
            yaml.dump(task_data, f)

        worker_id = "worker-hb"
        self.assertTrue(self.task_source.claim_task("HB-1", worker_id))
        
        # Read claimed task to get initial heartbeat timestamp
        working_file = os.path.join(self.task_source.working_dir, "HB-1.yaml")
        with open(working_file, "r", encoding="utf-8") as f:
            t1 = yaml.safe_load(f)
        hb_start = t1.get("last_heartbeat_at")
        self.assertIsNotNone(hb_start)
        
        # Wait briefly and send heartbeat
        time.sleep(0.1)
        self.task_source.heartbeat_task("HB-1", worker_id)
        
        with open(working_file, "r", encoding="utf-8") as f:
            t2 = yaml.safe_load(f)
        hb_after = t2.get("last_heartbeat_at")
        
        self.assertNotEqual(hb_start, hb_after)

    def test_stale_task_recovery(self):
        """Prove that stale tasks are recovered back to inbox if worker heartbeat expires."""
        task_data = {
            "task_id": "STALE-1",
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Stale lease test",
            "autonomy_level": 2,
            "status": "inbox"
        }
        
        inbox_file = os.path.join(self.task_source.inbox_dir, "STALE-1.yaml")
        with open(inbox_file, "w", encoding="utf-8") as f:
            yaml.dump(task_data, f)

        worker_id = "worker-dead"
        self.assertTrue(self.task_source.claim_task("STALE-1", worker_id))
        
        # Stale recovery (lease timeout is 2 seconds)
        # Verify immediately not recovered
        recovered = self.task_source.recover_stale_tasks()
        self.assertEqual(recovered, 0)
        
        # Wait for lease to expire
        time.sleep(2.1)
        recovered = self.task_source.recover_stale_tasks()
        self.assertEqual(recovered, 1)
        
        # Verify back in inbox
        pending = self.task_source.fetch_pending_tasks()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].task_id, "STALE-1")
        self.assertEqual(pending[0].status, "inbox")

    def test_duplicate_claim_prevention(self):
        """Prove that multiple workers cannot claim the same task simultaneously."""
        task_data = {
            "task_id": "DUP-CLAIM",
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Concurreny claim test",
            "autonomy_level": 2,
            "status": "inbox"
        }
        
        inbox_file = os.path.join(self.task_source.inbox_dir, "DUP-CLAIM.yaml")
        with open(inbox_file, "w", encoding="utf-8") as f:
            yaml.dump(task_data, f)

        # Worker A claims first
        self.assertTrue(self.task_source.claim_task("DUP-CLAIM", "worker-A"))
        
        # Worker B tries to claim
        self.assertFalse(self.task_source.claim_task("DUP-CLAIM", "worker-B"))
        
        # Verify Worker A remains owner
        working_file = os.path.join(self.task_source.working_dir, "DUP-CLAIM.yaml")
        with open(working_file, "r", encoding="utf-8") as f:
            claimed = yaml.safe_load(f)
        self.assertEqual(claimed.get("worker_id"), "worker-A")

    def test_graceful_restart_resume(self):
        """Prove that a worker can resume its own active/delegated tasks on startup."""
        task_data = {
            "task_id": "RESUME-1",
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Resume task test",
            "autonomy_level": 2,
            "status": "delegated",
            "worker_id": "worker-main"
        }
        
        # Place directly in working directory (simulating in-progress task before crash)
        working_file = os.path.join(self.task_source.working_dir, "RESUME-1.yaml")
        with open(working_file, "w", encoding="utf-8") as f:
            yaml.dump(task_data, f)
            
        # Startup worker and fetch its active tasks
        worker_id = "worker-main"
        active_tasks = []
        
        if os.path.exists(self.task_source.working_dir):
            for file in os.listdir(self.task_source.working_dir):
                if file.endswith((".yaml", ".yml")):
                    file_path = os.path.join(self.task_source.working_dir, file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    task = Task.from_dict(data)
                    if task.status == "delegated" and task.worker_id == worker_id:
                        active_tasks.append(task)
                        
        self.assertEqual(len(active_tasks), 1)
        self.assertEqual(active_tasks[0].task_id, "RESUME-1")
        self.assertEqual(active_tasks[0].status, "delegated")

if __name__ == "__main__":
    unittest.main()
