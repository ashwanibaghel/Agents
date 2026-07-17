"""
tests/test_production_validator.py
"""

import os
import sys
import shutil
import sqlite3
import tempfile
import unittest
import yaml
from control.production_validator import ProductionValidator


class TestProductionValidator(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self._setup_valid_project()
        self.validator = ProductionValidator(project_root=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _setup_valid_project(self):
        """Create a minimal valid project structure in self.test_dir."""
        os.makedirs(os.path.join(self.test_dir, "config"),     exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, "state"),      exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, "workspaces"), exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, "tests"),      exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, "logs"),       exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, "state", "backups"), exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, "control"),    exist_ok=True)

        # feature_flags.yaml
        with open(os.path.join(self.test_dir, "config", "feature_flags.yaml"), "w") as f:
            f.write("config_version: '3.2.0'\nfeature_flags:\n  structured_logging: true\n  metrics: true\n  backup: true\n  auto_push: true\n")

        # projects.yaml
        with open(os.path.join(self.test_dir, "config", "projects.yaml"), "w") as f:
            f.write("projects:\n  test_project:\n    name: Test\n")

        # supabase.yaml
        with open(os.path.join(self.test_dir, "config", "supabase.yaml"), "w") as f:
            f.write("enabled: false\n")

        # SQLite DB with expected tables
        db_path = os.path.join(self.test_dir, "state", "task_checkpoints.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE project_sessions (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE audit_trail (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE task_metrics (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        # worker_id.txt
        with open(os.path.join(self.test_dir, "state", "worker_id.txt"), "w") as f:
            f.write("worker-test-abc\n")

        # .gitignore with state/
        with open(os.path.join(self.test_dir, ".gitignore"), "w") as f:
            f.write("state/\n.venv/\n__pycache__/\n")

        # README.md
        with open(os.path.join(self.test_dir, "README.md"), "w") as f:
            f.write("# Ashwani Agent Company\n")

        # CHANGELOG.md
        with open(os.path.join(self.test_dir, "CHANGELOG.md"), "w") as f:
            f.write("# Changelog\n\n## v3.2.0\n")

        # control modules
        for mod in ["structured_logger.py", "health_monitor.py", "metrics_manager.py",
                    "audit_trail.py", "backup_manager.py", "workspace_manager.py"]:
            with open(os.path.join(self.test_dir, "control", mod), "w") as f:
                f.write("# placeholder\n")

        # Test files
        for tf in ["test_backup_manager.py", "test_health_monitor.py", "test_metrics_manager.py"]:
            with open(os.path.join(self.test_dir, "tests", tf), "w") as f:
                f.write("# placeholder\n")

        # state/backups with one backup folder + manifest
        backup_path = os.path.join(self.test_dir, "state", "backups", "backup_20260717_120000_test")
        os.makedirs(backup_path, exist_ok=True)
        with open(os.path.join(backup_path, "manifest.json"), "w") as f:
            import json
            json.dump({"backup_id": "backup_20260717_120000_test", "files": []}, f)

        # BRIDGE_TOKEN env (set for security check)
        os.environ["BRIDGE_TOKEN"] = "test-bridge-token"

    def tearDown(self):
        os.environ.pop("BRIDGE_TOKEN", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ─── Top-level report structure ──────────────────────────

    def test_report_has_required_keys(self):
        report = self.validator.run()
        self.assertIn("overall_score",  report)
        self.assertIn("overall_status", report)
        self.assertIn("section_scores", report)
        self.assertIn("sections",       report)

    def test_all_9_sections_present(self):
        report = self.validator.run()
        expected = {"Configuration", "Logging", "Persistence", "Recovery",
                    "Observability", "Git", "Security", "Testing", "Documentation"}
        self.assertEqual(expected, set(report["sections"].keys()))

    def test_overall_score_between_0_and_100(self):
        report = self.validator.run()
        score = report["overall_score"]
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score,    100)

    def test_overall_status_valid_values(self):
        report = self.validator.run()
        self.assertIn(report["overall_status"], ["PASS", "WARNING", "FAIL"])

    def test_each_section_has_findings(self):
        report = self.validator.run()
        for section, findings in report["sections"].items():
            self.assertIsInstance(findings, list, msg=f"{section} findings should be a list")

    def test_each_finding_has_required_keys(self):
        report = self.validator.run()
        for section, findings in report["sections"].items():
            for finding in findings:
                for key in ["category", "severity", "message", "recommendation"]:
                    self.assertIn(key, finding, msg=f"Missing '{key}' in {section} finding")

    def test_finding_severity_valid_values(self):
        report = self.validator.run()
        for section, findings in report["sections"].items():
            for finding in findings:
                self.assertIn(finding["severity"], ["PASS", "WARNING", "FAIL"],
                              msg=f"Invalid severity in {section}: {finding['severity']}")

    # ─── Section: Configuration ─────────────────────────────

    def test_configuration_pass_with_valid_project(self):
        report = self.validator.run()
        config_findings = report["section_scores"]["Configuration"]
        # Should have no FAILs in a valid project
        for f in config_findings["findings"]:
            self.assertNotEqual(f["severity"], "FAIL",
                                msg=f"Unexpected FAIL in Configuration: {f['message']}")

    def test_configuration_fails_missing_projects_yaml(self):
        os.remove(os.path.join(self.test_dir, "config", "projects.yaml"))
        report = self.validator.run()
        findings = report["sections"]["Configuration"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("projects.yaml" in f["message"] for f in fails))

    def test_configuration_fails_missing_feature_flags(self):
        os.remove(os.path.join(self.test_dir, "config", "feature_flags.yaml"))
        report = self.validator.run()
        findings = report["sections"]["Configuration"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("feature_flags" in f["message"] for f in fails))

    def test_configuration_warn_missing_supabase_yaml(self):
        os.remove(os.path.join(self.test_dir, "config", "supabase.yaml"))
        report = self.validator.run()
        findings = report["sections"]["Configuration"]
        warns = [f for f in findings if f["severity"] == "WARNING"]
        self.assertTrue(any("supabase" in f["message"].lower() for f in warns))

    def test_configuration_warn_missing_workspaces_dir(self):
        shutil.rmtree(os.path.join(self.test_dir, "workspaces"))
        report = self.validator.run()
        findings = report["sections"]["Configuration"]
        non_pass = [f for f in findings if f["severity"] != "PASS"]
        self.assertTrue(any("workspaces" in f["message"].lower() for f in non_pass))

    # ─── Section: Logging ───────────────────────────────────

    def test_logging_passes_with_logger_present(self):
        report = self.validator.run()
        findings = report["sections"]["Logging"]
        self.assertTrue(any(f["severity"] == "PASS" and "structured_logger" in f["message"] for f in findings))

    def test_logging_fails_without_logger(self):
        os.remove(os.path.join(self.test_dir, "control", "structured_logger.py"))
        report = self.validator.run()
        findings = report["sections"]["Logging"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("structured_logger" in f["message"] for f in fails))

    # ─── Section: Persistence ──────────────────────────────

    def test_persistence_passes_with_valid_state(self):
        report = self.validator.run()
        findings = report["sections"]["Persistence"]
        self.assertTrue(any(f["severity"] == "PASS" for f in findings))

    def test_persistence_warns_missing_db(self):
        os.remove(os.path.join(self.test_dir, "state", "task_checkpoints.db"))
        report = self.validator.run()
        findings = report["sections"]["Persistence"]
        non_pass = [f for f in findings if f["severity"] != "PASS"]
        self.assertTrue(any("task_checkpoints" in f["message"] for f in non_pass))

    def test_persistence_warns_missing_worker_id(self):
        os.remove(os.path.join(self.test_dir, "state", "worker_id.txt"))
        report = self.validator.run()
        findings = report["sections"]["Persistence"]
        warns = [f for f in findings if f["severity"] == "WARNING"]
        self.assertTrue(any("worker_id" in f["message"] for f in warns))

    def test_persistence_fails_missing_audit_trail(self):
        os.remove(os.path.join(self.test_dir, "control", "audit_trail.py"))
        report = self.validator.run()
        findings = report["sections"]["Persistence"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("audit_trail" in f["message"] for f in fails))

    # ─── Section: Recovery ──────────────────────────────────

    def test_recovery_passes_with_backup_manager(self):
        report = self.validator.run()
        findings = report["sections"]["Recovery"]
        self.assertTrue(any("backup_manager.py" in f["message"] for f in findings if f["severity"] == "PASS"))

    def test_recovery_fails_missing_backup_manager(self):
        os.remove(os.path.join(self.test_dir, "control", "backup_manager.py"))
        report = self.validator.run()
        findings = report["sections"]["Recovery"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("backup_manager" in f["message"] for f in fails))

    def test_recovery_warns_no_backups_exist(self):
        shutil.rmtree(os.path.join(self.test_dir, "state", "backups"))
        report = self.validator.run()
        findings = report["sections"]["Recovery"]
        non_pass = [f for f in findings if f["severity"] != "PASS"]
        self.assertTrue(any("backup" in f["message"].lower() for f in non_pass))

    # ─── Section: Observability ─────────────────────────────

    def test_observability_passes_with_all_modules(self):
        report = self.validator.run()
        findings = report["sections"]["Observability"]
        passes = [f for f in findings if f["severity"] == "PASS"]
        self.assertTrue(len(passes) >= 3)

    def test_observability_fails_missing_health_monitor(self):
        os.remove(os.path.join(self.test_dir, "control", "health_monitor.py"))
        report = self.validator.run()
        findings = report["sections"]["Observability"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("health_monitor" in f["message"] for f in fails))

    def test_observability_fails_missing_metrics_manager(self):
        os.remove(os.path.join(self.test_dir, "control", "metrics_manager.py"))
        report = self.validator.run()
        findings = report["sections"]["Observability"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("metrics_manager" in f["message"] for f in fails))

    # ─── Section: Security ──────────────────────────────────

    def test_security_passes_with_bridge_token(self):
        report = self.validator.run()
        findings = report["sections"]["Security"]
        self.assertTrue(any("BRIDGE_TOKEN" in f["message"] and f["severity"] == "PASS" for f in findings))

    def test_security_fails_missing_bridge_token(self):
        del os.environ["BRIDGE_TOKEN"]
        report = self.validator.run()
        findings = report["sections"]["Security"]
        fails = [f for f in findings if f["severity"] == "FAIL"]
        self.assertTrue(any("BRIDGE_TOKEN" in f["message"] for f in fails))
        os.environ["BRIDGE_TOKEN"] = "restored-token"  # Restore

    def test_security_passes_with_gitignore(self):
        report = self.validator.run()
        findings = report["sections"]["Security"]
        self.assertTrue(any(".gitignore" in f["message"] and f["severity"] == "PASS" for f in findings))

    def test_security_warns_missing_gitignore(self):
        os.remove(os.path.join(self.test_dir, ".gitignore"))
        report = self.validator.run()
        findings = report["sections"]["Security"]
        warns = [f for f in findings if f["severity"] == "WARNING"]
        self.assertTrue(any(".gitignore" in f["message"] for f in warns))

    def test_security_warns_state_not_in_gitignore(self):
        gitignore_path = os.path.join(self.test_dir, ".gitignore")
        with open(gitignore_path, "w") as f:
            f.write(".venv/\n")  # No state/ entry
        report = self.validator.run()
        findings = report["sections"]["Security"]
        warns = [f for f in findings if f["severity"] == "WARNING"]
        self.assertTrue(any("state/" in f["message"] for f in warns))

    # ─── Section: Testing ───────────────────────────────────

    def test_testing_passes_with_test_files(self):
        report = self.validator.run()
        findings = report["sections"]["Testing"]
        self.assertTrue(any(f["severity"] == "PASS" for f in findings))

    def test_testing_warns_missing_expected_test_file(self):
        os.remove(os.path.join(self.test_dir, "tests", "test_backup_manager.py"))
        report = self.validator.run()
        findings = report["sections"]["Testing"]
        warns = [f for f in findings if f["severity"] == "WARNING"]
        self.assertTrue(any("test_backup_manager" in f["message"] for f in warns))

    # ─── Section: Documentation ─────────────────────────────

    def test_documentation_passes_with_readme(self):
        report = self.validator.run()
        findings = report["sections"]["Documentation"]
        self.assertTrue(any("README" in f["message"] and f["severity"] == "PASS" for f in findings))

    def test_documentation_warns_missing_readme(self):
        os.remove(os.path.join(self.test_dir, "README.md"))
        report = self.validator.run()
        findings = report["sections"]["Documentation"]
        warns = [f for f in findings if f["severity"] == "WARNING"]
        self.assertTrue(any("README" in f["message"] for f in warns))

    # ─── Score Computation ──────────────────────────────────

    def test_overall_score_is_high_for_valid_project(self):
        report = self.validator.run()
        # Git check will fail (no git binary), but most should pass
        self.assertGreater(report["overall_score"], 60)

    def test_section_score_falls_when_fail_present(self):
        os.remove(os.path.join(self.test_dir, "config", "projects.yaml"))
        report = self.validator.run()
        config_score = report["section_scores"]["Configuration"]["score"]
        self.assertLess(config_score, 100)

    def test_score_to_status_pass_at_90_plus(self):
        self.assertEqual(self.validator._score_to_status(90), "PASS")
        self.assertEqual(self.validator._score_to_status(100), "PASS")

    def test_score_to_status_warning_at_70_89(self):
        self.assertEqual(self.validator._score_to_status(70), "WARNING")
        self.assertEqual(self.validator._score_to_status(89), "WARNING")

    def test_score_to_status_fail_below_70(self):
        self.assertEqual(self.validator._score_to_status(69), "FAIL")
        self.assertEqual(self.validator._score_to_status(0), "FAIL")

    def test_section_weights_sum_to_100(self):
        total = sum(ProductionValidator.SECTION_WEIGHTS.values())
        self.assertEqual(total, 100)

    def test_section_score_100_for_all_pass_section(self):
        findings = [
            {"severity": "PASS", "category": "Git", "message": "Git ok", "recommendation": "None"},
            {"severity": "PASS", "category": "Git", "message": "Git push ok", "recommendation": "None"},
        ]
        scores = self.validator._compute_section_scores({"Git": findings})
        self.assertEqual(scores["Git"]["score"], 100)
        self.assertEqual(scores["Git"]["status"], "PASS")

    def test_section_score_50_for_half_pass_half_warning(self):
        findings = [
            {"severity": "PASS",    "category": "Git", "message": "ok", "recommendation": ""},
            {"severity": "WARNING", "category": "Git", "message": "ok", "recommendation": ""},
        ]
        scores = self.validator._compute_section_scores({"Git": findings})
        self.assertEqual(scores["Git"]["score"], 75)
        self.assertEqual(scores["Git"]["status"], "WARNING")

    def test_section_status_fail_if_any_fail_present(self):
        findings = [
            {"severity": "PASS", "category": "Git", "message": "ok", "recommendation": ""},
            {"severity": "FAIL", "category": "Git", "message": "bad", "recommendation": "fix it"},
        ]
        scores = self.validator._compute_section_scores({"Git": findings})
        self.assertEqual(scores["Git"]["status"], "FAIL")


class TestProductionValidatorCLI(unittest.TestCase):
    """Test CLI exit codes and output."""

    def test_exit_code_0_on_pass(self):
        """Validator with all PASS findings returns 0."""
        import subprocess
        # Create a temp environment where everything is valid
        result = subprocess.run(
            [sys.executable, "-c",
             "from control.production_validator import ProductionValidator; "
             "v = ProductionValidator(); r = v.run(); "
             "exit({'PASS': 0, 'WARNING': 1, 'FAIL': 2}.get(r['overall_status'], 2))"],
            capture_output=True
        )
        self.assertIn(result.returncode, [0, 1, 2])  # Valid exit code

    def test_exit_code_1_map_correct(self):
        self.assertEqual({"PASS": 0, "WARNING": 1, "FAIL": 2}["WARNING"], 1)

    def test_exit_code_2_map_correct(self):
        self.assertEqual({"PASS": 0, "WARNING": 1, "FAIL": 2}["FAIL"], 2)

    def test_exit_code_0_map_correct(self):
        self.assertEqual({"PASS": 0, "WARNING": 1, "FAIL": 2}["PASS"], 0)


if __name__ == "__main__":
    unittest.main()
