# System Architecture

## Overview
The Ashwani Agent Company system is a production-grade agent orchestration framework. It coordinates autonomous developers (Antigravity AI agents) working on multiple git repositories, ensuring concurrency safety, environment isolation, validation gates, and system-level observability.

## System Topology
```
           +---------------------------------------------+
           |               ChatGPT / User                |
           +---------------------------------------------+
                                  |
                                  v
           +---------------------------------------------+
           |               Bridge Server                 |
           |             (bridge_server.py)              |
           +---------------------------------------------+
              |                  |                  |
              | (FastAPI REST)   | (FastAPI REST)   | (SSE / SSE)
              v                  v                  v
     +-----------------+  +--------------+  +---------------+
     | /health /metrics|  | /diagnostics |  | Dashboard UI  |
     +-----------------+  +--------------+  +---------------+
              |                  |                  |
              +---------+        |        +---------+
                        |        |        |
                        v        v        v
           +---------------------------------------------+
           |                 State Store                 |
           |          (state/task_checkpoints.db)        |
           +---------------------------------------------+
                                  ^
                                  |
           +---------------------------------------------+
           |               Worker Daemon                 |
           |                 (main.py)                   |
           +---------------------------------------------+
              |                  |                  |
              v                  v                  v
       +--------------+   +--------------+   +--------------+
       |  Dispatcher  |   | Workspace Mgr|   |  Telemetry   |
       +--------------+   +--------------+   +--------------+
              |                  |                  |
              v                  v                  v
       +--------------+   +--------------+   +--------------+
       | Antigravity  |   |  Git / Repo  |   |  Result      |
       | Client/Worker|   |  Feature Br  |   |  Verifier    |
       +--------------+   +--------------+   +--------------+
```

## Component Responsibilities

### Core Orchestration
1. **Worker Daemon (`main.py`)**: Boot loops, handles system signals, checks configuration changes, triggers backup loops, and claims queued tasks from Supabase.
2. **Dispatcher (`control/dispatcher.py`)**: Responsible for dispatching claimed tasks to correct worker instances in parallel, utilizing thread pools.
3. **Workspace Manager (`control/workspace_manager.py`)**: Manages physical workspaces in the file system. Enforces project directory isolation and directory cleanup.

### Telemetry & Observability
1. **Telemetry Helper (`control/telemetry.py`)**: Unifies log transition events. Publishes events to the Event Bus and appends entries to the Audit Trail.
2. **Audit Trail (`control/audit_trail.py`)**: Thread-safe, crash-safe, append-only SQLite store documenting all worker transitions.
3. **Event Bus (`control/event_bus.py`)**: Lightweight event bus supporting publisher-subscriber communication.
4. **Health Monitor (`control/health_monitor.py`)**: Scans core components (Bridge, Worker, Supabase, Git, Workspace, Persistent Sessions, Antigravity Worker, Result Verifier, Dispatcher) strictly using read-only checks.
5. **Metrics Manager (`control/metrics_manager.py`)**: Collects startup counts, task execution latency statistics (P50/P95), and reuse rates. Computes values incrementally and caches results with a configurable TTL cache.

### Security & Recovery
1. **Backup Manager (`control/backup_manager.py`)**: Creates timestamped backups of DBs and configs, verifies SHA-256 checksums, and auto-purges old folders.
2. **Production Validator (`control/production_validator.py`)**: Analyzes system configuration, logging, persistence, recovery, observability, git, security, testing, and documentation to output a readiness score.
