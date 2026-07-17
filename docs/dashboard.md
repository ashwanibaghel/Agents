# Dashboard Overview

The Boss Dashboard is a visual administration panel for monitoring and controlling the worker system.

## Interface Sections

### 1. Stats Counter Row
Visual indicators representing the number of tasks in different states:
- **Inbox**: Tasks in queue.
- **Working**: Tasks currently executing.
- **Done**: Tasks successfully verified and completed.
- **Blocked**: Tasks suspended due to configuration errors or git issues.
- **Failed**: Tasks that failed execution or validation.

### 2. Operational Control Panel (Sprint 6)
Visual status panel pulling live data from the health monitor and metrics endpoints:
- **🤖 Worker**: Online status, worker ID, current uptime, and startup count.
- **📋 Queue**: Visual representation of the task queue states.
- **💬 Sessions**: Active sessions, conversation reuse count, and expired sessions count.
- **🔀 Git**: Git success rate and total failures.
- **💾 Backup**: Latest backup ID, age (e.g., "5m ago"), backup count, and total failures.
- **✅ Validator**: Last recorded readiness score, status, warnings, and failures.

### 3. System Component Health
Live health status list displaying the state (Healthy, Degraded, Unhealthy), response latencies, retry counts, and last check timestamps for all core components.

### 4. Runtime Observability Metrics
Displays incremental aggregates: P50/P95 latencies, fastest/slowest task runs, workspace/conversation reuse percentages, and Git success rates.

### 5. Recent Finished Tasks
A table showing completed tasks, including task IDs, target projects, execution status, and final commit/error messages. Clicking a row opens a modal showing the task objective, acceptance criteria, and full error stack trace (if failed).

## Data Fetching & UI Performance
- The dashboard does not read directly from SQLite database files or perform direct Git operations.
- The UI fetches data from `/dashboard` (which compiles data from health monitor, metrics, and task sources) and `/metrics` APIs.
- The UI performs a background pull every 5 seconds, displaying a countdown indicator.
