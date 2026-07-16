import os
from tools.terminal_tool import TerminalTool
from tools.file_tool import FileTool

class ResultVerifier:
    @staticmethod
    def verify_result(task: dict, workspace_info: dict, receipt_data: dict) -> tuple:
        """
        Verify the task outcomes independently.
        Returns:
            (success (bool), error_msg (str), validation_details (dict))
        """
        task_type = task.get("task_type")
        workspace_path = workspace_info.get("workspace")
        if not workspace_path or not os.path.exists(workspace_path):
            return False, "Workspace path is missing or invalid.", {}
            
        summary = receipt_data.get("summary")
        if not summary or len(summary.strip()) < 10:
            return False, "Receipt summary is missing or too short.", {}
            
        if task_type == "audit":
            # 1. Non-empty evidence_paths
            evidence_paths = receipt_data.get("evidence_paths", [])
            if not evidence_paths:
                return False, "Audit task requires non-empty evidence_paths.", {}

            # 2. Check each evidence path exists on disk.
            # Evidence paths may be absolute (Antigravity worker writes them as absolute).
            # For relative paths, resolve inside workspace. For absolute paths, just verify existence.
            for path in evidence_paths:
                if os.path.isabs(path):
                    resolved = os.path.normpath(path)
                else:
                    try:
                        resolved = FileTool.validate_path(workspace_path, path)
                    except PermissionError as pe:
                        return False, f"Evidence path validation failed: {str(pe)}", {}
                if not os.path.exists(resolved):
                    return False, f"Evidence path '{path}' does not exist on disk.", {}

            # 3. Independently verify repository remains clean
            res_git = TerminalTool.run_command(workspace_path, "git status --short")
            if not res_git.get("success"):
                return False, f"Failed to run git status check: {res_git.get('error')}", {}
            if res_git.get("output", "").strip():
                return False, f"Audit task left repository dirty: {res_git.get('output')}", {}

            # 4. Rerun validation commands
            commands = task.get("validation_commands", [])
            validation_details = []
            for cmd in commands:
                res_cmd = TerminalTool.run_command(workspace_path, cmd)
                validation_details.append({
                    "command": cmd,
                    "success": res_cmd.get("success"),
                    "output": res_cmd.get("output")
                })
                if not res_cmd.get("success"):
                    return False, f"Validation command '{cmd}' failed during independent verification.", {"validation_results": validation_details}

            return True, "Audit task successfully verified.", {"validation_results": validation_details}

        elif task_type == "code":
            # 1. At least one expected repository file changed
            files_changed = receipt_data.get("files_changed", [])
            if not files_changed:
                return False, "Code task requires at least one files_changed entry.", {}
                
            # 2. Files changed must resolve inside assigned workspace and not include secrets or receipt file
            for path in files_changed:
                try:
                    # Rejects secrets and path traversal
                    FileTool.validate_path(workspace_path, path)
                except PermissionError as pe:
                    return False, f"File changed validation failed: {str(pe)}", {}
                    
                # No receipt file in target repository
                if os.path.basename(path).endswith(".json") and "receipt" in path.lower():
                    return False, f"Receipt file '{path}' is not allowed inside the target repository.", {}
                    
            # 3. Independently compare git status/diff and verify files_changed consistency
            res_git = TerminalTool.run_command(workspace_path, "git status --short")
            if not res_git.get("success"):
                return False, f"Failed to run git status: {res_git.get('error')}", {}
                
            git_output = res_git.get("output", "")
            # Git status returns lines like: " M file.py" or "?? src/new.py"
            git_modified_paths = []
            for line in git_output.splitlines():
                if len(line) > 3:
                    path_part = line[3:].strip().strip('"').replace("\\", "/")
                    git_modified_paths.append(path_part)
                    
            # Every file in files_changed must be in git_modified_paths, and vice versa
            normalized_receipt_files = [f.replace("\\", "/").strip() for f in files_changed]
            for f in normalized_receipt_files:
                if f not in git_modified_paths:
                    return False, f"File '{f}' listed in receipt files_changed was not modified in actual Git status.", {}
                    
            for f in git_modified_paths:
                # Exclude any untracked or backup files like .bak
                if f.endswith(".bak"):
                    continue
                if f not in normalized_receipt_files:
                    return False, f"Workspace contains modification in '{f}' which is not documented in receipt files_changed.", {}
                    
            # 4. Run git diff check and git diff --cached --check (non-zero exit code if trailing whitespace, conflicts, etc.)
            res_diff = TerminalTool.run_command(workspace_path, "git diff --check")
            if not res_diff.get("success"):
                return False, f"Git diff check failed: {res_diff.get('output') or res_diff.get('error')}", {}
                
            res_diff_cached = TerminalTool.run_command(workspace_path, "git diff --cached --check")
            if not res_diff_cached.get("success"):
                return False, f"Git diff cached check failed: {res_diff_cached.get('output') or res_diff_cached.get('error')}", {}
                
            # 5. Rerun every configured validation command
            commands = task.get("validation_commands", [])
            validation_details = []
            for cmd in commands:
                res_cmd = TerminalTool.run_command(workspace_path, cmd)
                validation_details.append({
                    "command": cmd,
                    "success": res_cmd.get("success"),
                    "output": res_cmd.get("output")
                })
                if not res_cmd.get("success"):
                    return False, f"Validation command '{cmd}' failed during independent verification.", {"validation_results": validation_details}
                    
            return True, "Code task successfully verified.", {"validation_results": validation_details}
            
        else:
            return False, f"Unknown task_type: '{task_type}'.", {}
