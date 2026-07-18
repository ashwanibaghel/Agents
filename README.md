# Ashwani Agent Company (V3.2)

A production-grade agent orchestration framework designed to run, validate, monitor, and coordinate autonomous AI developers (Antigravity workers) across multiple projects.

---

## Quick Start Guide

This step-by-step guide walks a new developer through the complete setup of the orchestration framework from scratch.

### 1. Clone the Repository
```bash
git clone <repository_url> company
cd company
```

### 2. Environment Setup
Create a virtual environment and install the required dependencies:
```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

# Install required python packages
pip install -r requirements.txt
```

### 3. Configuration Setup
1. Copy the environment variables template:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and fill in the required values:
   - `SUPABASE_URL`: Your Supabase REST URL.
   - `SUPABASE_SERVICE_KEY`: Service role API key for state synchronization.
   - `BRIDGE_TOKEN`: A secure random token shared between the Bridge server and ChatGPT.
3. Configure projects in `config/projects.yaml` and Supabase settings in `config/supabase.yaml`.

### 4. Running the Worker Daemon
The worker polls Supabase for claimed tasks, prepares local workspaces, and coordinates the execution loop.
```bash
python main.py
```
On startup, the worker executes configuration diagnostics and starts the automatic local backup engine.

### 5. Running the Bridge Server
The bridge server provides REST APIs for dashboard visualization and external agent integration.
```bash
# Set bridge token for shell environment
# On Windows (PowerShell):
$env:BRIDGE_TOKEN="your-bridge-token"
# On Linux/macOS:
export BRIDGE_TOKEN="your-bridge-token"

# Run Bridge server
uvicorn bridge_server:app --host 0.0.0.0 --port 8080
```

### 6. Running Production Readiness Checks
Examine system health and score system compliance:
```bash
python production_check.py
```
For a clean PASS, ensure the `BRIDGE_TOKEN` variable is exported in your shell.

### 7. Triggering the First Local E2E Task
To run a test task through the system:
1. Seed a test task in the database. Run the included utility script:
   ```bash
   python scratch/seed_e2e_task.py
   ```
2. Start the worker (`python main.py`) to claim, checkout, execute, verify, and push the task changes automatically.
3. Open `http://localhost:8080/` in your browser to view the Boss Dashboard status panel.

---

## System Documentation

For detailed information on system design, refer to the following documents:

- [System Architecture](docs/architecture.md)
- [Task Execution Lifecycle](docs/task_lifecycle.md)
- [Persistent Sessions](docs/session_lifecycle.md)
- [Audit Trail & DB Schema](docs/audit_trail.md)
- [Event Bus Integration](docs/event_bus.md)
- [Health Monitor Specifications](docs/health_monitor.md)
- [Observability Metrics](docs/metrics.md)
- [Backup & Restore Strategies](docs/backup_manager.md)
- [Production Readiness Scoring](docs/production_validator.md)
- [Diagnostics Endpoint Schema](docs/diagnostics.md)
- [Dashboard UI Panels](docs/dashboard.md)
- [Mermaid Sequence Diagrams](docs/sequence_diagrams.md)
- [VPS & Cloud Deployment Guide](docs/deployment.md)
- [Troubleshooting & Recovery](docs/troubleshooting.md)

---

## Architectural Decision Records (ADRs)

Key architectural decisions are documented in the [ADR Index](docs/adr/):
- [ADR 001: Persistent Session Manager](docs/adr/001-persistent-sessions.md)
- [ADR 002: Immutable Audit Trail](docs/adr/002-audit-trail.md)
- [ADR 003: Health Monitor & Metrics](docs/adr/003-observability.md)
- [ADR 004: Backup Strategy](docs/adr/004-backup-strategy.md)

---

## V3.2.1 Production Hotfix & Deployment Modes

### Environment Variables & Precedence
The worker selects the task source (`SupabaseTaskSource` vs. `LocalTaskSource`) using the following strict precedence rules:
1. **APP_ENV** (`development`, `staging`, or `production`). If set to `production` or `staging`, the worker defaults to `use_supabase = True`. If set to `development`, it defaults to `False`.
2. **SUPABASE_ENABLED** (Environment override: `true`/`1` or `false`/`0`). If specified, it overrides the default decided by `APP_ENV`.
3. **config/supabase.yaml** (`enabled` property). This is only checked in `development` mode when no override is set in the environment. It is completely ignored in `production` and `staging` modes.
4. **Default Fallback**: `False` (for development safety).

### Startup Validation & Self-Test
- **Fail-Fast**: If `APP_ENV=production` and the resolved task source is NOT Supabase, the worker will abort startup immediately with a non-zero exit code.
- **Startup Self-Test**: When Supabase is enabled, the worker runs an validation suite at startup:
  1. *Read permission*: Verify tasks table is readable.
  2. *Write permission*: Upsert a temporary self-test system record.
  3. *Claim task permission*: Create and claim a temporary self-test task.
  4. *Heartbeat update permission*: Call the worker's heartbeat implementation on the claimed task.
- If any self-test fails when `APP_ENV=production`, the worker aborts startup and exits.
- All temporary self-test records are guaranteed to be deleted during startup before the polling loop begins.

### Persistent Conversation Policy (V3.2.1)
Each project maintains exactly ONE persistent Antigravity conversation that is kept alive as long as it exists on the language server. Time-based activity expiration is completely disabled (conversations are never expired after days/weeks/months of inactivity).

#### Session Status Classification
- **ACTIVE**: The conversation exists on the server and was recently active.
- **IDLE**: The conversation exists but has been inactive for more than 5 minutes. It is resumed normally.
- **MISSING**: The conversation genuinely no longer exists on the server (the API returned "not found", "deleted", or "expired"). This is the only state that triggers `new_conversation()`.
- **BROKEN**: A temporary infrastructure issue (DNS, SSL, timeout, connection reset, 502/503, rate limit).

#### Backoff & Retry Flow
When a `BROKEN` state is encountered:
1. The `conversation_id` and project session are kept unchanged.
2. The current workspace lock is released.
3. The retry metadata is saved locally in SQLite: `retry_count` is incremented, `last_error` is recorded, and `next_retry_at` is set using exponential backoff:
   - **Retry 1**: 30 seconds
   - **Retry 2**: 2 minutes
   - **Retry 3**: 5 minutes
   - **Retry 4**: 15 minutes
   - **Retry >4**: The task is marked as permanently `FAILED` in Supabase.
4. While in backoff, the task remains claimed in Supabase. Heartbeats are sent only while the worker process is alive.
5. If the worker process crashes, heartbeats stop. After lease expiration (10 minutes), the stale task recovery mechanism resets the task status to `inbox`.
6. When a worker claims the task again, it reads the retry metadata from SQLite/project state and resumes the backoff/retry sequence.
