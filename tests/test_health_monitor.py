import unittest
import time
import os
import shutil
import tempfile
import sqlite3
from control.health_monitor import HealthMonitor

class TestHealthMonitor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_checkpoints.db")
        self.monitor = HealthMonitor(db_path=self.db_path)

    def tearDown(self):
        self.monitor = None
        import gc
        gc.collect()
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_healthy_state(self):
        # Component check should return HEALTHY and correct schema keys
        def dummy_healthy():
            return "HEALTHY", "Component is operational.", {"details_key": 42}
            
        res = self.monitor.check_component("DummyComponent", dummy_healthy)
        self.assertEqual(res["component"], "DummyComponent")
        self.assertEqual(res["health_state"], "HEALTHY")
        self.assertEqual(res["status"], "Component is operational.")
        self.assertEqual(res["details"]["details_key"], 42)
        self.assertIsNotNone(res["latency_ms"])
        self.assertEqual(res["retry_count"], 0)
        self.assertIsNone(res["last_error"])

    def test_degraded_state(self):
        def dummy_degraded():
            return "DEGRADED", "Latency is higher than expected.", {"latency": 500}
            
        res = self.monitor.check_component("DegradedComponent", dummy_degraded)
        self.assertEqual(res["health_state"], "DEGRADED")
        self.assertEqual(res["status"], "Latency is higher than expected.")
        self.assertEqual(res["retry_count"], 1)
        self.assertEqual(res["last_error"], "Latency is higher than expected.")

    def test_unhealthy_state(self):
        def dummy_unhealthy():
            return "UNHEALTHY", "Database connection lost.", {}
            
        res = self.monitor.check_component("UnhealthyComponent", dummy_unhealthy)
        self.assertEqual(res["health_state"], "UNHEALTHY")
        self.assertEqual(res["status"], "Database connection lost.")
        self.assertEqual(res["retry_count"], 1)
        
        # Test retry counter increment
        res2 = self.monitor.check_component("UnhealthyComponent", dummy_unhealthy)
        self.assertEqual(res2["retry_count"], 2)

    def test_timeout_handling(self):
        # Slow component check exceeding timeout must return UNHEALTHY with Timeout message
        def dummy_slow():
            time.sleep(0.5)
            return "HEALTHY", "Done", {}
            
        res = self.monitor.check_component("SlowComponent", dummy_slow, timeout=0.1)
        self.assertEqual(res["health_state"], "UNHEALTHY")
        self.assertTrue("Timeout" in res["status"])
        self.assertEqual(res["retry_count"], 1)

    def test_retry_tracking_reset(self):
        def dummy_unhealthy():
            return "UNHEALTHY", "Failed", {}
            
        def dummy_healthy():
            return "HEALTHY", "Recovered", {}
            
        self.monitor.check_component("RetryComponent", dummy_unhealthy)
        res_fail = self.monitor.check_component("RetryComponent", dummy_unhealthy)
        self.assertEqual(res_fail["retry_count"], 2)
        
        # Successful check resets retry count
        res_ok = self.monitor.check_component("RetryComponent", dummy_healthy)
        self.assertEqual(res_ok["retry_count"], 0)
        self.assertIsNone(res_ok["last_error"])

if __name__ == "__main__":
    unittest.main()
