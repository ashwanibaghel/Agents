import unittest
import os
import shutil
import tempfile
import json
from unittest.mock import patch, MagicMock
from control.structured_logger import StructuredLogger

class TestStructuredLogger(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.worker_id_file = os.path.join(self.test_dir, "worker_id.txt")
        self.log_dir = os.path.join(self.test_dir, "logs")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_worker_id_persistence(self):
        # 1. First run generates UUID
        logger1 = StructuredLogger(log_dir=self.log_dir, worker_id_file=self.worker_id_file)
        id1 = logger1.worker_id
        self.assertTrue(id1.startswith("worker-"))
        self.assertTrue(os.path.exists(self.worker_id_file))

        # 2. Second run reuses UUID
        logger2 = StructuredLogger(log_dir=self.log_dir, worker_id_file=self.worker_id_file)
        id2 = logger2.worker_id
        self.assertEqual(id1, id2)

    @patch("sys.stdout")
    def test_json_logging_format(self, mock_stdout):
        mock_stdout.write = MagicMock()
        logger = StructuredLogger(log_dir=self.log_dir, worker_id_file=self.worker_id_file)
        
        logger.info(
            "Test message",
            trace_id="test-trace",
            task_id="task-123",
            project_id="oi_labs",
            step="PREPARING",
            duration_ms=150
        )
        
        # Verify JSON stdout write occurred
        self.assertTrue(mock_stdout.write.called)
        written_str = mock_stdout.write.call_args[0][0]
        data = json.loads(written_str)
        
        self.assertEqual(data["level"], "INFO")
        self.assertEqual(data["message"], "Test message")
        self.assertEqual(data["trace_id"], "test-trace")
        self.assertEqual(data["task_id"], "task-123")
        self.assertEqual(data["project_id"], "oi_labs")
        self.assertEqual(data["step"], "PREPARING")
        self.assertEqual(data["duration_ms"], 150)
        self.assertEqual(data["worker_id"], logger.worker_id)

    @patch("sys.stderr")
    def test_logging_failure_non_crashing(self, mock_stderr):
        # Pass a completely invalid directory path that cannot be created/written to
        # on Windows (e.g. using invalid chars or empty path) to trigger failures
        invalid_log_dir = "/invalid_path/\\/?*:"
        logger = StructuredLogger(log_dir=invalid_log_dir, worker_id_file=self.worker_id_file)
        
        # Ensure file logging was safely disabled but did not raise an exception
        self.assertFalse(logger.file_logging_enabled)
        
        # Ensure we can still log messages without crashing
        logger.error("Test failure resilient logging")
        self.assertTrue(mock_stderr.write.called)

    @patch("sys.stdout")
    @patch("os.makedirs")
    def test_logger_init_permission_denied(self, mock_makedirs, mock_stdout):
        # Simulate PermissionError on makedirs
        mock_makedirs.side_effect = PermissionError("Access Denied")
        logger = StructuredLogger(log_dir=self.log_dir, worker_id_file=self.worker_id_file)
        self.assertFalse(logger.file_logging_enabled)
        
        # Verify logging still writes to stdout
        logger.info("Test message after init failure")
        self.assertTrue(mock_stdout.write.called)

    @patch("sys.stdout")
    @patch("builtins.open")
    def test_logger_write_exception_non_crashing(self, mock_open, mock_stdout):
        # Initialize normally first
        logger = StructuredLogger(log_dir=self.log_dir, worker_id_file=self.worker_id_file)
        self.assertTrue(logger.file_logging_enabled)
        
        # Mock file write to throw an exception
        mock_open.side_effect = OSError("Disk Full")
        
        # Ensure calling log does not crash the worker
        try:
            logger.info("Test message during OSS error")
        except Exception as e:
            self.fail(f"Logger raised an exception during OSError simulation: {e}")
            
        self.assertTrue(mock_stdout.write.called)

if __name__ == "__main__":
    unittest.main()
