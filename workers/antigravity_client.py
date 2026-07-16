import os
import subprocess
import json

class AntigravityClient:
    DEFAULT_EXE_PATH = r"C:\Users\ashwa\AppData\Local\Programs\Antigravity IDE\resources\app\extensions\antigravity\bin\language_server_windows_x64.exe"
    DEFAULT_BAT_PATH = r"C:\Users\ashwa\.gemini\antigravity-ide\bin\agentapi.bat"
    
    def __init__(self, bat_path=None):
        self.bat_path = bat_path or self.DEFAULT_BAT_PATH
        self.use_direct_exe = (bat_path is None)

    def exists(self) -> bool:
        """Check if either the direct language server executable or bat script exists."""
        if self.use_direct_exe and os.path.exists(self.DEFAULT_EXE_PATH):
            return True
        return os.path.exists(self.bat_path)

    def _execute(self, args: list, timeout: float = 60.0) -> dict:
        """Helper to run agentapi subprocess and capture output defensively."""
        if not self.exists():
            return {
                "success": False,
                "output": "",
                "error": f"No valid agentapi execution endpoint found (not found at '{self.bat_path}').",
                "response": {}
            }
            
        if self.use_direct_exe and os.path.exists(self.DEFAULT_EXE_PATH):
            cmd = [self.DEFAULT_EXE_PATH, "agentapi"] + args
        else:
            cmd = [self.bat_path] + args
            
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout
            )
            stdout = res.stdout
            stderr = res.stderr
            combined_output = (stdout + "\n" + stderr).strip()
            
            # Parse JSON response defensively
            parsed_json = {}
            error_msg = None
            
            try:
                start_idx = combined_output.find("{")
                if start_idx != -1:
                    json_str = combined_output[start_idx:]
                    parsed_json = json.loads(json_str)
                else:
                    error_msg = "No JSON response found in command output."
            except Exception as json_err:
                error_msg = f"Failed to parse JSON response: {str(json_err)}"
                
            success = (res.returncode == 0) and not error_msg and ("error" not in parsed_json or not parsed_json["error"])
            
            if "error" in parsed_json and parsed_json["error"]:
                error_msg = parsed_json["error"]
                
            return {
                "success": success,
                "output": combined_output,
                "error": error_msg or (None if success else f"Command returned exit code: {res.returncode}"),
                "response": parsed_json.get("response", {})
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Command timed out after {timeout} seconds.",
                "response": {}
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"Execution failed: {str(e)}",
                "response": {}
            }

    def new_conversation(self, prompt: str, model: str = None) -> dict:
        """Create a new Antigravity coding conversation."""
        args = ["new-conversation"]
        if model:
            args.append(f"--model={model}")
        args.append(prompt)
        return self._execute(args, timeout=120.0)

    def send_message(self, recipient_id: str, content: str) -> dict:
        """Send a message to an active Antigravity conversation thread."""
        args = ["send-message", recipient_id, content]
        return self._execute(args, timeout=60.0)

    def get_conversation_metadata(self, conversation_id: str) -> dict:
        """Fetch metadata for a given conversation ID."""
        args = ["get-conversation-metadata", conversation_id]
        return self._execute(args, timeout=30.0)

    @staticmethod
    def extract_conversation_id(response_json: dict) -> str:
        """Defensively search for and extract the conversation ID from any response JSON."""
        # 1. Direct keys
        for k in ["conversationId", "conversation_id", "id"]:
            if k in response_json:
                return response_json[k]
        # 2. Check under 'response'
        res = response_json.get("response", {})
        if isinstance(res, dict):
            for k in ["conversationId", "conversation_id", "id"]:
                if k in res:
                    return res[k]
            # 3. Check under response -> conversationMetadata -> metadata
            meta = res.get("conversationMetadata", {}).get("metadata", {})
            if isinstance(meta, dict):
                for k in ["conversationId", "conversation_id", "id"]:
                    if k in meta:
                        return meta[k]
        # 4. Search recursively
        def recurse(d):
            if not isinstance(d, dict):
                return None
            for k, v in d.items():
                if k.lower() in ["conversationid", "conversation_id"] and isinstance(v, str):
                    return v
                res_val = recurse(v)
                if res_val:
                    return res_val
            return None
        return recurse(response_json)
