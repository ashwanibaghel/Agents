import os
import sqlite3
import requests
import json
from typing import List, Dict

class ContextBuilder:
    def __init__(self, db_path: str = "state/task_checkpoints.db"):
        self.db_path = db_path
        
        self.supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        self.supabase_enabled = os.environ.get("SUPABASE_ENABLED", "false").lower() == "true"
        
        self.headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json"
        }

    def _get_task(self, task_id: str) -> dict:
        if self.supabase_enabled and self.supabase_url:
            url = f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}"
            try:
                r = requests.get(url, headers=self.headers, timeout=5.0)
                if r.status_code == 200 and r.json():
                    return r.json()[0]
            except Exception:
                pass
        return {}

    def _get_artifacts(self, task_id: str) -> List[dict]:
        if self.supabase_enabled and self.supabase_url:
            url = f"{self.supabase_url}/rest/v1/task_artifacts?task_id=eq.{task_id}&select=name,path,type,size,summary,indexing_status"
            try:
                r = requests.get(url, headers=self.headers, timeout=5.0)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
        
        # SQLite fallback
        if os.path.exists(self.db_path):
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT name, path, type, size, summary, indexing_status 
                        FROM task_artifacts 
                        WHERE task_id = ?
                    """, (task_id,))
                    return [dict(row) for row in cursor.fetchall()]
            except Exception:
                pass
        return []

    def _get_knowledge(self, task_id: str) -> List[dict]:
        if self.supabase_enabled and self.supabase_url:
            url = f"{self.supabase_url}/rest/v1/task_knowledge?task_id=eq.{task_id}&select=name,chunk_index,chunk_text,promoted_level"
            try:
                r = requests.get(url, headers=self.headers, timeout=5.0)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
        
        # SQLite fallback
        if os.path.exists(self.db_path):
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT name, chunk_index, chunk_text, promoted_level 
                        FROM task_knowledge 
                        WHERE task_id = ?
                    """, (task_id,))
                    return [dict(row) for row in cursor.fetchall()]
            except Exception:
                pass
        return []

    def build_task_context(self, task_id: str) -> str:
        """Compile task meta, artifact lists, and knowledge chunks into an optimized context prompt."""
        task = self._get_task(task_id)
        if not task:
            # Fallback if task is missing from Supabase (or local-only task)
            task = {
                "task_id": task_id,
                "project": "unknown",
                "objective": "Unknown objective",
                "status": "unknown",
                "summary": "No task metadata available."
            }
            
        artifacts = self._get_artifacts(task_id)
        knowledge = self._get_knowledge(task_id)
        
        lines = []
        lines.append(f"# TASK CONTEXT: {task.get('task_id')}")
        lines.append(f"**Project**: {task.get('project')}")
        lines.append(f"**Objective**: {task.get('objective')}")
        lines.append(f"**Status**: {task.get('status')}")
        lines.append(f"**Summary**: {task.get('summary') or 'No summary summary available.'}")
        lines.append("")
        
        lines.append("## ARTIFACTS LIST")
        if artifacts:
            for art in artifacts:
                lines.append(f"- **{art['name']}** (Path: `{art['path']}`, Type: {art['type']}, Size: {art['size']} bytes)")
                lines.append(f"  *Indexing Status*: {art.get('indexing_status')}")
                lines.append(f"  *Summary*: {art.get('summary') or 'None'}")
        else:
            lines.append("No engineering artifacts found for this task.")
        lines.append("")
        
        lines.append("## DETAILED KNOWLEDGE CHUNKS")
        if knowledge:
            for kn in sorted(knowledge, key=lambda x: (x["name"], x["chunk_index"])):
                lines.append(f"### File: {kn['name']} (Chunk {kn['chunk_index']}, Scope: {kn.get('promoted_level', 'TASK')})")
                lines.append(f"```text\n{kn['chunk_text']}\n```")
        else:
            lines.append("No detailed knowledge chunks indexed for this task.")
            
        return "\n".join(lines)
