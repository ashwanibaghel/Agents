# ADR 002: Immutable Audit Trail

## Status
Accepted

## Context
Production agent environments require strict compliance, debugging transparency, and historical accountability. We need an immutable record of every single lifecycle transition (e.g., claiming a task, checking out a branch, running verification, pushing commits). This record must be append-only, thread-safe, crash-safe, restart-safe, and resilient to disk write failures or unexpected worker exits.

## Decision
We implemented a dedicated **Immutable Audit Trail** (`control/audit_trail.py`).
1. **SQLite Backed**: Data is persisted in the `audit_trail` table within `state/task_checkpoints.db`.
2. **Append-Only Enforcement**: The API exposes only `append` and read-only query methods. No update or delete operations are exposed or permitted.
3. **Thread-Safe**: Guarded by a reentrant threading lock (`threading.Lock`) inside Python to serialize concurrent database writes.
4. **Resiliency**: Database transactions are committed immediately on write to ensure persistence in the event of worker crashes.
5. **Duplicate Protection**: Rejects writing duplicate events with the same `(trace_id, event_type, status)` or `(task_id, event_type, status)` to prevent log spamming and double-counting in metrics.

## Alternatives Considered
- **Text/JSON Log Files**: Rejected because logs can be easily modified, corrupted by concurrent writes, or truncated during crashes. SQLite provides ACID guarantees and structured querying.
- **Remote Log Aggregators (e.g., Elastic, Splunk)**: Rejected to keep local setup self-contained, offline-compatible, and lightweight.

## Consequences
- **Compliance & Transparency**: A structured, sequential record of worker operations is preserved permanently.
- **Resilience**: The system restarts cleanly and resumes tracking without losing history.
- **Storage**: Requires periodic maintenance if the database grows extremely large (addressed in the backup and cleanup strategies).

## Future Considerations
- Adding cryptographic verification (e.g., hash chains or Merkle trees) to audit trail entries to mathematically guarantee immutability.
