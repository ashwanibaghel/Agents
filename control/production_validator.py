"""
control/production_validator.py

Production Readiness Validator for Ashwani Agent Company.

Inspects 9 independent sections and produces a readiness score.
Sections: Configuration, Logging, Persistence, Recovery, Observability,
          Git, Security, Testing, Documentation

Output per finding:
  - category
  - severity  (PASS / WARNING / FAIL)
  - message
  - recommendation
"""

import os
import sys
import subprocess
import sqlite3
import yaml
import json
from typing import List, Dict, Any, Tuple


class ProductionValidator:
    """
    Validates system production readiness across 9 sections.
    Safe to call at any time — read-only, never modifies state.
    """

    SECTION_WEIGHTS = {
        "Configuration":    15,
        "Logging":          10,
        "Persistence":      15,
        "Recovery":         15,
        "Observability":    10,
        "Git":              10,
        "Security":         10,
        "Testing":          10,
        "Documentation":     5,
    }

    def __init__(self, project_root: str = "."):
        self.project_root = os.path.abspath(project_root)

    def run(self) -> Dict[str, Any]:
        """
        Run all validation checks.
        Returns a structured report dict.
        """
        section_results: Dict[str, List[Dict]] = {}

        section_results["Configuration"]   = self._check_configuration()
        section_results["Logging"]         = self._check_logging()
        section_results["Persistence"]     = self._check_persistence()
        section_results["Recovery"]        = self._check_recovery()
        section_results["Observability"]   = self._check_observability()
        section_results["Git"]             = self._check_git()
        section_results["Security"]        = self._check_security()
        section_results["Testing"]         = self._check_testing()
        section_results["Documentation"]   = self._check_documentation()

        section_scores = self._compute_section_scores(section_results)
        overall_score  = self._compute_overall_score(section_scores)
        overall_status = self._score_to_status(overall_score)

        return {
            "overall_score":  overall_score,
            "overall_status": overall_status,
            "section_scores": section_scores,
            "sections":       section_results,
        }

    # ─────────────────────────────────────────────────────────
    # Section checks
    # ─────────────────────────────────────────────────────────

    def _check_configuration(self) -> List[Dict]:
        findings = []

        # Python version
        if sys.version_info >= (3, 10):
            findings.append(self._finding("Configuration", "PASS",
                f"Python version: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "No action required."))
        elif sys.version_info >= (3, 8):
            findings.append(self._finding("Configuration", "WARNING",
                f"Python {sys.version_info.major}.{sys.version_info.minor} — 3.10+ recommended.",
                "Upgrade to Python 3.10+ for best compatibility."))
        else:
            findings.append(self._finding("Configuration", "FAIL",
                f"Python {sys.version_info.major}.{sys.version_info.minor} is too old (< 3.8).",
                "Upgrade to Python 3.10+."))

        # projects.yaml
        projects_path = self._path("config/projects.yaml")
        if os.path.exists(projects_path):
            try:
                with open(projects_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if "projects" in cfg:
                    findings.append(self._finding("Configuration", "PASS",
                        "config/projects.yaml present with valid schema.",
                        "No action required."))
                else:
                    findings.append(self._finding("Configuration", "FAIL",
                        "config/projects.yaml missing 'projects' root key.",
                        "Add 'projects:' root key to config/projects.yaml."))
            except Exception as e:
                findings.append(self._finding("Configuration", "FAIL",
                    f"config/projects.yaml parse error: {e}",
                    "Fix YAML syntax in config/projects.yaml."))
        else:
            findings.append(self._finding("Configuration", "FAIL",
                "config/projects.yaml not found.",
                "Create config/projects.yaml with 'projects:' root key."))

        # feature_flags.yaml
        ff_path = self._path("config/feature_flags.yaml")
        if os.path.exists(ff_path):
            try:
                with open(ff_path, encoding="utf-8") as f:
                    ff = yaml.safe_load(f) or {}
                version = ff.get("config_version")
                if version:
                    findings.append(self._finding("Configuration", "PASS",
                        f"feature_flags.yaml present. Config version: {version}.",
                        "No action required."))
                else:
                    findings.append(self._finding("Configuration", "WARNING",
                        "feature_flags.yaml missing config_version field.",
                        "Add 'config_version: X.Y.Z' to feature_flags.yaml."))
            except Exception as e:
                findings.append(self._finding("Configuration", "FAIL",
                    f"feature_flags.yaml parse error: {e}",
                    "Fix YAML syntax in feature_flags.yaml."))
        else:
            findings.append(self._finding("Configuration", "FAIL",
                "config/feature_flags.yaml not found.",
                "Create config/feature_flags.yaml."))

        # supabase.yaml
        sb_path = self._path("config/supabase.yaml")
        if os.path.exists(sb_path):
            findings.append(self._finding("Configuration", "PASS",
                "config/supabase.yaml present.",
                "No action required."))
        else:
            findings.append(self._finding("Configuration", "WARNING",
                "config/supabase.yaml not found.",
                "Create config/supabase.yaml if Supabase integration is required."))

        # Workspace folder
        ws_dir = self._path("workspaces")
        if os.path.exists(ws_dir) and os.path.isdir(ws_dir):
            findings.append(self._finding("Configuration", "PASS",
                f"workspaces/ directory exists: {ws_dir}",
                "No action required."))
        else:
            findings.append(self._finding("Configuration", "WARNING",
                "workspaces/ directory does not exist.",
                "Create workspaces/ directory or run the worker at least once."))

        return findings

    def _check_logging(self) -> List[Dict]:
        findings = []

        logger_path = self._path("control/structured_logger.py")
        if os.path.exists(logger_path):
            findings.append(self._finding("Logging", "PASS",
                "control/structured_logger.py exists.",
                "No action required."))
        else:
            findings.append(self._finding("Logging", "FAIL",
                "control/structured_logger.py missing.",
                "Create structured logger module."))

        # Check feature flag
        ff_path = self._path("config/feature_flags.yaml")
        if os.path.exists(ff_path):
            try:
                with open(ff_path, encoding="utf-8") as f:
                    ff = yaml.safe_load(f) or {}
                flags = ff.get("feature_flags", {})
                if flags.get("structured_logging", False):
                    findings.append(self._finding("Logging", "PASS",
                        "structured_logging feature flag is ENABLED.",
                        "No action required."))
                else:
                    findings.append(self._finding("Logging", "WARNING",
                        "structured_logging feature flag is disabled.",
                        "Enable structured_logging in feature_flags.yaml for production."))
            except Exception:
                pass

        # Check logs directory
        logs_dir = self._path("logs")
        if os.path.exists(logs_dir):
            findings.append(self._finding("Logging", "PASS",
                "logs/ directory exists.",
                "No action required."))
        else:
            findings.append(self._finding("Logging", "WARNING",
                "logs/ directory does not exist.",
                "Run the worker once — logger will create it automatically."))

        return findings

    def _check_persistence(self) -> List[Dict]:
        findings = []

        # state/ directory
        state_dir = self._path("state")
        if os.path.exists(state_dir):
            findings.append(self._finding("Persistence", "PASS",
                "state/ directory exists.",
                "No action required."))
        else:
            findings.append(self._finding("Persistence", "FAIL",
                "state/ directory missing.",
                "Create state/ directory."))

        # task_checkpoints.db
        db_path = self._path("state/task_checkpoints.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()]
                conn.close()
                expected = {"project_sessions", "audit_trail", "task_metrics"}
                present  = expected.intersection(set(tables))
                missing  = expected - set(tables)
                if not missing:
                    findings.append(self._finding("Persistence", "PASS",
                        f"task_checkpoints.db healthy. Tables: {', '.join(sorted(tables))}",
                        "No action required."))
                else:
                    findings.append(self._finding("Persistence", "WARNING",
                        f"task_checkpoints.db missing tables: {', '.join(sorted(missing))}",
                        "Run the worker once to auto-create missing schema tables."))
            except Exception as e:
                findings.append(self._finding("Persistence", "FAIL",
                    f"task_checkpoints.db error: {e}",
                    "Check SQLite DB integrity."))
        else:
            findings.append(self._finding("Persistence", "WARNING",
                "state/task_checkpoints.db does not exist yet.",
                "Run the worker once to initialize the database."))

        # worker_id.txt
        worker_id_path = self._path("state/worker_id.txt")
        if os.path.exists(worker_id_path):
            findings.append(self._finding("Persistence", "PASS",
                "state/worker_id.txt present (persistent worker identity).",
                "No action required."))
        else:
            findings.append(self._finding("Persistence", "WARNING",
                "state/worker_id.txt missing.",
                "Run the worker once to generate a persistent worker identity."))

        # Audit trail check
        audit_trail_path = self._path("control/audit_trail.py")
        if os.path.exists(audit_trail_path):
            findings.append(self._finding("Persistence", "PASS",
                "control/audit_trail.py (immutable audit trail) present.",
                "No action required."))
        else:
            findings.append(self._finding("Persistence", "FAIL",
                "control/audit_trail.py missing.",
                "Restore audit_trail.py from Sprint 3."))

        return findings

    def _check_recovery(self) -> List[Dict]:
        findings = []

        # backup_manager.py
        bm_path = self._path("control/backup_manager.py")
        if os.path.exists(bm_path):
            findings.append(self._finding("Recovery", "PASS",
                "control/backup_manager.py exists.",
                "No action required."))
        else:
            findings.append(self._finding("Recovery", "FAIL",
                "control/backup_manager.py missing.",
                "Create backup_manager.py (Sprint 5)."))

        # backups/ directory and backups present
        backup_dir = self._path("state/backups")
        if os.path.exists(backup_dir):
            subdirs = [d for d in os.listdir(backup_dir)
                       if os.path.isdir(os.path.join(backup_dir, d))]
            if subdirs:
                findings.append(self._finding("Recovery", "PASS",
                    f"state/backups/ has {len(subdirs)} backup(s).",
                    "No action required."))
            else:
                findings.append(self._finding("Recovery", "WARNING",
                    "state/backups/ exists but contains no backups.",
                    "Run a backup: from control.backup_manager import BackupManager; BackupManager().run_backup()"))
        else:
            findings.append(self._finding("Recovery", "WARNING",
                "state/backups/ directory does not exist.",
                "Create a backup first: BackupManager().run_backup()"))

        # feature flag
        ff_path = self._path("config/feature_flags.yaml")
        if os.path.exists(ff_path):
            try:
                with open(ff_path, encoding="utf-8") as f:
                    ff = yaml.safe_load(f) or {}
                if ff.get("feature_flags", {}).get("backup", False):
                    findings.append(self._finding("Recovery", "PASS",
                        "backup feature flag is ENABLED.",
                        "No action required."))
                else:
                    findings.append(self._finding("Recovery", "WARNING",
                        "backup feature flag is disabled in feature_flags.yaml.",
                        "Set backup: true in feature_flags.yaml."))
            except Exception:
                pass

        return findings

    def _check_observability(self) -> List[Dict]:
        findings = []

        for fname, label in [
            ("control/health_monitor.py",  "Health Monitor"),
            ("control/metrics_manager.py", "Metrics Manager"),
            ("control/audit_trail.py",     "Audit Trail"),
        ]:
            fpath = self._path(fname)
            if os.path.exists(fpath):
                findings.append(self._finding("Observability", "PASS",
                    f"{label} ({fname}) present.",
                    "No action required."))
            else:
                findings.append(self._finding("Observability", "FAIL",
                    f"{label} ({fname}) missing.",
                    f"Restore {fname}."))

        # Metrics feature flag
        ff_path = self._path("config/feature_flags.yaml")
        if os.path.exists(ff_path):
            try:
                with open(ff_path, encoding="utf-8") as f:
                    ff = yaml.safe_load(f) or {}
                if ff.get("feature_flags", {}).get("metrics", False):
                    findings.append(self._finding("Observability", "PASS",
                        "metrics feature flag is ENABLED.",
                        "No action required."))
                else:
                    findings.append(self._finding("Observability", "WARNING",
                        "metrics feature flag is disabled.",
                        "Enable metrics in feature_flags.yaml."))
            except Exception:
                pass

        return findings

    def _check_git(self) -> List[Dict]:
        findings = []

        # Git executable
        try:
            result = subprocess.run(
                ["git", "--version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=5
            )
            version = result.stdout.decode(errors="replace").strip()
            findings.append(self._finding("Git", "PASS",
                f"Git available: {version}",
                "No action required."))
        except Exception as e:
            findings.append(self._finding("Git", "FAIL",
                f"Git executable not found: {e}",
                "Install Git and add to PATH."))

        # auto_push flag
        ff_path = self._path("config/feature_flags.yaml")
        if os.path.exists(ff_path):
            try:
                with open(ff_path, encoding="utf-8") as f:
                    ff = yaml.safe_load(f) or {}
                if ff.get("feature_flags", {}).get("auto_push", False):
                    findings.append(self._finding("Git", "PASS",
                        "auto_push feature flag is ENABLED.",
                        "No action required."))
                else:
                    findings.append(self._finding("Git", "WARNING",
                        "auto_push feature flag is disabled.",
                        "Enable auto_push for full Git lifecycle automation."))
            except Exception:
                pass

        # workspace manager
        ws_manager_path = self._path("control/workspace_manager.py")
        if os.path.exists(ws_manager_path):
            findings.append(self._finding("Git", "PASS",
                "control/workspace_manager.py present.",
                "No action required."))
        else:
            findings.append(self._finding("Git", "FAIL",
                "control/workspace_manager.py missing.",
                "Restore workspace_manager.py."))

        return findings

    def _check_security(self) -> List[Dict]:
        findings = []

        # BRIDGE_TOKEN env
        bridge_token = os.environ.get("BRIDGE_TOKEN")
        if bridge_token:
            findings.append(self._finding("Security", "PASS",
                "BRIDGE_TOKEN environment variable is set.",
                "No action required."))
        else:
            findings.append(self._finding("Security", "FAIL",
                "BRIDGE_TOKEN environment variable is not set.",
                "Set BRIDGE_TOKEN before starting the bridge server."))

        # .env or secrets in repo (basic check)
        for danger in [".env", "secrets.yaml", "secrets.json"]:
            danger_path = self._path(danger)
            if os.path.exists(danger_path):
                findings.append(self._finding("Security", "WARNING",
                    f"Potential secrets file found in project root: {danger}",
                    f"Ensure {danger} is in .gitignore and never committed to Git."))

        # .gitignore
        gitignore_path = self._path(".gitignore")
        if os.path.exists(gitignore_path):
            findings.append(self._finding("Security", "PASS",
                ".gitignore present.",
                "No action required."))
        else:
            findings.append(self._finding("Security", "WARNING",
                ".gitignore missing.",
                "Add .gitignore to prevent secrets and virtual environments from being committed."))

        # state/ in .gitignore
        if os.path.exists(gitignore_path):
            with open(gitignore_path, encoding="utf-8") as f:
                gi_content = f.read()
            if "state/" in gi_content:
                findings.append(self._finding("Security", "PASS",
                    "state/ directory is in .gitignore.",
                    "No action required."))
            else:
                findings.append(self._finding("Security", "WARNING",
                    "state/ is NOT in .gitignore.",
                    "Add 'state/' to .gitignore to prevent database files from being committed."))

        return findings

    def _check_testing(self) -> List[Dict]:
        findings = []

        tests_dir = self._path("tests")
        if os.path.exists(tests_dir):
            test_files = [f for f in os.listdir(tests_dir) if f.startswith("test_") and f.endswith(".py")]
            if test_files:
                findings.append(self._finding("Testing", "PASS",
                    f"tests/ directory has {len(test_files)} test file(s): {', '.join(sorted(test_files))}",
                    "No action required."))
            else:
                findings.append(self._finding("Testing", "FAIL",
                    "tests/ directory exists but has no test files.",
                    "Add unit tests to tests/ directory."))
        else:
            findings.append(self._finding("Testing", "FAIL",
                "tests/ directory missing.",
                "Create tests/ directory and add unit tests."))

        # Expected test modules
        for expected_test in [
            "test_backup_manager.py",
            "test_health_monitor.py",
            "test_metrics_manager.py",
        ]:
            tp = self._path(f"tests/{expected_test}")
            if os.path.exists(tp):
                findings.append(self._finding("Testing", "PASS",
                    f"{expected_test} present.",
                    "No action required."))
            else:
                findings.append(self._finding("Testing", "WARNING",
                    f"{expected_test} missing.",
                    f"Add unit tests for this module: {expected_test}"))

        return findings

    def _check_documentation(self) -> List[Dict]:
        findings = []

        readme_path = self._path("README.md")
        if os.path.exists(readme_path):
            findings.append(self._finding("Documentation", "PASS",
                "README.md present.",
                "No action required."))
        else:
            findings.append(self._finding("Documentation", "WARNING",
                "README.md missing.",
                "Add README.md with project overview, setup instructions, and usage."))

        changelog_exists = any(
            os.path.exists(self._path(f)) for f in ["CHANGELOG.md", "CHANGES.md", "HISTORY.md"]
        )
        if changelog_exists:
            findings.append(self._finding("Documentation", "PASS",
                "CHANGELOG.md or equivalent present.",
                "No action required."))
        else:
            findings.append(self._finding("Documentation", "WARNING",
                "CHANGELOG.md missing.",
                "Add CHANGELOG.md to track version history."))

        return findings

    # ─────────────────────────────────────────────────────────
    # Scoring
    # ─────────────────────────────────────────────────────────

    def _compute_section_scores(self, sections: Dict[str, List[Dict]]) -> Dict[str, Dict]:
        scores = {}
        for section, findings in sections.items():
            if not findings:
                scores[section] = {"score": 100, "status": "PASS", "findings": findings}
                continue
            total  = len(findings)
            passes = sum(1 for f in findings if f["severity"] == "PASS")
            warns  = sum(1 for f in findings if f["severity"] == "WARNING")
            fails  = sum(1 for f in findings if f["severity"] == "FAIL")

            # Scoring: PASS=1.0, WARNING=0.5, FAIL=0.0
            raw_score = (passes * 1.0 + warns * 0.5) / total * 100
            score     = round(raw_score)

            if fails > 0:
                status = "FAIL"
            elif warns > 0:
                status = "WARNING"
            else:
                status = "PASS"

            scores[section] = {
                "score":    score,
                "status":   status,
                "findings": findings,
            }
        return scores

    def _compute_overall_score(self, section_scores: Dict[str, Dict]) -> int:
        total_weight = sum(self.SECTION_WEIGHTS.values())
        weighted_sum = sum(
            section_scores[sec]["score"] * weight
            for sec, weight in self.SECTION_WEIGHTS.items()
            if sec in section_scores
        )
        return round(weighted_sum / total_weight)

    def _score_to_status(self, score: int) -> str:
        if score >= 90:
            return "PASS"
        elif score >= 70:
            return "WARNING"
        else:
            return "FAIL"

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def _path(self, rel: str) -> str:
        return os.path.join(self.project_root, rel)

    def _finding(self, category: str, severity: str, message: str, recommendation: str) -> Dict:
        return {
            "category":       category,
            "severity":       severity,
            "message":        message,
            "recommendation": recommendation,
        }
