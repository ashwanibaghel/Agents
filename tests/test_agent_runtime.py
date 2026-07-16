import os
import shutil
import tempfile
import time
import sys
import unittest
import git

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from tools.file_tool import FileTool
from tools.terminal_tool import TerminalTool, TerminalToolError
from tools.git_tool import GitTool, GitError
from brains.base_brain import BaseBrain
from brains.scripted_brain import ScriptedBrain
from agents.base_agent import BaseAgent, AgentStatus
from control.dispatcher import Dispatcher
from control.workspace_manager import WorkspaceManager


class MockBrain(BaseBrain):
    def __init__(self, actions_list):
        self.actions_list = actions_list
        self.call_count = 0

    def think(self, context: dict) -> dict:
        idx = context.get("iteration", 0)
        if idx < len(self.actions_list):
            return self.actions_list[idx]
        return {
            "thought_summary": "Task complete.",
            "action": "COMPLETE_TASK",
            "action_input": {"summary": "Done"},
            "reason": "Complete",
            "task_complete": True
        }


class TestAgentRuntime(unittest.TestCase):
    def setUp(self):
        # Create temp workspace
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_path = os.path.join(self.temp_dir, "test_workspace")
        os.makedirs(self.workspace_path, exist_ok=True)
        
        # Initialize Git repo
        self.repo = git.Repo.init(self.workspace_path)
        readme_path = os.path.join(self.workspace_path, "README.md")
        with open(readme_path, "w") as f:
            f.write("# Test Readme\n")
        self.repo.index.add([readme_path])
        self.repo.index.commit("Initial commit")

        # Setup base agent
        self.agent = BaseAgent(
            agent_id="TEST-001",
            name="Test Agent",
            project="OI Labs",
            autonomy_level=2
        )
        self.agent.workspace_info = {
            "project": "oi_labs",
            "workspace": self.workspace_path,
            "branch": "master",
            "commit_sha": "dummy",
            "status": "READY"
        }

    def tearDown(self):
        # Close git repo to release locks
        self.repo.close()
        # Clean up files
        try:
            # Clean read-only files on Windows
            def remove_readonly(func, p, excinfo):
                import stat
                try:
                    os.chmod(p, stat.S_IWRITE)
                    func(p)
                except Exception:
                    pass
            shutil.rmtree(self.temp_dir, onerror=remove_readonly)
        except Exception:
            pass

    def test_fake_completion_rejected_without_observations(self):
        # Brain tries to immediately complete without doing anything
        brain_actions = [
            {
                "thought_summary": "Attempting immediate complete",
                "action": "COMPLETE_TASK",
                "action_input": {"summary": "Completed immediately!"},
                "reason": "Immediate complete",
                "task_complete": True
            }
        ]
        self.agent.brain = MockBrain(brain_actions)
        
        task = {
            "id": "T-001",
            "project": "oi_labs",
            "title": "Audit dataset training readiness",
            "task_type": "audit"
        }
        
        # Run task - should hit max iteration and fail/block because completion gate rejected it
        res = self.agent.run_task(task, max_iterations=2)
        self.assertEqual(res["status"], "BLOCKED")
        self.assertIn("Max iterations reached", res["summary"])

    def test_readonly_audit_completes_after_inspection(self):
        # Brain lists files first, then completes
        brain_actions = [
            {
                "thought_summary": "Inspecting workspace",
                "action": "LIST_FILES",
                "action_input": {},
                "reason": "Need list",
                "task_complete": False
            },
            {
                "thought_summary": "Completing audit",
                "action": "COMPLETE_TASK",
                "action_input": {"summary": "Audited workspace successfully."},
                "reason": "Inspection done",
                "task_complete": True
            }
        ]
        self.agent.brain = MockBrain(brain_actions)
        
        task = {
            "id": "T-001",
            "project": "oi_labs",
            "title": "Audit dataset training readiness",
            "task_type": "audit"
        }
        
        res = self.agent.run_task(task, max_iterations=5)
        self.assertEqual(res["status"], "DONE")
        self.assertEqual(res["summary"], "Audited workspace successfully.")
        self.assertEqual(len(res["actions_executed"]), 2)

    def test_code_task_cannot_complete_without_file_changes(self):
        # Code task tries to complete without modifying anything
        brain_actions = [
            {
                "thought_summary": "Trying to complete code task directly",
                "action": "COMPLETE_TASK",
                "action_input": {"summary": "Done"},
                "reason": "Done",
                "task_complete": True
            }
        ]
        self.agent.brain = MockBrain(brain_actions)
        
        task = {
            "id": "T-002",
            "project": "oi_labs",
            "title": "Fix bug in backend",
            "task_type": "code"
        }
        
        res = self.agent.run_task(task, max_iterations=2)
        self.assertEqual(res["status"], "BLOCKED")
        # Observation history should record the completion gate error
        has_gate_error = False
        for action_log in res["actions_executed"]:
            if action_log["action"] == "COMPLETE_TASK":
                has_gate_error = True
        self.assertTrue(has_gate_error)

    def test_code_task_completes_with_changes_and_validation(self):
        # 1. Write file
        # 2. Run command (validation)
        # 3. Complete
        brain_actions = [
            {
                "thought_summary": "Writing code fix",
                "action": "WRITE_FILE",
                "action_input": {"path": "src/fix.py", "content": "print('fixed')"},
                "reason": "Write fix",
                "task_complete": False
            },
            {
                "thought_summary": "Running validation checks",
                "action": "RUN_COMMAND",
                "action_input": {"command": "python --version"},
                "reason": "Validate",
                "task_complete": False
            },
            {
                "thought_summary": "Completing code task",
                "action": "COMPLETE_TASK",
                "action_input": {"summary": "Code modifications verified and completed."},
                "reason": "Done",
                "task_complete": True
            }
        ]
        self.agent.brain = MockBrain(brain_actions)
        
        task = {
            "id": "T-002",
            "project": "oi_labs",
            "title": "Fix bug in backend",
            "task_type": "code"
        }
        
        res = self.agent.run_task(task, max_iterations=5)
        self.assertEqual(res["status"], "DONE")
        self.assertEqual(res["files_changed"], ["src/fix.py"])
        self.assertEqual(res["validation_commands"], ["python --version"])

    def test_command_allowlist_rejects_unallowed_commands(self):
        with self.assertRaises(TerminalToolError):
            TerminalTool.validate_command("rm -rf /")
        with self.assertRaises(TerminalToolError):
            TerminalTool.validate_command("git push origin main")
        with self.assertRaises(TerminalToolError):
            TerminalTool.validate_command("pip install requests")

    def test_command_chaining_rejected(self):
        with self.assertRaises(TerminalToolError):
            TerminalTool.validate_command("python --version; rm -rf /")
        with self.assertRaises(TerminalToolError):
            TerminalTool.validate_command("python --version && git status")
        with self.assertRaises(TerminalToolError):
            TerminalTool.validate_command("python --version | grep 3")

    def test_path_traversal_rejected(self):
        with self.assertRaises(PermissionError):
            FileTool.validate_path(self.workspace_path, "../outside.txt")
        with self.assertRaises(PermissionError):
            FileTool.validate_path(self.workspace_path, "/etc/passwd")

    def test_env_read_rejected(self):
        # Create env file inside workspace
        env_path = os.path.join(self.workspace_path, ".env")
        with open(env_path, "w") as f:
            f.write("SECRET_KEY=12345")
            
        with self.assertRaises(PermissionError):
            FileTool.read_file(self.workspace_path, ".env")
        with self.assertRaises(PermissionError):
            FileTool.validate_path(self.workspace_path, ".env")

    def test_symlink_escape_rejected(self):
        # Create folder outside workspace
        outside_dir = os.path.join(self.temp_dir, "outside_dir")
        os.makedirs(outside_dir, exist_ok=True)
        secret_file = os.path.join(outside_dir, "secret.key")
        with open(secret_file, "w") as f:
            f.write("SECRET")
            
        # Try to create symlink pointing outside
        symlink_path = os.path.join(self.workspace_path, "escape_link")
        try:
            os.symlink(secret_file, symlink_path)
        except OSError:
            # Skip test if OS policies don't support symlink creation without admin privs
            self.skipTest("Symlink creation not supported in current environment.")
            
        with self.assertRaises(PermissionError):
            FileTool.read_file(self.workspace_path, "escape_link")

    def test_max_iteration_limit_stops_agent(self):
        # Brain always wants to do GET_GIT_STATUS
        brain_actions = [
            {
                "thought_summary": "Looping status",
                "action": "GET_GIT_STATUS",
                "action_input": {},
                "reason": "Loop status",
                "task_complete": False
            }
        ]
        self.agent.brain = MockBrain(brain_actions)
        
        task = {
            "id": "T-001",
            "project": "oi_labs",
            "title": "Audit dataset",
            "task_type": "audit"
        }
        
        res = self.agent.run_task(task, max_iterations=4)
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["iterations"], 4)

    def test_tool_result_output_truncation(self):
        large_file = os.path.join(self.workspace_path, "large.txt")
        with open(large_file, "w") as f:
            f.write("A" * 15000)
            
        content = FileTool.read_file(self.workspace_path, "large.txt")
        self.assertTrue(content.endswith("... [TRUNCATED DUE TO SIZE LIMIT] ..."))
        self.assertEqual(len(content), 8038) # 8000 chars + truncation marker length

    def test_task_result_contains_evidence(self):
        brain_actions = [
            {
                "thought_summary": "Inspecting",
                "action": "LIST_FILES",
                "action_input": {},
                "reason": "Inspection",
                "task_complete": False
            },
            {
                "thought_summary": "Completing",
                "action": "COMPLETE_TASK",
                "action_input": {"summary": "Successfully audited dataset."},
                "reason": "Complete",
                "task_complete": True
            }
        ]
        self.agent.brain = MockBrain(brain_actions)
        
        task = {
            "id": "T-001",
            "project": "oi_labs",
            "title": "Audit dataset training readiness",
            "task_type": "audit"
        }
        
        res = self.agent.run_task(task, max_iterations=3)
        self.assertEqual(res["status"], "DONE")
        self.assertTrue("actions_executed" in res)
        self.assertTrue("started_at" in res)
        self.assertTrue("completed_at" in res)
        self.assertEqual(len(res["actions_executed"]), 2)
        
    def test_missing_task_type_blocked(self):
        brain_actions = []
        self.agent.brain = MockBrain(brain_actions)
        
        # Missing task_type field
        task = {
            "id": "T-001",
            "project": "oi_labs",
            "title": "Audit dataset training readiness"
        }
        
        res = self.agent.run_task(task, max_iterations=5)
        self.assertEqual(res["status"], "BLOCKED")
        self.assertIn("task_type is required", res["summary"])


if __name__ == "__main__":
    unittest.main()
