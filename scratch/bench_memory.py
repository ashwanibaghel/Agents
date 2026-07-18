"""
bench_memory.py — Memory Profiling
=====================================
Uses tracemalloc to detect memory growth during long-running indexing.
Runs 200 consecutive indexing cycles and reports:
  - peak memory (MB)
  - memory growth per cycle
  - leak detection (growth > 1MB after warmup = potential leak)
"""
import os, sys, time, sqlite3, uuid, json, tracemalloc, gc, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()

DB_PATH    = "state/task_checkpoints.db"
RUN_ID     = f"BENCH-MEM-{uuid.uuid4().hex[:6].upper()}"
N_CYCLES   = 200
SEP        = "=" * 70
SAMPLE_AT  = [1, 10, 25, 50, 100, 150, 200]   # snapshot at these cycle counts

def banner(t): print(f"\n{SEP}\n  {t}\n{SEP}")

os.makedirs("state", exist_ok=True)
with sqlite3.connect(DB_PATH, timeout=10) as conn:
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
from control.embedding_provider import EmbeddingProviderRegistry, MockEmbeddingProvider
EmbeddingProviderRegistry.register("mock", MockEmbeddingProvider(dimension=768))

def make_content(i):
    return (
        f"# Report {i}\nTask: BENCH-{i}\n\n"
        "## Findings\nComponent X runs on port 8000. "
        "The system uses SQLite with WAL mode for concurrent access. "
        "Lease-based locking prevents duplicate indexing. " * 8
    )

banner(f"MEMORY PROFILING — {RUN_ID}")
print(f"  Cycles  : {N_CYCLES} consecutive indexing runs")
print(f"  Sampled : {SAMPLE_AT}")
print(f"  Method  : tracemalloc + gc.collect() between cycles")
print()

tracemalloc.start()

indexer   = KnowledgeIndexer(db_path=DB_PATH, provider_name="mock")
worker_id = f"mem-bench-{uuid.uuid4().hex[:6]}"

snapshots = []   # (cycle, current_mb, peak_mb)
t_start   = time.perf_counter()

for cycle in range(1, N_CYCLES + 1):
    tid     = f"{RUN_ID}-C{cycle:04d}"
    content = make_content(cycle)

    # Seed
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("""INSERT OR REPLACE INTO task_artifacts
            (task_id, name, path, type, size, summary, content, indexing_status)
            VALUES (?,?,?,?,?,?,?,'PENDING')""",
            (tid, "RECON.md", "RECON.md", "markdown",
             len(content), content[:150], content))
        conn.commit()

    # Index
    if indexer.claim_artifact_lease(tid, "RECON.md", worker_id):
        indexer.index_artifact(tid, "RECON.md", content)

    # Sample
    if cycle in SAMPLE_AT:
        current, peak = tracemalloc.get_traced_memory()
        current_mb = current / 1024 / 1024
        peak_mb    = peak    / 1024 / 1024
        snapshots.append((cycle, round(current_mb, 3), round(peak_mb, 3)))
        elapsed = time.perf_counter() - t_start
        print(f"  Cycle {cycle:>3d}: current={current_mb:.3f}MB  peak={peak_mb:.3f}MB  elapsed={elapsed:.2f}s")

    # Cleanup DB (keep DB small)
    if cycle % 50 == 0:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(f"DELETE FROM task_artifacts WHERE task_id LIKE '{RUN_ID}%'")
            conn.execute(f"DELETE FROM task_knowledge  WHERE task_id LIKE '{RUN_ID}%'")
            conn.commit()
        gc.collect()

tracemalloc.stop()
total_elapsed = time.perf_counter() - t_start

# Analysis
first_mb = snapshots[0][1] if snapshots else 0
last_mb  = snapshots[-1][1] if snapshots else 0
growth   = last_mb - first_mb
leaked   = growth > 1.0   # > 1MB after 200 cycles = potential leak
peak_all = max(s[2] for s in snapshots) if snapshots else 0

print(f"\n{SEP}")
print(f"  MEMORY ANALYSIS RESULTS")
print(f"{SEP}")
print(f"  Total cycles    : {N_CYCLES}")
print(f"  Total time      : {total_elapsed:.2f}s")
print(f"  Memory @ cycle 1 : {first_mb:.3f} MB")
print(f"  Memory @ cycle {N_CYCLES}: {last_mb:.3f} MB")
print(f"  Net growth      : {growth:+.3f} MB")
print(f"  Peak memory     : {peak_all:.3f} MB")
print(f"  Leak detected   : {'YES ❌' if leaked else 'NO ✅'}")
print(f"  Verdict         : {'MEMORY LEAK DETECTED' if leaked else 'STABLE — no leak after 200 cycles'}")

print(f"\n  Cycle  Current(MB)  Peak(MB)")
print(f"  {'─'*5}  {'─'*11}  {'─'*8}")
for cyc, cur, pk in snapshots:
    print(f"  {cyc:>5}  {cur:>11.3f}  {pk:>8.3f}")

results = {
    "run_id": RUN_ID, "cycles": N_CYCLES,
    "timestamp": datetime.datetime.utcnow().isoformat(),
    "growth_mb": round(growth, 3), "peak_mb": round(peak_all, 3),
    "leak_detected": leaked, "snapshots": snapshots
}
rpath = os.path.join("state", f"bench_memory_{RUN_ID}.json")
with open(rpath, "w") as f: json.dump(results, f, indent=2)
print(f"\n  Results saved: {rpath}")
