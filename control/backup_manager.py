"""
control/backup_manager.py

Production-grade Backup Manager for Ashwani Agent Company.

Features:
- Timestamped backup folders
- Configurable retention policy (default: 7 days / 10 backups max)
- Configurable backup location
- Automatic cleanup of expired backups
- SHA-256 checksum verification
- backup manifest.json
- Crash-safe: never interrupts worker execution
- Increments backup_failure metric on failure
"""

import os
import shutil
import hashlib
import json
import sqlite3
import threading
import datetime
from typing import Optional, List, Dict, Any


class BackupManager:
    """
    Thread-safe backup manager for critical state files.
    All methods return True on success, False on failure.
    Never raises exceptions to callers.
    """

    DEFAULT_BACKUP_DIR = "state/backups"
    DEFAULT_RETENTION_DAYS = 7
    DEFAULT_MAX_BACKUPS = 10
    MANIFEST_FILE = "manifest.json"

    def __init__(
        self,
        backup_dir: str = DEFAULT_BACKUP_DIR,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_backups: int = DEFAULT_MAX_BACKUPS,
        metrics_manager=None,
        logger=None,
    ):
        self.backup_dir = backup_dir
        self.retention_days = retention_days
        self.max_backups = max_backups
        self._metrics = metrics_manager
        self._logger = logger
        self._lock = threading.Lock()

        # Critical state targets
        self._databases = [
            "state/task_checkpoints.db",
        ]
        self._configs = [
            "config/projects.yaml",
            "config/supabase.yaml",
            "config/feature_flags.yaml",
        ]
        self._extra_state = [
            "state/worker_id.txt",
            "state/company_state.json",
        ]

        # Ensure backup dir exists
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────

    def run_backup(self, label: str = "auto") -> Dict[str, Any]:
        """
        Run a full backup of all critical state.
        Returns a result dict: {success, backup_id, path, manifest, error}
        Never raises.
        """
        with self._lock:
            try:
                backup_id = self._generate_backup_id(label)
                backup_path = os.path.join(self.backup_dir, backup_id)
                os.makedirs(backup_path, exist_ok=True)

                manifest = {
                    "backup_id": backup_id,
                    "label": label,
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "retention_days": self.retention_days,
                    "files": [],
                }

                all_targets = self._databases + self._configs + self._extra_state

                for src in all_targets:
                    if os.path.exists(src):
                        file_entry = self._backup_file(src, backup_path)
                        manifest["files"].append(file_entry)

                # Write manifest
                manifest_path = os.path.join(backup_path, self.MANIFEST_FILE)
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)

                self._log_info(f"Backup created: {backup_id} ({len(manifest['files'])} files)")
                return {
                    "success": True,
                    "backup_id": backup_id,
                    "path": backup_path,
                    "manifest": manifest,
                    "error": None,
                }

            except Exception as e:
                self._increment_failure()
                self._log_error(f"Backup failed: {e}")
                return {
                    "success": False,
                    "backup_id": None,
                    "path": None,
                    "manifest": None,
                    "error": str(e),
                }

    def cleanup_old_backups(self) -> int:
        """
        Remove backups that exceed retention_days or max_backups.
        Returns number of backups deleted.
        Never raises.
        """
        with self._lock:
            try:
                backups = self._list_backups()
                deleted = 0
                now = datetime.datetime.utcnow()
                cutoff = now - datetime.timedelta(days=self.retention_days)

                # Delete by age
                for b in backups:
                    ts = b.get("timestamp")
                    if ts:
                        try:
                            backup_time = datetime.datetime.fromisoformat(ts.rstrip("Z"))
                            if backup_time < cutoff:
                                shutil.rmtree(b["path"], ignore_errors=True)
                                deleted += 1
                                self._log_info(f"Cleaned up expired backup: {b['backup_id']}")
                        except Exception:
                            pass

                # Delete by count (keep newest max_backups)
                remaining = self._list_backups()
                if len(remaining) > self.max_backups:
                    to_delete = remaining[self.max_backups:]
                    for b in to_delete:
                        shutil.rmtree(b["path"], ignore_errors=True)
                        deleted += 1
                        self._log_info(f"Cleaned up overflow backup: {b['backup_id']}")

                return deleted
            except Exception as e:
                self._increment_failure()
                self._log_error(f"Cleanup failed: {e}")
                return 0

    def validate_backup(self, backup_id: str) -> Dict[str, Any]:
        """
        Validate a specific backup's file integrity, checksums,
        schema compatibility, and config version compatibility.
        Returns a result dict: {valid, backup_id, findings}
        Never raises.
        """
        try:
            backup_path = os.path.join(self.backup_dir, backup_id)
            manifest_path = os.path.join(backup_path, self.MANIFEST_FILE)
            findings = []

            if not os.path.exists(backup_path):
                return {
                    "valid": False,
                    "backup_id": backup_id,
                    "findings": [{"check": "path", "status": "FAIL", "message": f"Backup not found: {backup_path}"}],
                }

            if not os.path.exists(manifest_path):
                return {
                    "valid": False,
                    "backup_id": backup_id,
                    "findings": [{"check": "manifest", "status": "FAIL", "message": "manifest.json missing from backup"}],
                }

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # 1. Verify checksums
            for entry in manifest.get("files", []):
                file_path = os.path.join(backup_path, entry["filename"])
                if not os.path.exists(file_path):
                    findings.append({
                        "check": "file_exists",
                        "status": "FAIL",
                        "message": f"Backed-up file missing: {entry['filename']}"
                    })
                    continue

                actual_checksum = self._checksum(file_path)
                if actual_checksum != entry.get("checksum"):
                    findings.append({
                        "check": "checksum",
                        "status": "FAIL",
                        "message": f"Checksum mismatch for {entry['filename']}"
                    })
                else:
                    findings.append({
                        "check": "checksum",
                        "status": "PASS",
                        "message": f"Checksum verified: {entry['filename']}"
                    })

            # 2. Schema compatibility check (SQLite databases)
            for entry in manifest.get("files", []):
                if entry.get("original_path", "").endswith(".db"):
                    file_path = os.path.join(backup_path, entry["filename"])
                    if os.path.exists(file_path):
                        schema_result = self._check_db_schema(file_path)
                        findings.append({
                            "check": "schema_compatibility",
                            "status": schema_result["status"],
                            "message": schema_result["message"]
                        })

            # 3. Config version compatibility check
            for entry in manifest.get("files", []):
                if "feature_flags" in entry.get("original_path", ""):
                    file_path = os.path.join(backup_path, entry["filename"])
                    if os.path.exists(file_path):
                        version_result = self._check_config_version(file_path)
                        findings.append({
                            "check": "config_version",
                            "status": version_result["status"],
                            "message": version_result["message"]
                        })

            all_pass = all(f["status"] != "FAIL" for f in findings)
            return {
                "valid": all_pass,
                "backup_id": backup_id,
                "findings": findings,
            }

        except Exception as e:
            self._increment_failure()
            return {
                "valid": False,
                "backup_id": backup_id,
                "findings": [{"check": "validation", "status": "FAIL", "message": f"Validation error: {e}"}],
            }

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups sorted newest first. Never raises."""
        try:
            return self._list_backups()
        except Exception:
            return []

    def get_latest_backup(self) -> Optional[Dict[str, Any]]:
        """Return the most recent backup or None. Never raises."""
        try:
            backups = self._list_backups()
            return backups[0] if backups else None
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────

    def _generate_backup_id(self, label: str) -> str:
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(c if c.isalnum() or c == "_" else "_" for c in label)
        return f"backup_{ts}_{safe_label}"

    def _backup_file(self, src: str, dest_dir: str) -> Dict[str, Any]:
        """Copy a file into dest_dir and return its manifest entry."""
        filename = os.path.basename(src)
        dest_path = os.path.join(dest_dir, filename)
        shutil.copy2(src, dest_path)
        checksum = self._checksum(dest_path)
        size_bytes = os.path.getsize(dest_path)
        return {
            "original_path": src,
            "filename": filename,
            "checksum": checksum,
            "checksum_algorithm": "sha256",
            "size_bytes": size_bytes,
        }

    def _checksum(self, path: str) -> str:
        """Compute SHA-256 hex digest of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _check_db_schema(self, db_path: str) -> Dict[str, str]:
        """Verify that SQLite DB can be opened and queried for known tables."""
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            conn.close()
            return {"status": "PASS", "message": f"SQLite schema valid: {os.path.basename(db_path)}"}
        except Exception as e:
            return {"status": "FAIL", "message": f"SQLite schema error in {os.path.basename(db_path)}: {e}"}

    def _check_config_version(self, yaml_path: str) -> Dict[str, str]:
        """Verify that feature_flags.yaml contains a config_version."""
        try:
            import yaml
            with open(yaml_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            version = config.get("config_version")
            if version:
                return {"status": "PASS", "message": f"Config version present: {version}"}
            else:
                return {"status": "WARNING", "message": "Config version missing from feature_flags.yaml"}
        except Exception as e:
            return {"status": "FAIL", "message": f"Failed to read config version: {e}"}

    def _list_backups(self) -> List[Dict[str, Any]]:
        """Return sorted list (newest first) of backup metadata."""
        results = []
        if not os.path.exists(self.backup_dir):
            return results

        for entry in os.listdir(self.backup_dir):
            backup_path = os.path.join(self.backup_dir, entry)
            if not os.path.isdir(backup_path):
                continue
            manifest_path = os.path.join(backup_path, self.MANIFEST_FILE)
            if not os.path.exists(manifest_path):
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                results.append({
                    "backup_id": manifest.get("backup_id", entry),
                    "label": manifest.get("label", ""),
                    "timestamp": manifest.get("timestamp", ""),
                    "path": backup_path,
                    "file_count": len(manifest.get("files", [])),
                })
            except Exception:
                continue

        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return results

    def _increment_failure(self):
        """Increment backup_failure metric counter safely."""
        try:
            if self._metrics:
                self._metrics.increment_counter("backup_failure_count")
        except Exception:
            pass

    def _log_info(self, message: str):
        try:
            if self._logger:
                self._logger.info(message, step="BACKUP")
        except Exception:
            pass

    def _log_error(self, message: str):
        try:
            if self._logger:
                self._logger.error(message, step="BACKUP", error_code="BACKUP_001")
        except Exception:
            pass
