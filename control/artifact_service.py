import os
import sqlite3
import requests
from typing import List
from control.storage_provider import StorageProvider
from control.event_bus import EventBus

class ArtifactService:
    def __init__(self, storage_provider: StorageProvider, event_bus: EventBus, db_path: str = "state/task_checkpoints.db"):
        self.storage = storage_provider
        self.event_bus = event_bus
        self.db_path = db_path
        
        self.supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        self.supabase_enabled = os.environ.get("SUPABASE_ENABLED", "false").lower() == "true"
        
        self.headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates"
        }

    def _determine_type(self, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if ext in [".md", ".markdown"]:
            return "markdown"
        elif ext in [".json"]:
            return "json"
        elif ext in [".yaml", ".yml"]:
            return "yaml"
        elif ext in [".txt"]:
            return "text"
        elif ext in [".log"]:
            return "log"
        elif ext in [".csv"]:
            return "csv"
        else:
            return "text"

    def save_artifacts(self, task_id: str, evidence_paths: List[str]) -> List[dict]:
        """Read artifacts from storage, save them with status PENDING, and publish events."""
        saved_artifacts = []
        
        for path in evidence_paths:
            try:
                filename = os.path.basename(path)
                size = self.storage.get_size(path)
                content = self.storage.read_file(path)
                file_type = self._determine_type(filename)
                
                summary = content[:300] + "..." if len(content) > 300 else content
                
                artifact_data = {
                    "task_id": task_id,
                    "name": filename,
                    "path": path,
                    "type": file_type,
                    "size": size,
                    "summary": summary.strip(),
                    "content": content,
                    "indexing_status": "PENDING"
                }
                
                # 1. Save to Supabase if enabled
                if self.supabase_enabled and self.supabase_url:
                    url = f"{self.supabase_url}/rest/v1/task_artifacts"
                    r = requests.post(url, headers=self.headers, json=[artifact_data], timeout=10.0)
                    if r.status_code not in [200, 201]:
                        print(f"⚠️ Supabase artifact save failed: {r.text}")
                
                # 2. Save to local SQLite
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute("""
                            INSERT INTO task_artifacts (task_id, name, path, type, size, summary, content, indexing_status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
                            ON CONFLICT(task_id, name) DO UPDATE SET
                                path=excluded.path,
                                type=excluded.type,
                                size=excluded.size,
                                summary=excluded.summary,
                                content=excluded.content,
                                indexing_status='PENDING',
                                retry_count=0,
                                indexing_error=NULL,
                                updated_at=CURRENT_TIMESTAMP
                        """, (task_id, filename, path, file_type, size, summary.strip(), content))
                        conn.commit()
                except Exception as sqlite_err:
                    print(f"⚠️ SQLite artifact save error: {sqlite_err}")
                
                saved_artifacts.append(artifact_data)
                
                # 3. Publish Event via EventBus
                self.event_bus.publish("artifact_created", {
                    "task_id": task_id,
                    "name": filename,
                    "path": path
                })
                
            except Exception as e:
                print(f"⚠️ Failed to process/save artifact '{path}': {e}")
                
        return saved_artifacts
