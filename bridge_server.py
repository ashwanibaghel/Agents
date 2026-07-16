"""
Bridge Server — ChatGPT Manager ↔ Supabase Task Backend
Exposes three tools for the ChatGPT Manager:
  POST /tasks/create       → create_task
  GET  /tasks/{task_id}    → get_task_status
  GET  /report             → get_boss_report

Security:
  All endpoints require Bearer token matching BRIDGE_TOKEN env var.
  Supabase service_role key is NEVER exposed to ChatGPT.
"""

import os
import uuid
import datetime
import json

import yaml
import requests
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
from dotenv import load_dotenv
from fastapi.openapi.utils import get_openapi

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")  # service_role key — server-side only

if not BRIDGE_TOKEN:
    raise RuntimeError("BRIDGE_TOKEN env var is required")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY env vars are required")

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# ── Auth ──────────────────────────────────────────────────────────────────────

bearer_scheme = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if credentials.credentials != BRIDGE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return credentials

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Ashwani Agent Company — Manager Bridge",
    description="ChatGPT Manager tool endpoints backed by Supabase.",
    version="1.0.0",
    servers=[
        {
            "url": "https://agents-x52u.onrender.com",
            "description": "Production"
        }
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Request / Response models ──────────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    project: str = Field(..., description="Project ID e.g. oi_labs, dkffj")
    task_type: str = Field(..., description="Task type e.g. audit, feature, fix")
    objective: str = Field(..., description="Clear one-sentence objective")
    context: Optional[str] = Field("", description="Background context")
    acceptance_criteria: Optional[List[str]] = Field(default_factory=list)
    constraints: Optional[List[str]] = Field(default_factory=list)
    validation_commands: Optional[List[str]] = Field(default_factory=list)
    autonomy_level: Optional[int] = Field(2, ge=1, le=5)

class TaskStatusResponse(BaseModel):
    task_id: str
    project: str
    status: str
    objective: str
    worker_id: Optional[str]
    claimed_at: Optional[str]
    updated_at: Optional[str]
    summary: Optional[str]
    error_message: Optional[str]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _sb_get(path: str) -> dict | list:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    r = requests.get(url, headers=SB_HEADERS, timeout=10)
    if r.status_code not in [200, 206]:
        raise HTTPException(status_code=502, detail=f"Supabase error: {r.text}")
    return r.json()

def _sb_post(path: str, payload: dict) -> dict:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    r = requests.post(url, headers=SB_HEADERS, json=payload, timeout=10)
    if r.status_code not in [200, 201]:
        raise HTTPException(status_code=502, detail=f"Supabase error: {r.text}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else data

# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/tasks/create", dependencies=[Depends(verify_token)])
def create_task(req: CreateTaskRequest):
    """
    Create a new task and insert it into Supabase inbox.
    Returns the generated task_id so ChatGPT Manager can track it.
    """
    # Generate a deterministic prefix from project + short uuid
    prefix = req.project.upper().replace("_", "-")[:8]
    short_id = str(uuid.uuid4())[:8].upper()
    task_id = f"{prefix}-{short_id}"

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    row = {
        "task_id": task_id,
        "project": req.project,
        "task_type": req.task_type,
        "objective": req.objective,
        "context": req.context or "",
        "acceptance_criteria": req.acceptance_criteria or [],
        "constraints": req.constraints or [],
        "validation_commands": req.validation_commands or [],
        "autonomy_level": req.autonomy_level,
        "status": "inbox",
        "created_at": now,
        "updated_at": now,
    }

    result = _sb_post("tasks", row)

    return {
        "success": True,
        "task_id": task_id,
        "status": "inbox",
        "message": f"Task {task_id} created and queued for {req.project}.",
    }


@app.get("/tasks/{task_id}", dependencies=[Depends(verify_token)])
def get_task_status(task_id: str):
    """
    Retrieve current status of a specific task by task_id.
    """
    # Validate task_id — no path traversal
    if not task_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid task_id format")

    rows = _sb_get(f"tasks?task_id=eq.{task_id}")
    if not rows:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    row = rows[0]
    return TaskStatusResponse(
        task_id=row["task_id"],
        project=row["project"],
        status=row["status"],
        objective=row["objective"],
        worker_id=row.get("worker_id"),
        claimed_at=row.get("claimed_at"),
        updated_at=row.get("updated_at"),
        summary=row.get("summary"),
        error_message=row.get("error_message"),
    )


@app.get("/report", dependencies=[Depends(verify_token)])
def get_boss_report():
    """
    Boss summary report: all tasks grouped by status.
    ChatGPT Manager calls this to get a complete company snapshot.
    """
    rows = _sb_get("tasks?order=updated_at.desc&limit=100")

    by_status: dict[str, list] = {}
    for row in rows:
        s = row.get("status", "unknown")
        by_status.setdefault(s, []).append({
            "task_id": row["task_id"],
            "project": row["project"],
            "objective": row["objective"],
            "worker_id": row.get("worker_id"),
            "updated_at": row.get("updated_at"),
            "summary": row.get("summary"),
            "error_message": row.get("error_message"),
        })

    total = len(rows)
    done = len(by_status.get("done", []))
    working = len(by_status.get("delegated", [])) + len(by_status.get("claimed", []))
    blocked = len(by_status.get("blocked", []))
    failed = len(by_status.get("failed", []))
    inbox = len(by_status.get("inbox", []))

    return {
        "report_generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "totals": {
            "total": total,
            "inbox": inbox,
            "working": working,
            "done": done,
            "blocked": blocked,
            "failed": failed,
        },
        "tasks_by_status": by_status,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "ashwani-agent-company-bridge"}


@app.get("/openapi.json", include_in_schema=False)
def get_openapi_endpoint():
    return app.openapi()


# ── Custom OpenAPI operationIds ───────────────────────────────────────────────

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
        
    openapi_schema = get_openapi(
        title="Ashwani Agent Company Bridge API",
        version="1.0.0",
        description="ChatGPT Action Connector bridge for managing agent tasks.",
        routes=app.routes,
        servers=[
            {
                "url": "https://agents-x52u.onrender.com",
                "description": "Production"
            }
        ]
    )
    
    # Ensure operationIds are stable and set exactly as required
    for route in app.routes:
        if hasattr(route, "endpoint") and hasattr(route, "path"):
            if route.path == "/tasks/create":
                route.operation_id = "create_task"
            elif route.path == "/tasks/{task_id}":
                route.operation_id = "get_task_status"
            elif route.path == "/report":
                route.operation_id = "get_boss_report"
                
    # Regenerate schema with correct operationIds
    openapi_schema = get_openapi(
        title="Ashwani Agent Company Bridge API",
        version="1.0.0",
        description="ChatGPT Action Connector bridge for managing agent tasks.",
        routes=app.routes,
        servers=[
            {
                "url": "https://agents-x52u.onrender.com",
                "description": "Production"
            }
        ]
    )
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bridge_server:app", host="0.0.0.0", port=8080, reload=False)
