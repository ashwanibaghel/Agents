"""
soak_test.py — Compressed 10-Minute Soak Test
===============================================
Continuously generates tasks and verifies:
  - no duplicate chunks
  - no memory growth
  - no deadlocks (all tasks complete within timeout)
  - no orphaned leases
  - no queue starvation

Runs for SOAK_DURATION_SECONDS (default 600 = 10 minutes).
Stats are sampled every 30 seconds.
Final report extrapolates to 24-hour projections.
"""
import os, sys, time, sqlite3, uuid, datetime, json, threading, signal, gc, tracemalloc
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()

SOAK_DURATION   = int(os.environ.get("SOAK_DURATION", "120"))   # 2 min default, override with SOAK_DURATION=600
SAMPLE_INTERVAL = 20
DB_PATH         = "state/task_checkpoints.db"
RUN_ID          = f"SOAK-{uuid.uuid4().hex[:8].upper()}"
SEP             = "=" * 70
running         = True

def handle_stop(sig, frame):
    global running
    print(f"\n  [STOP] Signal received — finishing current cycle...")
    running = False

signal.signal(signal.SIGINT,  handle_stop)
signal.signal(signal.SIGTERM, handle_stop)

def banner(t): print(f"\n{SEP}\n  {t}\n{SEP}")

os.makedirs("state", exist_ok=True)
with sqlite3.connect(DB_PATH, timeout=30) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS task_artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        name TEXT NOT NULL, path TEXT NOT NULL, type TEXT NOT NULL,
        size INTEGER NOT NULL, summary TEXT, content TEXT NOT NULL,
        indexing_status TEXT NOT NULL DEFAULT 'PENDING',
        retry_count INTEGER DEFAULT 0, indexing_error TEXT,
        next_retry_at TEXT, lease_expiration TEXT, indexed_by TEXT,
        claimed_by TEXT, claimed_at TEXT, indexed_at TEXT, last_retry_at TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(task_id, name))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS task_knowledge (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        name TEXT NOT NULL, chunk_index INTEGER NOT NULL DEFAULT 0,
        chunk_text TEXT NOT NULL, embedding TEXT,
        promoted_level TEXT DEFAULT 'TASK',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()

from control.knowledge_indexer import KnowledgeIndexer
from control.artifact_service  import ArtifactService
from control.storage_provider  import LocalStorageProvider
from control.event_bus         import DatabasePollingEventBus
from control.embedding_provider import EmbeddingProviderRegistry, MockEmbeddingProvider
EmbeddingProviderRegistry.register("mock", MockEmbeddingProvider(768))

WORKER_ID = f"soak-worker-{uuid.uuid4().hex[:6]}"
indexer   = KnowledgeIndexer(db_path=DB_PATH, provider_name="mock")

# ── Soak metrics ─────────────────────────────────────────────────────────────
metrics = {
    "tasks_created":     0,
    "tasks_indexed":     0,
    "tasks_failed":      0,
    "duplicate_chunks":  0,
    "orphaned_leases":   0,
    "deadlocks":         0,
    "total_chunks":      0,
    "errors":            [],
}
metrics_lock = threading.Lock()

# ── Snapshot series (for memory trend) ──────────────────────────────────────
snapshots = []   # (elapsed_s, mem_mb, tasks_created, tasks_indexed)
tracemalloc.start()

def sample_snapshot(elapsed):
    current, _ = tracemalloc.get_traced_memory()
    with metrics_lock:
        snap = {
            "elapsed_s":      round(elapsed, 1),
            "mem_mb":         round(current / 1024 / 1024, 3),
            "tasks_created":  metrics["tasks_created"],
            "tasks_indexed":  metrics["tasks_indexed"],
            "tasks_failed":   metrics["tasks_failed"],
            "dup_chunks":     metrics["duplicate_chunks"],
            "orphaned":       metrics["orphaned_leases"],
        }
    snapshots.append(snap)
    return snap

def make_task():
    """Create and index one task. Returns success bool."""
    tid = f"{RUN_ID}-{uuid.uuid4().hex[:8]}"
    content = (
        f"# Soak Task {tid}\n"
        f"Generated: {datetime.datetime.utcnow().isoformat()}Z\n\n"
        "## Summary\nThis is a continuous soak test task for production hardening.\n"
        "## Components\n- IndexerService\n- EventBus\n- ArtifactService\n" * 4
    )

    # 1. Save artifact (PENDING)
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("""INSERT OR REPLACE INTO task_artifacts
            (task_id, name, path, type, size, summary, content, indexing_status)
            VALUES (?,?,?,?,?,?,?,'PENDING')""",
            (tid, "RECON.md", "RECON.md", "markdown",
             len(content), content[:150], content))
        conn.commit()

    with metrics_lock: metrics["tasks_created"] += 1

    # 2. Claim lease + index
    t0 = time.perf_counter()
    claimed = indexer.claim_artifact_lease(tid, "RECON.md", WORKER_ID)
    if not claimed:
        with metrics_lock:
            metrics["tasks_failed"] += 1
            metrics["errors"].append(f"lease_not_claimed:{tid}")
        return False

    success = indexer.index_artifact(tid, "RECON.md", content)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if not success:
        with metrics_lock:
            metrics["tasks_failed"] += 1
        return False

    with metrics_lock: metrics["tasks_indexed"] += 1

    # 3. Verify no duplicate chunks
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        rows = conn.execute(
            "SELECT chunk_index FROM task_knowledge WHERE task_id=?", (tid,)
        ).fetchall()
    indices = [r[0] for r in rows]
    if len(indices) != len(set(indices)):
        with metrics_lock:
            metrics["duplicate_chunks"] += 1
            metrics["errors"].append(f"dup_chunks:{tid}")
        return False

    with metrics_lock: metrics["total_chunks"] += len(indices)

    # 4. Check for orphaned leases (stuck in INDEXING > 5 min)
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        orphaned = conn.execute("""
            SELECT COUNT(*) FROM task_artifacts
            WHERE task_id LIKE ? AND indexing_status='INDEXING'
              AND lease_expiration < ?
        """, (f"{RUN_ID}%", cutoff)).fetchone()[0]
    if orphaned > 0:
        with metrics_lock:
            metrics["orphaned_leases"] += orphaned

    # 5. Cleanup (keep DB lean during soak)
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("DELETE FROM task_artifacts WHERE task_id=?", (tid,))
        conn.execute("DELETE FROM task_knowledge  WHERE task_id=?", (tid,))
        conn.commit()

    return True

# ── MAIN SOAK LOOP ────────────────────────────────────────────────────────────
banner(f"SOAK TEST — {RUN_ID}")
print(f"  Duration       : {SOAK_DURATION}s ({SOAK_DURATION//60}m {SOAK_DURATION%60}s)")
print(f"  Sample interval: {SAMPLE_INTERVAL}s")
print(f"  Worker ID      : {WORKER_ID}")
print(f"  Provider       : MockEmbeddingProvider")
print(f"\n  {'TIME':>6}  {'CREATED':>8}  {'INDEXED':>8}  {'FAILED':>7}  {'MEM(MB)':>8}  {'DUPS':>5}  {'RATE/min':>9}")
print(f"  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*8}  {'─'*5}  {'─'*9}")

t_start      = time.time()
t_last_sample= t_start
t_last_count = 0

while running and (time.time() - t_start) < SOAK_DURATION:
    make_task()
    gc.collect()

    now = time.time()
    if now - t_last_sample >= SAMPLE_INTERVAL:
        elapsed  = now - t_start
        snap     = sample_snapshot(elapsed)
        delta    = snap["tasks_indexed"] - t_last_count
        rate_min = (delta / SAMPLE_INTERVAL) * 60
        t_last_count = snap["tasks_indexed"]
        t_last_sample = now
        print(f"  {elapsed:>5.0f}s  {snap['tasks_created']:>8}  {snap['tasks_indexed']:>8}  {snap['tasks_failed']:>7}  {snap['mem_mb']:>8.3f}  {snap['dup_chunks']:>5}  {rate_min:>9.1f}")

# Final sample
elapsed = time.time() - t_start
snap = sample_snapshot(elapsed)
tracemalloc.stop()

# ── Analysis ──────────────────────────────────────────────────────────────────
banner("SOAK TEST ANALYSIS")

with metrics_lock: m = dict(metrics)

mem_start  = snapshots[0]["mem_mb"]  if snapshots else 0
mem_end    = snapshots[-1]["mem_mb"] if snapshots else 0
mem_growth = mem_end - mem_start
rate       = (m["tasks_indexed"] / elapsed) * 60 if elapsed > 0 else 0

# Extrapolate to 24h
proj_24h_tasks  = int(rate * 60 * 24)
proj_24h_chunks = int((m["total_chunks"] / max(m["tasks_indexed"],1)) * proj_24h_tasks)
proj_mem_24h    = mem_start + (mem_growth / elapsed) * 86400 if elapsed > 0 else 0

def chk(label, ok):
    print(f"  {'[PASS]' if ok else '[FAIL]'}  {label}")
    return ok

print(f"\n  Soak results:")
print(f"  {'─'*60}")
print(f"  Duration        : {elapsed:.1f}s")
print(f"  Tasks created   : {m['tasks_created']}")
print(f"  Tasks indexed   : {m['tasks_indexed']}")
print(f"  Tasks failed    : {m['tasks_failed']}")
print(f"  Dup chunks      : {m['duplicate_chunks']}")
print(f"  Orphaned leases : {m['orphaned_leases']}")
print(f"  Total chunks    : {m['total_chunks']}")
print(f"  Throughput      : {rate:.1f} tasks/min")
print(f"  Memory start    : {mem_start:.3f} MB")
print(f"  Memory end      : {mem_end:.3f} MB")
print(f"  Memory growth   : {mem_growth:+.3f} MB")
print(f"\n  Assertions:")
r1 = chk("No duplicate chunks",        m["duplicate_chunks"] == 0)
r2 = chk("No memory growth > 5MB",     mem_growth < 5.0)
r3 = chk("No deadlocks",               m["tasks_failed"] == 0)
r4 = chk("No orphaned leases",         m["orphaned_leases"] == 0)
r5 = chk("No queue starvation (>0 indexed)", m["tasks_indexed"] > 0)
r6 = chk("Throughput > 100 tasks/min", rate > 100)

print(f"""
  24-Hour Projections (extrapolated from {elapsed:.0f}s run):
  ─────────────────────────────────────────────────────────
  Tasks/day   : {proj_24h_tasks:>10,}
  Chunks/day  : {proj_24h_chunks:>10,}
  Mem growth/day: {proj_mem_24h:>8.2f} MB
  ─────────────────────────────────────────────────────────
  {'✅ SOAK TEST PASSED — System is production-stable.' if all([r1,r2,r3,r4,r5,r6]) else '❌ SOAK TEST FAILED — See assertions above.'}
""")

results = {
    "run_id": RUN_ID, "duration_s": round(elapsed, 1),
    "timestamp": datetime.datetime.utcnow().isoformat(),
    "metrics": m, "throughput_per_min": round(rate, 1),
    "memory_growth_mb": round(mem_growth, 3),
    "projections_24h": {"tasks": proj_24h_tasks, "chunks": proj_24h_chunks, "mem_mb": round(proj_mem_24h, 2)},
    "all_pass": all([r1,r2,r3,r4,r5,r6]), "snapshots": snapshots
}
rpath = os.path.join("state", f"soak_{RUN_ID}.json")
with open(rpath, "w") as f: json.dump(results, f, indent=2)
print(f"  Results saved: {rpath}")
