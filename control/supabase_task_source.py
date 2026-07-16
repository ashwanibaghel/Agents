import json
import requests
import datetime
from control.task_source import TaskSource
from control.task_models import Task

class SupabaseTaskSource(TaskSource):
    def __init__(self, supabase_url: str, supabase_key: str, lease_timeout_seconds: float = 300.0):
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key
        self.lease_timeout = lease_timeout_seconds
        
        self.headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json"
        }

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
                return len(data) > 0
            return False
        except Exception as e:
            print(f"⚠️ Supabase claim error: {str(e)}")
            return False

    def update_task_status(self, task_id: str, status: str, evidence: dict = None):
        """Update status and final evidence of a task in Supabase."""
        url = f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Map statuses internally
        mapped_status = status.lower()
        if mapped_status == "working":
            mapped_status = "delegated"  # Keep consistent with BaseAgent lifecycle
            
        payload = {
            "status": mapped_status,
            "updated_at": now_iso
        }
        
        if status == "DONE" and evidence:
            payload.update({
                "summary": evidence.get("summary"),
                "evidence_paths": evidence.get("evidence_paths", []),
                "files_changed": evidence.get("files_changed", []),
                "validation_results": evidence.get("validation_results", [])
            })
        elif (status in ["FAILED", "BLOCKED"]) and evidence:
            payload.update({
                "error_message": evidence.get("error")
            })
            
        try:
            response = requests.patch(url, headers=self.headers, json=payload, timeout=10.0)
            if response.status_code not in [200, 204]:
                print(f"⚠️ Supabase status update failed ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"⚠️ Supabase update error: {str(e)}")

    def release_task(self, task_id: str):
        """Release a task back to 'inbox' status, clearing worker claim data."""
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
            if response.status_code not in [200, 204]:
                print(f"⚠️ Supabase release failed ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"⚠️ Supabase release error: {str(e)}")

    def heartbeat_task(self, task_id: str, worker_id: str):
        """Update last_heartbeat_at so lease remains alive during long execution."""
        url = f"{self.supabase_url}/rest/v1/tasks?task_id=eq.{task_id}&worker_id=eq.{worker_id}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = {"last_heartbeat_at": now_iso, "updated_at": now_iso}
        try:
            requests.patch(url, headers=self.headers, json=payload, timeout=10.0)
        except Exception:
            pass  # Heartbeat failure is non-fatal

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
                    self.release_task(row["task_id"])
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
