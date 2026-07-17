"""
tests/test_backup_manager.py
"""

import os
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from control.backup_manager import BackupManager


class TestBackupManager(unittest.TestCase):

    def setUp(self):
        self.test_dir   = tempfile.mkdtemp()
        self.backup_dir = os.path.join(self.test_dir, "backups")
        self.state_dir  = os.path.join(self.test_dir, "state")
        self.config_dir = os.path.join(self.test_dir, "config")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)

        # Create fake state files
        self._db_path      = os.path.join(self.state_dir, "task_checkpoints.db")
        self._worker_id    = os.path.join(self.state_dir, "worker_id.txt")
        self._ff_yaml      = os.path.join(self.config_dir, "feature_flags.yaml")
        self._projects_yaml = os.path.join(self.config_dir, "projects.yaml")

        conn = sqlite3.connect(self._db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, val TEXT)")
        conn.commit()
        conn.close()

        with open(self._worker_id, "w") as f:
            f.write("worker-test-abc123\n")
        with open(self._ff_yaml, "w") as f:
            f.write("config_version: '3.2.0'\nfeature_flags:\n  backup: true\n")
        with open(self._projects_yaml, "w") as f:
            f.write("projects:\n  test_project:\n    name: Test\n")

        # Build a manager pointing to our temp files
        self.manager = BackupManager(backup_dir=self.backup_dir)
        self.manager._databases   = [self._db_path]
        self.manager._configs     = [self._ff_yaml, self._projects_yaml]
        self.manager._extra_state = [self._worker_id]

    def tearDown(self):
        self.manager = None
        import gc
        gc.collect()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ─── Creation ───────────────────────────────────────────

    def test_backup_creates_folder(self):
        result = self.manager.run_backup("test_run")
        self.assertTrue(result["success"])
        self.assertTrue(os.path.isdir(result["path"]))

    def test_backup_creates_manifest(self):
        result = self.manager.run_backup("manifest_check")
        manifest_path = os.path.join(result["path"], "manifest.json")
        self.assertTrue(os.path.exists(manifest_path))

    def test_backup_manifest_structure(self):
        result = self.manager.run_backup("structure_check")
        manifest = result["manifest"]
        self.assertIn("backup_id",  manifest)
        self.assertIn("label",      manifest)
        self.assertIn("timestamp",  manifest)
        self.assertIn("files",      manifest)
        self.assertIn("retention_days", manifest)

    def test_backup_includes_all_files(self):
        result = self.manager.run_backup("files_check")
        filenames = [f["filename"] for f in result["manifest"]["files"]]
        self.assertIn("task_checkpoints.db",  filenames)
        self.assertIn("worker_id.txt",        filenames)
        self.assertIn("feature_flags.yaml",   filenames)
        self.assertIn("projects.yaml",        filenames)

    def test_backup_id_contains_label(self):
        result = self.manager.run_backup("my_label")
        self.assertIn("my_label", result["backup_id"])

    def test_backup_id_contains_timestamp(self):
        import re
        result = self.manager.run_backup("ts_check")
        self.assertTrue(re.search(r"\d{8}_\d{6}", result["backup_id"]))

    def test_manifest_has_checksums(self):
        result = self.manager.run_backup("checksum_check")
        for entry in result["manifest"]["files"]:
            self.assertIn("checksum",            entry)
            self.assertIn("checksum_algorithm",  entry)
            self.assertEqual("sha256", entry["checksum_algorithm"])
            self.assertEqual(64, len(entry["checksum"]))

    def test_manifest_has_size_bytes(self):
        result = self.manager.run_backup("size_check")
        for entry in result["manifest"]["files"]:
            self.assertIn("size_bytes", entry)
            self.assertGreater(entry["size_bytes"], 0)

    def test_manifest_has_original_paths(self):
        result = self.manager.run_backup("path_check")
        for entry in result["manifest"]["files"]:
            self.assertIn("original_path", entry)

    def test_backup_copies_file_contents(self):
        result = self.manager.run_backup("content_check")
        backed_up_id = os.path.join(result["path"], "worker_id.txt")
        self.assertTrue(os.path.exists(backed_up_id))
        with open(backed_up_id) as f:
            content = f.read()
        self.assertIn("worker-test-abc123", content)

    # ─── Checksums ──────────────────────────────────────────

    def test_checksum_is_deterministic(self):
        path = self._worker_id
        c1 = self.manager._checksum(path)
        c2 = self.manager._checksum(path)
        self.assertEqual(c1, c2)

    def test_checksum_changes_on_content_change(self):
        path = self._worker_id
        c1 = self.manager._checksum(path)
        with open(path, "a") as f:
            f.write("extra_content")
        c2 = self.manager._checksum(path)
        self.assertNotEqual(c1, c2)

    # ─── Validation ─────────────────────────────────────────

    def test_validate_backup_passes_clean_backup(self):
        result = self.manager.run_backup("validate_pass")
        validation = self.manager.validate_backup(result["backup_id"])
        self.assertTrue(validation["valid"])
        self.assertTrue(any(f["status"] == "PASS" for f in validation["findings"]))

    def test_validate_backup_fails_missing_backup_id(self):
        validation = self.manager.validate_backup("nonexistent_backup_id_xyz")
        self.assertFalse(validation["valid"])
        self.assertTrue(any(f["status"] == "FAIL" for f in validation["findings"]))

    def test_validate_backup_detects_corrupted_file(self):
        result = self.manager.run_backup("corrupt_test")
        # Corrupt one file
        files = [e for e in result["manifest"]["files"] if e["filename"].endswith(".txt")]
        if files:
            target = os.path.join(result["path"], files[0]["filename"])
            with open(target, "w") as f:
                f.write("CORRUPTED CONTENT!!!")
        validation = self.manager.validate_backup(result["backup_id"])
        self.assertFalse(validation["valid"])
        fail_checks = [f for f in validation["findings"] if f["status"] == "FAIL"]
        self.assertTrue(len(fail_checks) > 0)

    def test_validate_backup_detects_missing_file(self):
        result = self.manager.run_backup("missing_file_test")
        # Delete one backed-up file
        files = result["manifest"]["files"]
        if files:
            target = os.path.join(result["path"], files[0]["filename"])
            os.remove(target)
        # Also corrupt manifest checksum so it thinks file should exist
        validation = self.manager.validate_backup(result["backup_id"])
        self.assertFalse(validation["valid"])

    def test_validate_backup_missing_manifest(self):
        result = self.manager.run_backup("no_manifest_test")
        manifest_path = os.path.join(result["path"], "manifest.json")
        os.remove(manifest_path)
        validation = self.manager.validate_backup(result["backup_id"])
        self.assertFalse(validation["valid"])

    def test_validate_backup_schema_check_passes(self):
        result = self.manager.run_backup("schema_test")
        validation = self.manager.validate_backup(result["backup_id"])
        schema_findings = [f for f in validation["findings"] if f["check"] == "schema_compatibility"]
        self.assertTrue(len(schema_findings) > 0)
        self.assertTrue(all(f["status"] == "PASS" for f in schema_findings))

    def test_validate_backup_config_version_check(self):
        result = self.manager.run_backup("config_version_test")
        validation = self.manager.validate_backup(result["backup_id"])
        version_findings = [f for f in validation["findings"] if f["check"] == "config_version"]
        self.assertTrue(len(version_findings) > 0)

    # ─── List & Latest ──────────────────────────────────────

    def test_list_backups_empty_initially(self):
        mgr = BackupManager(backup_dir=os.path.join(self.test_dir, "empty_backups"))
        backups = mgr.list_backups()
        self.assertEqual(backups, [])

    def test_list_backups_after_creation(self):
        self.manager.run_backup("list_test_1")
        self.manager.run_backup("list_test_2")
        backups = self.manager.list_backups()
        self.assertEqual(len(backups), 2)

    def test_list_backups_sorted_newest_first(self):
        import time
        self.manager.run_backup("oldest")
        time.sleep(0.01)
        self.manager.run_backup("newest")
        backups = self.manager.list_backups()
        self.assertEqual(len(backups), 2)
        # Newest should be first
        self.assertIn("newest", backups[0]["label"])

    def test_get_latest_backup_none_when_empty(self):
        mgr = BackupManager(backup_dir=os.path.join(self.test_dir, "empty_backups2"))
        latest = mgr.get_latest_backup()
        self.assertIsNone(latest)

    def test_get_latest_backup_returns_newest(self):
        import time
        self.manager.run_backup("first")
        time.sleep(0.01)
        self.manager.run_backup("second")
        latest = self.manager.get_latest_backup()
        self.assertIn("second", latest["label"])

    # ─── Cleanup ────────────────────────────────────────────

    def test_cleanup_by_max_count(self):
        mgr = BackupManager(backup_dir=self.backup_dir, max_backups=2, retention_days=365)
        mgr._databases   = [self._db_path]
        mgr._configs     = []
        mgr._extra_state = []

        import time
        for label in ["b1", "b2", "b3", "b4"]:
            mgr.run_backup(label)
            time.sleep(0.01)

        before = len(mgr.list_backups())
        self.assertEqual(before, 4)

        deleted = mgr.cleanup_old_backups()
        self.assertEqual(deleted, 2)
        after = len(mgr.list_backups())
        self.assertEqual(after, 2)

    def test_cleanup_by_age(self):
        import datetime, time
        mgr = BackupManager(backup_dir=self.backup_dir, max_backups=100, retention_days=0)
        mgr._databases   = [self._db_path]
        mgr._configs     = []
        mgr._extra_state = []

        mgr.run_backup("old_backup")
        time.sleep(0.01)  # Ensure timestamp is in the past

        deleted = mgr.cleanup_old_backups()
        self.assertGreaterEqual(deleted, 1)

    def test_cleanup_returns_zero_when_nothing_to_delete(self):
        mgr = BackupManager(backup_dir=self.backup_dir, max_backups=100, retention_days=365)
        mgr._databases   = [self._db_path]
        mgr._configs     = []
        mgr._extra_state = []
        mgr.run_backup("single")
        deleted = mgr.cleanup_old_backups()
        self.assertEqual(deleted, 0)

    def test_cleanup_never_raises(self):
        broken_mgr = BackupManager(backup_dir="/invalid/path/to/backups")
        try:
            broken_mgr.cleanup_old_backups()
        except Exception as e:
            self.fail(f"cleanup_old_backups raised an exception: {e}")

    # ─── Safe failure ────────────────────────────────────────

    def test_run_backup_never_raises_on_error(self):
        """Backup manager must return failure dict, never raise, when an exception occurs."""
        from unittest.mock import patch
        import shutil

        mgr = BackupManager(backup_dir=self.backup_dir)
        mgr._databases   = [self._db_path]
        mgr._configs     = []
        mgr._extra_state = []

        # Patch shutil.copy2 to raise an exception, simulating a write failure
        with patch("control.backup_manager.shutil.copy2", side_effect=IOError("Simulated disk full")):
            try:
                result = mgr.run_backup("fail_safe")
                # Should return failure dict, not raise
                self.assertFalse(result["success"])
                self.assertIsNotNone(result["error"])
            except Exception as e:
                self.fail(f"run_backup raised an exception: {e}")

    def test_validate_backup_never_raises(self):
        try:
            result = self.manager.validate_backup("totally_invalid_id_xyz")
            self.assertFalse(result["valid"])
        except Exception as e:
            self.fail(f"validate_backup raised an exception: {e}")

    # ─── Concurrency ─────────────────────────────────────────

    def test_concurrent_backups_safe(self):
        errors = []

        def do_backup(i):
            try:
                self.manager.run_backup(f"concurrent_{i}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=do_backup, args=(i,)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(errors, [], f"Concurrent backup errors: {errors}")
        backups = self.manager.list_backups()
        self.assertEqual(len(backups), 5)


if __name__ == "__main__":
    unittest.main()
