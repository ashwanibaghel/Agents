import sqlite3
import json
import os
import datetime

class CheckpointManager:
    def __init__(self, db_path="state/task_checkpoints.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        
    def _init_db(self):
        """Initialize SQLite database and perform self-healing column migrations."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    task_id TEXT PRIMARY KEY,
                    project TEXT,
                    status TEXT,
                    worker_id TEXT,
                    iteration INTEGER,
                    observations TEXT,
                    actions TEXT,
                    timestamp TEXT,
                    checkpoint_data TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_sessions (
                    project_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    workspace_path TEXT,
                    repository_url TEXT,
                    default_branch TEXT,
                    current_branch TEXT,
                    last_commit TEXT,
                    last_activity TEXT,
                    status TEXT,
                    locked_by TEXT,
                    locked_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_memories (
                    project_id TEXT PRIMARY KEY,
                    architecture TEXT,
                    pending_todos TEXT,
                    known_bugs TEXT,
                    recent_decisions TEXT,
                    coding_style TEXT,
                    framework TEXT,
                    backend_notes TEXT,
                    oracle_notes TEXT,
                    design_rules TEXT,
                    owner_instructions TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    summary TEXT,
                    content TEXT NOT NULL,
                    indexing_status TEXT DEFAULT 'PENDING',
                    indexing_error TEXT,
                    indexed_at TEXT,
                    retry_count INTEGER DEFAULT 0,
                    last_retry_at TEXT,
                    next_retry_at TEXT,
                    claimed_by TEXT,
                    claimed_at TEXT,
                    lease_expiration TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(task_id, name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    promoted_level TEXT DEFAULT 'TASK',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            
            # Programmatically migrate task_artifacts and task_knowledge if missing
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(task_artifacts)")
            existing_art_cols = [col[1] for col in cursor.fetchall()]
            art_cols_to_add = [
                ("indexing_status", "TEXT DEFAULT 'PENDING'"),
                ("indexing_error", "TEXT"),
                ("indexed_at", "TEXT"),
                ("retry_count", "INTEGER DEFAULT 0"),
                ("last_retry_at", "TEXT"),
                ("next_retry_at", "TEXT"),
                ("claimed_by", "TEXT"),
                ("claimed_at", "TEXT"),
                ("lease_expiration", "TEXT")
            ]
            for col_name, col_type in art_cols_to_add:
                if col_name not in existing_art_cols:
                    conn.execute(f"ALTER TABLE task_artifacts ADD COLUMN {col_name} {col_type}")
            
            cursor.execute("PRAGMA table_info(task_knowledge)")
            existing_kn_cols = [col[1] for col in cursor.fetchall()]
            if "promoted_level" not in existing_kn_cols:
                conn.execute("ALTER TABLE task_knowledge ADD COLUMN promoted_level TEXT DEFAULT 'TASK'")
            conn.commit()
            
            # Programmatically migrate/add columns if they are missing
            columns_to_add = [
                ("provider", "TEXT"),
                ("conversation_id", "TEXT"),
                ("delegated_at", "TEXT"),
                ("last_followup_at", "TEXT"),
                ("worker_model", "TEXT"),
                ("delegation_status", "TEXT")
            ]
            
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(checkpoints)")
            existing_columns = [col[1] for col in cursor.fetchall()]
            
            for col_name, col_type in columns_to_add:
                if col_name not in existing_columns:
                    conn.execute(f"ALTER TABLE checkpoints ADD COLUMN {col_name} {col_type}")
            conn.commit()
            
            cursor.execute("PRAGMA table_info(project_sessions)")
            existing_session_cols = [col[1] for col in cursor.fetchall()]
            session_cols_to_add = [
                ("retry_count", "INTEGER DEFAULT 0"),
                ("last_error", "TEXT"),
                ("next_retry_at", "TEXT")
            ]
            for col_name, col_type in session_cols_to_add:
                if col_name not in existing_session_cols:
                    conn.execute(f"ALTER TABLE project_sessions ADD COLUMN {col_name} {col_type}")
            conn.commit()
            
    def save_checkpoint(self, task_id, project, status, worker_id, iteration, observations, actions, checkpoint_data=None):
        obs_json = json.dumps(observations)
        act_json = json.dumps(actions)
        cp_json = json.dumps(checkpoint_data or {})
        ts = datetime.datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO checkpoints (task_id, project, status, worker_id, iteration, observations, actions, timestamp, checkpoint_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    worker_id=excluded.worker_id,
                    iteration=excluded.iteration,
                    observations=excluded.observations,
                    actions=excluded.actions,
                    timestamp=excluded.timestamp,
                    checkpoint_data=excluded.checkpoint_data
            """, (task_id, project, status, worker_id, iteration, obs_json, act_json, ts, cp_json))
            conn.commit()

    def save_delegation_state(self, task_id, project, status, worker_id, provider, conversation_id, delegated_at, last_followup_at, worker_model, delegation_status):
        """Save delegation state columns to database checkpoint."""
        ts = datetime.datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO checkpoints (
                    task_id, project, status, worker_id, iteration, observations, actions, timestamp, checkpoint_data,
                    provider, conversation_id, delegated_at, last_followup_at, worker_model, delegation_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    worker_id=excluded.worker_id,
                    timestamp=excluded.timestamp,
                    provider=excluded.provider,
                    conversation_id=excluded.conversation_id,
                    delegated_at=excluded.delegated_at,
                    last_followup_at=excluded.last_followup_at,
                    worker_model=excluded.worker_model,
                    delegation_status=excluded.delegation_status
            """, (task_id, project, status, worker_id, 0, "[]", "[]", ts, "{}",
                  provider, conversation_id, delegated_at, last_followup_at, worker_model, delegation_status))
            conn.commit()
            
    def load_checkpoint(self, task_id) -> dict:
        """Query and return active task checkpoints, including delegation columns."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT project, status, worker_id, iteration, observations, actions, checkpoint_data,
                       provider, conversation_id, delegated_at, last_followup_at, worker_model, delegation_status
                FROM checkpoints WHERE task_id=?
            """, (task_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "task_id": task_id,
                    "project": row[0],
                    "status": row[1],
                    "worker_id": row[2],
                    "iteration": row[3],
                    "observations": json.loads(row[4]) if row[4] else [],
                    "actions": json.loads(row[5]) if row[5] else [],
                    "checkpoint_data": json.loads(row[6]) if row[6] else {},
                    "provider": row[7],
                    "conversation_id": row[8],
                    "delegated_at": row[9],
                    "last_followup_at": row[10],
                    "worker_model": row[11],
                    "delegation_status": row[12]
                }
        return None
        
    def delete_checkpoint(self, task_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM checkpoints WHERE task_id=?", (task_id,))
            conn.commit()
