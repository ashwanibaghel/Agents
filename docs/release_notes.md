# Release Notes — V3.2.0

**Release Tag**: `v3.2.0`
**Release Git Commit**: `ca984370b08bb56c43791141514d7820cf1f312d`
**Release Date**: 2026-07-18
**Framework Status**: RELEASED / FROZEN

---

## What's New in V3.2.0

This release completes the **Production Hardening & Reliability** phase of the Ashwani Agent Company orchestration framework. It focuses on enterprise-grade security, fault isolation, automated disaster recovery, non-blocking observabilities, performance caching, and extensive release packaging.

### Core Features

1. **Persistent Session Manager (Sprint 2)**
   - Thread-safe project-level SQLite workspace locking.
   - Intelligent conversation reuse, reducing API prompt overhead and warm-up latency.
   - 24-hour expiration validation.

2. **Immutable Audit Trail & Event Bus (Sprint 3)**
   - SQLite-backed log mapping worker state transitions.
   - Reentrant thread locking and duplicate write protection.
   - Subscriber-publisher messaging integration with exception isolation.

3. **Observability Metrics & Health Monitoring (Sprint 4)**
   - Component status checking (Bridge, Worker, Supabase, Git, Workspaces, Sessions, Verifier, Dispatcher) strictly using read-only API probes.
   - Non-blocking execution latencies (P50/P95) incrementally computed and cached with a 30s TTL to prevent database lockups.

4. **Automated Backup Manager (Sprint 5)**
   - Safe SQLite database replication and configuration backups.
   - unique timestamped folder structures with SHA-256 integrity verification.
   - Configurable retention window (max 10 backups) and auto-pruning.

5. **Diagnostics API & Operations Dashboard (Sprint 6)**
   - Read-only `/diagnostics` REST endpoint returning cached configurations, feature flags, backups, git branch, and the latest audit events.
   - Visual dashboard panels displaying live observability stats.

6. **Dynamic Build Packaging (Sprint 7)**
   - Dynamic build manifest compiler creating `release_manifest.json` on compilation.
   - Comprehensive documentation indexes, architectural graphs, troubleshooting indices, and sequence flows.

---

## Final Validation Metrics
- **Unit Tests**: **260/260 (100% PASS)**
- **Readiness Score**: **99% (PASS)**
- **Security Check**: 88% PASS (local env secrets verified)
- **Git State**: Clean working tree, tagged `v3.2.0`.
