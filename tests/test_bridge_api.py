import os
import unittest
from fastapi.testclient import TestClient
from dotenv import load_dotenv

# Ensure env vars are mocked/loaded before importing bridge_server
os.environ["BRIDGE_TOKEN"] = "test-token-12345"
os.environ["SUPABASE_URL"] = "https://mockproject.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "mock-service-key-abcdef"

import bridge_server
from bridge_server import app

class TestBridgeAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = "test-token-12345"
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        
        # Mock external Supabase requests within bridge_server
        self.original_sb_post = bridge_server._sb_post
        self.original_sb_get = bridge_server._sb_get
        
        # Simple mock DB storage
        self.mock_db = {}
        
        def mock_post(path, payload):
            task_id = payload.get("task_id")
            self.mock_db[task_id] = payload
            return payload
            
        def mock_get(path):
            # Parse query params out (e.g. tasks?task_id=eq.ID)
            if "task_id=eq." in path:
                task_id = path.split("task_id=eq.")[1].split("&")[0]
                task = self.mock_db.get(task_id)
                return [task] if task else []
            # Return all tasks sorted by updated_at desc for report
            return list(self.mock_db.values())
            
        bridge_server._sb_post = mock_post
        bridge_server._sb_get = mock_get

    def tearDown(self):
        bridge_server._sb_post = self.original_sb_post
        bridge_server._sb_get = self.original_sb_get

    def test_health_endpoint(self):
        """Verify the health check endpoint returns 200 OK without authentication."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "ashwani-agent-company-bridge"})

    def test_unauthorized_request_rejection(self):
        """Verify endpoints reject requests with missing or invalid tokens."""
        # 1. Missing auth header
        r1 = self.client.get("/report")
        self.assertEqual(r1.status_code, 401)  # HTTPBearer returns 401 for missing auth
        
        # 2. Invalid token
        r2 = self.client.get("/report", headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(r2.status_code, 401)
        self.assertEqual(r2.json()["detail"], "Unauthorized")

    def test_authorized_create_task(self):
        """Verify task creation endpoint queues task correctly and returns task_id."""
        payload = {
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Harmless audit test",
            "autonomy_level": 2
        }
        
        response = self.client.post("/tasks/create", json=payload, headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertTrue(data["success"])
        self.assertTrue(data["task_id"].startswith("OI-LABS-"))
        self.assertEqual(data["status"], "inbox")
        
        # Check mock database storage
        task_id = data["task_id"]
        self.assertIn(task_id, self.mock_db)
        self.assertEqual(self.mock_db[task_id]["project"], "oi_labs")
        self.assertEqual(self.mock_db[task_id]["objective"], "Harmless audit test")

    def test_get_task_status(self):
        """Verify get task status returns correct structure from DB."""
        # Setup pre-existing task in mock DB
        task_id = "OI-LABS-1234"
        self.mock_db[task_id] = {
            "task_id": task_id,
            "project": "oi_labs",
            "task_type": "audit",
            "objective": "Audit task test",
            "status": "claimed",
            "worker_id": "worker-main",
            "claimed_at": "2026-07-15T12:00:00Z",
            "updated_at": "2026-07-15T12:00:00Z",
            "summary": "Working",
            "error_message": None
        }
        
        response = self.client.get(f"/tasks/{task_id}", headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["task_id"], task_id)
        self.assertEqual(data["status"], "claimed")
        self.assertEqual(data["worker_id"], "worker-main")

    def test_get_boss_report(self):
        """Verify get boss report aggregates totals and tasks by status correctly."""
        # Setup pre-existing tasks
        self.mock_db["T1"] = {"task_id": "T1", "project": "oi_labs", "status": "done", "objective": "Obj 1"}
        self.mock_db["T2"] = {"task_id": "T2", "project": "dkffj", "status": "inbox", "objective": "Obj 2"}
        self.mock_db["T3"] = {"task_id": "T3", "project": "oi_labs", "status": "claimed", "objective": "Obj 3"}
        
        response = self.client.get("/report", headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertIn("report_generated_at", data)
        self.assertEqual(data["totals"]["total"], 3)
        self.assertEqual(data["totals"]["inbox"], 1)
        self.assertEqual(data["totals"]["done"], 1)
        self.assertEqual(data["totals"]["working"], 1)

    def test_openapi_json_operation_ids(self):
        """Verify OpenAPI schema endpoints exist, contain stable operationIds, and servers configuration."""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        
        # Verify servers block exists and contains the correct URL
        servers = schema.get("servers", [])
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["url"], "https://agents-x52u.onrender.com")
        
        paths = schema.get("paths", {})
        
        # Verify exact operationIds
        self.assertEqual(paths["/tasks/create"]["post"]["operationId"], "create_task")
        self.assertEqual(paths["/tasks/{task_id}"]["get"]["operationId"], "get_task_status")
        self.assertEqual(paths["/report"]["get"]["operationId"], "get_boss_report")

    def test_no_supabase_key_leakage_in_openapi_json(self):
        """Verify that the sensitive SUPABASE_SERVICE_KEY string is never leaked in OpenAPI output."""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        raw_schema_str = response.text
        
        sensitive_key = os.environ["SUPABASE_SERVICE_KEY"]
        self.assertNotIn(sensitive_key, raw_schema_str)

if __name__ == "__main__":
    unittest.main()
