import unittest
from fastapi.testclient import TestClient
from bridge_server import app

class TestOpenAPIInstructions(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_openapi_schema_contains_manager_instructions(self):
        """Verify that the OpenAPI JSON schema includes the critical manager instructions and intent router mappings."""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        
        # Verify info.description exists
        self.assertIn("info", schema)
        self.assertIn("description", schema["info"])
        description = schema["info"]["description"]
        
        # Verify key system instruction keywords
        self.assertIn("CRITICAL SYSTEM INSTRUCTIONS & INTENT ROUTING RULES", description)
        self.assertIn("PRIORITY HIERARCHY", description)
        self.assertIn("INTENT ROUTER MAPPINGS", description)
        
        # Verify all verification prompts are present in the instructions
        required_prompts = [
            "Check the worker response.",
            "Did DKFFJ finish?",
            "Show me the report.",
            "What did the worker do?",
            "Latest OI Lens discovery."
        ]
        for prompt in required_prompts:
            self.assertIn(prompt, description)

        # Verify internal actions mapping keywords
        self.assertIn("get_boss_report()", description)
        self.assertIn("get_task_status(task_id)", description)
        self.assertIn("create_task", description)
        self.assertIn("Web Search MUST NEVER be used", description)
        self.assertIn("priority over Web Search", description)

if __name__ == "__main__":
    unittest.main()
