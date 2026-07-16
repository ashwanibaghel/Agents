import os
import shutil
import tempfile
import sys
import unittest
import yaml
import git

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from tools.git_tool import GitTool, GitError
from control.workspace_manager import WorkspaceManager
from control.dispatcher import Dispatcher
from agents.oi_agent import OIAgent
from agents.dkffj_agent import DKFFJAgent
from agents.tehsil_agent import TehsilAgent


class TestWorkspaceGit(unittest.TestCase):
    def setUp(self):
        # Create a temp directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.project_root = os.path.join(self.temp_dir, "project_root")
        os.makedirs(self.project_root, exist_ok=True)
        
        # Create workspaces folder inside project_root
        self.workspaces_dir = os.path.join(self.project_root, "workspaces")
        os.makedirs(self.workspaces_dir, exist_ok=True)
        
        # Setup a local remote repository
        self.remote_dir = os.path.join(self.temp_dir, "remote_origin")
        os.makedirs(self.remote_dir, exist_ok=True)
        
        # Initialize remote git repository
        self.remote_repo = git.Repo.init(self.remote_dir)
        readme_path = os.path.join(self.remote_dir, "README.md")
        with open(readme_path, "w") as f:
            f.write("# Mock Origin\n")
        self.remote_repo.index.add([readme_path])
        self.remote_repo.index.commit("Initial commit")

        # Create config projects.yaml path
        self.config_dir = os.path.join(self.project_root, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_path = os.path.join(self.config_dir, "projects.yaml")

    def tearDown(self):
        # Clean up temp directory
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def write_config(self, config_dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_dict, f)

    def test_git_tool_detection(self):
        self.assertTrue(GitTool.is_git_installed())

    def test_workspace_isolation_and_clone(self):
        # Setup configuration with mock repository URL (local file path)
        config = {
            "company": {"name": "Test Company", "max_parallel_agents": 2},
            "projects": {
                "oi_labs": {
                    "name": "OI Labs",
                    "agent": "oi_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/oi-labs"
                },
                "dkffj": {
                    "name": "DKFFJ",
                    "agent": "dkffj_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/dkffj"
                }
            }
        }
        self.write_config(config)

        wm = WorkspaceManager(project_root=self.project_root, config_path=self.config_path)
        
        # Prepare workspaces
        info_oi = wm.prepare_workspace("oi_labs")
        info_dk = wm.prepare_workspace("dkffj")

        # Verify workspaces are READY and isolated
        self.assertEqual(info_oi["status"], "READY")
        self.assertEqual(info_dk["status"], "READY")
        self.assertTrue(os.path.exists(info_oi["workspace"]))
        self.assertTrue(os.path.exists(info_dk["workspace"]))
        self.assertNotEqual(info_oi["workspace"], info_dk["workspace"])

        # Check git repositories exist
        self.assertTrue(GitTool.is_git_repository(info_oi["workspace"]))
        self.assertTrue(GitTool.is_git_repository(info_dk["workspace"]))

    def test_missing_repository_url_blocked(self):
        config = {
            "company": {"name": "Test Company", "max_parallel_agents": 1},
            "projects": {
                "oi_labs": {
                    "name": "OI Labs",
                    "agent": "oi_agent",
                    "repository": "", # Blank repository
                    "workspace": "workspaces/oi-labs"
                }
            }
        }
        self.write_config(config)
        wm = WorkspaceManager(project_root=self.project_root, config_path=self.config_path)

        with self.assertRaises(ValueError) as ctx:
            wm.prepare_workspace("oi_labs")
        self.assertIn("Repository URL not configured", str(ctx.exception))

    def test_invalid_repository_does_not_crash_dispatcher(self):
        config = {
            "company": {"name": "Test Company", "max_parallel_agents": 1},
            "projects": {
                "oi_labs": {
                    "name": "OI Labs",
                    "agent": "oi_agent",
                    "repository": "/invalid/path/that/does/not/exist",
                    "workspace": "workspaces/oi-labs"
                }
            }
        }
        self.write_config(config)
        wm = WorkspaceManager(project_root=self.project_root, config_path=self.config_path)
        
        agents = {"oi_labs": OIAgent()}
        dispatcher = Dispatcher(agents=agents, max_parallel_agents=1, workspace_manager=wm)
        
        tasks = [{
            "id": "OI-001",
            "project": "oi_labs",
            "title": "Test task",
            "task_type": "audit"
        }]

        # Dispatch task - should not crash, returns BLOCKED
        results = dispatcher.dispatch(tasks)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "BLOCKED")
        self.assertIn("Failed to clone repository", results[0]["reason"])

    def test_one_failed_project_does_not_stop_others(self):
        config = {
            "company": {"name": "Test Company", "max_parallel_agents": 2},
            "projects": {
                "oi_labs": {
                    "name": "OI Labs",
                    "agent": "oi_agent",
                    "repository": "/invalid/path",
                    "workspace": "workspaces/oi-labs"
                },
                "dkffj": {
                    "name": "DKFFJ",
                    "agent": "dkffj_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/dkffj"
                }
            }
        }
        self.write_config(config)
        wm = WorkspaceManager(project_root=self.project_root, config_path=self.config_path)
        
        agents = {
            "oi_labs": OIAgent(),
            "dkffj": DKFFJAgent()
        }
        dispatcher = Dispatcher(agents=agents, max_parallel_agents=2, workspace_manager=wm)
        
        tasks = [
            {"id": "OI-001", "project": "oi_labs", "title": "Audit dataset", "task_type": "audit"},
            {"id": "DK-001", "project": "dkffj", "title": "Test workflow", "task_type": "audit"}
        ]

        results = dispatcher.dispatch(tasks)
        self.assertEqual(len(results), 2)
        
        # Sort results by status to verify
        results_map = {res["project"]: res for res in results}
        self.assertEqual(results_map["OI Labs"]["status"], "BLOCKED")
        self.assertEqual(results_map["DKFFJ"]["status"], "DONE")

    def test_existing_git_workspace_detected_and_pulled(self):
        config = {
            "company": {"name": "Test Company", "max_parallel_agents": 1},
            "projects": {
                "oi_labs": {
                    "name": "OI Labs",
                    "agent": "oi_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/oi-labs"
                }
            }
        }
        self.write_config(config)
        wm = WorkspaceManager(project_root=self.project_root, config_path=self.config_path)

        # 1. Clone workspace first
        info_first = wm.prepare_workspace("oi_labs")
        sha_first = info_first["commit_sha"]

        # 2. Add new commit to remote origin
        readme_path = os.path.join(self.remote_dir, "README.md")
        with open(readme_path, "a") as f:
            f.write("New lines of code\n")
        self.remote_repo.index.add([readme_path])
        self.remote_repo.index.commit("Second commit")

        # 3. Call prepare_workspace again, it should detect and pull
        info_second = wm.prepare_workspace("oi_labs")
        sha_second = info_second["commit_sha"]

        self.assertNotEqual(sha_first, sha_second)
        self.assertEqual(info_second["status"], "READY")

    def test_workspace_path_cannot_escape_workspaces_directory(self):
        config = {
            "company": {"name": "Test Company", "max_parallel_agents": 1},
            "projects": {
                "oi_labs": {
                    "name": "OI Labs",
                    "agent": "oi_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/../escaped_directory" # Attempts to escape
                }
            }
        }
        self.write_config(config)
        wm = WorkspaceManager(project_root=self.project_root, config_path=self.config_path)

        with self.assertRaises(ValueError) as ctx:
            wm.prepare_workspace("oi_labs")
        self.assertIn("escapes the main workspaces directory", str(ctx.exception))

    def test_parallel_dispatch_and_agent_workspace_binding(self):
        config = {
            "company": {"name": "Test Company", "max_parallel_agents": 3},
            "projects": {
                "oi_labs": {
                    "name": "OI Labs",
                    "agent": "oi_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/oi-labs"
                },
                "dkffj": {
                    "name": "DKFFJ",
                    "agent": "dkffj_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/dkffj"
                },
                "tehsil": {
                    "name": "Tehsil Projects",
                    "agent": "tehsil_agent",
                    "repository": self.remote_dir,
                    "workspace": "workspaces/tehsil"
                }
            }
        }
        self.write_config(config)
        wm = WorkspaceManager(project_root=self.project_root, config_path=self.config_path)

        agents = {
            "oi_labs": OIAgent(),
            "dkffj": DKFFJAgent(),
            "tehsil": TehsilAgent()
        }
        dispatcher = Dispatcher(agents=agents, max_parallel_agents=3, workspace_manager=wm)

        tasks = [
            {"id": "OI-001", "project": "oi_labs", "title": "Task 1", "task_type": "audit"},
            {"id": "DK-001", "project": "dkffj", "title": "Task 2", "task_type": "audit"},
            {"id": "TE-001", "project": "tehsil", "title": "Task 3", "task_type": "audit"}
        ]

        results = dispatcher.dispatch(tasks)
        self.assertEqual(len(results), 3)
        for res in results:
            self.assertEqual(res["status"], "DONE")

        # Verify workspace info was bound to the agents correctly
        self.assertIsNotNone(agents["oi_labs"].workspace_info)
        self.assertIsNotNone(agents["dkffj"].workspace_info)
        self.assertIsNotNone(agents["tehsil"].workspace_info)
        self.assertEqual(agents["oi_labs"].workspace_info["project"], "oi_labs")
        self.assertEqual(agents["dkffj"].workspace_info["project"], "dkffj")
        self.assertEqual(agents["tehsil"].workspace_info["project"], "tehsil")


if __name__ == "__main__":
    unittest.main()
