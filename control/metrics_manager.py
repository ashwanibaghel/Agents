import sqlite3
import os
import time
import datetime
import threading
from typing import Optional, Dict, Any, List
from control.structured_logger import logger
from control import error_codes

class MetricsManager:
    """Manages lightweight, TTL-cached runtime metrics and safe, non-blocking persistence."""
    
    def __init__(self, db_path: str = "state/task_checkpoints.db", cache_ttl_seconds: float = 30.0):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._timers: Dict[str, Dict[str, float]] = {}  # trace_id -> phase -> start_time
        
        # Mandatory Change 1: Configurable Cache TTL & Cached Report State
        self.cache_ttl = cache_ttl_seconds
        self._cached_report: Optional[Dict[str, Any]] = None
        self._last_cache_time: float = 0.0
        
        # Mandatory Change 4: Internal Metrics Failure Counter
        self._metrics_failures: int = 0
        
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Sets up the SQLite database schemas safely and silently."""
        self._safe_execute(lambda conn: conn.execute("""
            CREATE TABLE IF NOT EXISTS task_metrics (
                task_id TEXT,
                trace_id TEXT PRIMARY KEY,
                project_id TEXT,
                status TEXT,
                execution_time_ms INTEGER DEFAULT 0,
                verification_time_ms INTEGER DEFAULT 0,
                push_time_ms INTEGER DEFAULT 0,
                git_success INTEGER DEFAULT 0,
                verifier_success INTEGER DEFAULT 0,
                workspace_reused INTEGER DEFAULT 0,
                conversation_reused INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL
            )
        """))
        self._safe_execute(lambda conn: conn.execute("""
            CREATE TABLE IF NOT EXISTS worker_metrics (
                worker_id TEXT PRIMARY KEY,
                startup_count INTEGER DEFAULT 0,
                boot_timestamp TEXT,
                last_heartbeat TEXT
            )
        """))
        self._safe_execute(lambda conn: conn.execute("""
            CREATE TABLE IF NOT EXISTS reliability_counters (
                counter_name TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        """))
        self._safe_execute(lambda conn: conn.execute("INSERT OR IGNORE INTO reliability_counters (counter_name, value) VALUES ('session_expiry_count', 0)"))
        self._safe_execute(lambda conn: conn.execute("INSERT OR IGNORE INTO reliability_counters (counter_name, value) VALUES ('verifier_failures', 0)"))
        self._safe_execute(lambda conn: conn.execute("INSERT OR IGNORE INTO reliability_counters (counter_name, value) VALUES ('git_failures', 0)"))
        self._safe_execute(lambda conn: conn.execute("INSERT OR IGNORE INTO reliability_counters (counter_name, value) VALUES ('retry_count', 0)"))

    def _safe_execute(self, op_func) -> bool:
        """
        Mandatory Change 4: Execute database operations safely.
        If persistence fails, continue worker execution, log the error, and increment failure counter.
        """
        try:
            with self._lock:
                with sqlite3.connect(self.db_path) as conn:
                    op_func(conn)
                    conn.commit()
            return True
        except Exception as e:
            self._metrics_failures += 1
            # Emit structured log
            logger.error(
                f"Metrics persistence failed: {str(e)}",
                error_code=error_codes.METRICS_001,
                step="METRICS"
            )
            return False

    # --- Worker Metrics tracking (Mandatory Change 6) ---

    def record_worker_boot(self, worker_id: str):
        """Increments worker startup_count and sets boot_timestamp on boot ONLY."""
        now_str = datetime.datetime.utcnow().isoformat() + "Z"
        self._safe_execute(lambda conn: conn.execute("""
            INSERT INTO worker_metrics (worker_id, startup_count, boot_timestamp, last_heartbeat)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                startup_count = startup_count + 1,
                boot_timestamp = excluded.boot_timestamp,
                last_heartbeat = excluded.last_heartbeat
        """, (worker_id, now_str, now_str)))

    def record_worker_heartbeat(self, worker_id: str):
        """Updates worker last_heartbeat timestamp independently of startup count."""
        now_str = datetime.datetime.utcnow().isoformat() + "Z"
        self._safe_execute(lambda conn: conn.execute("""
            UPDATE worker_metrics SET last_heartbeat = ? WHERE worker_id = ?
        """, (now_str, worker_id)))

    # --- Reliability Counters tracking ---

    def increment_counter(self, counter_name: str, amount: int = 1):
        self._safe_execute(lambda conn: conn.execute("""
            INSERT INTO reliability_counters (counter_name, value)
            VALUES (?, ?)
            ON CONFLICT(counter_name) DO UPDATE SET value = value + excluded.value
        """, (counter_name, amount)))

    # --- Timers & Task Metrics tracking ---

    def start_task_metric(self, trace_id: str, task_id: str, project_id: str):
        now_str = datetime.datetime.utcnow().isoformat() + "Z"
        self._safe_execute(lambda conn: conn.execute("""
            INSERT OR IGNORE INTO task_metrics (
                task_id, trace_id, project_id, status, timestamp
            ) VALUES (?, ?, ?, 'RUNNING', ?)
        """, (task_id, trace_id, project_id, now_str)))

    def start_timer(self, trace_id: str, phase: str):
        if trace_id not in self._timers:
            self._timers[trace_id] = {}
        self._timers[trace_id][phase] = time.time()

    def stop_timer(self, trace_id: str, phase: str):
        if trace_id not in self._timers or phase not in self._timers[trace_id]:
            return
        
        start_time = self._timers[trace_id][phase]
        duration_ms = int((time.time() - start_time) * 1000)
        
        col_map = {
            "execution": "execution_time_ms",
            "verification": "verification_time_ms",
            "push": "push_time_ms"
        }
        col_name = col_map.get(phase)
        if not col_name:
            return

        self._safe_execute(lambda conn: conn.execute(f"""
            UPDATE task_metrics SET {col_name} = ? WHERE trace_id = ?
        """, (duration_ms, trace_id)))

    def record_workspace_reuse(self, trace_id: str, reused: bool):
        val = 1 if reused else 0
        self._safe_execute(lambda conn: conn.execute("""
            UPDATE task_metrics SET workspace_reused = ? WHERE trace_id = ?
        """, (val, trace_id)))

    def record_conversation_reuse(self, trace_id: str, reused: bool):
        val = 1 if reused else 0
        self._safe_execute(lambda conn: conn.execute("""
            UPDATE task_metrics SET conversation_reused = ? WHERE trace_id = ?
        """, (val, trace_id)))

    def record_verifier_result(self, trace_id: str, success: bool):
        val = 1 if success else 0
        self._safe_execute(lambda conn: conn.execute("""
            UPDATE task_metrics SET verifier_success = ? WHERE trace_id = ?
        """, (val, trace_id)))

    def record_git_result(self, trace_id: str, success: bool):
        val = 1 if success else 0
        self._safe_execute(lambda conn: conn.execute("""
            UPDATE task_metrics SET git_success = ? WHERE trace_id = ?
        """, (val, trace_id)))

    def complete_task_metric(self, trace_id: str, status: str):
        self._safe_execute(lambda conn: conn.execute("""
            UPDATE task_metrics SET status = ? WHERE trace_id = ?
        """, (status, trace_id)))

    # --- Query API with TTL Caching (Mandatory Change 1 & 5) ---

    def get_percentile(self, times: List[int], percentile: float) -> float:
        if not times:
            return 0.0
        sorted_times = sorted(times)
        idx = int(len(sorted_times) * percentile)
        idx = min(max(0, idx), len(sorted_times) - 1)
        return float(sorted_times[idx])

    def get_metrics_report(self) -> Dict[str, Any]:
        """
        Returns the metrics report. Serves from warm in-memory cache if TTL is valid.
        Under warm cache, this function returns instantly (<1ms).
        """
        now = time.time()
        with self._lock:
            if self._cached_report and (now - self._last_cache_time < self.cache_ttl):
                return self._cached_report

        # Recalculate metrics
        report = self._calculate_metrics()
        
        with self._lock:
            self._cached_report = report
            self._last_cache_time = now
            
        return report

    def _calculate_metrics(self) -> Dict[str, Any]:
        """Performs actual SQLite reads to compute execution, statistical, and success metrics."""
        report = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # 1. Task Metrics
                cursor.execute("SELECT count(*) FROM task_metrics")
                total = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM task_metrics WHERE status = 'DONE'")
                completed = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM task_metrics WHERE status = 'FAILED'")
                failed = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM task_metrics WHERE status = 'BLOCKED'")
                blocked = cursor.fetchone()[0]
                
                report["task_metrics"] = {
                    "total_tasks": total,
                    "completed_tasks": completed,
                    "failed_tasks": failed,
                    "blocked_tasks": blocked
                }

                # 2. Execution Durations
                cursor.execute("SELECT execution_time_ms, verification_time_ms, push_time_ms FROM task_metrics WHERE status = 'DONE'")
                rows = cursor.fetchall()
                exec_times = [r["execution_time_ms"] for r in rows if r["execution_time_ms"] > 0]
                verify_times = [r["verification_time_ms"] for r in rows if r["verification_time_ms"] > 0]
                push_times = [r["push_time_ms"] for r in rows if r["push_time_ms"] > 0]
                
                avg_exec = sum(exec_times) / len(exec_times) if exec_times else 0.0
                p50_exec = self.get_percentile(exec_times, 0.5)
                p95_exec = self.get_percentile(exec_times, 0.95)
                fastest = min(exec_times) if exec_times else 0
                slowest = max(exec_times) if exec_times else 0

                report["execution_metrics"] = {
                    "average_execution": avg_exec,
                    "median_execution": p50_exec,
                    "P95_execution": p95_exec,
                    "fastest_task": fastest,
                    "slowest_task": slowest,
                    "average_verification_time": sum(verify_times) / len(verify_times) if verify_times else 0.0,
                    "average_push_time": sum(push_times) / len(push_times) if push_times else 0.0
                }

                # 3. Reliability Metrics
                cursor.execute("SELECT counter_name, value FROM reliability_counters")
                reliability = {row["counter_name"]: row["value"] for row in cursor.fetchall()}
                # Inject metrics_failures counter
                reliability["metrics_failures"] = self._metrics_failures
                report["reliability_metrics"] = reliability

                # 4. Reuse stats
                cursor.execute("SELECT count(*), sum(workspace_reused), sum(conversation_reused) FROM task_metrics")
                count, workspace_reused, conversation_reused = cursor.fetchone()
                report["reuse_metrics"] = {
                    "workspace_reuse_rate": float(workspace_reused) / count if count and workspace_reused else 0.0,
                    "conversation_reuse_rate": float(conversation_reused) / count if count and conversation_reused else 0.0
                }

                # 5. Success stats
                cursor.execute("SELECT count(*), sum(git_success), sum(verifier_success) FROM task_metrics WHERE status = 'DONE'")
                done_count, git_success, verifier_success = cursor.fetchone()
                report["success_metrics"] = {
                    "git_success_rate": float(git_success) / done_count if done_count and git_success else 0.0,
                    "verifier_success_rate": float(verifier_success) / done_count if done_count and verifier_success else 0.0
                }

                # 6. Worker metrics
                cursor.execute("SELECT startup_count, boot_timestamp, last_heartbeat FROM worker_metrics LIMIT 1")
                worker_row = worker_row = cursor.fetchone()
                if worker_row:
                    startup_count = worker_row["startup_count"]
                    boot_ts = worker_row["boot_timestamp"]
                    last_hb = worker_row["last_heartbeat"]
                    
                    uptime = 0
                    if boot_ts and last_hb:
                        b = datetime.datetime.fromisoformat(boot_ts.replace("Z", "+00:00"))
                        h = datetime.datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                        uptime = int((h - b).total_seconds())
                    
                    report["worker_metrics"] = {
                        "startup_count": startup_count,
                        "worker_uptime": uptime
                    }
                else:
                    report["worker_metrics"] = {
                        "startup_count": 0,
                        "worker_uptime": 0
                    }
        except Exception as e:
            # Fallback if DB is fully locked/corrupted
            report["task_metrics"] = {"total_tasks": 0, "completed_tasks": 0, "failed_tasks": 0, "blocked_tasks": 0}
            report["execution_metrics"] = {"average_execution": 0.0, "median_execution": 0.0, "P95_execution": 0.0, "fastest_task": 0, "slowest_task": 0}
            report["reliability_metrics"] = {"session_expiry_count": 0, "verifier_failures": 0, "git_failures": 0, "retry_count": 0, "metrics_failures": self._metrics_failures}
            report["reuse_metrics"] = {"workspace_reuse_rate": 0.0, "conversation_reuse_rate": 0.0}
            report["success_metrics"] = {"git_success_rate": 0.0, "verifier_success_rate": 0.0}
            report["worker_metrics"] = {"startup_count": 0, "worker_uptime": 0}
            
        return report

# Global metrics manager singleton
metrics_manager = MetricsManager()
