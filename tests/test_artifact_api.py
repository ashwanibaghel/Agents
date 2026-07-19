import os
import unittest
import json
from fastapi.testclient import TestClient

# Mock environment variables
os.environ["BRIDGE_TOKEN"] = "test-token-12345"
os.environ["SUPABASE_URL"] = "https://mockproject.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "mock-service-key-abcdef"
os.environ["GEMINI_API_KEY"] = "mock-gemini-key"

import bridge_server
from bridge_server import app

class TestArtifactAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = "test-token-12345"
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        
        self.original_sb_get = bridge_server._sb_get
        self.original_sb_post = bridge_server._sb_post
        
        # Mocks
        self.mock_tasks = {}
        self.mock_artifacts = []
        self.mock_knowledge = []
        
        def mock_get(path):
            if "task_artifacts" in path:
                results = []
                task_id = None
                name = None
                
                if "task_id=eq." in path:
                    task_id = path.split("task_id=eq.")[1].split("&")[0].split("?")[0]
                if "name=eq." in path:
                    name = path.split("name=eq.")[1].split("&")[0].split("?")[0]
                    
                for art in self.mock_artifacts:
                    if (not task_id or art["task_id"] == task_id) and (not name or art["name"] == name):
                        results.append(art)
                return results
                
            elif "task_knowledge" in path:
                results = []
                task_id = None
                if "task_id=eq." in path:
                    task_id = path.split("task_id=eq.")[1].split("&")[0].split("?")[0]
                    
                for kn in self.mock_knowledge:
                    if not task_id or kn["task_id"] == task_id:
                        results.append(kn)
                return results
                
            elif "tasks" in path:
                if "task_id=eq." in path:
                    task_id = path.split("task_id=eq.")[1].split("&")[0].split("?")[0]
                    t = self.mock_tasks.get(task_id)
                    return [t] if t else []
                return list(self.mock_tasks.values())
            return []
            
        def mock_post(path, payload):
            return payload
            
        bridge_server._sb_get = mock_get
        bridge_server._sb_post = mock_post

    def tearDown(self):
        bridge_server._sb_get = self.original_sb_get
        bridge_server._sb_post = self.original_sb_post

    def test_get_task_artifacts_metadata(self):
        """Verify GET /tasks/{task_id}/artifacts returns metadata without content."""
        task_id = "TEST-TASK-001"
        self.mock_artifacts.append({
            "task_id": task_id,
            "name": "RECON.md",
            "path": "docs/RECON.md",
            "type": "markdown",
            "size": 120,
            "summary": "Engineering recon summary",
            "content": "Secret code content..."
        })
        
        response = self.client.get(f"/tasks/{task_id}/artifacts", headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "RECON.md")
        self.assertEqual(data[0]["path"], "docs/RECON.md")
        self.assertNotIn("content", data[0])

    def test_get_task_artifact_content(self):
        """Verify GET /tasks/{task_id}/artifacts/{name} returns file content."""
        task_id = "TEST-TASK-001"
        self.mock_artifacts.append({
            "task_id": task_id,
            "name": "RECON.md",
            "path": "docs/RECON.md",
            "type": "markdown",
            "size": 120,
            "summary": "Engineering recon summary",
            "content": "Secret code content..."
        })
        
        response = self.client.get(f"/tasks/{task_id}/artifacts/RECON.md", headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["name"], "RECON.md")
        self.assertEqual(data["content"], "Secret code content...")

    def test_knowledge_search(self):
        """Verify POST /knowledge/search returns 501 Not Implemented."""
        task_id = "TEST-TASK-001"
        payload = {"query": "database config", "task_id": task_id}
        response = self.client.post("/knowledge/search", json=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, 501)
        self.assertIn("Semantic search is currently disabled", response.json()["detail"])

    def test_boss_report_artifact_list(self):
        """Verify boss report includes artifact_list (evidence_paths)."""
        self.mock_tasks["TEST-TASK-001"] = {
            "task_id": "TEST-TASK-001",
            "project": "oi_labs",
            "objective": "Task obj",
            "status": "done",
            "evidence_paths": ["docs/RECON.md"]
        }
        response = self.client.get("/report", headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        task = data["tasks_by_status"]["done"][0]
        self.assertEqual(task["artifact_list"], ["docs/RECON.md"])
