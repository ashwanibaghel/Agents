import os
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from control.receipt_monitor import ReceiptMonitor
from control.result_verifier import ResultVerifier
from control.checkpoint_manager import CheckpointManager
from control.task_source import LocalTaskSource
from control.task_models import Task
from control.task_parser import TaskParser
from workers.antigravity_worker import AntigravityWorker
from workers.antigravity_client import AntigravityClient


class TestCompletionGate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.receipt_dir = os.path.join(self.temp_dir, "receipts")
        self.workspace_dir = os.path.join(self.temp_dir, "workspace")
        os.makedirs(self.receipt_dir, exist_ok=True)
        os.makedirs(self.workspace_dir, exist_ok=True)
        
        # Initialize Git in dummy workspace so Git commands don't fail
        import subprocess
        subprocess.run(["git", "init"], cwd=self.workspace_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.workspace_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.workspace_dir, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "initial commit"], cwd=self.workspace_dir, capture_output=True)
        
        self.monitor = ReceiptMonitor(receipt_dir=self.receipt_dir, poll_interval=0.01, timeout=0.1)
        self.workspace_info = {"workspace": self.workspace_dir}

    def tearDown(self):
        try:
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

    def test_canonical_receipt_path(self):
        self.assertEqual(self.monitor.receipt_dir, os.path.abspath(self.receipt_dir))

    def write_receipt(self, task_id, data):
        path = os.path.join(self.receipt_dir, f"{task_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_malformed_json_rejection(self):
        path = os.path.join(self.receipt_dir, "T-001.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        res = self.monitor.check_receipt("T-001")
        self.assertFalse(res["success"])
        self.assertIn("Malformed JSON", res["error"])

    def test_wrong_task_id_rejection(self):
        self.write_receipt("T-001", {
            "task_id": "T-002",
            "status": "DONE",
            "summary": "Completed task.",
            "completed_at": "2026"
        })
        res = self.monitor.check_receipt("T-001")
        self.assertFalse(res["success"])
        self.assertIn("Task ID mismatch", res["error"])

    def test_wrong_conversation_id_rejection(self):
        self.write_receipt("T-001", {
            "task_id": "T-001",
            "conversation_id": "conv-real",
            "status": "DONE",
            "summary": "Completed task.",
            "completed_at": "2026"
        })
        res = self.monitor.check_receipt("T-001", conversation_id="conv-expected")
        self.assertFalse(res["success"])
        self.assertIn("Conversation ID mismatch", res["error"])

    def test_unknown_receipt_status_rejection(self):
        self.write_receipt("T-001", {
            "task_id": "T-001",
            "status": "UNKNOWN_STATUS",
            "summary": "Completed task.",
            "completed_at": "2026"
        })
        res = self.monitor.check_receipt("T-001")
        self.assertFalse(res["success"])
        self.assertIn("Invalid status", res["error"])

    def test_audit_receipt_requires_evidence_paths(self):
        task = {"id": "T-001", "task_type": "audit"}
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Successfully audited backend",
            "evidence_paths": [],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("requires non-empty evidence_paths", err)

    def test_evidence_path_traversal_rejection(self):
        task = {"id": "T-001", "task_type": "audit"}
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Successfully audited backend",
            "evidence_paths": ["../../etc/passwd"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("validation failed", err)

    def test_missing_evidence_path_rejection(self):
        task = {"id": "T-001", "task_type": "audit"}
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Successfully audited backend",
            "evidence_paths": ["nonexistent_evidence.txt"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("does not exist on disk", err)

    def test_audit_dirty_repository_rejection(self):
        # Create dirty file in workspace
        dirty_file = os.path.join(self.workspace_dir, "dirty.txt")
        with open(dirty_file, "w") as f:
            f.write("dirty")
            
        task = {"id": "T-001", "task_type": "audit"}
        
        # Create evidence file so it exists
        evidence_file = os.path.join(self.workspace_dir, "evidence.txt")
        with open(evidence_file, "w") as f:
            f.write("evidence")
            
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Successfully audited backend",
            "evidence_paths": ["evidence.txt"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("left repository dirty", err)

    def test_code_receipt_without_actual_changes_rejected(self):
        task = {"id": "T-001", "task_type": "code"}
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Implemented feature",
            "files_changed": [],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("requires at least one files_changed entry", err)

    def test_receipt_files_changed_mismatch_rejected(self):
        # Create file in workspace but don't add to git or register wrong change
        actual_file = os.path.join(self.workspace_dir, "code.py")
        with open(actual_file, "w") as f:
            f.write("print('hello')")
            
        task = {"id": "T-001", "task_type": "code"}
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Implemented feature",
            "files_changed": ["different_file.py"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("was not modified in actual Git status", err)

    def test_failed_validation_rejected(self):
        actual_file = os.path.join(self.workspace_dir, "code.py")
        with open(actual_file, "w") as f:
            f.write("print('hello')")
            
        task = {
            "id": "T-001",
            "task_type": "code",
            "validation_commands": ["python nonexistent_script.py"]
        }
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Implemented feature",
            "files_changed": ["code.py"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("failed during independent verification", err)

    def test_git_diff_check_failure_rejected(self):
        # Create a file with trailing whitespace
        actual_file = os.path.join(self.workspace_dir, "code.py")
        with open(actual_file, "w") as f:
            f.write("print('hello')  \n")  # trailing space
            
        # Stage the file in git so git diff cached can detect it
        import subprocess
        subprocess.run(["git", "add", "code.py"], cwd=self.workspace_dir, capture_output=True)
            
        task = {
            "id": "T-001",
            "task_type": "code",
            "validation_commands": ["git status --short"]
        }
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Implemented feature",
            "files_changed": ["code.py"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("Git diff", err)

    def test_valid_audit_becomes_done(self):
        evidence_file = os.path.join(self.workspace_dir, "evidence.txt")
        with open(evidence_file, "w") as f:
            f.write("evidence")
            
        # Commit it to git so repository is clean
        import subprocess
        subprocess.run(["git", "add", "evidence.txt"], cwd=self.workspace_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add evidence"], cwd=self.workspace_dir, capture_output=True)
        
        task = {
            "id": "T-001",
            "task_type": "audit",
            "validation_commands": ["git status --short"]
        }
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Successfully audited backend module entry points",
            "evidence_paths": ["evidence.txt"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertTrue(verified)

    def test_valid_code_task_becomes_done(self):
        actual_file = os.path.join(self.workspace_dir, "code.py")
        with open(actual_file, "w") as f:
            f.write("print('hello')\n")
            
        task = {
            "id": "T-001",
            "task_type": "code",
            "validation_commands": ["git status --short"]
        }
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Implemented coding task",
            "files_changed": ["code.py"],
            "completed_at": "2026"
        }
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertTrue(verified)

    def test_blocked_receipt_becomes_blocked(self):
        self.write_receipt("T-001", {
            "task_id": "T-001",
            "status": "BLOCKED",
            "summary": "Task is blocked.",
            "completed_at": "2026"
        })
        res = self.monitor.check_receipt("T-001")
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "BLOCKED")

    def test_failed_receipt_becomes_failed(self):
        self.write_receipt("T-001", {
            "task_id": "T-001",
            "status": "FAILED",
            "summary": "Task failed.",
            "completed_at": "2026"
        })
        res = self.monitor.check_receipt("T-001")
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "FAILED")

    def test_receipt_timeout_never_becomes_done(self):
        res = self.monitor.wait_for_receipt("NONEXISTENT_TASK")
        self.assertFalse(res["success"])
        self.assertTrue(res.get("timeout"))

    def test_receipt_itself_never_dirties_target_repository(self):
        # The receipt folder is state/receipts which is outside the workspace path workspaces/oi-labs
        # Therefore, writing or updating receipts never changes target repository files.
        self.assertTrue(self.monitor.receipt_dir.startswith(os.path.abspath(self.temp_dir)))
        self.assertFalse(self.monitor.receipt_dir.startswith(self.workspace_dir))

    def test_feature_receipt_passes_with_branch_and_file_change(self):
        import subprocess
        # 1. Create branch containing task ID
        subprocess.run(["git", "checkout", "-b", "feature/task-T-001"], cwd=self.workspace_dir, capture_output=True)
        
        # 2. Create a dummy file and commit it (so it shows in git status / diff)
        file_path = os.path.join(self.workspace_dir, "feature.py")
        with open(file_path, "w") as f:
            f.write("# dummy feature")
            
        subprocess.run(["git", "add", "feature.py"], cwd=self.workspace_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add feature file"], cwd=self.workspace_dir, capture_output=True)
        
        task = {
            "id": "T-001",
            "task_type": "feature",
            "validation_commands": ["git status --short"]
        }
        receipt = {
            "task_id": "T-001",
            "status": "DONE",
            "summary": "Implemented feature task",
            "files_changed": ["feature.py"],
            "completed_at": "2026"
        }
        
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertTrue(verified, f"Feature verification failed: {err}")

    def test_feature_receipt_fails_without_branch(self):
        import subprocess
        # 1. Create feature.py and commit to main (not a task branch containing T-002)
        file_path = os.path.join(self.workspace_dir, "feature.py")
        with open(file_path, "w") as f:
            f.write("# dummy feature")
            
        subprocess.run(["git", "add", "feature.py"], cwd=self.workspace_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add feature file"], cwd=self.workspace_dir, capture_output=True)
        
        task = {
            "id": "T-002",
            "task_type": "feature"
        }
        receipt = {
            "task_id": "T-002",
            "status": "DONE",
            "summary": "Implemented feature task",
            "files_changed": ["feature.py"],
            "completed_at": "2026"
        }
        
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("No git branch found containing task ID", err)

    def test_feature_receipt_fails_without_file_change(self):
        import subprocess
        # Create branch containing task ID, but no files in files_changed
        subprocess.run(["git", "checkout", "-b", "feature/task-T-003"], cwd=self.workspace_dir, capture_output=True)
        
        task = {
            "id": "T-003",
            "task_type": "feature"
        }
        receipt = {
            "task_id": "T-003",
            "status": "DONE",
            "summary": "Implemented feature task",
            "files_changed": [],
            "completed_at": "2026"
        }
        
        verified, err, _ = ResultVerifier.verify_result(task, self.workspace_info, receipt)
        self.assertFalse(verified)
        self.assertIn("requires at least one files_changed entry", err)


if __name__ == "__main__":
    unittest.main()
