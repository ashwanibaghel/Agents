import subprocess
import shlex

class TerminalToolError(Exception):
    """Custom exception raised when terminal commands violate validation or safety checks."""
    pass

class TerminalTool:
    # Phase 1 Command Allowlist
    ALLOWLIST = [
        "git status --short",
        "git diff --check",
        "git diff --cached --check",
        "git branch --list",
        "git diff --name-only main...",
        "git diff --check main...",
        "git diff --name-only master...",
        "git diff --check master...",
        "git diff --stat main...",
        "git diff --stat master...",
        "git diff main...",
        "git diff master...",
        "git status",
        "git rev-parse HEAD",
        "git rev-parse --abbrev-ref HEAD",
        "git config --get remote.origin.url",
        "python --version",
        "pytest",
        "python -m pytest",
        "npm test",
        "npm run test",
        "npm run lint",
        "npm run build"
    ]
    
    @staticmethod
    def validate_command(command_str: str) -> list:
        """
        Validate that the command string is empty-free, has no chaining, piping, redirection,
        shell invocation, or destructive patterns, and matches the strict command allowlist.
        """
        # 1. Reject empty commands
        if not command_str or not command_str.strip():
            raise TerminalToolError("Empty command is not allowed.")
            
        # 2. Reject chaining, piping, redirection characters
        forbidden_chars = [";", "&&", "||", "|", "\n", "\r", ">", "<"]
        for char in forbidden_chars:
            if char in command_str:
                raise TerminalToolError(f"Command contains forbidden character: '{char}'.")
                
        # 3. Parse command into tokens
        try:
            tokens = shlex.split(command_str)
        except Exception as e:
            raise TerminalToolError(f"Failed to parse command arguments: {str(e)}")
            
        # 4. Check against allowlist
        normalized_cmd = " ".join(tokens)
        if normalized_cmd not in TerminalTool.ALLOWLIST:
            raise TerminalToolError(
                f"Command '{normalized_cmd}' is not in the allowlist. "
                f"Allowed commands are: {', '.join(TerminalTool.ALLOWLIST)}"
            )
            
        return tokens

    @staticmethod
    def run_command(workspace_path: str, command_str: str) -> dict:
        """
        Safely execute a validated allowlisted command in the workspace_path.
        Returns a dict conforming to ToolResult model structure.
        """
        try:
            tokens = TerminalTool.validate_command(command_str)
        except TerminalToolError as e:
            return {
                "success": False,
                "action": "RUN_COMMAND",
                "output": "",
                "error": str(e),
                "metadata": {}
            }
            
        # On Windows, pytest/npm run in paths. Let's make sure if it's pytest or npm,
        # subprocess.run can execute it using shell=False. On Windows, executables like 'npm' or 'pytest'
        # might actually be 'npm.cmd' or 'pytest.exe'. Subprocess.run with shell=False might require
        # the exact filename on Windows unless shell=True, but we must use shell=False for safety.
        # Wait, how does Windows handle 'pytest' or 'npm' with shell=False?
        # Windows search path will resolve 'pytest.exe' or 'npm.cmd' automatically if they are on path
        # in Python subprocess, but let's be careful. If 'pytest' is called, python subprocess.run
        # usually resolves it. If it fails, the user can call `python -m pytest` which runs python.exe
        # directly (this is highly portable!). That's why `python -m pytest` is in the allowlist!
        try:
            res = subprocess.run(
                tokens,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                shell=False,
                timeout=30
            )
            
            output = res.stdout + res.stderr
            success = (res.returncode == 0)
            
            # Truncate output if it exceeds size limits
            if len(output) > 8000:
                output = output[:8000] + "\n... [TRUNCATED] ..."
                
            return {
                "success": success,
                "action": "RUN_COMMAND",
                "output": output,
                "error": None if success else f"Command returned non-zero exit code: {res.returncode}",
                "metadata": {"returncode": res.returncode}
            }
        except subprocess.TimeoutExpired as e:
            output = e.stdout + e.stderr if e.stdout else ""
            if len(output) > 8000:
                output = output[:8000] + "\n... [TRUNCATED] ..."
            return {
                "success": False,
                "action": "RUN_COMMAND",
                "output": output,
                "error": "Command timed out after 30 seconds.",
                "metadata": {}
            }
        except Exception as e:
            return {
                "success": False,
                "action": "RUN_COMMAND",
                "output": "",
                "error": f"Failed to run command: {str(e)}",
                "metadata": {}
            }
