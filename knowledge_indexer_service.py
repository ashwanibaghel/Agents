import os
import time
import uuid
import signal
import sys
from dotenv import load_dotenv

# Load environments
load_dotenv()

from control.event_bus import DatabasePollingEventBus
from control.knowledge_indexer import KnowledgeIndexer

# Unique indexer ID for leasing lock ownership
INDEXER_WORKER_ID = f"indexer-worker-{str(uuid.uuid4())[:8].upper()}"
running = True

def handle_shutdown(signum, frame):
    global running
    print("\n🛑 Shutdown signal received. Exiting indexer service gracefully...")
    running = False

# Register signal handlers for clean exit
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def run_indexer_service():
    print("=" * 60)
    print(f"🚀 Ashwani Agent Company Knowledge Indexer Service")
    print(f"Worker ID: {INDEXER_WORKER_ID}")
    print(f"Process ID: {os.getpid()}")
    print(f"Embedding Provider: {os.environ.get('EMBEDDING_PROVIDER', 'gemini')}")
    print(f"Supabase Backend: {os.environ.get('SUPABASE_ENABLED', 'false')}")
    print("=" * 60)

    # Initialize components
    event_bus = DatabasePollingEventBus()
    # Resolve embedding provider name from environment
    provider_name = os.environ.get("EMBEDDING_PROVIDER", "gemini")
    indexer = KnowledgeIndexer(provider_name=provider_name)

    # Subscribe to artifact creation events
    def on_artifact_created(event_payload: dict):
        task_id = event_payload.get("task_id")
        name = event_payload.get("name")
        content = event_payload.get("content")
        
        print(f"📢 [Event] Index event triggered for artifact: {name} (Task: {task_id})")
        
        # Try to claim locking lease
        if indexer.claim_artifact_lease(task_id, name, INDEXER_WORKER_ID):
            print(f"🔒 [Lock] Claimed lease for artifact: {name}. Commencing chunk indexing...")
            start_time = time.time()
            
            # Perform sliding-window chunking, API vector embedding, and DB insertion
            success = indexer.index_artifact(task_id, name, content)
            latency = time.time() - start_time
            
            if success:
                print(f"✅ [Indexed] Successfully processed artifact: {name} in {latency:.2f}s.")
            else:
                print(f"❌ [Failed] Processing failed for artifact: {name}.")
        else:
            # Lease already claimed by another indexing worker instance
            print(f"⏭️  [Skipped] Lease already claimed or not eligible for artifact: {name}")

    event_bus.subscribe("artifact_created", on_artifact_created)

    print("📡 Polling database for PENDING indexing tasks...")
    
    poll_interval = float(os.environ.get("INDEXER_POLL_INTERVAL", "5.0"))
    
    while running:
        try:
            event_bus.poll()
        except Exception as e:
            print(f"⚠️ Indexer loop error: {e}")
        time.sleep(poll_interval)

    print("👋 Indexer service stopped.")

if __name__ == "__main__":
    run_indexer_service()
