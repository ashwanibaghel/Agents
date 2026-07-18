import os
import sys
import json
import uuid
import time
import datetime
from typing import Optional

class StructuredLogger:
    """
    Production structured JSON logger.

    Every log line is a JSON object with fields:
      timestamp, service, worker_id, task_id, artifact,
      event, duration_ms, status, error, message, level,
      trace_id, project_id, conversation_id, branch, step, error_code
    """
    def __init__(
        self,
        service: str = "worker",
        log_dir: str = "logs",
        worker_id_file: str = "state/worker_id.txt"
    ):
        self.service = service
        self.log_dir = os.path.abspath(log_dir)
        self.log_file = os.path.join(self.log_dir, f"{service}.log")
        self.worker_id_file = os.path.abspath(worker_id_file)
        self.worker_id = ""
        self.file_logging_enabled = False
        self._init_worker_id()
        self._init_file_logger()

    def _init_worker_id(self):
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
            self.worker_id = f"worker-fallback-{str(uuid.uuid4())[:8]}"

    def _init_file_logger(self):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8"):
                self.file_logging_enabled = True
        except Exception:
            self.file_logging_enabled = False

    def log(
        self,
        level: str,
        message: str,
        # Core identity fields
        service: Optional[str] = None,
        worker_id: Optional[str] = None,
        task_id: Optional[str] = None,
        artifact: Optional[str] = None,
        event: Optional[str] = None,
        # Timing & outcome
        duration_ms: Optional[float] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
        # Legacy / extended
        trace_id: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch: Optional[str] = None,
        step: Optional[str] = None,
        error_code: Optional[str] = None,
        # Arbitrary extra fields
        **extra
    ):
        try:
            entry = {
                "timestamp":       datetime.datetime.utcnow().isoformat() + "Z",
                "level":           level.upper(),
                "service":         service or self.service,
                "worker_id":       worker_id or self.worker_id,
                "task_id":         task_id or "",
                "artifact":        artifact or "",
                "event":           event or "",
                "duration_ms":     round(duration_ms, 3) if duration_ms is not None else None,
                "status":          status or "",
                "error":           error or "",
                "message":         message,
                # Extended fields
                "trace_id":        trace_id or "",
                "project_id":      project_id or "",
                "conversation_id": conversation_id or "",
                "branch":          branch or "",
                "step":            step or "",
                "error_code":      error_code or "",
            }
            # Strip None values to keep log lines lean
            entry = {k: v for k, v in entry.items() if v is not None and v != ""}
            if extra:
                entry.update(extra)

            line = json.dumps(entry, ensure_ascii=False)

            if level.upper() in ("ERROR", "CRITICAL"):
                sys.stderr.write(line + "\n"); sys.stderr.flush()
            else:
                sys.stdout.write(line + "\n"); sys.stdout.flush()

            if self.file_logging_enabled:
                try:
                    with open(self.log_file, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception:
                    pass
        except Exception as e:
            sys.stderr.write(f"LOGGER_FAILURE: {e}\n")

    # ── Convenience helpers ──────────────────────────────────────────────
    def debug(self, msg, **kw):    self.log("DEBUG",    msg, **kw)
    def info(self, msg, **kw):     self.log("INFO",     msg, **kw)
    def warning(self, msg, **kw):  self.log("WARNING",  msg, **kw)
    def error(self, msg, **kw):    self.log("ERROR",    msg, **kw)
    def critical(self, msg, **kw): self.log("CRITICAL", msg, **kw)

    def event(self, event_name: str, task_id: str = "", artifact: str = "",
              duration_ms: float = None, status: str = "OK", **kw):
        """Log a named pipeline event with full context."""
        self.log("INFO", f"[{event_name}] {status}",
                 event=event_name, task_id=task_id, artifact=artifact,
                 duration_ms=duration_ms, status=status, **kw)


# ── Service-specific singleton loggers ───────────────────────────────────────
logger          = StructuredLogger(service="worker")
indexer_logger  = StructuredLogger(service="knowledge_indexer", log_dir="logs")
bridge_logger   = StructuredLogger(service="bridge_server",     log_dir="logs")
