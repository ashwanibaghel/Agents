import os
import json
import yaml
import time
import sqlite3
import datetime
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Metrics counters (Prometheus-style) ─────────────────────────────────────
_metrics_lock  = threading.Lock()
_metrics = {
    "requests_total":            0,
    "requests_errors_total":     0,
    "tasks_created_total":       0,
    "tasks_indexed_total":       0,
    "artifacts_pending_total":   0,
    "artifacts_indexed_total":   0,
    "indexer_queue_depth":       0,
    "request_duration_ms_sum":   0.0,
    "request_duration_ms_count": 0,
}
_start_time = time.time()

def _inc(key, amount=1):
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + amount

def _set(key, value):
    with _metrics_lock:
        _metrics[key] = value


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
SQLITE_DB    = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "state", "task_checkpoints.db"))

# Structured logger (non-blocking, best-effort)
try:
    import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from control.structured_logger import bridge_logger as _log
except Exception:
    class _log:  # noqa: minimal fallback
        @staticmethod
        def event(*a, **kw): pass
        @staticmethod
        def info(*a, **kw): pass
        @staticmethod
        def error(*a, **kw): pass

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

        elif path.startswith("/tasks/") and path.endswith("/context"):
            # get_task_context — returns indexed knowledge for a task
            task_id = path.split("/tasks/")[1].split("/context")[0]
            self._get_task_context(task_id)

        elif path == "/indexer/queue":
            # get_indexer_queue — returns pending/failed indexing queue
            self._get_indexer_queue()

        elif path == "/metrics":
            # Prometheus-compatible metrics (no auth required for scraping)
            self._send_metrics()
            return

        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        t0 = time.perf_counter()
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        _inc("requests_total")

        if not self._authenticate():
            _inc("requests_errors_total")
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
                _inc("tasks_created_total")
                dur = (time.perf_counter() - t0) * 1000
                _inc("request_duration_ms_sum", dur)
                _inc("request_duration_ms_count")
                _log.event("create_task", task_id=body.get("task_id",""),
                           duration_ms=dur, status="OK")
            except Exception as e:
                _inc("requests_errors_total")
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
        instructions = (
            "Ashwani Agent Company Bridge API.\n\n"
            "CRITICAL SYSTEM INSTRUCTIONS & INTENT ROUTING RULES:\n"
            "You are the Manager Agent for Ashwani Agent Company. You coordinate and monitor autonomous developers (Antigravity workers).\n\n"
            "PRIORITY HIERARCHY:\n"
            "1. Internal Actions (get_task_status, get_boss_report, create_task, get_task_artifacts, get_task_artifact_content, search_knowledge) MUST always take priority over Web Search.\n"
            "2. If an internal Action exists, Web Search MUST NEVER be used.\n"
            "3. Worker state, task status, receipts, reports, project context, and engineering artifacts (e.g., RECON.md, AI_MANAGER_DOCS, TECHNICAL_DISCOVERY) are strictly internal. Do not search the public internet for them.\n"
            "4. ONLY use Web Search when the user explicitly asks for public internet information.\n\n"
            "INTENT ROUTER MAPPINGS:\n"
            "- Queries about worker response, reply, task status, task progress, or whether a project finished:\n"
            "  * \"Check the worker response.\"\n"
            "  * \"Check worker reply\"\n"
            "  * \"Did DKFFJ finish?\"\n"
            "  * \"Did the worker finish?\"\n"
            "  * \"What is the status of my task?\"\n"
            "  -> Route to get_boss_report() or get_task_status(task_id). If task_id is unknown, FIRST call get_boss_report() to find it.\n\n"
            "- Queries about reports, receipts, output, recent discoveries, or actions taken:\n"
            "  * \"Show me the report.\"\n"
            "  * \"Show report\"\n"
            "  * \"latest worker output\"\n"
            "  * \"What did the worker do?\"\n"
            "  * \"what happened?\"\n"
            "  * \"Latest OI Lens discovery.\"\n"
            "  -> Route to get_boss_report() to get the status summary of all recent tasks.\n\n"
            "- Queries about report contents, worker findings, project structure, or specific artifacts (e.g., RECON.md, AI_MANAGER_DOCS, TECHNICAL_DISCOVERY):\n"
            "  * \"What did the worker discover?\"\n"
            "  * \"Summarize RECON.md\"\n"
            "  * \"Explain discovery\"\n"
            "  * \"What architecture did the worker find?\"\n"
            "  * \"Show me the documentation\"\n"
            "  -> FIRST call get_task_status(task_id) or get_boss_report() to find the task_id.\n"
            "  -> THEN call get_task_artifacts(task_id) to inspect the list of available artifacts.\n"
            "  -> THEN call get_task_artifact_content(task_id, name) to read the full content of the target file.\n\n"
            "- Queries seeking semantic answers from task files or project knowledge (e.g., \"Where is auth implemented?\", \"What DB does OI Lens use?\"):\n"
            "  -> Call search_knowledge(query, task_id, project_id) to perform semantic search, then summarize based on returned chunks."
        )
        spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "Ashwani Agent Company Manager Bridge",
                "version": "1.0.0",
                "description": instructions
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
                            "required": True,
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
                                "required": True,
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
                },
                "/tasks/{task_id}/artifacts": {
                    "get": {
                        "summary": "Retrieve all artifacts (metadata only) associated with a task",
                        "operationId": "get_task_artifacts",
                        "parameters": [
                            {
                                "name": "task_id",
                                "in": "path",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                }
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Artifact list retrieved successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "type": "object"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/tasks/{task_id}/artifacts/{artifact_name}": {
                    "get": {
                        "summary": "Retrieve the complete text/content of a specific task artifact",
                        "operationId": "get_task_artifact_content",
                        "parameters": [
                            {
                                "name": "task_id",
                                "in": "path",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                }
                            },
                            {
                                "name": "artifact_name",
                                "in": "path",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                }
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Artifact content retrieved successfully",
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
                "/knowledge/search": {
                    "post": {
                        "summary": "Perform semantic vector/similarity search across all task knowledge chunks",
                        "operationId": "search_knowledge",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["query"],
                                        "properties": {
                                            "query": {
                                                "type": "string"
                                            },
                                            "task_id": {
                                                "type": "string"
                                            },
                                            "project_id": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "Search completed successfully",
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
                "/tasks/{task_id}/context": {
                    "get": {
                        "summary": "Build a full GPT-ready engineering context prompt for a task",
                        "operationId": "get_task_context",
                        "description": "Aggregates task metadata, artifact summaries, and indexed knowledge chunks into a rich context block. Call this when summarizing what a worker discovered or building a system prompt for the Manager.",
                        "parameters": [
                            {
                                "name": "task_id",
                                "in": "path",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                }
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Context built successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "task_id": {"type": "string"},
                                                "context": {"type": "string"}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/indexer/queue": {
                    "get": {
                        "summary": "Return all artifacts in a non-INDEXED state",
                        "operationId": "get_indexer_queue",
                        "description": "Returns artifacts that are PENDING, FAILED, REINDEX_REQUIRED, or INDEXING. Use to monitor the async KnowledgeIndexer pipeline status.",
                        "responses": {
                            "200": {
                                "description": "Indexer queue returned successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "total_pending": {"type": "integer"},
                                                "items": {"type": "array", "items": {"type": "object"}}
                                            }
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

    def _get_task_context(self, task_id: str):
        """Build and return compiled knowledge context for a task (SQLite + Supabase)."""
        try:
            import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from control.context_builder import ContextBuilder
            builder = ContextBuilder()
            context = builder.build_task_context(task_id)
            _log.event("get_task_context", task_id=task_id, status="OK")
            self._send_json(200, {"task_id": task_id, "context": context})
        except Exception as e:
            _log.error(f"context_builder error: {e}", task_id=task_id)
            self._send_json(500, {"error": str(e)})

    def _get_indexer_queue(self):
        """Return current indexer queue depth (PENDING + FAILED artifacts)."""
        result = {"pending": [], "failed": [], "indexed_last_hour": 0}
        try:
            if os.path.exists(SQLITE_DB):
                with sqlite3.connect(SQLITE_DB, timeout=5) as conn:
                    conn.row_factory = sqlite3.Row
                    pending = conn.execute(
                        "SELECT task_id, name, retry_count FROM task_artifacts WHERE indexing_status='PENDING'"
                    ).fetchall()
                    failed = conn.execute(
                        "SELECT task_id, name, retry_count, indexing_error FROM task_artifacts WHERE indexing_status='FAILED'"
                    ).fetchall()
                    one_hour_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
                    recent = conn.execute(
                        "SELECT COUNT(*) FROM task_artifacts WHERE indexing_status='INDEXED' AND indexed_at > ?",
                        (one_hour_ago,)
                    ).fetchone()[0]
                    result["pending"] = [dict(r) for r in pending]
                    result["failed"]  = [dict(r) for r in failed]
                    result["indexed_last_hour"] = recent
            _set("indexer_queue_depth", len(result["pending"]) + len(result["failed"]))
            _log.event("get_indexer_queue", status="OK",
                       queue_depth=len(result["pending"]) + len(result["failed"]))
            self._send_json(200, result)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _send_metrics(self):
        """Emit Prometheus-compatible text metrics."""
        uptime = time.time() - _start_time
        avg_dur = (
            _metrics["request_duration_ms_sum"] / _metrics["request_duration_ms_count"]
            if _metrics["request_duration_ms_count"] > 0 else 0.0
        )

        # Refresh queue depth from DB
        try:
            if os.path.exists(SQLITE_DB):
                with sqlite3.connect(SQLITE_DB, timeout=5) as conn:
                    pending = conn.execute("SELECT COUNT(*) FROM task_artifacts WHERE indexing_status='PENDING'").fetchone()[0]
                    failed  = conn.execute("SELECT COUNT(*) FROM task_artifacts WHERE indexing_status='FAILED'").fetchone()[0]
                    indexed = conn.execute("SELECT COUNT(*) FROM task_artifacts WHERE indexing_status='INDEXED'").fetchone()[0]
                    chunks  = conn.execute("SELECT COUNT(*) FROM task_knowledge").fetchone()[0]
                    _set("artifacts_pending_total", pending)
                    _set("artifacts_indexed_total", indexed)
                    _set("indexer_queue_depth",     pending + failed)
        except Exception:
            pending = failed = indexed = chunks = 0

        lines = [
            "# HELP bridge_uptime_seconds Seconds since bridge server start",
            "# TYPE bridge_uptime_seconds gauge",
            f"bridge_uptime_seconds {uptime:.1f}",
            "",
            "# HELP bridge_requests_total Total HTTP requests handled",
            "# TYPE bridge_requests_total counter",
            f"bridge_requests_total {_metrics['requests_total']}",
            "",
            "# HELP bridge_requests_errors_total Total HTTP error responses",
            "# TYPE bridge_requests_errors_total counter",
            f"bridge_requests_errors_total {_metrics['requests_errors_total']}",
            "",
            "# HELP bridge_tasks_created_total Tasks created via API",
            "# TYPE bridge_tasks_created_total counter",
            f"bridge_tasks_created_total {_metrics['tasks_created_total']}",
            "",
            "# HELP bridge_request_duration_ms_avg Average request duration in ms",
            "# TYPE bridge_request_duration_ms_avg gauge",
            f"bridge_request_duration_ms_avg {avg_dur:.3f}",
            "",
            "# HELP artifacts_pending Artifacts waiting for indexing",
            "# TYPE artifacts_pending gauge",
            f"artifacts_pending {pending}",
            "",
            "# HELP artifacts_failed Artifacts in FAILED state",
            "# TYPE artifacts_failed gauge",
            f"artifacts_failed {failed}",
            "",
            "# HELP artifacts_indexed Total artifacts successfully indexed",
            "# TYPE artifacts_indexed gauge",
            f"artifacts_indexed {indexed}",
            "",
            "# HELP knowledge_chunks_total Total knowledge chunks stored",
            "# TYPE knowledge_chunks_total gauge",
            f"knowledge_chunks_total {chunks}",
            "",
            "# HELP indexer_queue_depth Current indexer queue depth (pending+failed)",
            "# TYPE indexer_queue_depth gauge",
            f"indexer_queue_depth {_metrics['indexer_queue_depth']}",
        ]

        body = "\n".join(lines) + "\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


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
