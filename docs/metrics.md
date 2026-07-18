# Runtime Metrics

The system collects, aggregates, and exposes operational metrics to monitor performance and reliability.

## Core Metrics

- **Task Execution Latencies**: Tracks P50, P95, minimum, maximum, and average execution durations.
- **Reliability Metrics**: Tracks task successes, failures, blocked status counts, retry events, and session expiries.
- **Resource Reuse Rates**: Tracks workspace and conversation reuse percentages.
- **Worker Metrics**: Tracks uptime, startup count, and heartbeat timestamps.

## Performance Requirements & Implementation

To prevent performance degradation (especially with large datasets), metrics are designed with the following safeguards:
1. **Incremental Aggregation**: Rather than scanning the SQLite database on every HTTP request, metrics (e.g., averages, counts) are updated incrementally in memory as tasks complete.
2. **TTL Cache**: Database-backed aggregations (such as P50/P95 latencies) are computed on demand and cached in memory with a configurable TTL (default: 30–60 seconds).
3. **Optimized Queries**: SQLite queries are heavily indexed on `trace_id` and `task_id` fields to ensure fast read times.
