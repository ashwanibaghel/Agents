import os
import json
import yaml
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Load Configuration
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "supabase.yaml"))

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found at: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

config = load_config()
SUPABASE_URL = config.get("supabase_url", "").rstrip("/")
SUPABASE_KEY = config.get("supabase_key", "")
BRIDGE_TOKEN = config.get("bridge_token", "")

class ManagerBridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        # CORS headers for API access
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._send_json(200, {})

    def _authenticate(self) -> bool:
        """Authenticate request using Bearer Token or custom header."""
        auth_header = self.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split("Bearer ")[1].strip()
        else:
            token = self.headers.get("X-Bridge-Token", "").strip()
            
        if not token or token != BRIDGE_TOKEN:
            self._send_json(401, {"error": "Unauthorized. Invalid or missing bridge token."})
            return False
        return True

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        # OpenAPI specification access (public/unauthenticated or authenticated depending on setup)
        # We allow public read for schema to make ChatGPT Action registration easy
        if path == "/openapi.json":
            self._send_openapi_spec()
            return

        if not self._authenticate():
            return

        if path == "/tasks":
            # get_task_status
            task_id = query.get("task_id", [None])[0]
            if not task_id:
                self._send_json(400, {"error": "Missing query parameter 'task_id'."})
                return
            self._get_task_status(task_id)
            
        elif path == "/boss_report":
            # get_boss_report
            self._get_boss_report()
            
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if not self._authenticate():
            return

        if path == "/tasks":
            # create_task
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_json(400, {"error": "Empty request body."})
                return
                
            try:
                body = json.loads(self.rfile.read(content_length).decode("utf-8"))
                self._create_task(body)
            except Exception as e:
                self._send_json(400, {"error": f"Invalid JSON payload: {str(e)}"})
        else:
            self._send_json(404, {"error": "Not Found"})

    def _get_task_status(self, task_id: str):
        """Fetch task details from Supabase database."""
        url = f"{SUPABASE_URL}/rest/v1/tasks?task_id=eq.{task_id}"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }
        try:
            res = requests.get(url, headers=headers, timeout=10.0)
            if res.status_code == 200:
                rows = res.json()
                if rows:
                    self._send_json(200, rows[0])
                else:
                    self._send_json(404, {"error": f"Task '{task_id}' not found."})
            else:
                self._send_json(res.status_code, {"error": f"Database fetch error: {res.text}"})
        except Exception as e:
            self._send_json(500, {"error": f"Bridge Server error: {str(e)}"})

    def _get_boss_report(self):
        """Fetch summary of recent tasks from Supabase database."""
        url = f"{SUPABASE_URL}/rest/v1/tasks?select=task_id,project,status,summary,updated_at&order=updated_at.desc&limit=20"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }
        try:
            res = requests.get(url, headers=headers, timeout=10.0)
            if res.status_code == 200:
                self._send_json(200, {"tasks": res.json()})
            else:
                self._send_json(res.status_code, {"error": f"Database fetch error: {res.text}"})
        except Exception as e:
            self._send_json(500, {"error": f"Bridge Server error: {str(e)}"})

    def _create_task(self, body: dict):
        """Insert a new task into the Supabase tasks table."""
        url = f"{SUPABASE_URL}/rest/v1/tasks"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        
        # Enforce status is inbox on creation
        body["status"] = "inbox"
        
        try:
            res = requests.post(url, headers=headers, json=body, timeout=10.0)
            if res.status_code in [200, 201]:
                self._send_json(201, {"success": True, "task": res.json()[0]})
            else:
                self._send_json(res.status_code, {"error": f"Database insert failed: {res.text}"})
        except Exception as e:
            self._send_json(500, {"error": f"Bridge Server error: {str(e)}"})

    def _send_openapi_spec(self):
        """Generate and send OpenAPI specification for Custom GPT integration."""
        spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "Ashwani Agent Company Manager Bridge",
                "version": "1.0.0",
                "description": "API endpoints exposing create_task, get_task_status, and get_boss_report tools for Custom GPT."
            },
            "servers": [
                {
                    "url": f"https://{self.headers.get('Host', 'localhost:8000')}"
                }
            ],
            "paths": {
                "/tasks": {
                    "post": {
                        "summary": "Create a new coding task",
                        "operationId": "create_task",
                        "requestBody": {
                            "required": true,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/TaskInput"
                                    }
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "Task created successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "get": {
                        "summary": "Get task status by task ID",
                        "operationId": "get_task_status",
                        "parameters": [
                            {
                                "name": "task_id",
                                "in": "query",
                                "required": true,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "The exact ID of the task to query"
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Task status retrieved successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/boss_report": {
                    "get": {
                        "summary": "Get status summary report of all recent tasks",
                        "operationId": "get_boss_report",
                        "responses": {
                            "200": {
                                "description": "Report retrieved successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "TaskInput": {
                        "type": "object",
                        "required": ["task_id", "project", "task_type", "objective"],
                        "properties": {
                            "task_id": {
                                "type": "string",
                                "description": "Unique identifier for the task, e.g. OI-BRIDGE-001"
                            },
                            "project": {
                                "type": "string",
                                "description": "The project ID, e.g. oi_labs or dkffj"
                            },
                            "task_type": {
                                "type": "string",
                                "enum": ["audit", "code"],
                                "description": "Type of coding task"
                            },
                            "objective": {
                                "type": "string",
                                "description": "Goal details of what needs to be achieved"
                            },
                            "context": {
                                "type": "string",
                                "description": "Optional background context details"
                            },
                            "acceptance_criteria": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Checklist items to verify task completion"
                            },
                            "constraints": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Operational constraints"
                            },
                            "validation_commands": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Shell commands to run during verification"
                            },
                            "autonomy_level": {
                                "type": "integer",
                                "default": 1,
                                "description": "Autonomy configuration level (1-3)"
                            }
                        }
                    }
                }
            }
        }
        self._send_json(200, spec)

def run_server(port=8000):
    server = HTTPServer(("0.0.0.0", port), ManagerBridgeHandler)
    print(f"🚀 Manager Bridge Server is running on port {port}...")
    server.serve_forever()

if __name__ == "__main__":
    import sys
    port = 8000
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    run_server(port)
