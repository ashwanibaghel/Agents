import unittest
import time
import os
import shutil
import tempfile
import sqlite3
import threading
from control.metrics_manager import MetricsManager

class TestMetricsManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_checkpoints.db")
        # Initialize with a low TTL (0.1s) for testing TTL expiration behavior
        self.manager = MetricsManager(db_path=self.db_path, cache_ttl_seconds=0.1)

    def tearDown(self):
        self.manager = None
        import gc
        gc.collect()
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_metric_recording(self):
        # Record task run
        self.manager.start_task_metric("trace-1", "task-1", "oi_labs")
        self.manager.record_workspace_reuse("trace-1", True)
        self.manager.record_conversation_reuse("trace-1", False)
        
        # Test timer recording
        self.manager.start_timer("trace-1", "execution")
        time.sleep(0.02)
        self.manager.stop_timer("trace-1", "execution")
        
        self.manager.record_verifier_result("trace-1", True)
        self.manager.record_git_result("trace-1", True)
        self.manager.complete_task_metric("trace-1", "DONE")
        
        # Pull metrics
        report = self.manager.get_metrics_report()
        tm = report["task_metrics"]
        self.assertEqual(tm["total_tasks"], 1)
        self.assertEqual(tm["completed_tasks"], 1)
        self.assertEqual(tm["failed_tasks"], 0)
        
        reuse = report["reuse_metrics"]
        self.assertEqual(reuse["workspace_reuse_rate"], 1.0)
        self.assertEqual(reuse["conversation_reuse_rate"], 0.0)

    def test_percentile_calculation(self):
        # Insert raw test durations
        times = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        p50 = self.manager.get_percentile(times, 0.5)
        p95 = self.manager.get_percentile(times, 0.95)
        self.assertEqual(p50, 60.0)
        self.assertEqual(p95, 100.0)
        
        # Edge cases
        self.assertEqual(self.manager.get_percentile([], 0.5), 0.0)

    def test_averages(self):
        # Setup multiple finished tasks
        for i in range(1, 6):
            trace_id = f"trace-avg-{i}"
            self.manager.start_task_metric(trace_id, f"task-avg-{i}", "project-x")
            self.manager.start_timer(trace_id, "execution")
            # Fake timer start offset to mock varying execution durations
            self.manager._timers[trace_id]["execution"] -= (i * 1.0) # mock i seconds
            self.manager.stop_timer(trace_id, "execution")
            self.manager.complete_task_metric(trace_id, "DONE")
            
        report = self.manager.get_metrics_report()
        em = report["execution_metrics"]
        self.assertGreater(em["average_execution"], 0.0)
        self.assertGreater(em["median_execution"], 0.0)
        self.assertGreater(em["fastest_task"], 0)
        self.assertGreater(em["slowest_task"], 0)

    def test_startup_count(self):
        # Simulate worker boots
        self.manager.record_worker_boot("worker-test-1")
        report1 = self.manager.get_metrics_report()
        self.assertEqual(report1["worker_metrics"]["startup_count"], 1)
        
        # Second boot
        self.manager.record_worker_boot("worker-test-1")
        # Wait for TTL to expire to force fresh calculations
        time.sleep(0.12)
        report2 = self.manager.get_metrics_report()
        self.assertEqual(report2["worker_metrics"]["startup_count"], 2)

    def test_uptime(self):
        self.manager.record_worker_boot("worker-uptime-1")
        # Heartbeat check
        self.manager.record_worker_heartbeat("worker-uptime-1")
        time.sleep(0.12)
        report = self.manager.get_metrics_report()
        # Uptime is diff of boot vs last_heartbeat
        self.assertGreaterEqual(report["worker_metrics"]["worker_uptime"], 0)

    def test_ttl_caching(self):
        # Change TTL to 5.0 seconds
        self.manager.cache_ttl = 5.0
        self.manager.start_task_metric("trace-cache-1", "task-cache-1", "oi_labs")
        self.manager.complete_task_metric("trace-cache-1", "DONE")
        
        # First call gets result and caches it
        report1 = self.manager.get_metrics_report()
        self.assertEqual(report1["task_metrics"]["total_tasks"], 1)
        
        # Record another task run
        self.manager.start_task_metric("trace-cache-2", "task-cache-2", "oi_labs")
        self.manager.complete_task_metric("trace-cache-2", "DONE")
        
        # Second call returns warm cache (so count remains 1!)
        report2 = self.manager.get_metrics_report()
        self.assertEqual(report2["task_metrics"]["total_tasks"], 1)
        
        # Force cache expiration
        self.manager.cache_ttl = 0.0
        report3 = self.manager.get_metrics_report()
        self.assertEqual(report3["task_metrics"]["total_tasks"], 2)

    def test_safe_persistence_failure(self):
        broken_manager = MetricsManager(db_path=self.db_path)
        
        # Safe execute triggers error
        def raise_err(conn):
            raise sqlite3.OperationalError("mock failure")
            
        success = broken_manager._safe_execute(raise_err)
        self.assertFalse(success)
        self.assertEqual(broken_manager._metrics_failures, 1)

    def test_concurrent_writes(self):
        num_threads = 10
        threads = []
        
        def writer(index):
            trace = f"trace-concurrent-{index}"
            self.manager.start_task_metric(trace, f"task-{index}", "project-concurrent")
            self.manager.complete_task_metric(trace, "DONE")
            
        for i in range(num_threads):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        time.sleep(0.12)
        report = self.manager.get_metrics_report()
        self.assertEqual(report["task_metrics"]["total_tasks"], num_threads)

if __name__ == "__main__":
    unittest.main()
