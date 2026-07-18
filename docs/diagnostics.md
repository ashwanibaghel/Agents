# Diagnostics Endpoint

The `GET /diagnostics` API endpoint provides a read-only snapshot of the system's runtime status.

## Specifications
- **Route**: `GET /diagnostics` (Read-only internal API, auth-free by design)
- **Response Format**: `application/json`

## API Authentication Contract (Other Secured Endpoints)
Endpoints requiring token authorization (e.g. `/report`, `/tasks/create`) follow standard FastAPI framework behavior:
- **Missing Authorization header**: Returns `403 Forbidden` (`detail: "Not authenticated"`). This is raised automatically by the FastAPI framework's `HTTPBearer` scheme.
- **Invalid token credentials**: Returns `401 Unauthorized` (`detail: "Unauthorized"`). This is raised by the application logic (`verify_token` dependency) once formatting is verified.

## Schema Fields

- **`worker`**: Worker ID, startup count, uptime, and last heartbeat timestamp.
- **`metrics_summary`**: Total tasks claimed, completed, failed, blocked, Git success rate, verifier success rate, and backup failure count.
- **`configuration`**: Project runtime version and a list of active projects loaded.
- **`feature_flags`**: State of system flags (`persistent_sessions`, `structured_logging`, `metrics`, `auto_push`, `chaos_testing`, `backup`).
- **`environment`**: Python version, OS platform, current working directory, and request UTC timestamp.
- **`component_versions`**: Internal version declarations for system modules (Bridge, Logger, Audit, Metrics, Health, Backup, Validator, Telemetry).
- **`recent_audit_events`**: The 10 most recent records from the SQLite audit trail.
- **`backup_status`**: Total backups count and details of the 5 most recent backups (backup ID, created timestamp, label, file count).
- **`git_status`**: Active git branch and short hash of the latest commit of the parent repository.
- **`validator_status`**: Last cached validation result (score, status, warnings, failures) from `state/validator_cache.json`.

## Safety Constraints
The diagnostics endpoint is designed to be completely read-only. It reads cached states and files without initiating git commands that write, executing backups, creating new persistent sessions, or running the production validator.
