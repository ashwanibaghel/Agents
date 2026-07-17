import unittest
import os
import shutil
import tempfile
import sqlite3
import threading
from control.audit_trail import AuditTrailManager

class TestAuditTrail(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_checkpoints.db")
        self.manager = AuditTrailManager(db_path=self.db_path)

    def tearDown(self):
        self.manager = None
        import gc
        gc.collect()
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_audit_insert(self):
        # Insert a valid transition record
        success = self.manager.append(
            event_type="TASK_CLAIMED",
            status="CLAIMED",
            trace_id="trace-111",
            worker_id="worker-abc",
            task_id="task-123",
            project_id="oi_labs",
            conversation_id="conv-456",
            branch="task-branch",
            message="Task claimed by worker-abc"
        )
        self.assertTrue(success)
        
        # Verify it exists in database
        records = self.manager.get_records(trace_id="trace-111")
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["event_type"], "TASK_CLAIMED")
        self.assertEqual(r["status"], "CLAIMED")
        self.assertEqual(r["worker_id"], "worker-abc")
        self.assertEqual(r["task_id"], "task-123")
        self.assertEqual(r["project_id"], "oi_labs")
        self.assertEqual(r["conversation_id"], "conv-456")
        self.assertEqual(r["branch"], "task-branch")
        self.assertEqual(r["message"], "Task claimed by worker-abc")
        self.assertIsNotNone(r["timestamp"])

    def test_append_only_behavior(self):
        # Append-only check: verify no SQL update/delete functions exist
        self.assertFalse(hasattr(self.manager, "update"))
        self.assertFalse(hasattr(self.manager, "delete"))
        self.assertFalse(hasattr(self.manager, "remove"))
        
        # Ensure we cannot modify tables directly via exposed functions (only appends)
        self.manager.append(event_type="TEST", status="OK", trace_id="trace-999")
        records1 = self.manager.get_records(trace_id="trace-999")
        self.assertEqual(len(records1), 1)

    def test_duplicate_protection(self):
        # 1. Duplicate protection using trace_id
        success1 = self.manager.append(
            event_type="WORKSPACE_PREPARED",
            status="PREPARED",
            trace_id="trace-dup-1",
            task_id="task-dup-1"
        )
        self.assertTrue(success1)
        
        # Attempt to insert same transition for same trace_id
        success2 = self.manager.append(
            event_type="WORKSPACE_PREPARED",
            status="PREPARED",
            trace_id="trace-dup-1",
            task_id="task-dup-1",
            message="Duplicate attempt"
        )
        self.assertFalse(success2)  # Should be rejected
        
        # Verify only 1 record exists in database
        records = self.manager.get_records(trace_id="trace-dup-1")
        self.assertEqual(len(records), 1)
        
        # 2. Duplicate protection using task_id when trace_id is None
        success3 = self.manager.append(
            event_type="TASK_CLAIMED",
            status="CLAIMED",
            trace_id=None,
            task_id="task-dup-2"
        )
        self.assertTrue(success3)
        
        success4 = self.manager.append(
            event_type="TASK_CLAIMED",
            status="CLAIMED",
            trace_id=None,
            task_id="task-dup-2",
            message="Duplicate task_id transition"
        )
        self.assertFalse(success4)  # Should be rejected
        
        records_task = self.manager.get_records(task_id="task-dup-2")
        self.assertEqual(len(records_task), 1)

    def test_restart_recovery(self):
        # Insert record
        self.manager.append(
            event_type="SESSION_CREATED",
            status="CREATED",
            trace_id="trace-recovery",
            task_id="task-recovery"
        )
        
        # Shutdown/destroy manager instance (simulating restart)
        del self.manager
        
        # Instantiate new manager pointing to same SQLite file
        new_manager = AuditTrailManager(db_path=self.db_path)
        
        # Verify record survives restart
        records = new_manager.get_records(trace_id="trace-recovery")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event_type"], "SESSION_CREATED")
        self.assertEqual(records[0]["status"], "CREATED")

    def test_concurrent_writes(self):
        # Concurrently write multiple different transitions from separate threads
        num_threads = 10
        threads = []
        errors = []
        
        def writer_thread(index):
            try:
                # Write unique trace_id records to avoid collision rejection
                success = self.manager.append(
                    event_type=f"THREAD_EVENT_{index}",
                    status="SUCCESS",
                    trace_id=f"trace-thread-{index}",
                    task_id=f"task-thread-{index}"
                )
                if not success:
                    errors.append(f"Thread {index} insert returned False")
            except Exception as e:
                errors.append(f"Thread {index} failed with exception: {e}")
                
        for i in range(num_threads):
            t = threading.Thread(target=writer_thread, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        self.assertEqual(len(errors), 0, f"Concurrent write errors occurred: {errors}")
        
        # Verify all records exist
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM audit_trail")
            count = cursor.fetchone()[0]
            self.assertEqual(count, num_threads)

if __name__ == "__main__":
    unittest.main()
