"""
control/telemetry.py

Shared telemetry helpers for the Ashwani Agent Company worker system.

Responsibilities:
  - log_transition(): publish an event to the event bus AND append to audit trail
  - Nothing else.

Design philosophy (V4.x ready):
  All dependencies (logger, audit_trail, event_bus) are explicit parameters with
  sensible defaults that fall back to the global singletons. This makes the module
  fully testable without patching globals, and ready for multi-worker or distributed
  migration where each worker carries its own logger/audit context.
"""

from __future__ import annotations
from typing import Any, Dict, Optional, TYPE_CHECKING

# Lazy defaults — only imported if caller does not inject their own
def _default_logger():
    from control.structured_logger import logger as _l
    return _l


def _default_event_bus():
    from control.event_bus import event_bus as _eb
    return _eb


def _default_audit_trail():
    from control.audit_trail import audit_trail as _at
    return _at


def _default_event_class():
    from control.event_bus import Event as _E
    return _E


def log_transition(
    event_type: str,
    status: str,
    task_id: str,
    project_id: str,
    trace_id: str,
    *,
    # Optional context
    conversation_id: Optional[str] = None,
    branch: Optional[str] = None,
    error_code: Optional[str] = None,
    message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    # Injectable dependencies (default to global singletons for backward compat)
    worker_id: Optional[str] = None,
    _logger=None,
    _event_bus=None,
    _audit_trail=None,
    _Event=None,
) -> None:
    """
    Publish a lifecycle transition to both the event bus and audit trail.

    All side effects are best-effort — a failure here must NEVER crash the worker.

    Parameters
    ----------
    event_type        : e.g. "TASK_CLAIMED", "WORKSPACE_PREPARED", "GIT_PUSH"
    status            : e.g. "PREPARED", "PUSHED", "FAILED"
    task_id           : Task being transitioned
    project_id        : Project owning the task
    trace_id          : Correlation ID for the current dispatch cycle
    conversation_id   : Antigravity conversation ID if applicable
    branch            : Git branch if applicable
    error_code        : Structured error code from control.error_codes
    message           : Human-readable description of the transition
    metadata          : Arbitrary extra context (must be JSON-serializable)
    worker_id         : Override worker identity (defaults to logger.worker_id)
    _logger           : Inject a custom logger (useful for tests)
    _event_bus        : Inject a custom event bus (useful for tests / multi-worker)
    _audit_trail      : Inject a custom audit trail (useful for tests / multi-worker)
    _Event            : Inject Event class (useful for tests)
    """
    try:
        log = _logger or _default_logger()
        bus = _event_bus or _default_event_bus()
        trail = _audit_trail or _default_audit_trail()
        EventClass = _Event or _default_event_class()

        _worker_id = worker_id or getattr(log, "worker_id", "")

        evt_data: Dict[str, Any] = {
            "trace_id":        trace_id,
            "worker_id":       _worker_id,
            "task_id":         task_id,
            "project_id":      project_id,
            "conversation_id": conversation_id,
            "branch":          branch,
            "status":          status,
            "error_code":      error_code,
            "message":         message,
            "metadata":        metadata or {},
        }

        try:
            bus.publish(EventClass(event_type, evt_data))
        except Exception:
            pass  # event bus failure must never crash the worker

        try:
            trail.append(
                event_type=event_type,
                status=status,
                trace_id=trace_id,
                worker_id=_worker_id,
                task_id=task_id,
                project_id=project_id,
                conversation_id=conversation_id,
                branch=branch,
                error_code=error_code,
                message=message,
                metadata=metadata,
            )
        except Exception:
            pass  # audit trail failure must never crash the worker

    except Exception:
        pass  # telemetry must never crash the caller
