import os
from brains.base_brain import BaseBrain

class ScriptedBrain(BaseBrain):
    def think(self, context: dict) -> dict:
        iteration = context.get("iteration", 0)
        project = context.get("project", "")
        observations = context.get("observations", [])

        # Parse files from the LIST_FILES observation if available
        files = []
        for obs in observations:
            if obs.get("action") == "LIST_FILES" and obs.get("success"):
                output = obs.get("output", "")
                lines = output.splitlines()
                if len(lines) > 1:
                    # The first line is "Found X files..." - skip it
                    files = [line.strip() for line in lines[1:] if line.strip() and not line.startswith("...")]
                break

        # Define file safety filtering
        def is_safe_file(filepath: str) -> bool:
            path_lower = filepath.lower().replace("\\", "/")
            # Exclude folders
            for segment in path_lower.split("/"):
                if segment in ["node_modules", ".git", "dist", "build", ".next", "__pycache__"]:
                    return False
            # Exclude lock, credentials, and binary files
            base_name = os.path.basename(path_lower)
            if base_name in [".env", "id_rsa"] or base_name.endswith(".key") or base_name.endswith(".pem"):
                return False
            if base_name.endswith(("-lock.json", "lock.yaml", "lock.json", ".lock")):
                return False
            if base_name.endswith((".zip", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".pyc", ".db", ".exe", ".idx", ".pack")):
                return False
            return True

        safe_files = [f for f in files if is_safe_file(f)]

        # Select a file to read based on preference order:
        # 1. README.md
        # 2. README*
        # 3. pyproject.toml
        # 4. package.json
        # 5. requirements.txt
        # 6. safe extensions
        selected_file = None
        
        # 1. README.md
        for f in safe_files:
            if os.path.basename(f).lower() == "readme.md":
                selected_file = f
                break
        # 2. README*
        if not selected_file:
            for f in safe_files:
                if os.path.basename(f).lower().startswith("readme"):
                    selected_file = f
                    break
        # 3. pyproject.toml
        if not selected_file:
            for f in safe_files:
                if os.path.basename(f).lower() == "pyproject.toml":
                    selected_file = f
                    break
        # 4. package.json
        if not selected_file:
            for f in safe_files:
                if os.path.basename(f).lower() == "package.json":
                    selected_file = f
                    break
        # 5. requirements.txt
        if not selected_file:
            for f in safe_files:
                if os.path.basename(f).lower() == "requirements.txt":
                    selected_file = f
                    break
        # 6. Safe code/text extensions
        if not selected_file:
            safe_exts = (".py", ".js", ".ts", ".tsx", ".md", ".json", ".yaml", ".yml")
            for f in safe_files:
                if f.lower().endswith(safe_exts):
                    selected_file = f
                    break

        # Fallback to first safe file
        if not selected_file and safe_files:
            selected_file = safe_files[0]
            
        # Hard fallback
        if not selected_file:
            selected_file = "README.md"

        # Derive search query based on selected file or task context
        search_query = "main"
        if selected_file:
            # Use file name root (e.g., 'package' or 'README')
            search_query = os.path.splitext(os.path.basename(selected_file))[0]
        else:
            task_title = context.get("task", {}).get("title", "")
            words = [w for w in task_title.split() if len(w) > 4]
            if words:
                search_query = words[0]

        # Scripted Loop Steps
        if iteration == 0:
            return {
                "thought_summary": "Listing files to locate configuration and audit-worthy documents.",
                "action": "LIST_FILES",
                "action_input": {},
                "reason": "Need to understand the directory structure of the repository.",
                "task_complete": False
            }
        elif iteration == 1:
            return {
                "thought_summary": f"Reading contents of the chosen file '{selected_file}'.",
                "action": "READ_FILE",
                "action_input": {"path": selected_file},
                "reason": "Auditing the file context and configuration setup.",
                "task_complete": False
            }
        elif iteration == 2:
            return {
                "thought_summary": f"Searching code for pattern related to '{search_query}'.",
                "action": "SEARCH_CODE",
                "action_input": {"query": search_query},
                "reason": "Locating usage and imports across the codebase.",
                "task_complete": False
            }
        elif iteration == 3:
            return {
                "thought_summary": "Checking the active repository git status.",
                "action": "GET_GIT_STATUS",
                "action_input": {},
                "reason": "Verifying repository is clean and has no uncommitted changes.",
                "task_complete": False
            }
        elif iteration == 4:
            return {
                "thought_summary": "Running validation command (git status --short).",
                "action": "RUN_COMMAND",
                "action_input": {"command": "git status --short"},
                "reason": "Checking if any modifications were made.",
                "task_complete": False
            }
        elif iteration == 5:
            # Complete task with descriptive summary of observations
            summary_msg = f"Audit completed for project '{project}'. Read '{selected_file}', searched query '{search_query}', and verified clean git state."
            return {
                "thought_summary": "Completing audit after capturing inspections.",
                "action": "COMPLETE_TASK",
                "action_input": {"summary": summary_msg},
                "reason": "Inspected files and verified git status successfully.",
                "task_complete": True
            }
        else:
            return {
                "thought_summary": "Task is already complete.",
                "action": "COMPLETE_TASK",
                "action_input": {"summary": "Task complete."},
                "reason": "Task complete.",
                "task_complete": True
            }
