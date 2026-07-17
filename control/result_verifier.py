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

        elif task_type in ["code", "feature"]:
            task_id = task.get("id", "")
            
            # Find and checkout task branch if feature task
            original_branch = None
            if task_type == "feature":
                import subprocess
                task_branch = f"task-{task_id}"
                res_branches = TerminalTool.run_command(workspace_path, "git branch --list")
                if not res_branches.get("success"):
                    return False, f"Failed to list branches: {res_branches.get('error')}", {}
                
                branches_output = res_branches.get("output", "")
                has_branch = False
                for line in branches_output.splitlines():
                    b_name = line.replace("*", "").strip()
                    if b_name == task_branch:
                        has_branch = True
                        break
                        
                if not has_branch:
                    return False, f"No git branch found with exact name '{task_branch}'.", {}
                
                # Get current branch to restore later
                res_curr = TerminalTool.run_command(workspace_path, "git rev-parse --abbrev-ref HEAD")
                if res_curr.get("success"):
                    original_branch = res_curr.get("output", "").strip()
                    
                # Checkout task branch
                checkout_res = subprocess.run(["git", "checkout", task_branch], cwd=workspace_path, capture_output=True, text=True)
                if checkout_res.returncode != 0:
                    return False, f"Failed to checkout task branch '{task_branch}': {checkout_res.stderr}", {}

            try:
                # 1. At least one expected repository file changed
                files_changed = receipt_data.get("files_changed", [])
                if not files_changed:
                    return False, f"{task_type.capitalize()} task requires at least one files_changed entry.", {}
                    
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
                    
                # 4. Independently compare git status/diff and verify files_changed consistency
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

                # For feature tasks, also get committed files on current branch vs main/master
                if task_type == "feature":
                    # Try main (allowlisted), fall back to master
                    res_branch_diff = TerminalTool.run_command(workspace_path, "git diff --name-only main...")
                    if not res_branch_diff.get("success") or not res_branch_diff.get("output", "").strip():
                        res_branch_diff = TerminalTool.run_command(workspace_path, "git diff --name-only master...")
                    if res_branch_diff.get("success"):
                        diff_output = res_branch_diff.get("output", "")
                        for line in diff_output.splitlines():
                            if line.strip():
                                git_modified_paths.append(line.strip().replace("\\", "/"))

                
                # De-duplicate modified paths
                git_modified_paths = list(set(git_modified_paths))
                        
                # Every file in files_changed must be in git_modified_paths, and vice versa
                normalized_receipt_files = [f.replace("\\", "/").strip() for f in files_changed]
                for f in normalized_receipt_files:
                    if f not in git_modified_paths:
                        return False, f"File '{f}' listed in receipt files_changed was not modified in actual Git status or branch diff.", {}
                        
                for f in git_modified_paths:
                    # Exclude any untracked or backup files like .bak
                    if f.endswith(".bak"):
                        continue
                    if f not in normalized_receipt_files:
                        return False, f"Workspace contains modification in '{f}' which is not documented in receipt files_changed.", {}
                        
                # 5. Run git diff check
                if task_type == "feature":
                    res_diff = TerminalTool.run_command(workspace_path, "git diff --check main...")
                    if not res_diff.get("success") or "unknown revision" in (res_diff.get("error") or "").lower() or "unknown revision" in (res_diff.get("output") or "").lower():
                        res_diff = TerminalTool.run_command(workspace_path, "git diff --check master...")
                    if not res_diff.get("success"):
                        # Fallback to local check if branch comparison fails
                        res_diff_local = TerminalTool.run_command(workspace_path, "git diff --check")
                        if not res_diff_local.get("success"):
                            return False, f"Git diff check failed: {res_diff_local.get('output') or res_diff_local.get('error')}", {}
                else:
                    res_diff = TerminalTool.run_command(workspace_path, "git diff --check")
                    if not res_diff.get("success"):
                        return False, f"Git diff check failed: {res_diff.get('output') or res_diff.get('error')}", {}
                    
                    res_diff_cached = TerminalTool.run_command(workspace_path, "git diff --cached --check")
                    if not res_diff_cached.get("success"):
                        return False, f"Git diff cached check failed: {res_diff_cached.get('output') or res_diff_cached.get('error')}", {}
                    
                # 6. Rerun every configured validation command
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
                        
                return True, f"{task_type.capitalize()} task successfully verified.", {"validation_results": validation_details}
            finally:
                if original_branch:
                    import subprocess
                    subprocess.run(["git", "checkout", original_branch], cwd=workspace_path, capture_output=True)
            
        else:
            return False, f"Unknown task_type: '{task_type}'.", {}

    @staticmethod
    def generate_feature_proofs(workspace_path: str) -> str:
        """
        Generate a markdown proof report for a feature task containing diff, diff --stat, status, branch, and commit.
        """
        # 1. Branch
        branch = "unknown"
        res_branch = TerminalTool.run_command(workspace_path, "git rev-parse --abbrev-ref HEAD")
        if res_branch.get("success"):
            branch = res_branch.get("output", "").strip()

        # 2. Commit hash
        commit = "unknown"
        res_commit = TerminalTool.run_command(workspace_path, "git rev-parse HEAD")
        if res_commit.get("success"):
            commit = res_commit.get("output", "").strip()

        # 3. GitHub URL
        github_url = None
        res_remote = TerminalTool.run_command(workspace_path, "git config --get remote.origin.url")
        if res_remote.get("success"):
            url = res_remote.get("output", "").strip()
            if url:
                if url.endswith(".git"):
                     url = url[:-4]
                if url.startswith("git@github.com:"):
                     url = "https://github.com/" + url[len("git@github.com:"):]
                elif url.startswith("git://github.com/"):
                     url = "https://github.com/" + url[len("git://github.com/"):]
                github_url = f"{url}/tree/{branch}"

        # Determine if base is main or master
        base_branch = "main"
        res_base_test = TerminalTool.run_command(workspace_path, "git diff --name-only main...")
        if not res_base_test.get("success"):
            base_branch = "master"

        # 4. git diff --stat
        diff_stat = "No changes."
        res_stat = TerminalTool.run_command(workspace_path, f"git diff --stat {base_branch}...")
        if res_stat.get("success"):
            diff_stat = res_stat.get("output", "")

        # 5. git diff
        git_diff = "No changes."
        res_diff = TerminalTool.run_command(workspace_path, f"git diff {base_branch}...")
        if res_diff.get("success"):
            git_diff = res_diff.get("output", "")

        # 6. git status
        git_status = "Clean."
        res_status = TerminalTool.run_command(workspace_path, "git status")
        if res_status.get("success"):
            git_status = res_status.get("output", "")

        # Format markdown output
        report = []
        report.append("\n\n### 🛡️ VERIFIED FEATURE PROOF (V2.1)")
        report.append(f"- **Current Branch**: `{branch}`")
        report.append(f"- **Commit Hash**: `{commit}`")
        if github_url:
            report.append(f"- **GitHub URL**: [{github_url}]({github_url})")
        report.append("\n**Git Status**:")
        report.append(f"```\n{git_status.strip()}\n```")
        report.append("\n**Git Diff Stat**:")
        report.append(f"```\n{diff_stat.strip()}\n```")
        report.append("\n**Git Diff**:")
        report.append(f"```diff\n{git_diff.strip()}\n```")
        
        return "\n".join(report)
