# Health Monitor

The Health Monitor performs read-only checks on core components to determine the operational health of the system.

## Checked Components

- **Bridge**: Checks Bridge API availability.
- **Worker**: Checks main process database heartbeat.
- **Supabase**: Verifies table counts and database responses.
- **Git**: Confirms Git CLI is accessible.
- **Workspace**: Verifies workspaces directory permissions.
- **Persistent Session Manager**: Inspects session records.
- **Antigravity Worker**: Evaluates dispatch capabilities.
- **Result Verifier**: Checks verifier execution state.
- **Dispatcher**: Validates thread pool and concurrency state.

## Health States

- **HEALTHY**: Component passed all health checks.
- **DEGRADED**: Component checks took longer than the configured warning threshold, or a non-critical check failed.
- **UNHEALTHY**: A critical health check failed or timed out.

## Strict Read-Only Contract
To ensure safety and reliability in production, health checks are strictly read-only:
- **No data modification**: Checks never write to databases.
- **No file generation**: Checks never write logs or temporary files.
- **No session allocation**: Checks never spawn AI conversation sessions.
- **No Git write commands**: Checks never push, checkout, commit, or pull.
- **Timeout safety**: Every health check runs under a configurable timeout. A timed-out check is marked unhealthy but never halts system execution.
