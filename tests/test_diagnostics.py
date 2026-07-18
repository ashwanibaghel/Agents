"""
tests/test_diagnostics.py

Unit tests for the GET /diagnostics endpoint in bridge_server.py.
All external dependencies (Supabase, metrics_manager, config files) are mocked.
"""
import os
import sys
import json
import unittest
import tempfile
from unittest.mock import patch, MagicMock


# ── Environment Setup ────────────────────────────────────────────────────────
# bridge_server raises RuntimeError at import if env vars are missing.

os.environ.setdefault("BRIDGE_TOKEN", "test-token")
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake-service-key")


def _get_client():
    """Return a FastAPI TestClient, importing bridge_server with mocked Supabase."""
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: [])
        mock_post.return_value = MagicMock(status_code=201, json=lambda: [{}])
        from fastapi.testclient import TestClient
        import bridge_server
        client = TestClient(bridge_server.app, raise_server_exceptions=False)
        return client, bridge_server


class TestDiagnosticsEndpoint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client, cls.bridge = _get_client()

    def _get_diag(self):
        response = self.client.get("/diagnostics")
        self.assertIn(response.status_code, [200, 500])
        return response.json()

    # ── Structure tests ──────────────────────────────────────────────────────

    def test_returns_200(self):
        response = self.client.get("/diagnostics")
        self.assertEqual(response.status_code, 200)

    def test_returns_json(self):
        response = self.client.get("/diagnostics")
        data = response.json()
        self.assertIsInstance(data, dict)

    def test_contains_worker_key(self):
        data = self._get_diag()
        self.assertIn("worker", data)

    def test_contains_environment_key(self):
        data = self._get_diag()
        self.assertIn("environment", data)

    def test_contains_component_versions_key(self):
        data = self._get_diag()
        self.assertIn("component_versions", data)

    def test_contains_configuration_key(self):
        data = self._get_diag()
        self.assertIn("configuration", data)

    def test_contains_feature_flags_key(self):
        data = self._get_diag()
        self.assertIn("feature_flags", data)

    def test_contains_backup_status_key(self):
        data = self._get_diag()
        self.assertIn("backup_status", data)

    def test_contains_git_status_key(self):
        data = self._get_diag()
        self.assertIn("git_status", data)

    def test_contains_validator_status_key(self):
        data = self._get_diag()
        self.assertIn("validator_status", data)

    def test_contains_recent_audit_events_key(self):
        data = self._get_diag()
        self.assertIn("recent_audit_events", data)

    def test_contains_metrics_summary_key(self):
        data = self._get_diag()
        self.assertIn("metrics_summary", data)

    # ── Environment section ──────────────────────────────────────────────────

    def test_environment_has_timestamp(self):
        data = self._get_diag()
        env = data.get("environment", {})
        self.assertIn("timestamp", env)

    def test_environment_has_python_version(self):
        data = self._get_diag()
        env = data.get("environment", {})
        self.assertIn("python_version", env)

    def test_environment_has_platform(self):
        data = self._get_diag()
        env = data.get("environment", {})
        self.assertIn("platform", env)

    def test_environment_has_cwd(self):
        data = self._get_diag()
        env = data.get("environment", {})
        self.assertIn("cwd", env)

    # ── Component versions ───────────────────────────────────────────────────

    def test_component_versions_present(self):
        data = self._get_diag()
        cv = data.get("component_versions", {})
        expected = ["bridge_server", "structured_logger", "audit_trail",
                    "metrics_manager", "health_monitor", "backup_manager",
                    "production_validator", "telemetry"]
        for key in expected:
            self.assertIn(key, cv, f"Missing component version key: {key}")

    def test_component_versions_are_string(self):
        data = self._get_diag()
        cv = data.get("component_versions", {})
        for k, v in cv.items():
            self.assertIsInstance(v, str, f"Version for {k} should be string")

    # ── Read-only contract ────────────────────────────────────────────────────

    def test_no_writes_to_supabase(self):
        """GET /diagnostics must never POST to Supabase."""
        with patch("requests.post") as mock_post:
            self.client.get("/diagnostics")
            mock_post.assert_not_called()

    def test_does_not_run_validator(self):
        """GET /diagnostics must never instantiate ProductionValidator."""
        with patch("control.production_validator.ProductionValidator") as mock_val:
            self.client.get("/diagnostics")
            mock_val.assert_not_called()

    def test_does_not_create_backup(self):
        """GET /diagnostics must never call BackupManager.run_backup."""
        with patch("control.backup_manager.BackupManager") as mock_bkm:
            self.client.get("/diagnostics")
            if mock_bkm.called:
                # If instantiated, run_backup must not have been called
                instance = mock_bkm.return_value
                instance.run_backup.assert_not_called()

    def test_validator_status_from_cache_only(self):
        """Validator status reads from state/validator_cache.json — never runs validator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = {"score": 0.91, "status": "PASS", "warnings": 2, "failures": 1}
            cache_path = os.path.join(tmpdir, "validator_cache.json")
            with open(cache_path, "w") as f:
                json.dump(cache, f)

            with patch("os.path.exists") as mock_exists, \
                 patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(cache))):
                mock_exists.return_value = True
                # No ProductionValidator should be instantiated
                with patch("control.production_validator.ProductionValidator") as mock_val:
                    self.client.get("/diagnostics")
                    mock_val.assert_not_called()

    # ── Backup status ─────────────────────────────────────────────────────────

    def test_backup_status_has_total_backups(self):
        data = self._get_diag()
        bk = data.get("backup_status", {})
        # Either total_backups present OR an error key
        self.assertTrue("total_backups" in bk or "error" in bk)

    def test_backup_status_has_recent_backups(self):
        data = self._get_diag()
        bk = data.get("backup_status", {})
        self.assertTrue("recent_backups" in bk or "error" in bk)

    # ── Git status ────────────────────────────────────────────────────────────

    def test_git_status_has_current_branch(self):
        data = self._get_diag()
        gs = data.get("git_status", {})
        self.assertTrue("current_branch" in gs or "error" in gs)

    def test_git_status_has_last_commit(self):
        data = self._get_diag()
        gs = data.get("git_status", {})
        self.assertTrue("last_commit" in gs or "error" in gs)

    def test_git_status_is_readonly_subprocess(self):
        """Verify only read-only git commands are used (rev-parse, log)."""
        import subprocess
        called_cmds = []

        original_run = subprocess.run

        def spy_run(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git":
                called_cmds.append(cmd)
            return original_run(cmd, **kwargs)

        with patch("subprocess.run", side_effect=spy_run):
            self.client.get("/diagnostics")

        for cmd in called_cmds:
            self.assertNotIn("push", cmd)
            self.assertNotIn("commit", cmd)
            self.assertNotIn("checkout", cmd)
            self.assertNotIn("merge", cmd)
            self.assertNotIn("reset", cmd)

    # ── Error resilience ──────────────────────────────────────────────────────

    def test_returns_200_even_if_metrics_manager_fails(self):
        """All sections are best-effort — endpoint must return 200 even if subsystems fail."""
        with patch("control.metrics_manager.MetricsManager.get_metrics_report",
                   side_effect=RuntimeError("db error")):
            response = self.client.get("/diagnostics")
            self.assertEqual(response.status_code, 200)

    def test_returns_200_even_if_audit_trail_fails(self):
        with patch("control.audit_trail.AuditTrailManager.get_recent",
                   side_effect=RuntimeError("db error")):
            response = self.client.get("/diagnostics")
            self.assertEqual(response.status_code, 200)

    def test_worker_section_has_error_key_on_failure(self):
        with patch("control.metrics_manager.MetricsManager.get_metrics_report",
                   side_effect=RuntimeError("metrics down")):
            data = self._get_diag()
            worker = data.get("worker", {})
            self.assertIn("error", worker)

    # ── No auth required ─────────────────────────────────────────────────────

    def test_diagnostics_does_not_require_bearer_token(self):
        """GET /diagnostics is operational/internal — no auth by design."""
        response = self.client.get("/diagnostics")
        self.assertNotEqual(response.status_code, 401)
        self.assertNotEqual(response.status_code, 403)


class TestAuditTrailGetRecent(unittest.TestCase):
    """Test the new get_recent() method added to AuditTrailManager."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmpdir.name, "test_audit.db")

    def tearDown(self):
        import gc
        gc.collect()  # flush SQLite connection refs before Windows cleanup
        try:
            self.tmpdir.cleanup()
        except Exception:
            pass  # Windows file lock — ignore, file is in temp anyway

    def _make_trail(self):
        from control.audit_trail import AuditTrailManager
        return AuditTrailManager(db_path=self.db_path)

    def test_get_recent_returns_list(self):
        trail = self._make_trail()
        result = trail.get_recent(limit=5)
        self.assertIsInstance(result, list)

    def test_get_recent_empty_when_no_records(self):
        trail = self._make_trail()
        result = trail.get_recent(limit=10)
        self.assertEqual(result, [])

    def test_get_recent_returns_correct_count(self):
        trail = self._make_trail()
        for i in range(8):
            trail.append(f"EVENT_{i}", "STATUS", task_id=f"T-{i}", project_id="test")
        result = trail.get_recent(limit=5)
        self.assertEqual(len(result), 5)

    def test_get_recent_returns_newest_first(self):
        trail = self._make_trail()
        trail.append("EVENT_FIRST", "STATUS_A", task_id="T-first", project_id="test")
        trail.append("EVENT_LAST", "STATUS_B", task_id="T-last", project_id="test")
        result = trail.get_recent(limit=10)
        # Newest first
        self.assertEqual(result[0]["event_type"], "EVENT_LAST")
        self.assertEqual(result[1]["event_type"], "EVENT_FIRST")

    def test_get_recent_returns_expected_fields(self):
        trail = self._make_trail()
        trail.append("TEST_EVENT", "TEST_STATUS",
                     trace_id="tr-1", task_id="T-xyz", project_id="proj")
        result = trail.get_recent(limit=1)
        self.assertTrue(len(result) > 0)
        rec = result[0]
        for field in ["id", "timestamp", "event_type", "status", "task_id"]:
            self.assertIn(field, rec)

    def test_get_recent_never_crashes(self):
        """get_recent must swallow all exceptions."""
        trail = self._make_trail()
        # Break the db path
        trail.db_path = "/nonexistent/path/db.sqlite"
        try:
            result = trail.get_recent()
            self.assertIsInstance(result, list)
        except Exception as e:
            self.fail(f"get_recent raised unexpectedly: {e}")


class TestDiagnosticsRefactorRegression(unittest.TestCase):
    """Regression: Verifies that refactored dispatcher/worker/main still export correct interfaces."""

    def test_dispatcher_has_execute_task(self):
        from control.dispatcher import Dispatcher
        self.assertTrue(hasattr(Dispatcher, "execute_task"))

    def test_dispatcher_has_dispatch(self):
        from control.dispatcher import Dispatcher
        self.assertTrue(hasattr(Dispatcher, "dispatch"))

    def test_dispatcher_has_find_agent(self):
        from control.dispatcher import Dispatcher
        self.assertTrue(hasattr(Dispatcher, "find_agent"))

    def test_dispatcher_has_private_prepare_workspace(self):
        from control.dispatcher import Dispatcher
        self.assertTrue(hasattr(Dispatcher, "_prepare_workspace"))

    def test_dispatcher_has_private_run_agent(self):
        from control.dispatcher import Dispatcher
        self.assertTrue(hasattr(Dispatcher, "_run_agent"))

    def test_antigravity_worker_has_dispatch_task(self):
        from workers.antigravity_worker import AntigravityWorker
        self.assertTrue(hasattr(AntigravityWorker, "dispatch_task"))

    def test_antigravity_worker_has_validate_isolation(self):
        from workers.antigravity_worker import AntigravityWorker
        self.assertTrue(hasattr(AntigravityWorker, "validate_isolation"))

    def test_antigravity_worker_has_build_prompt(self):
        from workers.antigravity_worker import AntigravityWorker
        self.assertTrue(hasattr(AntigravityWorker, "build_prompt"))

    def test_antigravity_worker_has_private_helpers(self):
        from workers.antigravity_worker import AntigravityWorker
        for method in ["_resume_from_checkpoint", "_setup_git_branch",
                       "_create_or_resume_session", "_save_delegation_state"]:
            self.assertTrue(hasattr(AntigravityWorker, method), f"Missing: {method}")

    def test_telemetry_module_importable(self):
        from control.telemetry import log_transition
        self.assertTrue(callable(log_transition))

    def test_audit_trail_has_get_recent(self):
        from control.audit_trail import AuditTrailManager
        self.assertTrue(hasattr(AuditTrailManager, "get_recent"))

    def test_dead_files_removed(self):
        """manager.py and task_router.py must no longer exist."""
        self.assertFalse(os.path.exists("control/manager.py"))
        self.assertFalse(os.path.exists("control/task_router.py"))

    def test_dispatcher_no_longer_has_local_log_transition(self):
        """dispatcher.py must not define its own log_transition — it imports from telemetry."""
        import inspect
        from control import dispatcher
        # log_transition should NOT be a function defined in dispatcher module itself
        # (it's imported from telemetry, so its __module__ should be control.telemetry)
        lt = getattr(dispatcher, "log_transition", None)
        if lt is not None:
            self.assertEqual(lt.__module__, "control.telemetry",
                             "dispatcher.log_transition must be imported from control.telemetry")

    def test_worker_no_longer_has_local_log_transition(self):
        """antigravity_worker.py must not define its own log_transition."""
        from workers import antigravity_worker
        lt = getattr(antigravity_worker, "log_transition", None)
        if lt is not None:
            self.assertEqual(lt.__module__, "control.telemetry",
                             "antigravity_worker.log_transition must be from control.telemetry")


if __name__ == "__main__":
    unittest.main()
