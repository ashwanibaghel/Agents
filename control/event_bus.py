import sqlite3
import requests
import os
import json
from abc import ABC, abstractmethod
from typing import Dict, List, Callable

class EventBus(ABC):
    @abstractmethod
    def publish(self, event_name: str, payload: dict):
        """Publish an event to the bus."""
        pass

    @abstractmethod
    def subscribe(self, event_name: str, callback: Callable[[dict], None]):
        """Subscribe to a specific event on the bus."""
        pass

    @abstractmethod
    def poll(self):
        """Poll the backing store/transport and dispatch events to subscribers."""
        pass


class DatabasePollingEventBus(EventBus):
    def __init__(self, db_path: str = "state/task_checkpoints.db"):
        self.db_path = db_path
        self._subscribers: Dict[str, List[Callable[[dict], None]]] = {}
        
        # Load Supabase config to support production polling
        self.supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        self.supabase_enabled = os.environ.get("SUPABASE_ENABLED", "false").lower() == "true"
        
        self.headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json"
        }

    def publish(self, event_name: str, payload: dict):
        # In a database-driven model, publishing is done by creating database rows
        # with indexing_status = 'PENDING'. This method can log the event or act as a notifier.
        print(f"📣 EventBus published event '{event_name}' with payload: {payload}")
        # Synchronously invoke subscribers for immediate local events if they exist
        if event_name in self._subscribers:
            for cb in self._subscribers[event_name]:
                try:
                    cb(payload)
                except Exception as e:
                    print(f"⚠️ Event callback error: {e}")

    def subscribe(self, event_name: str, callback: Callable[[dict], None]):
        self._subscribers.setdefault(event_name, []).append(callback)

    def _get_supabase_pending(self) -> List[dict]:
        if not self.supabase_url or not self.supabase_key:
            return []
        url = f"{self.supabase_url}/rest/v1/task_artifacts?indexing_status=neq.INDEXED&select=task_id,name,content,path,indexing_status,next_retry_at,lease_expiration"
        try:
            r = requests.get(url, headers=self.headers, timeout=10.0)
            if r.status_code == 200:
                import datetime
                now = datetime.datetime.now(datetime.timezone.utc)
                eligible = []
                for row in r.json():
                    status = row.get("indexing_status")
                    next_retry = row.get("next_retry_at")
                    lease_exp = row.get("lease_expiration")
                    
                    if next_retry and next_retry.endswith("Z"):
                        next_retry = next_retry[:-1] + "+00:00"
                    if lease_exp and lease_exp.endswith("Z"):
                        lease_exp = lease_exp[:-1] + "+00:00"
                        
                    can_claim = (
                        status in ["PENDING", "REINDEX_REQUIRED"] or
                        (status == "FAILED" and (not next_retry or datetime.datetime.fromisoformat(next_retry) <= now)) or
                        (status == "INDEXING" and lease_exp and datetime.datetime.fromisoformat(lease_exp) <= now)
                    )
                    if can_claim:
                        eligible.append(row)
                return eligible
        except Exception as e:
            print(f"⚠️ Supabase polling error: {e}")
        return []

    def _get_sqlite_pending(self) -> List[dict]:
        if not os.path.exists(self.db_path):
            return []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT task_id, name, content, path, indexing_status, next_retry_at, lease_expiration 
                    FROM task_artifacts 
                    WHERE indexing_status != 'INDEXED'
                """)
                import datetime
                now = datetime.datetime.now(datetime.timezone.utc)
                eligible = []
                for row in cursor.fetchall():
                    row_dict = dict(row)
                    status = row_dict.get("indexing_status")
                    next_retry = row_dict.get("next_retry_at")
                    lease_exp = row_dict.get("lease_expiration")
                    
                    if next_retry and next_retry.endswith("Z"):
                        next_retry = next_retry[:-1] + "+00:00"
                    if lease_exp and lease_exp.endswith("Z"):
                        lease_exp = lease_exp[:-1] + "+00:00"
                        
                    can_claim = (
                        status in ["PENDING", "REINDEX_REQUIRED"] or
                        (status == "FAILED" and (not next_retry or datetime.datetime.fromisoformat(next_retry) <= now)) or
                        (status == "INDEXING" and lease_exp and datetime.datetime.fromisoformat(lease_exp) <= now)
                    )
                    if can_claim:
                        eligible.append(row_dict)
                return eligible
        except Exception as e:
            print(f"⚠️ SQLite polling error: {e}")
        return []

    def poll(self):
        """Poll pending database records and trigger 'artifact_created' subscribers."""
        if "artifact_created" not in self._subscribers:
            return

        # Fetch pending artifacts
        pending_items = []
        if self.supabase_enabled:
            pending_items = self._get_supabase_pending()
        else:
            pending_items = self._get_sqlite_pending()

        for item in pending_items:
            payload = {
                "task_id": item["task_id"],
                "name": item["name"],
                "content": item["content"],
                "path": item["path"]
            }
            # Trigger subscribers
            for cb in self._subscribers["artifact_created"]:
                try:
                    cb(payload)
                except Exception as e:
                    print(f"⚠️ Subscriber callback error for artifact {item['name']}: {e}")
