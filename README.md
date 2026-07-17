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
