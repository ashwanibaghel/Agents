# Changelog

All notable changes to the Ashwani Agent Company orchestration framework are documented in this file.

---

## [3.2.0] — 2026-07-18

### Sprint 7 — Release Engineering & Documentation (V3.2 Final)
- **Documentation**: Added comprehensive developer `README.md` and complete project documentation inside `docs/` covering architecture, task lifecycles, health checks, backup policies, diagnostics API, and troubleshooting.
- **Architectural Decision Records (ADRs)**: Created ADRs under `docs/adr/` for Persistent Sessions, Immutable Audit Trail, Observability, and Backup Strategy.
- **Release Automation**: Created `tools/generate_manifest.py` to dynamically compile and export `release_manifest.json` including Git details and Python environmental variables.
- **Validation**: Verified 100% test coverage and ran `production_check.py` to achieve production validation passing score.

### Sprint 6 — Worker Refactor & Operations Dashboard
- **Telemetry Extraction**: Created `control/telemetry.py` containing a unified, in-process, dependency-injected transition logging system.
- **Orchestration Simplification**: Refactored `main.py`, `control/dispatcher.py`, and `workers/antigravity_worker.py` to remove duplicate logging logic, moving execution and workspace preparation into isolated helper methods.
- **Diagnostics API**: Implemented a completely read-only `GET /diagnostics` endpoint on the Bridge server returning cached configuration, version, environmental, backup, and latest audit trail records.
- **Operations Dashboard**: Upgraded the Boss Dashboard with a 6-panel "Operations" section (Worker, Queue, Sessions, Git, Backup, Validator status) utilizing existing data streams without adding API load.
- **Clean-up**: Deleted dead unused files `control/manager.py` and `control/task_router.py`.

### Sprint 5 — Backup Manager & Production Readiness Validator
- **Backup Manager**: Created `control/backup_manager.py` to safely back up active SQLite databases, identity configurations, and YAML settings with timestamped folders, SHA-256 checksum manifests, and automatic oldest-first pruning.
- **Readiness Validator**: Implemented `control/production_validator.py` executing 9 independent readiness evaluations (scoring, warnings, and failures report).
- **CLI Check**: Created `production_check.py` CLI utility returning status indicators and appropriate exit codes.

### Sprint 4 — Health Monitoring & Runtime Metrics
- **Health Monitor**: Created `control/health_monitor.py` exposing status, latency, and details for 9 core components using strictly read-only checks under timeout constraints.
- **Metrics Engine**: Implemented `control/metrics_manager.py` to collect task execution latencies (average, P50, P95) and compute aggregates incrementally. Utilized memory caches with 30s TTL to prevent database lockups.

### Sprint 3 — Immutable Audit Trail & Event Bus
- **Audit Trail**: Created `control/audit_trail.py` backing up worker states to an append-only, thread-safe, crash-resilient SQLite table with duplicate event submission protection.
- **Event Bus**: Implemented `control/event_bus.py` supporting in-process event publish-subscribe patterns with try-catch isolation.

### Sprint 2 — Persistent Session Manager
- **Session Store**: Created database schema and logic to persist active conversation sessions and locks for AI workers, reducing setup latency and prompt token overhead.
- **Concurrency Locking**: Implemented project-level session locks preventing concurrent execution clashes.

### Sprint 1 — Project Runtime & Setup
- **Config Management**: Implemented YAML parser for projects/Supabase settings.
- **Initialization**: Configured startup checks validating critical configuration values.

---

## [3.1.0] — V3.1 Base Release
- **Core Worker**: Implemented base worker polling Supabase, executing local tasks, running test verifications, and pushing git changes.
- **Bridge Server**: Created basic REST bridge interface routing agent commands to workspaces.
