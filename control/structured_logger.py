import os
import sys
import json
import uuid
import time
import datetime
from typing import Optional, Any

class StructuredLogger:
    def __init__(self, log_dir: str = "logs", worker_id_file: str = "state/worker_id.txt"):
        self.log_dir = os.path.abspath(log_dir)
        self.log_file = os.path.join(self.log_dir, "production_worker.log")
        self.worker_id_file = os.path.abspath(worker_id_file)
        self.worker_id = ""
        self.file_logging_enabled = False
        
        self._init_worker_id()
        self._init_file_logger()

    def _init_worker_id(self):
        """Generate and save a persistent worker UUID or reuse existing."""
        try:
            os.makedirs(os.path.dirname(self.worker_id_file), exist_ok=True)
            if os.path.exists(self.worker_id_file):
                with open(self.worker_id_file, "r", encoding="utf-8") as f:
                    self.worker_id = f.read().strip()
            if not self.worker_id or len(self.worker_id) < 10:
                self.worker_id = f"worker-{str(uuid.uuid4())[:8]}"
                with open(self.worker_id_file, "w", encoding="utf-8") as f:
                    f.write(self.worker_id)
        except Exception as e:
            # Best-effort fallback
            self.worker_id = f"worker-fallback-{str(uuid.uuid4())[:8]}"
            print(f"⚠️ Logger: Failed to persist worker UUID: {e}. Using transient ID: {self.worker_id}", file=sys.stderr)

    def _init_file_logger(self):
        """Initialize file logging directory and file handle safely."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            # Try to write a test line to verify write permissions
            with open(self.log_file, "a", encoding="utf-8") as f:
                self.file_logging_enabled = True
        except Exception as e:
            self.file_logging_enabled = False
            print(f"⚠️ Logger: File logging disabled. Directory creation or file access failed: {e}", file=sys.stderr)

    def log(
        self,
        level: str,
        message: str,
        trace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch: Optional[str] = None,
        step: Optional[str] = None,
        duration_ms: Optional[int] = None,
        status: Optional[str] = None,
        error_code: Optional[str] = None
    ):
        """Build and output a JSON structured log entry safely."""
        try:
            log_entry = {
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "level": level.upper(),
                "trace_id": trace_id or "",
                "worker_id": self.worker_id,
                "task_id": task_id or "",
                "project_id": project_id or "",
                "conversation_id": conversation_id or "",
                "branch": branch or "",
                "step": step or "",
                "duration_ms": duration_ms if duration_ms is not None else "",
                "status": status or "",
                "error_code": error_code or "",
                "message": message
            }
            
            # Serialize
            log_json = json.dumps(log_entry)
            
            # Output to stdout/stderr based on level
            if level.upper() in ["ERROR", "CRITICAL"]:
                sys.stderr.write(log_json + "\n")
                sys.stderr.flush()
            else:
                sys.stdout.write(log_json + "\n")
                sys.stdout.flush()
                
            # Output to file if enabled
            if self.file_logging_enabled:
                try:
                    with open(self.log_file, "a", encoding="utf-8") as f:
                        f.write(log_json + "\n")
                except Exception as fe:
                    # Fallback so logger never crashes the main app
                    print(f"⚠️ Logger: Failed to write log entry to file: {fe}", file=sys.stderr)
                    
        except Exception as le:
            # Final line of defense to prevent crashing
            print(f"⚠️ Logger Critical Failure: {le}", file=sys.stderr)

    # Convenience methods
    def debug(self, msg, **kwargs): self.log("DEBUG", msg, **kwargs)
    def info(self, msg, **kwargs): self.log("INFO", msg, **kwargs)
    def warning(self, msg, **kwargs): self.log("WARNING", msg, **kwargs)
    def error(self, msg, **kwargs): self.log("ERROR", msg, **kwargs)
    def critical(self, msg, **kwargs): self.log("CRITICAL", msg, **kwargs)

# Global singleton logger instance for use across the application
logger = StructuredLogger()
