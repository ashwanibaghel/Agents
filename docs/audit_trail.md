# Immutable Audit Trail

The audit trail is a structured, append-only SQLite log documenting all system actions and transitions.

## Database Schema
Table name: `audit_trail`
Database path: `state/task_checkpoints.db`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key, autoincrementing |
| `timestamp` | TEXT | ISO-8601 UTC timestamp of creation |
| `trace_id` | TEXT | Correlation ID linking all actions of a dispatch loop |
| `worker_id` | TEXT | Unique ID of the worker executing the task |
| `task_id` | TEXT | Task identifier |
| `project_id` | TEXT | Project identifier |
| `conversation_id`| TEXT | Active AI session conversation ID |
| `branch` | TEXT | Active git feature branch |
| `event_type` | TEXT | Transition identifier (e.g., `TASK_CLAIMED`, `WORKSPACE_PREPARED`, `GIT_PUSH`) |
| `status` | TEXT | Result of the transition (e.g., `PREPARED`, `PUSHED`, `FAILED`, `BLOCKED`) |
| `error_code` | TEXT | Structured error code (e.g., `CONFIG_001`, `GIT_003`) |
| `message` | TEXT | Human-readable details |
| `metadata_json` | TEXT | JSON object containing additional contextual variables |

## Core APIs

- **`append(...)`**: Appends a new event.
  - **Thread-safe**: Guarded by a Python lock.
  - **Duplicate Protection**: Skip inserts if a duplicate `(trace_id, event_type, status)` or `(task_id, event_type, status)` exists.
- **`get_records(...)`**: Queries records chronologically for debugging.
- **`get_recent(...)`**: Read-only query returning the latest N records ordered by `id DESC`.
