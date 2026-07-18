import os
import shutil
import git

class GitError(Exception):
    """Custom exception raised by GitTool for all git operations."""
    pass

class GitTool:
    @staticmethod
    def is_git_installed() -> bool:
        """Detect whether git is installed and available in the system path."""
        return shutil.which("git") is not None

    @staticmethod
    def clone_repository(repository_url: str, workspace_path: str):
        """Clone a git repository to the specified workspace path."""
        if not GitTool.is_git_installed():
            raise GitError("Git is not installed on the system.")
        
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(workspace_path), exist_ok=True)
        
        try:
            # Clones repository from url to path
            git.Repo.clone_from(repository_url, workspace_path)
        except Exception as e:
            raise GitError(f"Failed to clone repository from '{repository_url}': {str(e)}")

    @staticmethod
    def is_git_repository(workspace_path: str) -> bool:
        """Detect whether a workspace is already a git repository."""
        if not os.path.exists(workspace_path):
            return False
        try:
            with git.Repo(workspace_path) as repo:
                pass
            return True
        except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError):
            return False

    @staticmethod
    def get_remote_url(workspace_path: str, remote_name: str = "origin") -> str:
        """Get the remote URL for the configured remote_name."""
        if not GitTool.is_git_repository(workspace_path):
            raise GitError(f"Workspace path '{workspace_path}' is not a valid git repository.")
        try:
            with git.Repo(workspace_path) as repo:
                return repo.remotes[remote_name].url
        except Exception as e:
            raise GitError(f"Failed to get remote URL for '{remote_name}': {str(e)}")

    @staticmethod
    def pull_latest(workspace_path: str):
        """Pull latest changes safely from the remote branch."""
        if not GitTool.is_git_repository(workspace_path):
            raise GitError(f"Workspace path '{workspace_path}' is not a valid git repository.")
        try:
            with git.Repo(workspace_path) as repo:
                # Checkout main or master default branch if present and not current
                active_branch = repo.active_branch.name if not repo.head.is_detached else None
                for b in ["main", "master"]:
                    if b in repo.heads and b != active_branch:
                        repo.heads[b].checkout()
                        break
                # Fetch remote changes first
                repo.git.fetch("--all")
                # Pull with --ff-only to ensure safe, fast-forward changes without auto-merge to main
                active_branch_name = repo.active_branch.name if not repo.head.is_detached else "main"
                repo.git.pull("origin", active_branch_name, "--ff-only")
        except Exception as e:
            raise GitError(f"Failed to pull latest changes safely: {str(e)}")

    @staticmethod
    def get_current_branch(workspace_path: str) -> str:
        """Get current active branch of the git workspace."""
        if not GitTool.is_git_repository(workspace_path):
            raise GitError(f"Workspace path '{workspace_path}' is not a valid git repository.")
        try:
            with git.Repo(workspace_path) as repo:
                if repo.head.is_detached:
                    return "detached"
                return repo.active_branch.name
        except Exception as e:
            raise GitError(f"Failed to get current branch: {str(e)}")

    @staticmethod
    def get_current_commit_sha(workspace_path: str) -> str:
        """Get the current commit SHA of HEAD."""
        if not GitTool.is_git_repository(workspace_path):
            raise GitError(f"Workspace path '{workspace_path}' is not a valid git repository.")
        try:
            with git.Repo(workspace_path) as repo:
                return repo.head.commit.hexsha
        except Exception as e:
            raise GitError(f"Failed to get current commit SHA: {str(e)}")

    @staticmethod
    def get_repository_status(workspace_path: str) -> str:
        """Get the current repository status output."""
        if not GitTool.is_git_repository(workspace_path):
            raise GitError(f"Workspace path '{workspace_path}' is not a valid git repository.")
        try:
            with git.Repo(workspace_path) as repo:
                return repo.git.status("--porcelain")
        except Exception as e:
            raise GitError(f"Failed to get repository status: {str(e)}")

    @staticmethod
    def create_task_branch(workspace_path: str, branch_name: str):
        """Create a task branch and checkout to it. Switch to it if it exists."""
        if not GitTool.is_git_repository(workspace_path):
            raise GitError(f"Workspace path '{workspace_path}' is not a valid git repository.")
        try:
            with git.Repo(workspace_path) as repo:
                if branch_name in repo.heads:
                    repo.heads[branch_name].checkout()
                else:
                    repo.create_head(branch_name).checkout()
        except Exception as e:
            raise GitError(f"Failed to create or switch to task branch '{branch_name}': {str(e)}")
