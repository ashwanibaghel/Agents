import sqlite3
import datetime
import json
import os
import threading
from typing import Optional, List, Dict, Any

class AuditTrailManager:
    """Manages an immutable, append-only SQLite audit log for worker transitions."""
    
    def __init__(self, db_path: str = "state/task_checkpoints.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Creates the audit_trail table if it does not exist."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_trail (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        trace_id TEXT,
                        worker_id TEXT,
                        task_id TEXT,
                        project_id TEXT,
                        conversation_id TEXT,
                        branch TEXT,
                        event_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error_code TEXT,
                        message TEXT,
                        metadata_json TEXT
                    )
                """)
                # Create indices to optimize duplicate checking and retrieval
                conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_trail(trace_id, event_type, status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_trail(task_id, event_type, status)")

    def append(
        self,
        event_type: str,
        status: str,
        trace_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch: Optional[str] = None,
        error_code: Optional[str] = None,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Appends a new record to the audit trail.
        
        Performs duplicate protection: rejects the write if a record with the same
        (trace_id, event_type, status) already exists. If trace_id is not provided,
        checks (task_id, event_type, status).
        
        Returns:
            True if inserted successfully, False if skipped as duplicate.
        """
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        metadata_str = json.dumps(metadata) if metadata is not None else None
        
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Duplicate Protection
                if trace_id:
                    cursor.execute(
                        "SELECT 1 FROM audit_trail WHERE trace_id = ? AND event_type = ? AND status = ? LIMIT 1",
                        (trace_id, event_type, status)
                    )
                elif task_id:
                    cursor.execute(
                        "SELECT 1 FROM audit_trail WHERE task_id = ? AND event_type = ? AND status = ? LIMIT 1",
                        (task_id, event_type, status)
                    )
                else:
                    cursor.execute(
                        "SELECT 1 FROM audit_trail WHERE event_type = ? AND status = ? AND timestamp >= ? LIMIT 1",
                        (event_type, status, (datetime.datetime.utcnow() - datetime.timedelta(seconds=5)).isoformat() + "Z")
                    )
                
                if cursor.fetchone():
                    # Duplicate transition detected; skip writing
                    return False
                
                # Perform the insert
                cursor.execute("""
                    INSERT INTO audit_trail (
                        timestamp, trace_id, worker_id, task_id, project_id,
                        conversation_id, branch, event_type, status, error_code,
                        message, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp, trace_id, worker_id, task_id, project_id,
                    conversation_id, branch, event_type, status, error_code,
                    message, metadata_str
                ))
                conn.commit()
                return True

    def get_records(
        self,
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Retrieves chronological audit trail records."""
        query = "SELECT id, timestamp, trace_id, worker_id, task_id, project_id, conversation_id, branch, event_type, status, error_code, message, metadata_json FROM audit_trail"
        params = []
        
        if trace_id:
            query += " WHERE trace_id = ?"
            params.append(trace_id)
        elif task_id:
            query += " WHERE task_id = ?"
            params.append(task_id)
            
        query += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        
        records = []
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, tuple(params))
                for row in cursor.fetchall():
                    rec = dict(row)
                    if rec["metadata_json"]:
                        try:
                            rec["metadata"] = json.loads(rec["metadata_json"])
                        except Exception:
                            rec["metadata"] = {}
                    else:
                        rec["metadata"] = {}
                    records.append(rec)
        return records

    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns the most recent audit trail records (newest first). Read-only."""
        query = (
            "SELECT id, timestamp, trace_id, worker_id, task_id, project_id, "
            "conversation_id, branch, event_type, status, error_code, message "
            "FROM audit_trail ORDER BY id DESC LIMIT ?"
        )
        records = []
        try:
            with self._lock:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(query, (limit,))
                    for row in cursor.fetchall():
                        records.append(dict(row))
        except Exception:
            pass
        return records

# Global singleton instance
audit_trail = AuditTrailManager()
