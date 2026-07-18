from abc import ABC, abstractmethod
import os
import shutil
import yaml
import datetime
import contextlib
from control.task_models import Task
from control.task_parser import TaskParser

@contextlib.contextmanager
def file_lock(lock_path):
    """Simple atomic lock using OS O_CREAT and O_EXCL flags."""
    fd = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        yield fd
    except FileExistsError:
        raise BlockingIOError(f"Could not acquire lock on {lock_path}")
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass


class TaskSource(ABC):
    @abstractmethod
    def fetch_pending_tasks(self) -> list:
        pass
        
    @abstractmethod
    def claim_task(self, task_id: str, worker_id: str) -> bool:
        pass
        
    @abstractmethod
    def update_task_status(self, task_id: str, status: str, evidence: dict = None):
        pass
        
    @abstractmethod
    def release_task(self, task_id: str):
        pass


class LocalTaskSource(TaskSource):
    def __init__(self, base_dir: str, lease_timeout_seconds: float = 300.0):
        self.base_dir = base_dir
        self.lease_timeout = lease_timeout_seconds
        self.inbox_dir = os.path.join(base_dir, "inbox")
        self.working_dir = os.path.join(base_dir, "working")
        self.done_dir = os.path.join(base_dir, "done")
        self.blocked_dir = os.path.join(base_dir, "blocked")
        
        # Ensure directories exist
        for d in [self.inbox_dir, self.working_dir, self.done_dir, self.blocked_dir]:
            os.makedirs(d, exist_ok=True)
            
    def fetch_pending_tasks(self) -> list:
        """List and return all valid Tasks in the inbox directory."""
        tasks = []
        for file in os.listdir(self.inbox_dir):
            if file.endswith((".yaml", ".yml")):
                file_path = os.path.join(self.inbox_dir, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    task = TaskParser.parse_yaml(content)
                    tasks.append(task)
                except Exception:
                    # Skip invalid tasks
                    pass
        return tasks

    def claim_task(self, task_id: str, worker_id: str) -> bool:
        """
        Atomically claim a task from the inbox directory by moving it to the working folder.
        Uses atomic file renaming for inbox claims.
        """
        inbox_path = os.path.join(self.inbox_dir, f"{task_id}.yaml")
        if not os.path.exists(inbox_path):
            inbox_path = os.path.join(self.inbox_dir, f"{task_id}.yml")
            if not os.path.exists(inbox_path):
                return False
                
        working_path = os.path.join(self.working_dir, f"{task_id}.yaml")
        lock_path = os.path.join(self.working_dir, f"{task_id}.lock")
        
        # Lock file access for safety
        try:
            with file_lock(lock_path):
                # Perform atomic rename
                os.rename(inbox_path, working_path)
                
                # Write claiming details into task file
                with open(working_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                data["status"] = "working"
                data["worker_id"] = worker_id
                now_str = datetime.datetime.now().isoformat()
                data["claimed_at"] = now_str
                data["last_heartbeat_at"] = now_str
                
                with open(working_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f)
                return True
        except Exception:
            return False

    def claim_stale_task(self, task_id: str, worker_id: str) -> bool:
        """
        Atomically claim a stale task already in the working directory if its lease has timed out.
        Prevents double-execution using lock files.
        """
        src_path = os.path.join(self.working_dir, f"{task_id}.yaml")
        if not os.path.exists(src_path):
            src_path = os.path.join(self.working_dir, f"{task_id}.yml")
            if not os.path.exists(src_path):
                return False
                
        lock_path = os.path.join(self.working_dir, f"{task_id}.lock")
        
        try:
            with file_lock(lock_path):
                # Read contents inside locked region to verify staleness
                with open(src_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    
                # Verify status is working and it has a last heartbeat
                if data.get("status") != "working":
                    return False
                    
                hb_str = data.get("last_heartbeat_at")
                if not hb_str:
                    return False
                    
                hb_time = datetime.datetime.fromisoformat(hb_str)
                time_diff = (datetime.datetime.now() - hb_time).total_seconds()
                
                if time_diff >= self.lease_timeout:
                    # Task is indeed stale! Claim it
                    data["worker_id"] = worker_id
                    now_str = datetime.datetime.now().isoformat()
                    data["claimed_at"] = now_str
                    data["last_heartbeat_at"] = now_str
                    
                    with open(src_path, "w", encoding="utf-8") as f:
                        yaml.dump(data, f)
                    return True
        except Exception:
            pass
        return False

    def heartbeat_task(self, task_id: str, worker_id: str):
        """Update last_heartbeat_at timestamp to indicate progress."""
        src_path = os.path.join(self.working_dir, f"{task_id}.yaml")
        if not os.path.exists(src_path):
            src_path = os.path.join(self.working_dir, f"{task_id}.yml")
            if not os.path.exists(src_path):
                return
                
        lock_path = os.path.join(self.working_dir, f"{task_id}.lock")
        try:
            with file_lock(lock_path):
                with open(src_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data.get("worker_id") == worker_id:
                    data["last_heartbeat_at"] = datetime.datetime.now().isoformat()
                    with open(src_path, "w", encoding="utf-8") as f:
                        yaml.dump(data, f)
        except Exception:
            pass

    def update_task_status(self, task_id: str, status: str, evidence: dict = None):
        """Move task file or update status in place (delegated tasks stay in working folder)."""
        src_path = os.path.join(self.working_dir, f"{task_id}.yaml")
        if not os.path.exists(src_path):
            src_path = os.path.join(self.working_dir, f"{task_id}.yml")
            if not os.path.exists(src_path):
                return
                
        lock_path = os.path.join(self.working_dir, f"{task_id}.lock")
        
        try:
            with file_lock(lock_path):
                with open(src_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    
                data["status"] = status.lower()
                if evidence:
                    data["evidence"] = evidence
                    if "artifacts" in evidence:
                        data["artifacts"] = [{
                            "name": art["name"],
                            "path": art["path"],
                            "type": art["type"],
                            "size": art["size"],
                            "summary": art["summary"],
                            "content": art["content"]
                        } for art in evidence["artifacts"]]
                    
                with open(src_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f)
                    
                # Only move if task is resolved to DONE or BLOCKED
                if status.upper() == "DONE":
                    shutil.move(src_path, os.path.join(self.done_dir, f"{task_id}.yaml"))
                elif status.upper() == "BLOCKED":
                    shutil.move(src_path, os.path.join(self.blocked_dir, f"{task_id}.yaml"))
        except Exception as e:
            # Fallback simple move
            if status.upper() in ["DONE", "BLOCKED"]:
                dest_dir = self.done_dir if status.upper() == "DONE" else self.blocked_dir
                try:
                    shutil.move(src_path, os.path.join(dest_dir, f"{task_id}.yaml"))
                except Exception:
                    pass

    def release_task(self, task_id: str):
        """Manually release a claimed task back to inbox (does not trigger automatically)."""
        src_path = os.path.join(self.working_dir, f"{task_id}.yaml")
        if not os.path.exists(src_path):
            src_path = os.path.join(self.working_dir, f"{task_id}.yml")
            if not os.path.exists(src_path):
                return
                
        dest_path = os.path.join(self.inbox_dir, f"{task_id}.yaml")
        lock_path = os.path.join(self.working_dir, f"{task_id}.lock")
        
        try:
            with file_lock(lock_path):
                with open(src_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                data["status"] = "inbox"
                if "worker_id" in data:
                    del data["worker_id"]
                if "claimed_at" in data:
                    del data["claimed_at"]
                if "last_heartbeat_at" in data:
                    del data["last_heartbeat_at"]
                with open(src_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f)
                shutil.move(src_path, dest_path)
        except Exception:
            # Fallback move
            try:
                shutil.move(src_path, dest_path)
            except Exception:
                pass

    def recover_stale_tasks(self) -> int:
        """Find stale tasks in working folder and release them back to inbox."""
        recovered = 0
        if not os.path.exists(self.working_dir):
            return 0
        for file in os.listdir(self.working_dir):
            if file.endswith((".yaml", ".yml")):
                file_path = os.path.join(self.working_dir, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    task_id = data.get("task_id")
                    heartbeat_str = data.get("last_heartbeat_at")
                    if heartbeat_str and task_id:
                        hb = datetime.datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                        now = datetime.datetime.now(datetime.timezone.utc) if hb.tzinfo else datetime.datetime.now()
                        age_seconds = (now - hb).total_seconds()
                        if age_seconds > self.lease_timeout:
                            self.release_task(task_id)
                            recovered += 1
                except Exception:
                    pass
        return recovered

