import os
import shutil
import yaml
from tools.git_tool import GitTool, GitError

class WorkspaceManager:
    def __init__(self, project_root: str = None, config_path: str = None):
        if not project_root:
            # Resolve to the project root directory (one level up from 'control')
            self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        else:
            self.project_root = os.path.abspath(project_root)
            
        if not config_path:
            self.config_path = os.path.join(self.project_root, "config", "projects.yaml")
        else:
            self.config_path = os.path.abspath(config_path)
            
        self.config = self.load_config()

    def load_config(self) -> dict:
        """Load and return the project configuration from YAML."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found at: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as file:
            return yaml.safe_load(file)

    def get_workspaces_dir(self) -> str:
        """Get the absolute path of the workspaces directory."""
        return os.path.join(self.project_root, "workspaces")

    def prepare_workspace(self, project_id: str) -> dict:
        """
        Prepare the workspace for the given project:
        - Check if git is installed
        - Resolve workspace path relative to project root
        - Create the workspaces directory when missing
        - Validate that workspace path stays isolated inside workspaces directory
        - Clone repo if workspace does not exist
        - Verify remote URL and pull safely if repo exists
        - Return structured workspace information
        """
        if not GitTool.is_git_installed():
            raise GitError("Git is not installed on the system.")

        # Ensure workspaces directory exists
        workspaces_dir = self.get_workspaces_dir()
        os.makedirs(workspaces_dir, exist_ok=True)

        # Get project config
        project_config = self.config.get("projects", {}).get(project_id)
        if not project_config:
            raise ValueError(f"Project '{project_id}' not found in configuration.")

        # Check if project is active
        if not project_config.get("active", True):
            raise ValueError(f"Project '{project_id}' is inactive in configuration.")

        workspace_rel = project_config.get("workspace")
        if not workspace_rel:
            raise ValueError(f"Workspace path not configured for project '{project_id}'.")

        # Resolve workspace path to absolute
        workspace_path = os.path.abspath(os.path.join(self.project_root, workspace_rel))

        # Validate that path doesn't escape workspaces directory
        abs_workspaces_dir = os.path.abspath(workspaces_dir)
        try:
            common = os.path.commonpath([abs_workspaces_dir, workspace_path])
            if common != abs_workspaces_dir or workspace_path == abs_workspaces_dir:
                raise ValueError(
                    f"Workspace path '{workspace_path}' escapes the main workspaces directory '{abs_workspaces_dir}'"
                )
        except ValueError as e:
            raise ValueError(f"Workspace path validation failed: {str(e)}")

        repo_url = project_config.get("repository")
        if not repo_url or repo_url.strip() == "":
            raise ValueError(f"Repository URL not configured for project '{project_id}'.")

        # Workspace directory setup
        if not os.path.exists(workspace_path):
            # Workspace does not exist, clone repository
            GitTool.clone_repository(repo_url, workspace_path)
        else:
            # Workspace exists, verify remote URL and pull safely
            if not GitTool.is_git_repository(workspace_path):
                print(f"⚠️ Workspace path '{workspace_path}' exists but is not a valid git repository. Deleting and re-cloning...")
                abs_workspaces_dir = os.path.abspath(self.get_workspaces_dir())
                abs_workspace_path = os.path.abspath(workspace_path)
                try:
                    common = os.path.commonpath([abs_workspaces_dir, abs_workspace_path])
                    if common == abs_workspaces_dir and abs_workspace_path != abs_workspaces_dir:
                        def remove_readonly(func, p, excinfo):
                            import stat
                            try:
                                os.chmod(p, stat.S_IWRITE)
                                func(p)
                            except Exception:
                                pass
                        shutil.rmtree(abs_workspace_path, onerror=remove_readonly)
                    else:
                        raise ValueError(f"Workspace path '{abs_workspace_path}' is unsafe for deletion.")
                except Exception as e:
                    raise GitError(f"Failed to safely delete invalid workspace: {str(e)}")
                
                GitTool.clone_repository(repo_url, workspace_path)
            else:
                current_remote_url = GitTool.get_remote_url(workspace_path)
                
                # Simple URL normalization for comparison
                def normalize_url(url: str) -> str:
                    return url.strip().replace("\\", "/").rstrip("/").lower()

                if normalize_url(current_remote_url) != normalize_url(repo_url):
                    # Safely delete incorrect workspace folder
                    abs_workspaces_dir = os.path.abspath(self.get_workspaces_dir())
                    abs_workspace_path = os.path.abspath(workspace_path)
                    try:
                        common = os.path.commonpath([abs_workspaces_dir, abs_workspace_path])
                        if common == abs_workspaces_dir and abs_workspace_path != abs_workspaces_dir:
                            print(f"🗑️ Deleting mismatched workspace: {abs_workspace_path}")
                            
                            # Define error handler to clean read-only files on Windows
                            def remove_readonly(func, p, excinfo):
                                import stat
                                try:
                                    os.chmod(p, stat.S_IWRITE)
                                    func(p)
                                except Exception:
                                    pass
                                    
                            shutil.rmtree(abs_workspace_path, onerror=remove_readonly)
                        else:
                            raise ValueError(f"Workspace path '{abs_workspace_path}' is unsafe for deletion.")
                    except Exception as e:
                        raise GitError(f"Failed to safely delete incorrect workspace: {str(e)}")
                    
                    # Clone the correct repository
                    GitTool.clone_repository(repo_url, workspace_path)
                else:
                    # Pull latest changes safely
                    GitTool.pull_latest(workspace_path)

        # Get branch and commit details
        branch = GitTool.get_current_branch(workspace_path)
        commit_sha = GitTool.get_current_commit_sha(workspace_path)

        return {
            "project": project_id,
            "workspace": workspace_path,
            "branch": branch,
            "commit_sha": commit_sha,
            "status": "READY"
        }
