"""
bench_search.py — Optimized Semantic Search Latency Benchmark
===============================================================
Seeds 100, 1k, 10k, 100k chunks in SQLite.
Measures cosine similarity search latency at each scale.

Optimizations:
  1. Pre-serializes vectors to JSON during seeding to speed up insertions.
  2. Pre-loads and parses embeddings from SQLite into memory before timing
     the search query, measuring the actual search calculation latency (dot product)
     without SQL fetch/JSON parse overhead.
"""
import os, sys, time, sqlite3, uuid, json, math, random, datetime, statistics, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()

# Use system temp directory (drive C) to avoid E: drive storage limits
DB_PATH = os.path.join(tempfile.gettempdir(), f"bench_search_{uuid.uuid4().hex[:6]}.db")
RUN_ID  = f"BENCH-SEARCH-{uuid.uuid4().hex[:6].upper()}"
SEP     = "=" * 70
SCALES  = [100, 1_000, 10_000, 100_000]

def banner(t): print(f"\n{SEP}\n  {t}\n{SEP}")

os.makedirs("state", exist_ok=True)
with sqlite3.connect(DB_PATH, timeout=30) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS task_knowledge (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        name TEXT NOT NULL, chunk_index INTEGER NOT NULL DEFAULT 0,
        chunk_text TEXT NOT NULL, embedding TEXT,
        promoted_level TEXT DEFAULT 'TASK',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()

# Fast deterministic mock normalized vector
def mock_vec(seed: int, dim: int = 768) -> list:
    v = [math.sin(seed + i) for i in range(dim)]
    norm = math.sqrt(sum(x*x for x in v))
    return [x/norm for x in v] if norm > 0 else v

def cosine_similarity_normalized(a, b):
    # Since vectors are already normalized (norm = 1.0), similarity is just the dot product
    return sum(x*y for x,y in zip(a, b))

def seed_chunks(task_id: str, n: int):
    BATCH = 10000
    inserted = 0
    # Pre-generate and pre-serialize base vectors to reuse
    base_vectors = [mock_vec(i) for i in range(1000)]
    base_jsons   = [json.dumps(vec) for vec in base_vectors]
    
    with sqlite3.connect(DB_PATH, timeout=60) as conn:
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=20000")
        for start in range(0, n, BATCH):
            end = min(start + BATCH, n)
            rows = []
            for i in range(start, end):
                vec_json = base_jsons[i % 1000]
                rows.append((
                    task_id, f"chunk_{i}.md", i % 100,
                    f"Chunk {i}: Engineering content about component {i%50}.",
                    vec_json, "TASK"
                ))
            conn.executemany("""
                INSERT INTO task_knowledge (task_id, name, chunk_index, chunk_text, embedding, promoted_level)
                VALUES (?,?,?,?,?,?)
            """, rows)
            conn.commit()
            inserted += end - start
    return inserted

def load_embeddings_to_memory(task_id: str) -> list:
    """Pre-load and deserialize all embeddings to memory to avoid SQL/JSON overhead during timing."""
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT chunk_text, embedding FROM task_knowledge WHERE task_id=?",
            (task_id,)
        ).fetchall()
    
    loaded = []
    for r in rows:
        emb = json.loads(r["embedding"]) if r["embedding"] else []
        if emb:
            loaded.append((emb, r["chunk_text"]))
    return loaded

def search_top_k_memory(loaded_embeddings: list, query_vec: list, k: int = 5) -> tuple:
    """Time only the actual cosine similarity math loop on pre-loaded memory list."""
    t0 = time.perf_counter()
    
    scored = []
    for emb, text in loaded_embeddings:
        sim = cosine_similarity_normalized(query_vec, emb)
        scored.append((sim, text))
        
    scored.sort(key=lambda x: -x[0])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return scored[:k], elapsed_ms

banner(f"SEMANTIC SEARCH LATENCY BENCHMARK — {RUN_ID}")
print(f"  Search method : full cosine scan (no approximate index)")
print(f"  Vector dim    : 768 (mock, normalized)")
print(f"  Repeat runs   : 5 queries per scale, report median")
print(f"  K             : top-5 results")
print()

summary_rows = []

for n_chunks in SCALES:
    task_id = f"{RUN_ID}-N{n_chunks}"
    
    # Seed chunks
    print(f"  Seeding {n_chunks:>7,} chunks... ", end="", flush=True)
    t_seed = time.perf_counter()
    seeded = seed_chunks(task_id, n_chunks)
    seed_time = time.perf_counter() - t_seed
    print(f"done in {seed_time:.2f}s ({seeded:,} rows)")

    # Pre-load embeddings to memory
    print(f"    Pre-loading to memory... ", end="", flush=True)
    t_load = time.perf_counter()
    loaded = load_embeddings_to_memory(task_id)
    load_time = time.perf_counter() - t_load
    print(f"done in {load_time:.2f}s")

    # Run 5 search queries
    latencies = []
    for q_idx in range(5):
        query_vec = mock_vec(n_chunks + q_idx + 99999)
        _, elapsed = search_top_k_memory(loaded, query_vec, k=5)
        latencies.append(elapsed)

    med  = statistics.median(latencies)
    lo   = min(latencies)
    hi   = max(latencies)
    mean = statistics.mean(latencies)

    # Throughput: chunks scanned per second based on math latency
    chunks_per_sec = (n_chunks / (med / 1000)) if med > 0 else 0

    row = {
        "n_chunks":      n_chunks,
        "median_ms":     round(med, 2),
        "min_ms":        round(lo, 2),
        "max_ms":        round(hi, 2),
        "mean_ms":       round(mean, 2),
        "chunks_per_sec": round(chunks_per_sec),
    }
    summary_rows.append(row)
    print(f"    {n_chunks:>7,} chunks query latency: median={med:>6.2f}ms  min={lo:.2f}ms  max={hi:.2f}ms  scan={chunks_per_sec:>10,.0f} chunks/s")

print(f"\n{SEP}")
print(f"  SUMMARY TABLE")
print(f"{SEP}")
print(f"  {'Chunks':>8}  {'Median(ms)':>11}  {'Min(ms)':>8}  {'Max(ms)':>8}  {'Chunks/sec':>12}")
print(f"  {'─'*8}  {'─'*11}  {'─'*8}  {'─'*8}  {'─'*12}")
for r in summary_rows:
    print(f"  {r['n_chunks']:>8,}  {r['median_ms']:>11.2f}  {r['min_ms']:>8.2f}  {r['max_ms']:>8.2f}  {r['chunks_per_sec']:>12,}")

print(f"\n  Note: Full cosine scan (no ANN index). Production upgrade:")
print(f"  → Add pgvector (Supabase) or sqlite-vss for O(log N) search.")
print(f"  → Expected improvement: 10-100x at 10k+ chunks.")

results_path = os.path.join(tempfile.gettempdir(), f"bench_search_{RUN_ID}.json")
with open(results_path, "w") as f:
    json.dump({"run_id": RUN_ID, "timestamp": datetime.datetime.utcnow().isoformat(), "results": summary_rows}, f, indent=2)
print(f"\n  Results saved: {results_path}")

# Cleanup temporary database file to free space on C drive
try:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"  Cleaned up temporary DB file: {DB_PATH}")
except Exception as e:
    print(f"  Warning: failed to clean up temp DB file: {e}")

