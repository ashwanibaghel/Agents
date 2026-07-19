"""
test_enterprise_architecture.py
================================
Unit and integration tests for the Enterprise Knowledge Architecture:
  - StorageProvider / LocalStorageProvider
  - ArtifactService (save, PENDING status, event publishing)
  - DatabasePollingEventBus (publish, subscribe, poll)
  - KnowledgeIndexer (chunking, lease claiming, artifact indexing)
  - ContextBuilder (task context aggregation & fallback)
  - Bridge Server new endpoints
  - Worker refactor validation
  - KnowledgeIndexerService structure
"""

import json
import os
import sqlite3
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sqlite(tmp_path: str) -> str:
    """Create a minimal SQLite database matching the real schema."""
    db_path = os.path.join(tmp_path, "test_checkpoints.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                type TEXT NOT NULL,
                size INTEGER NOT NULL,
                summary TEXT,
                content TEXT NOT NULL,
                indexing_status TEXT NOT NULL DEFAULT 'PENDING',
                retry_count INTEGER DEFAULT 0,
                indexing_error TEXT,
                next_retry_at TEXT,
                lease_expiration TEXT,
                indexed_by TEXT,
                claimed_by TEXT,
                claimed_at TEXT,
                indexed_at TEXT,
                last_retry_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(task_id, name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                name TEXT NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                chunk_text TEXT NOT NULL,
                embedding TEXT,
                promoted_level TEXT DEFAULT 'TASK',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    return db_path


# ===========================================================================
# StorageProvider Tests
# ===========================================================================

class TestLocalStorageProvider:
    def test_read_file(self, tmp_path):
        from control.storage_provider import LocalStorageProvider
        f = tmp_path / "sample.md"
        f.write_text("# Hello World", encoding="utf-8")
        provider = LocalStorageProvider(base_dir=str(tmp_path))
        assert provider.read_file("sample.md") == "# Hello World"

    def test_read_file_absolute_path(self, tmp_path):
        from control.storage_provider import LocalStorageProvider
        f = tmp_path / "abs.md"
        f.write_text("Absolute", encoding="utf-8")
        provider = LocalStorageProvider()
        assert provider.read_file(str(f)) == "Absolute"

    def test_get_size(self, tmp_path):
        from control.storage_provider import LocalStorageProvider
        f = tmp_path / "sized.txt"
        f.write_bytes(b"12345")
        provider = LocalStorageProvider(base_dir=str(tmp_path))
        assert provider.get_size("sized.txt") == 5

    def test_missing_file_raises(self, tmp_path):
        from control.storage_provider import LocalStorageProvider
        provider = LocalStorageProvider(base_dir=str(tmp_path))
        with pytest.raises(FileNotFoundError):
            provider.read_file("nonexistent.md")


# ===========================================================================
# ArtifactService Tests
# ===========================================================================

class TestArtifactService:
    def _make_service(self, tmp_path, event_bus=None, storage=None):
        from control.artifact_service import ArtifactService
        from control.storage_provider import LocalStorageProvider
        from control.event_bus import DatabasePollingEventBus
        db = _make_sqlite(str(tmp_path))
        if storage is None:
            storage = LocalStorageProvider(base_dir=str(tmp_path))
        if event_bus is None:
            event_bus = DatabasePollingEventBus(db_path=db)
        return ArtifactService(storage_provider=storage, event_bus=event_bus, db_path=db), db

    def test_save_single_artifact(self, tmp_path):
        (tmp_path / "RECON.md").write_text("# Recon Report", encoding="utf-8")
        svc, db = self._make_service(tmp_path)
        saved = svc.save_artifacts("TASK-001", ["RECON.md"])
        assert len(saved) == 1
        assert saved[0]["name"] == "RECON.md"
        assert saved[0]["indexing_status"] == "PENDING"
        with sqlite3.connect(db) as conn:
            rows = conn.execute("SELECT * FROM task_artifacts WHERE task_id='TASK-001'").fetchall()
        assert len(rows) == 1

    def test_save_multiple_artifacts(self, tmp_path):
        (tmp_path / "A.md").write_text("File A", encoding="utf-8")
        (tmp_path / "B.json").write_text("{}", encoding="utf-8")
        svc, _ = self._make_service(tmp_path)
        saved = svc.save_artifacts("TASK-002", ["A.md", "B.json"])
        assert len(saved) == 2

    def test_skips_missing_files_gracefully(self, tmp_path):
        svc, _ = self._make_service(tmp_path)
        saved = svc.save_artifacts("TASK-003", ["does_not_exist.md"])
        assert len(saved) == 0

    def test_event_published_for_each_artifact(self, tmp_path):
        (tmp_path / "EVT.md").write_text("Event test", encoding="utf-8")
        mock_bus = MagicMock()
        svc, _ = self._make_service(tmp_path, event_bus=mock_bus)
        svc.save_artifacts("TASK-004", ["EVT.md"])
        mock_bus.publish.assert_called_once()
        event_name, payload = mock_bus.publish.call_args[0]
        assert event_name == "artifact_created"
        assert payload["task_id"] == "TASK-004"
        assert payload["name"] == "EVT.md"

    def test_artifact_upsert_on_duplicate(self, tmp_path):
        (tmp_path / "DUP.md").write_text("Original", encoding="utf-8")
        svc, db = self._make_service(tmp_path)
        svc.save_artifacts("TASK-005", ["DUP.md"])
        (tmp_path / "DUP.md").write_text("Updated", encoding="utf-8")
        svc.save_artifacts("TASK-005", ["DUP.md"])
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT content FROM task_artifacts WHERE task_id='TASK-005' AND name='DUP.md'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Updated"

    def test_type_detection(self, tmp_path):
        from control.artifact_service import ArtifactService
        svc = ArtifactService.__new__(ArtifactService)
        assert svc._determine_type("report.md") == "markdown"
        assert svc._determine_type("data.json") == "json"
        assert svc._determine_type("config.yaml") == "yaml"
        assert svc._determine_type("log.log") == "log"
        assert svc._determine_type("unknown.xyz") == "text"

    def test_summary_truncated_to_300_chars(self, tmp_path):
        long_content = "x" * 1000
        (tmp_path / "LONG.md").write_text(long_content, encoding="utf-8")
        svc, _ = self._make_service(tmp_path)
        saved = svc.save_artifacts("TASK-006", ["LONG.md"])
        assert saved[0]["summary"].endswith("...")
        assert len(saved[0]["summary"]) <= 305

    def test_worker_artifact_error_is_non_fatal(self, tmp_path):
        from control.artifact_service import ArtifactService
        from control.event_bus import DatabasePollingEventBus
        db = _make_sqlite(str(tmp_path))
        broken = MagicMock()
        broken.read_file.side_effect = Exception("Disk failure")
        broken.get_size.side_effect = Exception("Disk failure")
        svc = ArtifactService(
            storage_provider=broken,
            event_bus=DatabasePollingEventBus(db_path=db),
            db_path=db
        )
        saved = svc.save_artifacts("TASK-FAIL", ["broken.md"])
        assert saved == []


# ===========================================================================
# EventBus Tests
# ===========================================================================

class TestDatabasePollingEventBus:
    def test_subscribe_and_local_publish(self):
        from control.event_bus import DatabasePollingEventBus
        bus = DatabasePollingEventBus()
        received = []
        bus.subscribe("artifact_created", lambda p: received.append(p))
        bus.publish("artifact_created", {"task_id": "T1", "name": "x.md"})
        assert len(received) == 1
        assert received[0]["task_id"] == "T1"

    def test_subscribe_does_not_fire_for_different_event(self):
        from control.event_bus import DatabasePollingEventBus
        bus = DatabasePollingEventBus()
        received = []
        bus.subscribe("artifact_created", lambda p: received.append(p))
        bus.publish("other_event", {"data": "foo"})
        assert len(received) == 0

    def test_poll_sqlite_pending(self, tmp_path):
        from control.event_bus import DatabasePollingEventBus
        db = _make_sqlite(str(tmp_path))
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_artifacts (task_id, name, path, type, size, content, indexing_status) "
                "VALUES ('TASK-P1', 'poll.md', 'poll.md', 'markdown', 5, 'hello', 'PENDING')"
            )
            conn.commit()
        bus = DatabasePollingEventBus(db_path=db)
        received = []
        bus.subscribe("artifact_created", lambda p: received.append(p))
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            bus.poll()
        assert any(r["task_id"] == "TASK-P1" for r in received)

    def test_poll_skips_indexed_artifacts(self, tmp_path):
        from control.event_bus import DatabasePollingEventBus
        db = _make_sqlite(str(tmp_path))
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_artifacts (task_id, name, path, type, size, content, indexing_status) "
                "VALUES ('TASK-P2', 'indexed.md', 'indexed.md', 'markdown', 3, 'done', 'INDEXED')"
            )
            conn.commit()
        bus = DatabasePollingEventBus(db_path=db)
        received = []
        bus.subscribe("artifact_created", lambda p: received.append(p))
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            bus.poll()
        assert not any(r["name"] == "indexed.md" for r in received)

    def test_multiple_subscribers(self):
        from control.event_bus import DatabasePollingEventBus
        bus = DatabasePollingEventBus()
        r1, r2 = [], []
        bus.subscribe("artifact_created", lambda p: r1.append(p))
        bus.subscribe("artifact_created", lambda p: r2.append(p))
        bus.publish("artifact_created", {"name": "multi.md"})
        assert len(r1) == 1
        assert len(r2) == 1


# ===========================================================================
# KnowledgeIndexer Tests
# ===========================================================================

class TestKnowledgeIndexer:
    def _make_indexer(self, tmp_path):
        from control.knowledge_indexer import KnowledgeIndexer
        db = _make_sqlite(str(tmp_path))
        return KnowledgeIndexer(db_path=db), db

    def test_chunk_text_basic(self, tmp_path):
        idx, _ = self._make_indexer(tmp_path)
        text = "a" * 1000
        chunks = idx._chunk_text(text, chunk_size=400, overlap=50)
        assert len(chunks) > 1
        assert all(len(c) <= 400 for c in chunks)

    def test_chunk_text_empty(self, tmp_path):
        idx, _ = self._make_indexer(tmp_path)
        assert idx._chunk_text("") == []

    def test_chunk_text_short(self, tmp_path):
        idx, _ = self._make_indexer(tmp_path)
        chunks = idx._chunk_text("short text")
        assert len(chunks) == 1
        assert chunks[0] == "short text"

    def test_claim_artifact_lease_sqlite(self, tmp_path):
        idx, db = self._make_indexer(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_artifacts (task_id, name, path, type, size, content, indexing_status) "
                "VALUES ('TASK-L1', 'lease.md', 'lease.md', 'markdown', 4, 'data', 'PENDING')"
            )
            conn.commit()
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            claimed = idx.claim_artifact_lease("TASK-L1", "lease.md", "worker-001")
        assert claimed is True

    def test_claim_artifact_lease_already_indexing(self, tmp_path):
        idx, db = self._make_indexer(tmp_path)
        future_lease = "2099-12-31T23:59:59+00:00"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_artifacts "
                "(task_id, name, path, type, size, content, indexing_status, lease_expiration, indexed_by) "
                "VALUES ('TASK-L2', 'active.md', 'active.md', 'markdown', 4, 'x', 'INDEXING', ?, 'other-worker')",
                (future_lease,)
            )
            conn.commit()
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            claimed = idx.claim_artifact_lease("TASK-L2", "active.md", "worker-002")
        assert claimed is False

    def test_index_artifact_without_embedding(self, tmp_path):
        idx, db = self._make_indexer(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_artifacts (task_id, name, path, type, size, content, indexing_status) "
                "VALUES ('TASK-IDX', 'tst.md', 'tst.md', 'markdown', 11, 'hello world', 'INDEXING')"
            )
            conn.commit()
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            result = idx.index_artifact("TASK-IDX", "tst.md", "hello world")
        assert result is True
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM task_knowledge WHERE task_id='TASK-IDX'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["chunk_text"] == "hello world"
        assert rows[0]["embedding"] == "[]"


# ===========================================================================
# ContextBuilder Tests
# ===========================================================================

class TestContextBuilder:
    def test_build_context_with_local_data(self, tmp_path):
        from control.context_builder import ContextBuilder
        db = _make_sqlite(str(tmp_path))
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_artifacts (task_id, name, path, type, size, summary, content, indexing_status) "
                "VALUES ('TASK-CTX', 'ctx.md', 'ctx.md', 'markdown', 10, 'Summary text', 'Full content here', 'INDEXED')"
            )
            conn.execute(
                "INSERT INTO task_knowledge (task_id, name, chunk_index, chunk_text, embedding, promoted_level) "
                "VALUES ('TASK-CTX', 'ctx.md', 0, 'Chunk zero content', NULL, 'TASK')"
            )
            conn.commit()
        builder = ContextBuilder(db_path=db)
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            context = builder.build_task_context("TASK-CTX")
        assert "TASK-CTX" in context
        assert "ctx.md" in context
        assert "Chunk zero content" in context

    def test_build_context_empty_task(self, tmp_path):
        from control.context_builder import ContextBuilder
        db = _make_sqlite(str(tmp_path))
        builder = ContextBuilder(db_path=db)
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            context = builder.build_task_context("NONEXISTENT-TASK")
        assert "NONEXISTENT-TASK" in context
        assert "No engineering artifacts found" in context

    def test_knowledge_chunks_sorted_by_name_and_index(self, tmp_path):
        from control.context_builder import ContextBuilder
        db = _make_sqlite(str(tmp_path))
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_knowledge (task_id, name, chunk_index, chunk_text, promoted_level) "
                "VALUES "
                "('TASK-C3', 'b.md', 1, 'B chunk 1', 'TASK'), "
                "('TASK-C3', 'a.md', 0, 'A chunk 0', 'TASK'), "
                "('TASK-C3', 'a.md', 1, 'A chunk 1', 'TASK')"
            )
            conn.commit()
        builder = ContextBuilder(db_path=db)
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            context = builder.build_task_context("TASK-C3")
        a_pos = context.find("a.md")
        b_pos = context.find("b.md")
        assert a_pos < b_pos

    def test_build_context_fallback_to_artifacts_directly(self, tmp_path):
        from control.context_builder import ContextBuilder
        db = _make_sqlite(str(tmp_path))
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO task_artifacts (task_id, name, path, type, size, summary, content, indexing_status) "
                "VALUES ('TASK-FALLBACK', 'fallback.md', 'fallback.md', 'markdown', 25, 'Summary text', 'Fallback content directly', 'PENDING')"
            )
            # DO NOT insert into task_knowledge
            conn.commit()
        builder = ContextBuilder(db_path=db)
        with patch.dict(os.environ, {"SUPABASE_ENABLED": "false"}):
            context = builder.build_task_context("TASK-FALLBACK")
        assert "TASK-FALLBACK" in context
        assert "fallback.md" in context
        assert "Fallback content directly" in context


# ===========================================================================
# Worker Refactor Validation
# ===========================================================================

class TestWorkerArtifactServiceRefactor:
    def test_old_helpers_removed_from_main(self):
        with open("main.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "def extract_task_artifacts" not in source
        assert "def generate_gemini_embedding" not in source
        assert "def chunk_text" not in source

    def test_artifact_service_used_in_main(self):
        with open("main.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "ArtifactService" in source
        assert "save_artifacts" in source

    def test_note_comment_present_in_main(self):
        with open("main.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "KnowledgeIndexerService" in source


# ===========================================================================
# KnowledgeIndexerService Structure Tests
# ===========================================================================

class TestKnowledgeIndexerService:
    def test_service_file_exists(self):
        assert os.path.exists("knowledge_indexer_service.py")

    def test_service_has_main_guard(self):
        with open("knowledge_indexer_service.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert 'if __name__ == "__main__":' in source

    def test_service_subscribes_to_artifact_created(self):
        with open("knowledge_indexer_service.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "artifact_created" in source
        assert "subscribe" in source

    def test_service_uses_independent_event_bus(self):
        with open("knowledge_indexer_service.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "DatabasePollingEventBus" in source

    def test_service_does_not_import_bridge_server(self):
        with open("knowledge_indexer_service.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "bridge_server" not in source




