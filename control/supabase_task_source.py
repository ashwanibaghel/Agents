import json
import requests
import datetime
import time
from control.task_source import TaskSource
from control.task_models import Task

class SupabaseTaskSource(TaskSource):
    def __init__(self, supabase_url: str, supabase_key: str, lease_timeout_seconds: float = 600.0):
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key
        # Default lease timeout is 10 minutes (600s) for stale task recovery
        self.lease_timeout = lease_timeout_seconds
        self.started_at_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        self.headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json"
        }

    def log_event(self, task_id: str, event_type: str, old_status: str, new_status: str, message: str):
        """Log a task event into the task_events table in Supabase."""
        url = f"{self.supabase_url}/rest/v1/task_events"
        payload = {
            "task_id": task_id,
            "event_type": event_type,
            "old_status": old_status,
            "new_status": new_status,
            "message": message
        }
        try:
            requests.post(url, headers=self.headers, json=payload, timeout=5.0)
        except Exception:
            pass  # Defensive: fail silently if table doesn't exist yet

    def update_worker_heartbeat(self, worker_id: str, current_task_id: str = None):
        """Update last_heartbeat_at for the worker in the tasks table under a special system task (Upsert)."""
        task_id = f"SYSTEM-WORKER-{worker_id.upper()}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Postgrest upsert syntax: POST with Prefer: resolution=merge-duplicates
        payload = {
            "task_id": task_id,
            "project": "system",
            "task_type": "audit",
            "objective": "Worker presence heartbeat",
            "status": "done",
            "worker_id": worker_id,
            "last_heartbeat_at": now_iso,
            "updated_at": now_iso,
            "context": json.dumps({"current_task_id": current_task_id, "started_at": self.started_at_iso})
        }
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates"}
        try:
            requests.post(f"{self.supabase_url}/rest/v1/tasks?on_conflict=task_id", headers=headers, json=payload, timeout=5.0)
        except Exception:
            pass  # Defensive: fail silently if DB write fails

    def fetch_pending_tasks(self) -> list:
        """Fetch all tasks with status 'inbox' from Supabase."""
        url = f"{self.supabase_url}/rest/v1/tasks?status=eq.inbox"
        try:
            response = requests.get(url, headers=self.headers, timeout=10.0)
            if response.status_code != 200:
                print(f"⚠️ Supabase fetch failed ({response.status_code}): {response.text}")
                return []
            
            rows = response.json()
            tasks = []
            for row in rows:
                tasks.append(self._row_to_task(row))
            return tasks
        except Exception as e:
            print(f"⚠️ Supabase fetch error: {str(e)}")
            return []

    def fetch_active_tasks(self, worker_id: str) -> list:
        """Fetch all tasks with status 'delegated' or 'claimed' for this worker from Supabase."""
        url = f"{self.supabase_url}/rest/v1/tasks?worker_id=eq.{worker_id}&status=in.(claimed,delegated)"
        try:
            response = requests.get(url, headers=self.headers, timeout=10.0)
            if response.status_code != 200:
                print(f"⚠️ Supabase fetch active failed ({response.status_code}): {response.text}")
                return []
            
            rows = response.json()
            tasks = []
            for row in rows:
                tasks.append(self._row_to_task(row))
            return tasks
        except Exception as e:
            print(f"⚠️ Supabase fetch active error: {str(e)}")
            return []

    def claim_task(self, task_id: str, worker_id: str) -> bool:
        """Atomically claim a task by updating status from 'inbox' to 'claimed'."""
        url = f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}&status=eq.inbox"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        payload = {
            "status": "claimed",
            "worker_id": worker_id,
            "claimed_at": now_iso,
            "last_heartbeat_at": now_iso,
            "updated_at": now_iso
        }
        
        headers = {**self.headers, "Prefer": "return=representation"}
        try:
            response = requests.patch(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                if len(data) > 0:
                    self.log_event(task_id, "status_changed", "inbox", "claimed", f"Task claimed by worker {worker_id}")
                    # Also update worker_status table
                    self.update_worker_heartbeat(worker_id, task_id)
                    return True
            return False
        except Exception as e:
            print(f"⚠️ Supabase claim error: {str(e)}")
            return False

    def update_task_status(self, task_id: str, status: str, evidence: dict = None):
        """Update status and final evidence of a task in Supabase."""
        # Get old status first for events log
        old_status = "working"
        try:
            r_get = requests.get(f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}&select=status", headers=self.headers, timeout=5.0)
            if r_get.status_code == 200 and r_get.json():
                old_status = r_get.json()[0].get("status", "working")
        except Exception:
            pass

        url = f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        mapped_status = status.lower()
        if mapped_status == "working":
            mapped_status = "delegated"
            
        payload = {
            "status": mapped_status,
            "updated_at": now_iso
        }
        
        if mapped_status == "inbox":
            payload.update({
                "worker_id": None,
                "claimed_at": None,
                "last_heartbeat_at": None,
                "error_message": evidence.get("error") or evidence.get("summary") if evidence else None
            })
        elif status.upper() == "DONE" and evidence:
            payload.update({
                "summary": evidence.get("summary"),
                "evidence_paths": evidence.get("evidence_paths", []),
                "files_changed": evidence.get("files_changed", []),
                "validation_results": evidence.get("validation_results", [])
            })
            
            # Batch insert/upsert artifacts to task_artifacts table
            artifacts = evidence.get("artifacts", [])
            if artifacts:
                art_url = f"{self.supabase_url}/rest/v1/task_artifacts"
                art_headers = dict(self.headers)
                art_headers["Prefer"] = "resolution=merge-duplicates"
                art_payloads = []
                for art in artifacts:
                    art_payloads.append({
                        "task_id": task_id,
                        "name": art["name"],
                        "path": art["path"],
                        "type": art["type"],
                        "size": art["size"],
                        "summary": art["summary"],
                        "content": art["content"]
                    })
                try:
                    r_art = requests.post(art_url, headers=art_headers, json=art_payloads, timeout=10.0)
                    if r_art.status_code not in [200, 201]:
                        print(f"⚠️ Failed to insert task artifacts: {r_art.text}")
                except Exception as e:
                    print(f"⚠️ Error inserting task artifacts: {e}")
                    
            # Batch insert knowledge chunks to task_knowledge table
            knowledge = evidence.get("knowledge", [])
            if knowledge:
                kn_url = f"{self.supabase_url}/rest/v1/task_knowledge"
                try:
                    # Clear old chunks first to avoid duplicate growth on retry
                    requests.delete(f"{kn_url}?task_id=eq.{task_id}", headers=self.headers, timeout=5.0)
                except Exception:
                    pass
                kn_payloads = []
                for kn in knowledge:
                    kn_payloads.append({
                        "task_id": task_id,
                        "name": kn["name"],
                        "chunk_index": kn["chunk_index"],
                        "chunk_text": kn["chunk_text"],
                        "embedding": kn["embedding"]
                    })
                try:
                    r_kn = requests.post(kn_url, headers=self.headers, json=kn_payloads, timeout=10.0)
                    if r_kn.status_code not in [200, 201]:
                        print(f"⚠️ Failed to insert task knowledge: {r_kn.text}")
                except Exception as e:
                    print(f"⚠️ Error inserting task knowledge: {e}")

            # Local SQLite cache sync
            import sqlite3
            try:
                with sqlite3.connect("state/task_checkpoints.db") as conn:
                    for art in artifacts:
                        conn.execute("""
                            INSERT INTO task_artifacts (task_id, name, path, type, size, summary, content)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(task_id, name) DO UPDATE SET
                                path=excluded.path,
                                type=excluded.type,
                                size=excluded.size,
                                summary=excluded.summary,
                                content=excluded.content,
                                updated_at=CURRENT_TIMESTAMP
                        """, (task_id, art["name"], art["path"], art["type"], art["size"], art["summary"], art["content"]))
                    
                    conn.execute("DELETE FROM task_knowledge WHERE task_id = ?", (task_id,))
                    for kn in knowledge:
                        conn.execute("""
                            INSERT INTO task_knowledge (task_id, name, chunk_index, chunk_text, embedding)
                            VALUES (?, ?, ?, ?, ?)
                        """, (task_id, kn["name"], kn["chunk_index"], kn["chunk_text"], json.dumps(kn["embedding"])))
                    conn.commit()
            except Exception as sqlite_err:
                print(f"⚠️ SQLite artifacts cache update failed: {sqlite_err}")
        elif (status.upper() in ["FAILED", "BLOCKED"]) and evidence:
            payload.update({
                "error_message": evidence.get("error") or evidence.get("summary")
            })
            
        # Retry logic (up to 3 attempts) for robustness against transient timeouts
        for attempt in range(1, 4):
            try:
                response = requests.patch(url, headers=self.headers, json=payload, timeout=10.0)
                if response.status_code in [200, 204]:
                    self.log_event(task_id, "status_changed", old_status, mapped_status, f"Task status updated to {mapped_status}")
                    break
                else:
                    print(f"⚠️ Supabase update status failed (attempt {attempt}, code {response.status_code}): {response.text}")
            except Exception as e:
                print(f"⚠️ Supabase update error (attempt {attempt}): {str(e)}")
            if attempt < 3:
                time.sleep(2.0)

    def release_task(self, task_id: str, reason: str = "Task released back to inbox"):
        """Release a task back to 'inbox' status, clearing worker claim data."""
        # Get old status first for events log
        old_status = "claimed"
        try:
            r_get = requests.get(f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}&select=status", headers=self.headers, timeout=5.0)
            if r_get.status_code == 200 and r_get.json():
                old_status = r_get.json()[0].get("status", "claimed")
        except Exception:
            pass

        url = f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        payload = {
            "status": "inbox",
            "worker_id": None,
            "claimed_at": None,
            "last_heartbeat_at": None,
            "updated_at": now_iso
        }
        
        try:
            response = requests.patch(url, headers=self.headers, json=payload, timeout=10.0)
            if response.status_code in [200, 204]:
                self.log_event(task_id, "status_changed", old_status, "inbox", reason)
        except Exception as e:
            print(f"⚠️ Supabase release error: {str(e)}")

    def heartbeat_task(self, task_id: str, worker_id: str) -> bool:
        """Update last_heartbeat_at so lease remains alive during long execution."""
        url = f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}&worker_id=eq.{worker_id}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = {"last_heartbeat_at": now_iso, "updated_at": now_iso}
        try:
            r = requests.patch(url, headers=self.headers, json=payload, timeout=10.0)
            # Also update worker_status table heartbeat
            self.update_worker_heartbeat(worker_id, task_id)
            return r.status_code in (200, 204)
        except Exception:
            return False

    def recover_stale_tasks(self) -> int:
        """
        Find tasks in 'claimed'/'delegated' status whose heartbeat has expired
        and reset them to 'inbox' so another worker can pick them up.
        Returns number of tasks recovered.
        """
        url = f"{self.supabase_url}/rest/v1/tasks?status=in.(claimed,delegated)"
        try:
            response = requests.get(url, headers=self.headers, timeout=10.0)
            if response.status_code != 200:
                return 0
            rows = response.json()
        except Exception:
            return 0

        recovered = 0
        now = datetime.datetime.now(datetime.timezone.utc)
        for row in rows:
            heartbeat_str = row.get("last_heartbeat_at")
            if not heartbeat_str:
                continue
            try:
                hb = datetime.datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                age_seconds = (now - hb).total_seconds()
                if age_seconds > self.lease_timeout:
                    reason = f"Stale task recovered: no heartbeat for {int(age_seconds)}s (limit {int(self.lease_timeout)}s)"
                    self.release_task(row["task_id"], reason=reason)
                    print(f"♻️  Recovered stale task {row['task_id']} (heartbeat {int(age_seconds)}s ago)")
                    recovered += 1
            except Exception:
                continue
        return recovered

    def _row_to_task(self, row: dict) -> Task:
        """Convert a Supabase row dictionary to a Task model."""
        return Task(
            task_id=row.get("task_id"),
            project=row.get("project"),
            task_type=row.get("task_type"),
            objective=row.get("objective"),
            context=row.get("context", ""),
            acceptance_criteria=row.get("acceptance_criteria") or [],
            constraints=row.get("constraints") or [],
            validation_commands=row.get("validation_commands") or [],
            autonomy_level=row.get("autonomy_level", 1),
            status=row.get("status", "inbox"),
            worker_id=row.get("worker_id"),
            claimed_at=row.get("claimed_at"),
            last_heartbeat_at=row.get("last_heartbeat_at"),
            summary=row.get("summary"),
            evidence_paths=row.get("evidence_paths") or [],
            files_changed=row.get("files_changed") or [],
            validation_results=row.get("validation_results") or [],
            error_message=row.get("error_message")
        )
