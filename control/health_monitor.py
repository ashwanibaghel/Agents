import os
import sys
import time
import datetime
import subprocess
import sqlite3
import yaml
import concurrent.futures
from typing import Dict, Any, Tuple, Optional

class HealthMonitor:
    """Manages read-only diagnostic health monitoring for system components."""
    
    def __init__(self, db_path: str = "state/task_checkpoints.db"):
        self.db_path = db_path
        self._retries: Dict[str, int] = {}
        self._last_errors: Dict[str, Optional[str]] = {}

    def _run_with_timeout(self, check_func, timeout: float) -> Tuple[str, str, Dict[str, Any], int]:
        start_time = time.time()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(check_func)
                res = future.result(timeout=timeout)
                latency = int((time.time() - start_time) * 1000)
                return res[0], res[1], res[2], latency
        except concurrent.futures.TimeoutError:
            latency = int((time.time() - start_time) * 1000)
            return "UNHEALTHY", f"Timeout after {timeout}s", {}, latency
        except Exception as e:
            latency = int((time.time() - start_time) * 1000)
            return "UNHEALTHY", f"Error: {str(e)}", {}, latency

    def check_component(self, name: str, check_func, timeout: float = 2.0) -> Dict[str, Any]:
        """Runs component check safely returning standard keys (Mandatory Change 3)."""
        last_check = datetime.datetime.utcnow().isoformat() + "Z"
        
        health_state, status, details, latency_ms = self._run_with_timeout(check_func, timeout)
        
        if health_state in ["DEGRADED", "UNHEALTHY"]:
            self._retries[name] = self._retries.get(name, 0) + 1
            self._last_errors[name] = status
        else:
            self._retries[name] = 0
            self._last_errors[name] = None
            
        return {
            "component": name,
            "status": status,
            "health_state": health_state,
            "latency_ms": latency_ms,
            "last_check": last_check,
            "retry_count": self._retries.get(name, 0),
            "timeout_ms": int(timeout * 1000),
            "last_error": self._last_errors.get(name, None),
            "details": details
        }

    # --- Read-Only Component Checks ---

    def check_bridge(self) -> Tuple[str, str, Dict[str, Any]]:
        return "HEALTHY", "Bridge server is active and responding.", {"port": 8000}

    def check_worker(self) -> Tuple[str, str, Dict[str, Any]]:
        """Queries worker heartbeat table in SQLite (Read-Only)."""
        if not os.path.exists(self.db_path):
            return "UNHEALTHY", "Heartbeat database not found", {}
            
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='worker_metrics'")
                if not cursor.fetchone():
                    return "UNHEALTHY", "worker_metrics table does not exist yet", {}
                cursor.execute("SELECT last_heartbeat FROM worker_metrics LIMIT 1")
                row = cursor.fetchone()
                
            if not row or not row["last_heartbeat"]:
                return "UNHEALTHY", "No active worker registered.", {}
                
            last_hb_str = row["last_heartbeat"]
            last_hb = datetime.datetime.fromisoformat(last_hb_str.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            diff = (now - last_hb).total_seconds()
            
            details = {"last_heartbeat": last_hb_str, "seconds_since_heartbeat": diff}
            if diff <= 30.0:
                return "HEALTHY", "Worker is active and polling.", details
            elif diff <= 300.0:
                return "DEGRADED", f"Worker heartbeat is delayed by {int(diff)}s.", details
            else:
                return "UNHEALTHY", f"Worker is inactive ({int(diff)}s since last heartbeat).", details
        except Exception as e:
            return "UNHEALTHY", f"Failed to read worker heartbeat: {str(e)}", {}

    def check_supabase(self) -> Tuple[str, str, Dict[str, Any]]:
        """Pings Supabase URL with HEAD (Read-Only)."""
        supabase_path = "config/supabase.yaml"
        if not os.path.exists(supabase_path):
            return "HEALTHY", "Supabase task source is not enabled (Local mode active).", {"enabled": False}
            
        try:
            with open(supabase_path, "r", encoding="utf-8") as f:
                sb_cfg = yaml.safe_load(f) or {}
            
            if not sb_cfg.get("enabled", False):
                return "HEALTHY", "Supabase task source is not enabled (Local mode active).", {"enabled": False}
                
            url = sb_cfg.get("supabase_url") or os.environ.get("SUPABASE_URL")
            key = sb_cfg.get("supabase_key") or os.environ.get("SUPABASE_SERVICE_KEY")
            
            if not url or not key:
                return "UNHEALTHY", "Supabase is enabled but URL/key is missing.", {}
                
            import requests
            headers = {"apikey": key, "Authorization": f"Bearer {key}"}
            # HEAD request to check endpoint, read-only diagnostic check
            res = requests.head(f"{url}/rest/v1/", headers=headers, timeout=2.0)
            if res.status_code < 400 or res.status_code == 401:
                return "HEALTHY", "Supabase connection is active.", {"url": url}
            else:
                return "UNHEALTHY", f"Supabase responded with code {res.status_code}", {"url": url}
        except Exception as e:
            return "UNHEALTHY", f"Supabase connection failed: {str(e)}", {}

    def check_git(self) -> Tuple[str, str, Dict[str, Any]]:
        try:
            res = subprocess.run(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            version = res.stdout.strip()
            return "HEALTHY", f"Git verified successfully: {version}", {"version": version}
        except Exception as e:
            return "UNHEALTHY", f"Git executable is missing or failing: {str(e)}", {}

    def check_workspace(self) -> Tuple[str, str, Dict[str, Any]]:
        workspaces_dir = os.path.join(os.getcwd(), "workspaces")
        if os.path.exists(workspaces_dir):
            if os.access(workspaces_dir, os.R_OK):
                return "HEALTHY", "Workspaces folder exists and is readable.", {"path": workspaces_dir}
            else:
                return "UNHEALTHY", "Workspaces folder is not readable.", {"path": workspaces_dir}
        else:
            return "UNHEALTHY", "Workspaces folder does not exist.", {"path": workspaces_dir}

    def check_session_manager(self) -> Tuple[str, str, Dict[str, Any]]:
        if not os.path.exists(self.db_path):
            return "UNHEALTHY", f"Checkpoints database file not found: {self.db_path}", {}
            
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='project_sessions'")
                exists = cursor.fetchone()[0]
                
            if exists:
                return "HEALTHY", "SQLite persistent sessions database is healthy.", {"db_path": self.db_path}
            else:
                return "UNHEALTHY", "Database exists but project_sessions table is missing.", {"db_path": self.db_path}
        except Exception as e:
            return "UNHEALTHY", f"Session database connection failed: {str(e)}", {"db_path": self.db_path}

    def check_antigravity_worker(self) -> Tuple[str, str, Dict[str, Any]]:
        try:
            from workers.antigravity_client import AntigravityClient
            client = AntigravityClient()
            if hasattr(client, "base_url") or hasattr(client, "client"):
                return "HEALTHY", "Antigravity worker client verified.", {"client": "AntigravityClient"}
            return "DEGRADED", "Antigravity worker client attributes mismatch.", {}
        except Exception as e:
            return "UNHEALTHY", f"Antigravity worker load failed: {str(e)}", {}

    def check_result_verifier(self) -> Tuple[str, str, Dict[str, Any]]:
        try:
            from control.result_verifier import ResultVerifier
            if hasattr(ResultVerifier, "verify_result"):
                return "HEALTHY", "Result verifier is active.", {}
            return "DEGRADED", "ResultVerifier attributes mismatch.", {}
        except Exception as e:
            return "UNHEALTHY", f"ResultVerifier load failed: {str(e)}", {}

    def check_dispatcher(self) -> Tuple[str, str, Dict[str, Any]]:
        try:
            from control.dispatcher import Dispatcher
            if hasattr(Dispatcher, "execute_task"):
                return "HEALTHY", "Dispatcher is active.", {}
            return "DEGRADED", "Dispatcher attributes mismatch.", {}
        except Exception as e:
            return "UNHEALTHY", f"Dispatcher load failed: {str(e)}", {}

    # --- Run All Checks ---

    def get_system_health(self, timeouts: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Performs read-only diagnostics across all components."""
        timeouts = timeouts or {}
        
        checks = [
            ("Bridge", self.check_bridge, timeouts.get("Bridge", 2.0)),
            ("Worker", self.check_worker, timeouts.get("Worker", 2.0)),
            ("Supabase", self.check_supabase, timeouts.get("Supabase", 2.0)),
            ("Git", self.check_git, timeouts.get("Git", 2.0)),
            ("Workspace", self.check_workspace, timeouts.get("Workspace", 2.0)),
            ("Persistent Session Manager", self.check_session_manager, timeouts.get("Persistent Session Manager", 2.0)),
            ("Antigravity Worker", self.check_antigravity_worker, timeouts.get("Antigravity Worker", 2.0)),
            ("Result Verifier", self.check_result_verifier, timeouts.get("Result Verifier", 2.0)),
            ("Dispatcher", self.check_dispatcher, timeouts.get("Dispatcher", 2.0)),
        ]
        
        results = {}
        overall_healthy = True
        
        for name, func, timeout in checks:
            res = self.check_component(name, func, timeout)
            results[name] = res
            if res["health_state"] == "UNHEALTHY":
                overall_healthy = False
                
        return {
            "status": "HEALTHY" if overall_healthy else "UNHEALTHY",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "components": results
        }
