"""
bench_indexing.py — Indexing Throughput Benchmark
===================================================
Measures:
  - artifacts/minute
  - chunks/second
  - average embedding latency (per embed call)
  - average indexing latency (end-to-end per artifact)
"""
import os, sys, time, sqlite3, uuid, datetime, statistics, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()

DB_PATH  = "state/task_checkpoints.db"
RUN_ID   = f"BENCH-IDX-{uuid.uuid4().hex[:6].upper()}"
BATCH_SIZES = [10, 50, 100]   # artifacts per batch
CHUNK_SIZE  = 800
SEP = "=" * 70

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

def make_content(i, size_chars=1500):
    base = (
        f"# Engineering Report #{i}\n"
        f"**Task**: BENCH-TASK-{i:04d}\n\n"
        "## Summary\n"
        "This is a synthetic benchmark artifact for throughput measurement. "
        "It contains realistic engineering content including component descriptions, "
        "API endpoints, database schema findings, and performance characteristics.\n\n"
        "## Architecture\n"
        "- Backend: FastAPI + SQLite (WAL mode)\n"
        "- Queue: DatabasePollingEventBus\n"
        "- Indexer: KnowledgeIndexerService with lease locking\n\n"
        "## Key Findings\n"
        "All components are independently deployable. Lease-based locking "
        "prevents duplicate indexing across multiple indexer instances.\n"
    )
    # Pad to desired size
    while len(base) < size_chars:
        base += f"Additional context line {len(base)}. The system scales horizontally.\n"
    return base[:size_chars]

def seed_batch(batch_id, n):
    """Seed n artifacts as PENDING. Returns task_ids."""
    task_ids = []
    for i in range(n):
        tid = f"{RUN_ID}-B{batch_id}-{i:04d}"
        content = make_content(i)
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("""INSERT OR REPLACE INTO task_artifacts
                (task_id, name, path, type, size, summary, content, indexing_status)
                VALUES (?,?,?,?,?,?,?,'PENDING')""",
                (tid, "RECON.md", "RECON.md", "markdown",
                 len(content), content[:200], content))
            conn.commit()
        task_ids.append(tid)
    return task_ids

def run_batch_indexing(task_ids):
    """Index all artifacts in batch, return timing data."""
    indexer = KnowledgeIndexer(db_path=DB_PATH, provider_name="mock")
    worker_id = f"bench-worker-{uuid.uuid4().hex[:6]}"

    artifact_latencies = []
    embed_latencies    = []
    chunk_counts       = []

    for tid in task_ids:
        # Read content
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            row = conn.execute(
                "SELECT content, name FROM task_artifacts WHERE task_id=?", (tid,)
            ).fetchone()
        if not row: continue
        content, name = row

        # Time lease claim
        claimed = indexer.claim_artifact_lease(tid, name, worker_id)
        if not claimed: continue

        # Measure embedding latency separately
        from control.embedding_provider import EmbeddingProviderRegistry
        provider = EmbeddingProviderRegistry.get_provider("mock")

        # Chunk manually to measure embed latency
        chunks = indexer._chunk_text(content)
        emb_times = []
        for chunk in chunks:
            t0 = time.perf_counter()
            provider.embed_text(chunk)
            emb_times.append((time.perf_counter() - t0) * 1000)

        embed_latencies.extend(emb_times)
        chunk_counts.append(len(chunks))

        # End-to-end indexing
        t_art = time.perf_counter()
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("UPDATE task_artifacts SET indexing_status='PENDING', claimed_by=NULL, lease_expiration=NULL WHERE task_id=? AND name=?", (tid, name))
            conn.commit()
        indexer.claim_artifact_lease(tid, name, worker_id)
        indexer.index_artifact(tid, name, content)
        art_elapsed = (time.perf_counter() - t_art) * 1000
        artifact_latencies.append(art_elapsed)

    return artifact_latencies, embed_latencies, chunk_counts

banner(f"INDEXING THROUGHPUT BENCHMARK — {RUN_ID}")
print(f"  Artifact sizes  : ~1500 chars each")
print(f"  Chunk size      : {CHUNK_SIZE} chars (overlap=150)")
print(f"  Embedding       : MockEmbeddingProvider (768-dim, instant)")
print(f"  DB backend      : SQLite WAL")
print()

all_results = []

for batch_size in BATCH_SIZES:
    print(f"  {'─'*60}")
    print(f"  Batch: {batch_size} artifacts")
    task_ids = seed_batch(len(all_results), batch_size)

    t_total = time.perf_counter()
    art_lats, emb_lats, chunk_cnts = run_batch_indexing(task_ids)
    total_elapsed = time.perf_counter() - t_total

    total_chunks  = sum(chunk_cnts)
    arts_per_min  = (len(art_lats) / total_elapsed) * 60 if total_elapsed > 0 else 0
    chunks_per_sec= total_chunks / total_elapsed if total_elapsed > 0 else 0
    avg_art_lat   = statistics.mean(art_lats) if art_lats else 0
    p95_art_lat   = sorted(art_lats)[int(len(art_lats)*0.95)] if art_lats else 0
    avg_emb_lat   = statistics.mean(emb_lats) if emb_lats else 0

    row = {
        "batch_size":    batch_size,
        "indexed":       len(art_lats),
        "total_chunks":  total_chunks,
        "total_s":       round(total_elapsed, 3),
        "artifacts/min": round(arts_per_min, 1),
        "chunks/sec":    round(chunks_per_sec, 1),
        "avg_art_ms":    round(avg_art_lat, 2),
        "p95_art_ms":    round(p95_art_lat, 2),
        "avg_emb_ms":    round(avg_emb_lat, 4),
    }
    all_results.append(row)

    print(f"    Indexed          : {row['indexed']}/{batch_size} artifacts")
    print(f"    Total time       : {row['total_s']}s")
    print(f"    Artifacts/min    : {row['artifacts/min']}")
    print(f"    Chunks/sec       : {row['chunks/sec']}")
    print(f"    Avg art latency  : {row['avg_art_ms']}ms")
    print(f"    P95 art latency  : {row['p95_art_ms']}ms")
    print(f"    Avg embed latency: {row['avg_emb_ms']}ms")
    print(f"    Total chunks     : {row['total_chunks']}")

print(f"\n{SEP}")
print(f"  SUMMARY TABLE")
print(f"{SEP}")
print(f"  {'Batch':>6}  {'Arts/min':>9}  {'Chunks/s':>9}  {'AvgArt(ms)':>11}  {'P95Art(ms)':>11}  {'AvgEmb(ms)':>11}")
print(f"  {'─'*6}  {'─'*9}  {'─'*9}  {'─'*11}  {'─'*11}  {'─'*11}")
for r in all_results:
    print(f"  {r['batch_size']:>6}  {r['artifacts/min']:>9}  {r['chunks/sec']:>9}  {r['avg_art_ms']:>11}  {r['p95_art_ms']:>11}  {r['avg_emb_ms']:>11}")

# Save results JSON
results_path = os.path.join("state", f"bench_indexing_{RUN_ID}.json")
with open(results_path, "w") as f:
    json.dump({"run_id": RUN_ID, "timestamp": datetime.datetime.utcnow().isoformat(), "results": all_results}, f, indent=2)
print(f"\n  Results saved: {results_path}")
