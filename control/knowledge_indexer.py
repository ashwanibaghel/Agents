import os
import sqlite3
import requests
import datetime
import json
from typing import List, Dict
from control.embedding_provider import EmbeddingProviderRegistry

class KnowledgeIndexer:
    def __init__(self, db_path: str = "state/task_checkpoints.db", provider_name: str = "gemini"):
        self.db_path = db_path
        self.provider_name = provider_name
        self._current_worker_id = None   # set by claim_artifact_lease, used in update_artifact_status
        
        self.supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        self.supabase_enabled = os.environ.get("SUPABASE_ENABLED", "false").lower() == "true"
        
        self.headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json"
        }

    def _chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 150) -> List[str]:
        chunks = []
        if not text:
            return chunks
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start += chunk_size - overlap
        return chunks

    def claim_artifact_lease(self, task_id: str, name: str, worker_id: str) -> bool:
        """Attempt to claim a lease on an artifact for indexing (locking)."""
        now = datetime.datetime.now(datetime.timezone.utc)
        now_str = now.isoformat()
        lease_exp = (now + datetime.timedelta(minutes=5)).isoformat()
        self._current_worker_id = worker_id   # persisted to indexed_by on completion
        
        if self.supabase_enabled and self.supabase_url:
            # Query current status to ensure we can claim it safely
            url = f"{self.supabase_url}/rest/v1/task_artifacts?task_id=eq.{task_id}&name=eq.{name}"
            try:
                r = requests.get(url, headers=self.headers, timeout=10.0)
                if r.status_code == 200 and r.json():
                    row = r.json()[0]
                    status = row.get("indexing_status", "PENDING")
                    claimed_by = row.get("claimed_by")
                    lease_expiration = row.get("lease_expiration")
                    
                    can_claim = (
                        status in ["PENDING", "FAILED", "REINDEX_REQUIRED"] or
                        (status == "INDEXING" and lease_expiration and datetime.datetime.fromisoformat(lease_expiration) <= now)
                    )
                    
                    if can_claim:
                        # Attempt claim patch
                        patch_url = f"{self.supabase_url}/rest/v1/task_artifacts?task_id=eq.{task_id}&name=eq.{name}"
                        payload = {
                            "indexing_status": "INDEXING",
                            "claimed_by": worker_id,
                            "claimed_at": now_str,
                            "lease_expiration": lease_exp
                        }
                        r_patch = requests.patch(patch_url, headers=self.headers, json=payload, timeout=10.0)
                        if r_patch.status_code in [200, 204]:
                            return True
            except Exception as e:
                print(f"⚠️ Supabase lease claim failed: {e}")
                
        # SQLite implementation
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT indexing_status, claimed_by, lease_expiration 
                    FROM task_artifacts 
                    WHERE task_id = ? AND name = ?
                """, (task_id, name))
                row = cursor.fetchone()
                if row:
                    status = row["indexing_status"]
                    lease_expiration = row["lease_expiration"]
                    
                    can_claim = (
                        status in ["PENDING", "FAILED", "REINDEX_REQUIRED"] or
                        (status == "INDEXING" and lease_expiration and datetime.datetime.fromisoformat(lease_expiration) <= now)
                    )
                    
                    if can_claim:
                        conn.execute("""
                            UPDATE task_artifacts 
                            SET indexing_status = 'INDEXING',
                                claimed_by = ?,
                                claimed_at = ?,
                                lease_expiration = ?
                            WHERE task_id = ? AND name = ?
                        """, (worker_id, now_str, lease_exp, task_id, name))
                        conn.commit()
                        return True
        except Exception as sqlite_err:
            print(f"⚠️ SQLite lease claim error: {sqlite_err}")
            
        return False

    def update_artifact_status(self, task_id: str, name: str, status: str, error_msg: str = None):
        """Update artifact status, retry metrics, and timestamps on completion/failure."""
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Calculate retry backoff if failed
        next_retry_str = None
        retry_count = 0
        
        # We need to fetch the current retry count first
        if self.supabase_enabled and self.supabase_url:
            url = f"{self.supabase_url}/rest/v1/task_artifacts?task_id=eq.{task_id}&name=eq.{name}&select=retry_count"
            try:
                r = requests.get(url, headers=self.headers, timeout=5.0)
                if r.status_code == 200 and r.json():
                    retry_count = r.json()[0].get("retry_count") or 0
            except Exception:
                pass
        else:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT retry_count FROM task_artifacts WHERE task_id = ? AND name = ?", (task_id, name))
                    row = cursor.fetchone()
                    if row:
                        retry_count = row[0] or 0
            except Exception:
                pass
                
        if status == "FAILED":
            retry_count += 1
            # Exponential backoff: 30s * 2^retry
            backoff_secs = 30 * (2 ** min(retry_count, 6))
            next_retry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=backoff_secs)
            next_retry_str = next_retry.isoformat()
            
        payload = {
            "indexing_status": status,
            "indexing_error": error_msg,
            "indexed_at": now_str if status == "INDEXED" else None,
            "retry_count": retry_count if status == "FAILED" else 0,
            "last_retry_at": now_str if status == "FAILED" else None,
            "next_retry_at": next_retry_str,
            "indexed_by": self._current_worker_id if status == "INDEXED" else None,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expiration": None
        }
        
        # Update Supabase
        if self.supabase_enabled and self.supabase_url:
            url = f"{self.supabase_url}/rest/v1/task_artifacts?task_id=eq.{task_id}&name=eq.{name}"
            try:
                requests.patch(url, headers=self.headers, json=payload, timeout=10.0)
            except Exception as e:
                print(f"⚠️ Supabase status update failed: {e}")
                
        # Update SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE task_artifacts 
                    SET indexing_status = ?,
                        indexing_error = ?,
                        indexed_at = ?,
                        indexed_by = ?,
                        retry_count = ?,
                        last_retry_at = ?,
                        next_retry_at = ?,
                        claimed_by = NULL,
                        claimed_at = NULL,
                        lease_expiration = NULL
                    WHERE task_id = ? AND name = ?
                """, (status, error_msg, payload["indexed_at"],
                       payload.get("indexed_by"),
                       payload["retry_count"], payload["last_retry_at"],
                       next_retry_str, task_id, name))
                conn.commit()
        except Exception as sqlite_err:
            print(f"⚠️ SQLite status update failed: {sqlite_err}")

    def index_artifact(self, task_id: str, name: str, content: str) -> bool:
        """Perform chunking, embedding generation, and database updates."""
        try:
            provider = EmbeddingProviderRegistry.get_provider(self.provider_name)
        except Exception as e:
            self.update_artifact_status(task_id, name, "FAILED", f"Provider error: {e}")
            return False

        chunks = self._chunk_text(content)
        knowledge_payloads = []
        
        for idx, chunk in enumerate(chunks):
            try:
                emb = provider.embed_text(chunk)
                knowledge_payloads.append({
                    "task_id": task_id,
                    "name": name,
                    "chunk_index": idx,
                    "chunk_text": chunk,
                    "embedding": emb,
                    "promoted_level": "TASK"
                })
            except Exception as e:
                self.update_artifact_status(task_id, name, "FAILED", f"Embedding generation failed: {e}")
                return False

        # 1. Update Supabase knowledge
        if self.supabase_enabled and self.supabase_url:
            # Delete old chunks
            del_url = f"{self.supabase_url}/rest/v1/task_knowledge?task_id=eq.{task_id}&name=eq.{name}"
            try:
                requests.delete(del_url, headers=self.headers, timeout=5.0)
                # Batch insert
                ins_url = f"{self.supabase_url}/rest/v1/task_knowledge"
                r = requests.post(ins_url, headers=self.headers, json=knowledge_payloads, timeout=10.0)
                if r.status_code not in [200, 201]:
                    self.update_artifact_status(task_id, name, "FAILED", f"Supabase insert failed: {r.text}")
                    return False
            except Exception as e:
                self.update_artifact_status(task_id, name, "FAILED", f"Supabase request failed: {e}")
                return False

        # 2. Update SQLite knowledge
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM task_knowledge WHERE task_id = ? AND name = ?", (task_id, name))
                for kn in knowledge_payloads:
                    conn.execute("""
                        INSERT INTO task_knowledge (task_id, name, chunk_index, chunk_text, embedding, promoted_level)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (task_id, name, kn["chunk_index"], kn["chunk_text"], json.dumps(kn["embedding"]), kn["promoted_level"]))
                conn.commit()
        except Exception as sqlite_err:
            self.update_artifact_status(task_id, name, "FAILED", f"SQLite insert failed: {sqlite_err}")
            return False

        # Mark as INDEXED on success
        self.update_artifact_status(task_id, name, "INDEXED")
        return True
