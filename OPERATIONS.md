# OPERATIONS.md
# Engineering Manager — Knowledge Architecture
## Operations Manual v1.0

---

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Deployment](#2-deployment)
3. [Configuration](#3-configuration)
4. [Backup & Recovery](#4-backup--recovery)
5. [Failure Handling](#5-failure-handling)
6. [Restart Procedures](#6-restart-procedures)
7. [Monitoring](#7-monitoring)
8. [Alerts](#8-alerts)
9. [Runbooks](#9-runbooks)

---

## 1. System Overview

Three independently deployable services:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ENGINEERING MANAGER SYSTEM                       │
│                                                                     │
│  ┌─────────────────┐   ┌────────────────────┐   ┌───────────────┐  │
│  │  Bridge Server  │   │ Knowledge Indexer  │   │  Eng. Worker  │  │
│  │  (API Gateway)  │   │   (Background)     │   │  (Task Exec)  │  │
│  │  Port 8000      │   │  Polls DB every 2s │   │  On-demand    │  │
│  └────────┬────────┘   └────────┬───────────┘   └──────┬────────┘  │
│           │                     │                       │           │
│           └─────────────────────┴───────────────────────┘          │
│                                 │                                   │
│                    ┌────────────▼────────────┐                      │
│                    │     SQLite Database     │                      │
│                    │  state/task_checkpoints │                      │
│                    │  WAL mode, concurrent   │                      │
│                    └─────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Service Responsibilities
| Service | Command | Port | Restartable | Scales Horizontally |
|---|---|---|---|---|
| Bridge Server | `python bridge_server.py` | 8000 | Yes | No (single API) |
| Knowledge Indexer | `python knowledge_indexer_service.py` | — | Yes | **Yes (N instances)** |
| Engineering Worker | `python worker.py` | — | Yes | **Yes (N workers)** |

---

## 2. Deployment

### Prerequisites
```powershell
# Python 3.11+
python --version

# Required packages
pip install -r requirements.txt

# Create required directories
mkdir state, logs
```

### Environment Variables
```bash
# Core
SUPABASE_URL=https://your-project.supabase.co   # optional, SQLite is fallback
SUPABASE_SERVICE_KEY=your-key                     # optional
SUPABASE_ENABLED=false                            # set true to enable Supabase sync

# Embedding provider
EMBEDDING_PROVIDER=mock          # mock | gemini
GEMINI_API_KEY=your-key          # required if EMBEDDING_PROVIDER=gemini

# Indexer tuning
INDEXER_POLL_INTERVAL=2.0        # seconds between DB polls
INDEXER_LEASE_DURATION=300       # seconds before abandoned lease expires

# Bridge server
BRIDGE_TOKEN=your-secret-token   # required for API authentication
```

### Starting All Services

**Option A — Direct (development)**
```powershell
# Terminal 1
python bridge_server.py

# Terminal 2
python knowledge_indexer_service.py

# Terminal 3 (per task)
python worker.py
```

**Option B — Production (PM2 / Supervisor)**
```powershell
# Install PM2
npm install -g pm2

# Start all services
pm2 start bridge_server.py --interpreter python --name bridge
pm2 start knowledge_indexer_service.py --interpreter python --name indexer
pm2 save
pm2 startup
```

**Option C — Docker Compose**
```yaml
services:
  bridge:
    build: .
    command: python bridge_server.py
    ports: ["8000:8000"]
    env_file: .env
    volumes: ["./state:/app/state", "./logs:/app/logs"]

  indexer:
    build: .
    command: python knowledge_indexer_service.py
    env_file: .env
    volumes: ["./state:/app/state", "./logs:/app/logs"]
    deploy:
      replicas: 2       # horizontal scaling

  worker:
    build: .
    command: python worker.py
    env_file: .env
    volumes: ["./state:/app/state"]
    deploy:
      replicas: 5
```

### Health Check
```powershell
# Bridge server health
curl http://localhost:8000/metrics

# Prometheus scrape target
# prometheus.yml:
# scrape_configs:
#   - job_name: 'engineering_manager'
#     static_configs:
#       - targets: ['localhost:8000']
#     metrics_path: '/metrics'
```

---

## 3. Configuration

### Database (SQLite WAL)
```
state/task_checkpoints.db
  ├── task_artifacts    # artifact registry + indexing lifecycle
  ├── task_knowledge    # chunk storage + embeddings
  └── tasks             # task status
```

WAL mode is enabled automatically on first connection.

### Log Files
```
logs/
  ├── worker.log              # JSON structured logs from workers
  ├── knowledge_indexer.log   # JSON structured logs from indexer
  └── bridge_server.log       # JSON structured logs from bridge API
```

Each log line is a JSON object:
```json
{
  "timestamp": "2026-07-19T01:00:00.000Z",
  "level": "INFO",
  "service": "knowledge_indexer",
  "worker_id": "indexer-worker-A1B2C3",
  "task_id": "TASK-001",
  "artifact": "RECON.md",
  "event": "artifact_indexed",
  "duration_ms": 82.5,
  "status": "OK",
  "error": ""
}
```

### Chunking Parameters
Configured in `control/knowledge_indexer.py`:
```python
chunk_size  = 800   # characters per chunk
overlap     = 150   # character overlap between chunks
```

---

## 4. Backup & Recovery

### Backup Procedure

**Daily automated backup:**
```powershell
# backup.ps1
$date = Get-Date -Format "yyyyMMdd"
$backup_dir = "backups\$date"
New-Item -Path $backup_dir -ItemType Directory -Force

# Backup SQLite (safe with WAL mode using .backup command)
python -c "
import sqlite3
src = sqlite3.connect('state/task_checkpoints.db')
dst = sqlite3.connect('$backup_dir/task_checkpoints.db')
src.backup(dst)
dst.close(); src.close()
print('Backup complete')
"

# Backup logs
Copy-Item -Path "logs\*" -Destination "$backup_dir\" -Recurse
```

**Retention policy:** Keep 7 daily, 4 weekly, 3 monthly backups.

### Restore Procedure

```powershell
# Stop all services first
pm2 stop all

# Restore database
$restore_from = "backups\20260719\task_checkpoints.db"
Copy-Item -Path $restore_from -Destination "state\task_checkpoints.db" -Force

# Verify restore
python -c "
import sqlite3
with sqlite3.connect('state/task_checkpoints.db') as c:
    count = c.execute('SELECT COUNT(*) FROM task_artifacts').fetchone()[0]
    print(f'Restored: {count} artifacts')
"

# Restart services
pm2 start all
```

### Point-in-Time Recovery

SQLite WAL mode maintains a write-ahead log. To recover from WAL:
```powershell
# If main DB is corrupt but WAL exists
sqlite3 state/task_checkpoints.db ".recover" | sqlite3 state/task_checkpoints_recovered.db
```

---

## 5. Failure Handling

### Worker Crash
**Behavior:** Worker exits. Artifact is never saved (if crash before `save_artifacts`). No orphaned lease because worker never owned a lease.

**Recovery:** The task remains in `PENDING` status in the tasks table. The Bridge Server can be called to re-assign the task. No manual intervention for knowledge indexing.

### Indexer Crash During Indexing
**Behavior:** Artifact stuck in `INDEXING` status with `lease_expiration` set 5 minutes in the future.

**Recovery:** Automatic. After `lease_expiration` passes, any living indexer reclaims the artifact. No data loss — content was already saved.

**Monitor with:**
```powershell
python -c "
import sqlite3
with sqlite3.connect('state/task_checkpoints.db') as c:
    rows = c.execute(\"SELECT task_id, name, indexing_status, lease_expiration FROM task_artifacts WHERE indexing_status='INDEXING'\").fetchall()
    for r in rows: print(r)
"
```

### Gemini API Down
**Behavior:** `KnowledgeIndexer.index_artifact()` raises exception. Artifact marked `FAILED` with `retry_count += 1` and exponential backoff: `next_retry_at = now + (30s × 2^retry_count)`.

**Recovery:** Automatic when Gemini returns. Indexer continuously polls for `FAILED` artifacts with expired `next_retry_at`.

**Manual override:**
```powershell
# Force immediate retry by backdating next_retry_at
python -c "
import sqlite3, datetime
with sqlite3.connect('state/task_checkpoints.db') as c:
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
    c.execute(\"UPDATE task_artifacts SET next_retry_at=? WHERE indexing_status='FAILED'\", (past,))
    c.commit()
    print('All FAILED artifacts marked for immediate retry')
"
```

### Database Corruption
```powershell
# Check integrity
sqlite3 state/task_checkpoints.db "PRAGMA integrity_check;"

# If corrupt, recover from backup (see Section 4)
# OR recover from WAL
sqlite3 state/task_checkpoints.db ".recover" | sqlite3 state/task_checkpoints_new.db
Move-Item state/task_checkpoints_new.db state/task_checkpoints.db -Force
```

### Duplicate Artifacts (idempotency)
The `UNIQUE(task_id, name)` constraint on `task_artifacts` prevents duplicates at the DB level. `INSERT OR REPLACE` is used on re-submission.

### Max Retry Exceeded
After 7 retries (`retry_count >= 7`), backoff reaches `30 × 2^7 = 64 minutes`. Operator should investigate `indexing_error` field.

```powershell
# Find exhausted retries
python -c "
import sqlite3
with sqlite3.connect('state/task_checkpoints.db') as c:
    rows = c.execute(\"SELECT task_id, name, retry_count, indexing_error FROM task_artifacts WHERE retry_count >= 7\").fetchall()
    for r in rows: print(r)
"
```

---

## 6. Restart Procedures

### Restart Bridge Server (zero downtime with load balancer)
```powershell
# Graceful restart — in-flight requests complete
pm2 reload bridge

# Hard restart
pm2 restart bridge

# Verify health
curl http://localhost:8000/metrics
```

### Restart Knowledge Indexer
```powershell
# Safe to kill anytime — any in-progress artifact lease expires automatically
pm2 restart indexer

# Verify it picks up pending work
python -c "
import sqlite3
with sqlite3.connect('state/task_checkpoints.db') as c:
    pending = c.execute(\"SELECT COUNT(*) FROM task_artifacts WHERE indexing_status IN ('PENDING','FAILED')\").fetchone()[0]
    print(f'Pending/Failed to process: {pending}')
"
```

### Scale Indexers Up/Down
```powershell
# Add 2 more indexer instances (lease locking prevents races)
pm2 start knowledge_indexer_service.py --interpreter python --name indexer-2
pm2 start knowledge_indexer_service.py --interpreter python --name indexer-3

# Scale back
pm2 delete indexer-2
```

---

## 7. Monitoring

### Prometheus Metrics Endpoint
```
GET http://localhost:8000/metrics
```

**Available metrics:**
| Metric | Type | Description |
|---|---|---|
| `bridge_uptime_seconds` | gauge | Seconds since bridge server start |
| `bridge_requests_total` | counter | Total HTTP requests handled |
| `bridge_requests_errors_total` | counter | Total HTTP error responses |
| `bridge_tasks_created_total` | counter | Tasks created via API |
| `bridge_request_duration_ms_avg` | gauge | Average request duration in ms |
| `artifacts_pending` | gauge | Artifacts waiting for indexing |
| `artifacts_failed` | gauge | Artifacts in FAILED state |
| `artifacts_indexed` | gauge | Total artifacts successfully indexed |
| `knowledge_chunks_total` | gauge | Total knowledge chunks stored |
| `indexer_queue_depth` | gauge | Current queue depth (pending+failed) |

### Log-Based Monitoring

All services emit structured JSON logs. Use any log aggregator (ELK, Loki, CloudWatch):

```bash
# Tail live structured logs
Get-Content -Path logs\knowledge_indexer.log -Wait | ForEach-Object {
    $_ | ConvertFrom-Json | Select-Object timestamp, event, task_id, duration_ms, status
}

# Count events by type
Get-Content logs\knowledge_indexer.log | ForEach-Object {
    ($_ | ConvertFrom-Json).event
} | Group-Object | Sort-Object Count -Descending
```

### Indexer Queue Depth API
```bash
GET /indexer/queue   # returns pending + failed artifacts
```

### Key Dashboard Queries

**Queue depth over time:**
```
indexer_queue_depth
```

**Error rate:**
```
rate(bridge_requests_errors_total[5m]) / rate(bridge_requests_total[5m])
```

**Indexing throughput:**
```
rate(artifacts_indexed[1m]) * 60
```

---

## 8. Alerts

### Critical Alerts (PagerDuty / immediate)

| Alert | Condition | Action |
|---|---|---|
| Bridge Down | No successful request in 5 min | Restart bridge (`pm2 restart bridge`) |
| DB Unwritable | SQLite error on INSERT | Check disk space, permissions |
| Queue Starvation | `indexer_queue_depth > 50` for 10+ min | Start additional indexer instances |
| Max Retries | `artifacts_failed` with `retry_count >= 7` | Check Gemini API key, investigate error |

### Warning Alerts (Slack / 15 min response)

| Alert | Condition | Action |
|---|---|---|
| High Queue Depth | `indexer_queue_depth > 20` | Scale indexers |
| Slow Requests | `bridge_request_duration_ms_avg > 500ms` | Check DB lock contention |
| Memory Growth | Indexer RSS growing continuously | Restart indexer instance |
| FAILED artifacts | `artifacts_failed > 5` | Check Gemini availability |

### Alert Configuration (Prometheus Alertmanager)
```yaml
groups:
  - name: engineering_manager
    rules:
      - alert: BridgeDown
        expr: bridge_uptime_seconds == 0
        for: 5m
        labels: { severity: critical }

      - alert: QueueStarvation
        expr: indexer_queue_depth > 50
        for: 10m
        labels: { severity: critical }

      - alert: HighFailureRate
        expr: artifacts_failed > 5
        for: 5m
        labels: { severity: warning }

      - alert: SlowRequests
        expr: bridge_request_duration_ms_avg > 500
        for: 15m
        labels: { severity: warning }
```

---

## 9. Runbooks

### RB-001: Bridge Server Not Responding
```
1. Check if process is running:
   pm2 list | grep bridge

2. Check port:
   netstat -an | findstr 8000

3. Check logs:
   Get-Content logs/bridge_server.log -Tail 50

4. Restart:
   pm2 restart bridge

5. If still failing, check config:
   python -c "import yaml; print(yaml.safe_load(open('config/supabase.yaml')))"
```

### RB-002: Artifacts Stuck in PENDING
```
1. Verify indexer is running:
   pm2 list | grep indexer

2. If indexer is down, start it:
   pm2 start indexer

3. Check for PENDING count:
   python -c "
   import sqlite3
   with sqlite3.connect('state/task_checkpoints.db') as c:
       count = c.execute(\"SELECT COUNT(*) FROM task_artifacts WHERE indexing_status='PENDING'\").fetchone()[0]
       print(f'PENDING: {count}')
   "

4. If indexer is running but queue not draining after 5 min,
   check for orphaned leases:
   python -c "
   import sqlite3, datetime
   cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
   with sqlite3.connect('state/task_checkpoints.db') as c:
       orphans = c.execute(
           \"SELECT task_id, name, lease_expiration FROM task_artifacts WHERE indexing_status='INDEXING' AND lease_expiration < ?\",
           (cutoff,)
       ).fetchall()
       print(f'Orphaned leases: {len(orphans)}')
       for o in orphans: print(o)
   "

5. If orphans found, they will self-heal within 5 minutes.
   To force immediate recovery:
   pm2 restart indexer
```

### RB-003: Gemini API Errors
```
1. Verify API key is set:
   echo $env:GEMINI_API_KEY

2. Test API connectivity:
   python -c "
   import os; key = os.environ.get('GEMINI_API_KEY','')
   print('Key set' if key else 'KEY MISSING')
   "

3. If key is missing, set it and restart indexer:
   $env:GEMINI_API_KEY = 'your-key'
   pm2 restart indexer

4. Force retry of FAILED artifacts:
   python -c "
   import sqlite3, datetime
   past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
   with sqlite3.connect('state/task_checkpoints.db') as c:
       c.execute(\"UPDATE task_artifacts SET next_retry_at=? WHERE indexing_status='FAILED'\", (past,))
       c.commit()
   print('All FAILED artifacts marked for immediate retry')
   "
```

### RB-004: Database Disk Full
```
1. Check disk space:
   Get-PSDrive C | Select-Object Used,Free

2. Clean old knowledge chunks (keep last 30 days):
   python -c "
   import sqlite3, datetime
   cutoff = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()
   with sqlite3.connect('state/task_checkpoints.db') as c:
       deleted = c.execute('DELETE FROM task_knowledge WHERE created_at < ?', (cutoff,)).rowcount
       c.commit()
       c.execute('VACUUM')
   print(f'Deleted {deleted} old chunks')
   "

3. Or archive to cold storage and VACUUM:
   sqlite3 state/task_checkpoints.db "VACUUM;"
```

### RB-005: High Memory Usage
```
1. Check indexer memory:
   pm2 monit

2. Restart indexer (safe, any in-progress artifact self-heals via lease expiry):
   pm2 restart indexer

3. If recurs frequently, reduce batch size in knowledge_indexer_service.py
   or add gc.collect() call after each indexing cycle.
```

---

## Appendix: Quick Reference

```
# View all service logs
pm2 logs

# Watch queue depth
watch -n 5 'curl -s http://localhost:8000/metrics | grep indexer_queue'

# Full system status
python -c "
import sqlite3
with sqlite3.connect('state/task_checkpoints.db') as c:
    for status in ['PENDING','INDEXING','INDEXED','FAILED']:
        count = c.execute(f\"SELECT COUNT(*) FROM task_artifacts WHERE indexing_status='{status}'\").fetchone()[0]
        print(f'{status:>10}: {count}')
    chunks = c.execute('SELECT COUNT(*) FROM task_knowledge').fetchone()[0]
    print(f'{'CHUNKS':>10}: {chunks}')
"
```

---
*Last updated: 2026-07-19 | Version: 1.0.0 | Architecture: Enterprise Knowledge Architecture v1*
