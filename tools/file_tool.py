import os
import shutil

class FileTool:
    @staticmethod
    def validate_path(workspace_path: str, relative_path: str) -> str:
        """
        Validate that the target path resides inside the workspace and is safe.
        Rejects path traversal, absolute paths outside workspace, symlinks escaping, and secret files.
        """
        abs_workspace = os.path.abspath(workspace_path)
        
        # 1. Reject credentials and obvious secret file names
        base_name = os.path.basename(relative_path).lower()
        if base_name in [".env", "id_rsa"] or base_name.endswith(".key") or base_name.endswith(".pem"):
            raise PermissionError("Access to credentials, private keys, or .env files is forbidden.")
            
        # 2. Resolve the target path
        if os.path.isabs(relative_path):
            target_path = os.path.abspath(relative_path)
        else:
            target_path = os.path.abspath(os.path.join(abs_workspace, relative_path))
            
        # 3. Reject path traversal / workspace escape
        try:
            common = os.path.commonpath([abs_workspace, target_path])
            if common != abs_workspace or target_path == abs_workspace:
                raise PermissionError(f"Path '{relative_path}' escapes the assigned workspace directory.")
        except ValueError as e:
            raise PermissionError(f"Path validation failed: {str(e)}")
            
        # 4. Reject symlinks pointing outside the workspace (resolves symlinks recursively)
        # Note: If target_path or any parent directory is a symlink, realpath will expand it.
        real_target = os.path.realpath(target_path)
        try:
            common_real = os.path.commonpath([abs_workspace, real_target])
            if common_real != abs_workspace:
                raise PermissionError(f"Path '{relative_path}' resolves to a location outside the workspace via symlink.")
        except ValueError as e:
            raise PermissionError(f"Symlink validation failed: {str(e)}")
            
        return target_path

    @staticmethod
    def list_files(workspace_path: str) -> list:
        """Recursively list up to 500 files in the workspace (excluding .git)."""
        abs_workspace = os.path.abspath(workspace_path)
        file_list = []
        for root, dirs, files in os.walk(abs_workspace):
            if ".git" in dirs:
                dirs.remove(".git")
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, abs_workspace)
                try:
                    # Validate to filter out secrets/unauthorized files
                    FileTool.validate_path(workspace_path, rel_path)
                    file_list.append(rel_path)
                except PermissionError:
                    continue
                if len(file_list) >= 500:
                    break
            if len(file_list) >= 500:
                break
        return file_list

    @staticmethod
    def read_file(workspace_path: str, relative_path: str) -> str:
        """Read content of a file, limited to 8,000 characters."""
        target_path = FileTool.validate_path(workspace_path, relative_path)
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"File '{relative_path}' does not exist.")
        if os.path.isdir(target_path):
            raise IsADirectoryError(f"'{relative_path}' is a directory.")
            
        # Read the file up to slightly more than limit to detect if it requires truncation
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(10000)
            if len(content) > 8000:
                return content[:8000] + "\n... [TRUNCATED DUE TO SIZE LIMIT] ..."
            return content

    @staticmethod
    def search_code(workspace_path: str, query: str) -> list:
        """Search for query string inside files in workspace, capped at 50 matches."""
        abs_workspace = os.path.abspath(workspace_path)
        results = []
        count = 0
        for root, dirs, files in os.walk(abs_workspace):
            if ".git" in dirs:
                dirs.remove(".git")
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, abs_workspace)
                try:
                    FileTool.validate_path(workspace_path, rel_path)
                except PermissionError:
                    continue
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if query in line:
                                results.append({
                                    "file": rel_path,
                                    "line": line_num,
                                    "content": line.strip()
                                })
                                count += 1
                                if count >= 50:
                                    return results
                except Exception:
                    pass
        return results

    @staticmethod
    def write_file(workspace_path: str, relative_path: str, content: str):
        """Write content to a file, making a backup if it already exists."""
        target_path = FileTool.validate_path(workspace_path, relative_path)
        if os.path.isdir(target_path):
            raise IsADirectoryError(f"'{relative_path}' is a directory.")
            
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        
        # Backup existing file to preserve original content information
        if os.path.exists(target_path):
            backup_path = target_path + ".bak"
            shutil.copy2(target_path, backup_path)
            
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
