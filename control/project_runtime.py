import os
import sqlite3
import datetime
import subprocess
from tools.git_tool import GitTool, GitError

class SessionManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def get_session(self, project_id: str) -> dict:
        """Fetch the persistent session metadata for a project."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT conversation_id, workspace_path, repository_url, default_branch, 
                       current_branch, last_commit, last_activity, status, locked_by, locked_at, 
                       created_at, updated_at, retry_count, last_error, next_retry_at
                FROM project_sessions WHERE project_id = ?
            """, (project_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "project_id": project_id,
                    "conversation_id": row[0],
                    "workspace_path": row[1],
                    "repository_url": row[2],
                    "default_branch": row[3],
                    "current_branch": row[4],
                    "last_commit": row[5],
                    "last_activity": row[6],
                    "status": row[7],
                    "locked_by": row[8],
                    "locked_at": row[9],
                    "created_at": row[10],
                    "updated_at": row[11],
                    "retry_count": row[12] or 0,
                    "last_error": row[13],
                    "next_retry_at": row[14]
                }
        return None

    def save_session(self, project_id: str, conversation_id: str, workspace_path: str = None, 
                     repository_url: str = None, default_branch: str = None, current_branch: str = None, 
                     last_commit: str = None, status: str = "ACTIVE", locked_by: str = None, 
                     locked_at: str = None, retry_count: int = None, last_error: str = None,
                     next_retry_at: str = None) -> None:
         """Save or update the persistent session for a project."""
         now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
         session = self.get_session(project_id)
         
         # Keep existing fields if none are provided
         if session:
             workspace_path = workspace_path or session["workspace_path"]
             repository_url = repository_url or session["repository_url"]
             default_branch = default_branch or session["default_branch"]
             current_branch = current_branch or session["current_branch"]
             last_commit = last_commit or session["last_commit"]
             locked_by = locked_by if locked_by is not None else session["locked_by"]
             locked_at = locked_at if locked_at is not None else session["locked_at"]
             created_at = session["created_at"]
             retry_count = retry_count if retry_count is not None else session["retry_count"]
             last_error = last_error if last_error is not None else session["last_error"]
             next_retry_at = next_retry_at if next_retry_at is not None else session["next_retry_at"]
         else:
             created_at = now_str
             retry_count = retry_count or 0
 
         with sqlite3.connect(self.db_path) as conn:
             conn.execute("""
                 INSERT INTO project_sessions (
                     project_id, conversation_id, workspace_path, repository_url, default_branch, 
                     current_branch, last_commit, last_activity, status, locked_by, locked_at, 
                     created_at, updated_at, retry_count, last_error, next_retry_at
                 )
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(project_id) DO UPDATE SET
                     conversation_id = excluded.conversation_id,
                     workspace_path = excluded.workspace_path,
                     repository_url = excluded.repository_url,
                     default_branch = excluded.default_branch,
                     current_branch = excluded.current_branch,
                     last_commit = excluded.last_commit,
                     last_activity = excluded.last_activity,
                     status = excluded.status,
                     locked_by = excluded.locked_by,
                     locked_at = excluded.locked_at,
                     updated_at = excluded.updated_at,
                     retry_count = excluded.retry_count,
                     last_error = excluded.last_error,
                     next_retry_at = excluded.next_retry_at
             """, (project_id, conversation_id, workspace_path, repository_url, default_branch, 
                   current_branch, last_commit, now_str, status, locked_by, locked_at, 
                   created_at, now_str, retry_count, last_error, next_retry_at))
             conn.commit()

    def check_session_status(self, project_id: str, client) -> str:
        """Validate conversation presence with Antigravity server and transition states."""
        session = self.get_session(project_id)
        if not session or not session.get("conversation_id"):
            return "MISSING"
            
        conv_id = session["conversation_id"]
        res = client.get_conversation_metadata(conv_id)
        
        status = "ACTIVE"
        if not res.get("success"):
            # Check if conversation is deleted or broken
            err_msg = str(res.get("error") or "").lower()
            if any(term in err_msg for term in ["not found", "expired", "could not find", "deleted"]):
                status = "MISSING"
            else:
                status = "BROKEN"
        else:
            # Check last activity age to classify ACTIVE vs IDLE
            try:
                meta = res.get("response", {}).get("conversationMetadata", {})
                # Support both metadata wrapper levels
                if not meta:
                    meta = res.get("response", {})
                inner_meta = meta.get("metadata", meta)
                last_time_str = inner_meta.get("lastActivityTime") or inner_meta.get("updatedAt") or inner_meta.get("createdAt")
                if last_time_str:
                    last_time = datetime.datetime.fromisoformat(last_time_str.replace("Z", "+00:00"))
                    diff = (datetime.datetime.now(datetime.timezone.utc) - last_time).total_seconds()
                    if diff > 300.0:   # > 5 minutes → IDLE (resumable)
                        status = "IDLE"
                    # else: ACTIVE (recently used)
            except Exception:
                pass
                
        self.save_session(project_id, conversation_id=conv_id, status=status)
        return status

    def acquire_lock(self, project_id: str, worker_id: str) -> bool:
        """Centralized database-backed workspace locking."""
        session = self.get_session(project_id)
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        if session:
            locked_by = session.get("locked_by")
            locked_at_str = session.get("locked_at")
            
            # Lock is active if owned by someone else and lock hasn't expired (10 minutes lease)
            if locked_by and locked_by != worker_id:
                if locked_at_str:
                    try:
                        locked_at = datetime.datetime.fromisoformat(locked_at_str.replace("Z", "+00:00"))
                        if (datetime.datetime.now(datetime.timezone.utc) - locked_at).total_seconds() < 600.0:
                            return False # Locked by another worker
                    except Exception:
                        pass
                        
        # Save lock to database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE project_sessions 
                SET locked_by = ?, locked_at = ?, updated_at = ?
                WHERE project_id = ?
            """, (worker_id, now_str, now_str, project_id))
            conn.commit()
        return True

    def release_lock(self, project_id: str, worker_id: str) -> bool:
        """Release the workspace lock if owned by this worker."""
        session = self.get_session(project_id)
        if not session or session.get("locked_by") != worker_id:
            return False
            
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE project_sessions 
                SET locked_by = NULL, locked_at = NULL, updated_at = ?
                WHERE project_id = ?
            """, (now_str, project_id))
            conn.commit()
        return True


class WorkspaceManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def verify_or_update_workspace(self, project_id: str, repo_url: str, workspace_path: str) -> None:
        """Never delete workspace folder. Update remote URL if mismatched."""
        if not os.path.exists(workspace_path):
            os.makedirs(workspace_path, exist_ok=True)
            
        if not GitTool.is_git_repository(workspace_path):
            # Safe initialize
            subprocess.run(["git", "init"], cwd=workspace_path, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=workspace_path, capture_output=True)
        else:
            current_remote = GitTool.get_remote_url(workspace_path)
            def normalize(url):
                return url.strip().replace("\\", "/").rstrip("/").lower()
                
            if normalize(current_remote) != normalize(repo_url):
                # Safe remote update instead of re-cloning
                subprocess.run(["git", "remote", "set-url", "origin", repo_url], cwd=workspace_path, capture_output=True)


class MemoryManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def get_memory(self, project_id: str) -> dict:
        """Fetch persistent memory fields for a project."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT architecture, pending_todos, known_bugs, recent_decisions, 
                       coding_style, framework, backend_notes, oracle_notes, 
                       design_rules, owner_instructions, updated_at
                FROM project_memories WHERE project_id = ?
            """, (project_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "project_id": project_id,
                    "architecture": row[0],
                    "pending_todos": row[1],
                    "known_bugs": row[2],
                    "recent_decisions": row[3],
                    "coding_style": row[4],
                    "framework": row[5],
                    "backend_notes": row[6],
                    "oracle_notes": row[7],
                    "design_rules": row[8],
                    "owner_instructions": row[9],
                    "updated_at": row[10]
                }
        return None

    def save_memory(self, project_id: str, memory_data: dict) -> None:
        """Save or update persistent memory fields for a project."""
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        current = self.get_memory(project_id) or {}
        
        fields = [
            "architecture", "pending_todos", "known_bugs", "recent_decisions",
            "coding_style", "framework", "backend_notes", "oracle_notes",
            "design_rules", "owner_instructions"
        ]
        
        payload = {}
        for f in fields:
            payload[f] = memory_data.get(f) if f in memory_data else current.get(f)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO project_memories (
                    project_id, architecture, pending_todos, known_bugs, recent_decisions, 
                    coding_style, framework, backend_notes, oracle_notes, design_rules, 
                    owner_instructions, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    architecture = excluded.architecture,
                    pending_todos = excluded.pending_todos,
                    known_bugs = excluded.known_bugs,
                    recent_decisions = excluded.recent_decisions,
                    coding_style = excluded.coding_style,
                    framework = excluded.framework,
                    backend_notes = excluded.backend_notes,
                    oracle_notes = excluded.oracle_notes,
                    design_rules = excluded.design_rules,
                    owner_instructions = excluded.owner_instructions,
                    updated_at = excluded.updated_at
            """, (project_id, payload["architecture"], payload["pending_todos"], payload["known_bugs"],
                  payload["recent_decisions"], payload["coding_style"], payload["framework"],
                  payload["backend_notes"], payload["oracle_notes"], payload["design_rules"],
                  payload["owner_instructions"], now_str))
            conn.commit()

    def get_memory_prompt(self, project_id: str) -> str:
        """Generate formatted prompt context block from project memory."""
        mem = self.get_memory(project_id)
        if not mem:
            return ""
            
        blocks = ["[PROJECT PERSISTENT RUNTIME MEMORY]"]
        for k, v in mem.items():
            if k in ["project_id", "updated_at"]:
                continue
            if v and v.strip():
                blocks.append(f"### {k.replace('_', ' ').upper()}:\n{v.strip()}")
                
        if len(blocks) == 1:
            return ""
        return "\n".join(blocks) + "\n"


class GitManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def is_clean(self, workspace_path: str) -> bool:
        """Check if workspace working tree is clean."""
        res = subprocess.run(["git", "status", "--short"], cwd=workspace_path, capture_output=True, text=True)
        return res.returncode == 0 and not res.stdout.strip()

    def prepare_feature_branch(self, workspace_path: str, task_id: str, default_branch: str = "main") -> dict:
        """Strict clean tree checks and automated branch setup."""
        if not self.is_clean(workspace_path):
            return {
                "success": False,
                "error": "Workspace working tree is dirty; aborting branch checkout to prevent file overwrite."
            }
            
        try:
            # 1. Fetch
            subprocess.run(["git", "fetch", "--all"], cwd=workspace_path, capture_output=True, timeout=60)
            # 2. Checkout default branch
            subprocess.run(["git", "checkout", default_branch], cwd=workspace_path, capture_output=True)
            # 3. Pull
            subprocess.run(["git", "pull", "--ff-only"], cwd=workspace_path, capture_output=True)
            # 4. Checkout -B task-id
            branch_name = f"task-{task_id}"
            subprocess.run(["git", "checkout", "-B", branch_name], cwd=workspace_path, capture_output=True)
            return {"success": True, "branch": branch_name}
        except Exception as e:
            return {"success": False, "error": f"Failed setting up task branch: {str(e)}"}

    def publish_feature_branch(self, workspace_path: str, task_id: str) -> dict:
        """Automatically commit and push verified changes."""
        branch_name = f"task-{task_id}"
        try:
            # Explicit checkout of the task branch before any commits/push
            subprocess.run(["git", "checkout", branch_name], cwd=workspace_path, capture_output=True)
            # 1. Git add
            subprocess.run(["git", "add", "."], cwd=workspace_path, capture_output=True)
            # 2. Git commit
            msg = f"feat: completed task {task_id}"
            subprocess.run(["git", "commit", "-m", msg], cwd=workspace_path, capture_output=True)
            # 3. Git push
            subprocess.run(["git", "push", "origin", branch_name, "--force"], cwd=workspace_path, capture_output=True)
            
            # Fetch remote origin URL
            res_remote = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=workspace_path, capture_output=True, text=True)
            url = res_remote.stdout.strip()
            if url.endswith(".git"):
                url = url[:-4]
            if url.startswith("git@github.com:"):
                url = "https://github.com/" + url[len("git@github.com:"):]
            elif url.startswith("git://github.com/"):
                url = "https://github.com/" + url[len("git://github.com/"):]
                
            github_url = f"{url}/tree/{branch_name}" if url else None
            commit_sha = GitTool.get_current_commit_sha(workspace_path)
            
            return {
                "success": True,
                "branch": branch_name,
                "commit_sha": commit_sha,
                "github_url": github_url
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to publish git branch: {str(e)}"}


class TaskManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def inject_task_context(self, task: dict) -> str:
        """Inject structured task details to prevent conversation context drift."""
        task_id = task.get("id")
        project = task.get("project")
        objective = task.get("title")
        criteria = task.get("acceptance_criteria", [])
        
        criteria_str = "\n".join(f"- {c}" for c in criteria) if criteria else "None"
        
        return f"""[CURRENT ACTIVE TASK DETAILS]
Task ID: {task_id}
Project: {project}
Objective: {objective}
Acceptance Criteria:
{criteria_str}
--------------------------------------------------
"""


class ProjectRuntimeManager:
    def __init__(self, db_path="state/task_checkpoints.db"):
        self.db_path = db_path
        self.sessions = SessionManager(db_path)
        self.workspaces = WorkspaceManager(db_path)
        self.memories = MemoryManager(db_path)
        self.git = GitManager(db_path)
        self.tasks = TaskManager(db_path)
