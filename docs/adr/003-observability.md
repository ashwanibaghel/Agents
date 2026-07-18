# ADR 003: Health Monitor & Runtime Metrics

## Status
Accepted

## Context
For a production-grade worker deployment, operators need to know if the system components (e.g., Git connection, Supabase database, Bridge API, local workspaces) are functioning correctly and if the worker is performing efficiently. We need to collect runtime metrics (latencies, counts, success rates) and expose component health states without impacting performance or introducing security vulnerabilities.

## Decision
We implemented a non-intrusive **Health Monitor** (`control/health_monitor.py`) and a memory-cached **Metrics Manager** (`control/metrics_manager.py`).
1. **Read-Only Health Checks**: Component health checks are strictly read-only. They must never write to databases, spawn Git processes, modify files, or create AI sessions.
2. **Incremental Aggregates**: Metrics like average runtime, median (P50), P95, and success rates are computed incrementally when events occur, or retrieved via lightweight, indexed database queries. They are cached in memory.
3. **TTL-Cachings**: To prevent resource exhaustion, expensive SQL scans are cached using a TTL-based cache (default 30–60 seconds).
4. **Isolations**: All health checks run inside a try-except block with configurable timeouts. A failed check or timed-out dependency must never crash the main application.

## Alternatives Considered
- **Direct SQL Scanning on Every Request**: Rejected because scanning tens of thousands of rows on every GET request causes high CPU usage and lock contention.
- **Prometheus/StatsD Exporter**: Rejected to avoid external dependencies. The built-in memory metrics manager matches our lightweight footprint.

## Consequences
- **Observability**: Real-time insight into latencies and error rates is visible on the dashboard.
- **Safety**: Monitoring does not inadvertently alter state or leak database credentials.
- **Cache Staleness**: Operators see data delayed by up to 30–60 seconds (configurable cache TTL), which is acceptable for operational monitoring.

## Future Considerations
- Adding OpenTelemetry-compliant endpoints if the system scales to standard cloud orchestration tools.
