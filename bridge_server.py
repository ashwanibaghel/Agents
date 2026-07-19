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
import socket

# Programmatically resolve unresponsive local DNS for Supabase host (V3.2.1 DNS patch)
_original_getaddrinfo = socket.getaddrinfo
def _custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == "xrimbjoxmwqxryvxdojz.supabase.co":
        return _original_getaddrinfo("104.18.38.10", port, family, type, proto, flags)
    return _original_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _custom_getaddrinfo

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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    artifact_list: Optional[List[str]] = Field(default_factory=list)


# ── Artifact & Semantic Search models ──────────────────────────────────────────

class ArtifactMetadataResponse(BaseModel):
    name: str
    path: str
    type: str
    size: int
    summary: Optional[str] = None

class ArtifactContentResponse(BaseModel):
    name: str
    content: str

class KnowledgeSearchRequest(BaseModel):
    query: str
    task_id: Optional[str] = None
    project_id: Optional[str] = None

class KnowledgeSearchItem(BaseModel):
    name: str
    chunk_text: str
    similarity: float

class KnowledgeSearchResponse(BaseModel):
    matches: List[KnowledgeSearchItem]


# ── Math & Embedding Helpers ───────────────────────────────────────────────────

import math

# Cosine similarity and Gemini embedding helpers removed (vector search is disabled)

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
        artifact_list=row.get("evidence_paths") or []
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
        if row.get("project") == "system":
            continue
        s = row.get("status", "unknown")
        by_status.setdefault(s, []).append({
            "task_id": row["task_id"],
            "project": row["project"],
            "objective": row["objective"],
            "worker_id": row.get("worker_id"),
            "updated_at": row.get("updated_at"),
            "summary": row.get("summary"),
            "error_message": row.get("error_message"),
            "artifact_list": row.get("evidence_paths") or []
        })

    done = len(by_status.get("done", []))
    working = len(by_status.get("delegated", [])) + len(by_status.get("claimed", []))
    blocked = len(by_status.get("blocked", []))
    failed = len(by_status.get("failed", []))
    inbox = len(by_status.get("inbox", []))
    total = done + working + blocked + failed + inbox

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


@app.get("/tasks/{task_id}/artifacts", response_model=List[ArtifactMetadataResponse], dependencies=[Depends(verify_token)])
def get_task_artifacts(task_id: str):
    """
    Retrieve all artifacts (metadata only) associated with a task.
    """
    if not task_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid task_id format")
    rows = _sb_get(f"task_artifacts?task_id=eq.{task_id}&select=name,path,type,size,summary")
    return [
        ArtifactMetadataResponse(
            name=r["name"],
            path=r["path"],
            type=r["type"],
            size=r["size"],
            summary=r.get("summary")
        )
        for r in rows
    ]

@app.get("/tasks/{task_id}/artifacts/{artifact_name}", response_model=ArtifactContentResponse, dependencies=[Depends(verify_token)])
def get_task_artifact_content(task_id: str, artifact_name: str):
    """
    Retrieve the complete text/content of a specific task artifact.
    """
    if not task_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid task_id format")
    rows = _sb_get(f"task_artifacts?task_id=eq.{task_id}&name=eq.{artifact_name}&select=name,content")
    if not rows:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_name}' for task {task_id} not found")
    return ArtifactContentResponse(
        name=rows[0]["name"],
        content=rows[0]["content"]
    )

@app.post("/knowledge/search", response_model=KnowledgeSearchResponse, dependencies=[Depends(verify_token)])
def search_knowledge(req: KnowledgeSearchRequest):
    """
    Perform semantic vector/similarity search across all task knowledge chunks.
    """
    raise HTTPException(status_code=501, detail="Semantic search is currently disabled.")


# ── Context Builder Endpoint ───────────────────────────────────────────────────

class TaskContextResponse(BaseModel):
    task_id: str
    context: str

@app.get("/tasks/{task_id}/context", response_model=TaskContextResponse, dependencies=[Depends(verify_token)])
def get_task_context(task_id: str):
    """
    Build a full GPT-ready engineering context prompt for a task.
    Aggregates task metadata, artifact summaries, and knowledge chunks into
    a single rich context block the Manager can embed in system prompts.
    """
    if not task_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid task_id format")
    try:
        from control.context_builder import ContextBuilder
        builder = ContextBuilder()
        context_text = builder.build_task_context(task_id)
        return TaskContextResponse(task_id=task_id, context=context_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build task context: {str(e)}")


# ── Indexer Queue Endpoint ─────────────────────────────────────────────────────

class IndexerQueueItem(BaseModel):
    task_id: str
    name: str
    indexing_status: str
    retry_count: Optional[int] = 0
    indexing_error: Optional[str] = None

class IndexerQueueResponse(BaseModel):
    total_pending: int
    items: List[IndexerQueueItem]

@app.get("/indexer/queue", response_model=IndexerQueueResponse, dependencies=[Depends(verify_token)])
def get_indexer_queue():
    """
    Return all artifacts currently in a non-INDEXED state (pending, failed, retrying).
    Used for operational monitoring of the async KnowledgeIndexer pipeline.
    """
    rows = _sb_get(
        "task_artifacts?indexing_status=neq.INDEXED"
        "&select=task_id,name,indexing_status,retry_count,indexing_error"
        "&order=updated_at.desc&limit=100"
    )
    items = [
        IndexerQueueItem(
            task_id=r["task_id"],
            name=r["name"],
            indexing_status=r["indexing_status"],
            retry_count=r.get("retry_count", 0),
            indexing_error=r.get("indexing_error")
        )
        for r in rows
    ]
    return IndexerQueueResponse(total_pending=len(items), items=items)


from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def get_dashboard_ui():
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ashwani Agent Company — Boss Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.75);
            --card-border: rgba(255, 255, 255, 0.08);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent-primary: #6366f1;
            --accent-hover: #4f46e5;
            --status-online: #10b981;
            --status-offline: #ef4444;
            --status-working: #3b82f6;
            --status-blocked: #f59e0b;
            --status-inbox: #8b5cf6;
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-color);
            background-image: radial-gradient(circle at top right, rgba(99, 102, 241, 0.12), transparent 45%),
                              radial-gradient(circle at bottom left, rgba(16, 185, 129, 0.05), transparent 40%);
            background-attachment: fixed;
            color: var(--text-main);
            min-height: 100vh;
            padding: 2.5rem;
            overflow-x: hidden;
        }
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        ::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.2);
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.15);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.3);
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
        }
        .logo-container h1 {
            font-size: 1.8rem;
            font-weight: 700;
            background: linear-gradient(135deg, #a5b4fc, #818cf8, #6366f1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }
        .logo-container p {
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-top: 0.2rem;
        }
        .header-meta {
            display: flex;
            gap: 1.5rem;
            align-items: center;
        }
        .refresh-bar-container {
            width: 150px;
            height: 4px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 2px;
            overflow: hidden;
            position: relative;
        }
        .refresh-bar {
            height: 100%;
            width: 100%;
            background: var(--accent-primary);
            transition: width 0.1s linear;
        }
        .refresh-text {
            font-size: 0.8rem;
            color: var(--text-muted);
        }
        .worker-badge {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 0.6rem 1.2rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            backdrop-filter: blur(12px);
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            position: relative;
        }
        .status-dot.online {
            background-color: var(--status-online);
            box-shadow: 0 0 10px var(--status-online);
            animation: pulse-green 2s infinite;
        }
        .status-dot.offline {
            background-color: var(--status-offline);
            box-shadow: 0 0 10px var(--status-offline);
            animation: pulse-red 2s infinite;
        }
        @keyframes pulse-green {
            0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
            100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }
        @keyframes pulse-red {
            0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
            70% { box-shadow: 0 0 0 8px rgba(239, 68, 68, 0); }
            100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }
        .worker-info-text h4 {
            font-size: 0.85rem;
            font-weight: 600;
        }
        .worker-info-text p {
            font-size: 0.75rem;
            color: var(--text-muted);
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2.5rem;
        }
        .metric-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 1.25rem;
            backdrop-filter: blur(12px);
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .metric-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.15);
        }
        .metric-card h3 {
            font-size: 0.85rem;
            color: var(--text-muted);
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .metric-card .value {
            font-size: 2rem;
            font-weight: 700;
        }
        .metric-card.inbox { border-left: 3px solid var(--status-inbox); }
        .metric-card.working { border-left: 3px solid var(--status-working); }
        .metric-card.done { border-left: 3px solid var(--status-online); }
        .metric-card.blocked { border-left: 3px solid var(--status-blocked); }
        .metric-card.failed { border-left: 3px solid var(--status-offline); }
        
        .board-container {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 1.5rem;
            align-items: start;
            margin-bottom: 2.5rem;
        }
        @media (max-width: 1280px) {
            .board-container {
                grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            }
        }
        .board-column {
            background: rgba(17, 24, 39, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 16px;
            padding: 1rem;
            min-height: 500px;
            display: flex;
            flex-direction: column;
            gap: 1rem;
            backdrop-filter: blur(8px);
        }
        .column-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 0.75rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        .column-header h2 {
            font-size: 1rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .column-header .count {
            background: rgba(255, 255, 255, 0.06);
            border-radius: 999px;
            padding: 0.1rem 0.5rem;
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-muted);
        }
        .task-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            flex-grow: 1;
            overflow-y: auto;
            max-height: 600px;
            padding-right: 2px;
        }
        .task-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 1rem;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            flex-direction: column;
            gap: 0.6rem;
        }
        .task-card:hover {
            transform: translateY(-2px) scale(1.01);
            border-color: rgba(99, 102, 241, 0.4);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }
        .task-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .task-id-badge {
            font-family: monospace;
            font-size: 0.8rem;
            font-weight: 600;
            color: #818cf8;
        }
        .project-badge {
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            padding: 0.15rem 0.4rem;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: var(--text-muted);
        }
        .project-badge.oi_labs {
            color: #34d399;
            border-color: rgba(52, 211, 153, 0.2);
            background: rgba(52, 211, 153, 0.05);
        }
        .project-badge.dkffj {
            color: #fb923c;
            border-color: rgba(251, 146, 60, 0.2);
            background: rgba(251, 146, 60, 0.05);
        }
        .task-objective {
            font-size: 0.85rem;
            line-height: 1.35;
            color: var(--text-main);
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .task-card-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.7rem;
            color: var(--text-muted);
            border-top: 1px solid rgba(255, 255, 255, 0.04);
            padding-top: 0.5rem;
            margin-top: 0.2rem;
        }
        .empty-state {
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100px;
            color: var(--text-muted);
            font-size: 0.8rem;
            border: 1px dashed rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            font-style: italic;
        }
        .latest-completed-section {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            backdrop-filter: blur(12px);
        }
        .latest-completed-section h2 {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .completed-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
            text-align: left;
        }
        .completed-table th, .completed-table td {
            padding: 0.75rem 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        .completed-table th {
            color: var(--text-muted);
            font-weight: 500;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.5px;
        }
        .completed-table tbody tr:hover {
            background: rgba(255, 255, 255, 0.02);
        }
        .status-badge {
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            display: inline-block;
        }
        .status-badge.done {
            background: rgba(16, 185, 129, 0.1);
            color: var(--status-online);
            border: 1px solid rgba(16, 185, 129, 0.2);
        }
        .status-badge.blocked {
            background: rgba(245, 158, 11, 0.1);
            color: var(--status-blocked);
            border: 1px solid rgba(245, 158, 11, 0.2);
        }
        .status-badge.failed {
            background: rgba(239, 68, 68, 0.1);
            color: var(--status-offline);
            border: 1px solid rgba(239, 68, 68, 0.2);
        }
        
        /* Modal Design */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(8px);
            display: flex;
            justify-content: center;
            align-items: center;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
            z-index: 100;
        }
        .modal-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }
        .modal-card {
            background: #111827;
            border: 1px solid var(--card-border);
            border-radius: 16px;
            width: 90%;
            max-width: 600px;
            max-height: 80vh;
            overflow-y: auto;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
            transform: scale(0.95);
            transition: transform 0.3s ease;
        }
        .modal-overlay.active .modal-card {
            transform: scale(1);
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }
        .modal-close-btn {
            background: none;
            border: none;
            color: var(--text-muted);
            font-size: 1.5rem;
            cursor: pointer;
            transition: color 0.2s ease;
        }
        .modal-close-btn:hover {
            color: var(--text-main);
        }
        .modal-section {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        .modal-section h4 {
            font-size: 0.8rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 600;
        }
        .modal-section p, .modal-section ul {
            font-size: 0.9rem;
            line-height: 1.5;
        }
        .modal-section ul {
            padding-left: 1.25rem;
        }
        .modal-section pre {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.05);
            padding: 1rem;
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.8rem;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }
    </style>
</head>
<body>

    <header>
        <div class="logo-container">
            <h1>Ashwani Agent Company</h1>
            <p>V2 Operations Control Center — Boss Dashboard</p>
        </div>
        <div class="header-meta">
            <div class="refresh-text" id="refresh-text">Refreshing in 5.0s</div>
            <div class="refresh-bar-container">
                <div class="refresh-bar" id="refresh-bar"></div>
            </div>
            
            <div class="worker-badge">
                <div class="status-dot offline" id="worker-dot"></div>
                <div class="worker-info-text">
                    <h4 id="worker-status-label">Worker: OFFLINE</h4>
                    <p id="worker-heartbeat-label">Last seen: Never</p>
                </div>
            </div>
        </div>
    </header>

    <div class="metrics-grid">
        <div class="metric-card inbox">
            <h3>Inbox</h3>
            <div class="value" id="count-inbox">0</div>
        </div>
        <div class="metric-card working">
            <h3>Working</h3>
            <div class="value" id="count-working">0</div>
        </div>
        <div class="metric-card done">
            <h3>Completed</h3>
            <div class="value" id="count-done">0</div>
        </div>
        <div class="metric-card blocked">
            <h3>Blocked</h3>
            <div class="value" id="count-blocked">0</div>
        </div>
        <div class="metric-card failed">
            <h3>Failed</h3>
            <div class="value" id="count-failed">0</div>
        </div>
        <div class="metric-card">
            <h3>System Uptime</h3>
            <div class="value" id="uptime-value">0s</div>
        </div>
    </div>

    <div class="board-container">
        <!-- Inbox -->
        <div class="board-column">
            <div class="column-header">
                <h2>📥 Inbox</h2>
                <div class="count" id="badge-inbox">0</div>
            </div>
            <div class="task-list" id="list-inbox">
                <div class="empty-state">No pending tasks</div>
            </div>
        </div>
        <!-- Working -->
        <div class="board-column">
            <div class="column-header">
                <h2>⚙️ Working</h2>
                <div class="count" id="badge-working">0</div>
            </div>
            <div class="task-list" id="list-working">
                <div class="empty-state">No active agents</div>
            </div>
        </div>
        <!-- Done -->
        <div class="board-column">
            <div class="column-header">
                <h2>✅ Completed</h2>
                <div class="count" id="badge-done">0</div>
            </div>
            <div class="task-list" id="list-done">
                <div class="empty-state">No completed tasks</div>
            </div>
        </div>
        <!-- Blocked -->
        <div class="board-column">
            <div class="column-header">
                <h2>⚠️ Blocked</h2>
                <div class="count" id="badge-blocked">0</div>
            </div>
            <div class="task-list" id="list-blocked">
                <div class="empty-state">No blocked tasks</div>
            </div>
        </div>
        <!-- Failed -->
        <div class="board-column">
            <div class="column-header">
                <h2>🚨 Failed</h2>
                <div class="count" id="badge-failed">0</div>
            </div>
            <div class="task-list" id="list-failed">
                <div class="empty-state">No failed tasks</div>
            </div>
        </div>
    </div>

    <div class="latest-completed-section" style="margin-bottom: 2rem;">
        <h2>🔄 Persistent Runtime Sessions (V3.1)</h2>
        <table class="completed-table">
            <thead>
                <tr>
                    <th>Project</th>
                    <th>Conversation ID</th>
                    <th>Status</th>
                    <th>Workspace</th>
                    <th>Branch</th>
                    <th>Last Commit</th>
                    <th>Memory Size</th>
                    <th>Lock Owner</th>
                    <th>Last Updated</th>
                </tr>
            </thead>
            <tbody id="sessions-table-body">
                <tr>
                    <td colspan="9" style="text-align: center; color: var(--text-muted); font-style: italic;">No active sessions loaded</td>
                </tr>
            </tbody>
        </table>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-bottom: 2rem;">
        <!-- Health Section -->
        <div class="latest-completed-section" style="margin-bottom: 0;">
            <h2>❤️ System Component Health (V3.2)</h2>
            <table class="completed-table">
                <thead>
                    <tr>
                        <th>Component</th>
                        <th>Health State</th>
                        <th>Status</th>
                        <th>Latency</th>
                        <th>Last Check</th>
                        <th>Retries</th>
                    </tr>
                </thead>
                <tbody id="health-table-body">
                    <tr>
                        <td colspan="6" style="text-align: center; color: var(--text-muted); font-style: italic;">Loading health data...</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <!-- Metrics Section -->
        <div class="latest-completed-section" style="margin-bottom: 0;">
            <h2>📈 Runtime Observability Metrics (V3.2)</h2>
            <table class="completed-table">
                <thead>
                    <tr>
                        <th>Metric</th>
                        <th>Value</th>
                    </tr>
                </thead>
                <tbody id="metrics-table-body">
                    <tr>
                        <td colspan="2" style="text-align: center; color: var(--text-muted); font-style: italic;">Loading metrics...</td>
                    </tr>
                </tbody>
            </table>
        </div>
        </div>

        <!-- Operations Section (V3.2 Sprint 6) -->
        <div class="latest-completed-section" id="ops-section">
            <h2>⚙️ Operations Dashboard (V3.2)</h2>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1.25rem;margin-top:1rem;">

                <!-- Worker Panel -->
                <div style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:12px;padding:1.25rem;">
                    <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--accent-primary);margin-bottom:0.75rem;font-weight:600;">🤖 Worker</div>
                    <table style="width:100%;font-size:0.85rem;border-collapse:collapse;">
                        <tbody id="ops-worker-body">
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Status</td><td id="ops-w-status" style="text-align:right;font-weight:600;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Worker ID</td><td id="ops-w-id" style="text-align:right;font-family:monospace;font-size:0.78rem;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Uptime</td><td id="ops-w-uptime" style="text-align:right;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Startup Count</td><td id="ops-w-startups" style="text-align:right;">—</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Queue Panel -->
                <div style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:12px;padding:1.25rem;">
                    <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--accent-primary);margin-bottom:0.75rem;font-weight:600;">📋 Queue</div>
                    <table style="width:100%;font-size:0.85rem;border-collapse:collapse;">
                        <tbody>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Pending</td><td id="ops-q-pending" style="text-align:right;font-weight:600;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Running</td><td id="ops-q-running" style="text-align:right;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Completed</td><td id="ops-q-done" style="text-align:right;color:#10b981;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Failed</td><td id="ops-q-failed" style="text-align:right;color:#ef4444;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Blocked</td><td id="ops-q-blocked" style="text-align:right;color:#f59e0b;">—</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Sessions Panel -->
                <div style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:12px;padding:1.25rem;">
                    <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--accent-primary);margin-bottom:0.75rem;font-weight:600;">💬 Sessions</div>
                    <table style="width:100%;font-size:0.85rem;border-collapse:collapse;">
                        <tbody>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Active Sessions</td><td id="ops-s-active" style="text-align:right;font-weight:600;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Reused</td><td id="ops-s-reused" style="text-align:right;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Expiries</td><td id="ops-s-expired" style="text-align:right;color:#f59e0b;">—</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Git Panel -->
                <div style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:12px;padding:1.25rem;">
                    <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--accent-primary);margin-bottom:0.75rem;font-weight:600;">🔀 Git</div>
                    <table style="width:100%;font-size:0.85rem;border-collapse:collapse;">
                        <tbody>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Git Success Rate</td><td id="ops-git-rate" style="text-align:right;font-weight:600;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Failures</td><td id="ops-git-fail" style="text-align:right;color:#ef4444;">—</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Backup Panel -->
                <div style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:12px;padding:1.25rem;">
                    <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--accent-primary);margin-bottom:0.75rem;font-weight:600;">💾 Backup</div>
                    <table style="width:100%;font-size:0.85rem;border-collapse:collapse;">
                        <tbody>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Latest Backup</td><td id="ops-bk-id" style="text-align:right;font-family:monospace;font-size:0.76rem;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Backup Age</td><td id="ops-bk-age" style="text-align:right;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Total Backups</td><td id="ops-bk-count" style="text-align:right;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Backup Failures</td><td id="ops-bk-fail" style="text-align:right;color:#f59e0b;">—</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Validator Panel -->
                <div style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:12px;padding:1.25rem;">
                    <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--accent-primary);margin-bottom:0.75rem;font-weight:600;">✅ Validator</div>
                    <table style="width:100%;font-size:0.85rem;border-collapse:collapse;">
                        <tbody>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Readiness Score</td><td id="ops-vl-score" style="text-align:right;font-weight:600;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Status</td><td id="ops-vl-status" style="text-align:right;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Warnings</td><td id="ops-vl-warn" style="text-align:right;color:#f59e0b;">—</td></tr>
                            <tr><td style="color:var(--text-muted);padding:3px 0;">Failures</td><td id="ops-vl-fail" style="text-align:right;color:#ef4444;">—</td></tr>
                        </tbody>
                    </table>
                </div>

            </div>
        </div>

    <div class="latest-completed-section">
        <h2>📊 Recent Finished Tasks</h2>
        <table class="completed-table">
            <thead>
                <tr>
                    <th>Task ID</th>
                    <th>Project</th>
                    <th>Status</th>
                    <th>Objective</th>
                    <th>Summary / Error</th>
                    <th>Finished At</th>
                </tr>
            </thead>
            <tbody id="completed-table-body">
                <tr>
                    <td colspan="6" style="text-align: center; color: var(--text-muted); font-style: italic;">No records yet</td>
                </tr>
            </tbody>
        </table>
    </div>

    <!-- Details Modal -->
    <div class="modal-overlay" id="task-modal">
        <div class="modal-card">
            <div class="modal-header">
                <div>
                    <h2 id="modal-task-id" style="font-family: monospace; font-size: 1.4rem; color: #818cf8; margin-bottom: 0.25rem;">TASK-XXXX</h2>
                    <span class="project-badge" id="modal-project" style="font-size: 0.8rem;">OI_LABS</span>
                    <span class="status-badge" id="modal-status" style="margin-left: 0.5rem;">INBOX</span>
                </div>
                <button class="modal-close-btn" onclick="closeModal()">&times;</button>
            </div>
            
            <div class="modal-section">
                <h4>Objective</h4>
                <p id="modal-objective" style="font-weight: 500;"></p>
            </div>
            
            <div class="modal-section" id="modal-context-sec">
                <h4>Context</h4>
                <p id="modal-context" style="color: var(--text-muted);"></p>
            </div>

            <div class="modal-section" id="modal-criteria-sec">
                <h4>Acceptance Criteria</h4>
                <ul id="modal-criteria"></ul>
            </div>

            <div class="modal-section" id="modal-constraints-sec">
                <h4>Constraints</h4>
                <ul id="modal-constraints"></ul>
            </div>

            <div class="modal-section" id="modal-summary-sec">
                <h4>Completion Summary</h4>
                <p id="modal-summary" style="color: #34d399; font-weight: 500;"></p>
            </div>

            <div class="modal-section" id="modal-error-sec">
                <h4>Error Details</h4>
                <pre id="modal-error" style="color: #f87171;"></pre>
            </div>
            
            <div class="modal-section" id="modal-meta-sec">
                <h4>Execution Metadata</h4>
                <p id="modal-meta" style="color: var(--text-muted); font-size: 0.8rem; font-family: monospace;"></p>
            </div>
        </div>
    </div>

    <script>
        let countdown = 5.0;
        let lastUpdateTime = Date.now();
        let currentUptime = 0;
        let workerHeartbeat = null;
        let tasksMap = {};

        // Periodic Local Ticker
        setInterval(() => {
            // Tick Uptime
            if (currentUptime > 0) {
                currentUptime++;
                document.getElementById('uptime-value').innerText = formatDuration(currentUptime);
            }
            
            // Tick countdown
            countdown -= 0.1;
            if (countdown <= 0) {
                countdown = 5.0;
                fetchData();
            }
            
            // Update UI countdown
            document.getElementById('refresh-text').innerText = `Refreshing in ${countdown.toFixed(1)}s`;
            document.getElementById('refresh-bar').style.width = `${(countdown / 5.0) * 100}%`;
            
            // Update worker relative time
            if (workerHeartbeat) {
                const diff = Math.max(0, Math.floor((Date.now() - new Date(workerHeartbeat).getTime()) / 1000));
                document.getElementById('worker-heartbeat-label').innerText = `Last seen: ${diff}s ago`;
            }
        }, 100);

        function formatDuration(seconds) {
            if (seconds < 60) return `${seconds}s`;
            const m = Math.floor(seconds / 60);
            const s = seconds % 60;
            if (m < 60) return `${m}m ${s}s`;
            const h = Math.floor(m / 60);
            const rm = m % 60;
            return `${h}h ${rm}m`;
        }

        function openModal(taskId) {
            const task = tasksMap[taskId];
            if (!task) return;
            
            document.getElementById('modal-task-id').innerText = task.task_id;
            
            const projBadge = document.getElementById('modal-project');
            projBadge.innerText = task.project;
            projBadge.className = `project-badge ${task.project}`;
            
            const statusBadge = document.getElementById('modal-status');
            statusBadge.innerText = task.status;
            statusBadge.className = `status-badge ${task.status === 'claimed' || task.status === 'delegated' ? 'working' : task.status}`;
            
            document.getElementById('modal-objective').innerText = task.objective || 'No objective defined.';
            document.getElementById('modal-context').innerText = task.context || 'None';
            
            // Acceptance Criteria
            const criteriaList = document.getElementById('modal-criteria');
            criteriaList.innerHTML = '';
            const criteria = typeof task.acceptance_criteria === 'string' ? JSON.parse(task.acceptance_criteria) : task.acceptance_criteria;
            if (criteria && criteria.length > 0) {
                criteria.forEach(c => {
                    const li = document.createElement('li');
                    li.innerText = c;
                    criteriaList.appendChild(li);
                });
                document.getElementById('modal-criteria-sec').style.display = 'flex';
            } else {
                document.getElementById('modal-criteria-sec').style.display = 'none';
            }
            
            // Constraints
            const constraintsList = document.getElementById('modal-constraints');
            constraintsList.innerHTML = '';
            const constraints = typeof task.constraints === 'string' ? JSON.parse(task.constraints) : task.constraints;
            if (constraints && constraints.length > 0) {
                constraints.forEach(c => {
                    const li = document.createElement('li');
                    li.innerText = c;
                    constraintsList.appendChild(li);
                });
                document.getElementById('modal-constraints-sec').style.display = 'flex';
            } else {
                document.getElementById('modal-constraints-sec').style.display = 'none';
            }
            
            // Summary
            if (task.summary) {
                document.getElementById('modal-summary').innerText = task.summary;
                document.getElementById('modal-summary-sec').style.display = 'flex';
            } else {
                document.getElementById('modal-summary-sec').style.display = 'none';
            }
            
            // Error
            if (task.error_message) {
                document.getElementById('modal-error').innerText = task.error_message;
                document.getElementById('modal-error-sec').style.display = 'flex';
            } else {
                document.getElementById('modal-error-sec').style.display = 'none';
            }
            
            // Meta
            document.getElementById('modal-meta').innerText = `Worker ID: ${task.worker_id || 'None'}\nClaimed At: ${task.claimed_at || 'Never'}\nUpdated At: ${task.updated_at || 'Never'}`;
            
            document.getElementById('task-modal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('task-modal').classList.remove('active');
        }

        function renderTaskCard(task) {
            tasksMap[task.task_id] = task;
            const updatedTime = task.updated_at ? new Date(task.updated_at).toLocaleTimeString() : 'N/A';
            return `
                <div class="task-card" onclick="openModal('${task.task_id}')">
                    <div class="task-card-header">
                        <span class="task-id-badge">${task.task_id}</span>
                        <span class="project-badge ${task.project}">${task.project}</span>
                    </div>
                    <div class="task-objective">${task.objective}</div>
                    <div class="task-card-footer">
                        <span>${task.worker_id || 'no worker'}</span>
                        <span>${updatedTime}</span>
                    </div>
                </div>
            `;
        }

        async function fetchData() {
            try {
                const response = await fetch('/dashboard');
                const data = await response.json();
                
                // Update Counts
                document.getElementById('count-inbox').innerText = data.inbox;
                document.getElementById('count-working').innerText = data.working;
                document.getElementById('count-done').innerText = data.done;
                document.getElementById('count-blocked').innerText = data.blocked;
                document.getElementById('count-failed').innerText = data.failed;
                
                document.getElementById('badge-inbox').innerText = data.inbox;
                document.getElementById('badge-working').innerText = data.working;
                document.getElementById('badge-done').innerText = data.done;
                document.getElementById('badge-blocked').innerText = data.blocked;
                document.getElementById('badge-failed').innerText = data.failed;
                
                // Update Worker Status
                const dot = document.getElementById('worker-dot');
                const label = document.getElementById('worker-status-label');
                if (data.worker_status === 'ONLINE') {
                    dot.className = 'status-dot online';
                    label.innerText = `Worker: ONLINE ${data.current_task ? '[' + data.current_task + ']' : '[IDLE]'}`;
                } else {
                    dot.className = 'status-dot offline';
                    label.innerText = 'Worker: OFFLINE';
                }
                
                workerHeartbeat = data.heartbeat;
                if (workerHeartbeat) {
                    const diff = Math.max(0, Math.floor((Date.now() - new Date(workerHeartbeat).getTime()) / 1000));
                    document.getElementById('worker-heartbeat-label').innerText = `Last seen: ${diff}s ago`;
                } else {
                    document.getElementById('worker-heartbeat-label').innerText = 'Last seen: Never';
                }
                
                // Update Uptime
                currentUptime = data.uptime;
                document.getElementById('uptime-value').innerText = formatDuration(currentUptime);

                // Render Boards
                const columns = ['inbox', 'working', 'done', 'blocked', 'failed'];
                columns.forEach(col => {
                    const listElement = document.getElementById(`list-${col}`);
                    const tasksList = data.lists[col] || [];
                    if (tasksList.length === 0) {
                        listElement.innerHTML = `<div class="empty-state">${col === 'working' ? 'No active agents' : 'No tasks in ' + col}</div>`;
                    } else {
                        listElement.innerHTML = tasksList.map(renderTaskCard).join('');
                    }
                });

                // Render Completed Table
                const tbody = document.getElementById('completed-table-body');
                if (data.latest_completed.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); font-style: italic;">No records yet</td></tr>`;
                } else {
                    tbody.innerHTML = data.latest_completed.map(task => {
                        tasksMap[task.task_id] = task;
                        const summaryText = task.status === 'done' ? (task.summary || '') : (task.error_message || '');
                        const statusClass = task.status === 'done' ? 'done' : (task.status === 'blocked' ? 'blocked' : 'failed');
                        const finishedAt = task.updated_at ? new Date(task.updated_at).toLocaleString() : 'N/A';
                        return `
                            <tr style="cursor: pointer;" onclick="openModal('${task.task_id}')">
                                <td style="font-family: monospace; font-weight: 600; color: #818cf8;">${task.task_id}</td>
                                <td><span class="project-badge ${task.project}">${task.project}</span></td>
                                <td><span class="status-badge ${statusClass}">${task.status}</span></td>
                                <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${task.objective}</td>
                                <td style="max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-muted); font-style: italic;">${summaryText}</td>
                                <td>${finishedAt}</td>
                            </tr>
                        `;
                    }).join('');
                }

                // Render Persistent Sessions Table
                const sessionsTbody = document.getElementById('sessions-table-body');
                const sessionsList = data.sessions || [];
                if (sessionsList.length === 0) {
                    sessionsTbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); font-style: italic;">No active sessions loaded</td></tr>`;
                } else {
                    sessionsTbody.innerHTML = sessionsList.map(s => {
                        const statusClass = s.status.toLowerCase();
                        const updatedTime = s.updated_at ? new Date(s.updated_at).toLocaleString() : 'N/A';
                        const lockText = s.locked_by ? `<span style="color: #f59e0b; font-weight: 500;">🔒 ${s.locked_by}</span>` : '<span style="color: #10b981;">🔓 None</span>';
                        return `
                            <tr>
                                <td><span class="project-badge ${s.project_id}">${s.project_id}</span></td>
                                <td style="font-family: monospace; font-size: 0.85rem; color: #818cf8;">${s.conversation_id}</td>
                                <td><span class="status-badge ${statusClass}">${s.status}</span></td>
                                <td style="font-size: 0.8rem; font-family: monospace; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${s.workspace_path || 'N/A'}</td>
                                <td style="font-family: monospace; color: #fb7185;">${s.current_branch || 'N/A'}</td>
                                <td style="font-family: monospace; font-size: 0.8rem; max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${s.last_commit || 'N/A'}</td>
                                <td style="text-align: center; font-weight: 600; color: #a78bfa;">${s.memory_size} fields</td>
                                <td>${lockText}</td>
                                <td>${updatedTime}</td>
                            </tr>
                        `;
                    }).join('');
                }
                
                // Render Component Health
                const healthTbody = document.getElementById('health-table-body');
                if (data.health && data.health.components) {
                    const healthRows = [];
                    for (const [name, c] of Object.entries(data.health.components)) {
                        let stateClass = 'offline';
                        if (c.health_state === 'HEALTHY') stateClass = 'online';
                        else if (c.health_state === 'DEGRADED') stateClass = 'working';
                        
                        const lastCheckStr = c.last_check ? new Date(c.last_check).toLocaleTimeString() : 'N/A';
                        healthRows.push(`
                            <tr>
                                <td style="font-weight: 600; color: #a78bfa;">${name}</td>
                                <td><span class="status-badge ${stateClass}">${c.health_state}</span></td>
                                <td style="font-size: 0.85rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${c.status}</td>
                                <td style="font-family: monospace;">${c.latency_ms}ms</td>
                                <td>${lastCheckStr}</td>
                                <td style="text-align: center;">${c.retry_count}</td>
                            </tr>
                        `);
                    }
                    healthTbody.innerHTML = healthRows.join('');
                } else {
                    healthTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No health data available</td></tr>`;
                }

                // Render Metrics Table
                const metricsTbody = document.getElementById('metrics-table-body');
                if (data.metrics) {
                    const m = data.metrics;
                    const tm = m.task_metrics || {};
                    const em = m.execution_metrics || {};
                    const rm = m.reliability_metrics || {};
                    const reuse = m.reuse_metrics || {};
                    const succ = m.success_metrics || {};
                    const wm = m.worker_metrics || {};
                    
                    const formatPct = (val) => (val * 100).toFixed(1) + '%';
                    const formatDuration = (ms) => ms > 0 ? (ms / 1000).toFixed(2) + 's' : '0.00s';
                    
                    metricsTbody.innerHTML = `
                        <tr><td><strong>Total Tasks Claimed</strong></td><td>${tm.total_tasks || 0}</td></tr>
                        <tr><td><strong>Completed / Failed / Blocked</strong></td><td>
                            <span class="status-badge online">${tm.completed_tasks || 0}</span> / 
                            <span class="status-badge offline">${tm.failed_tasks || 0}</span> / 
                            <span class="status-badge blocked">${tm.blocked_tasks || 0}</span>
                        </td></tr>
                        <tr><td><strong>Average Runtime</strong></td><td>${formatDuration(em.average_execution)}</td></tr>
                        <tr><td><strong>Median Runtime (P50)</strong></td><td>${formatDuration(em.median_execution)}</td></tr>
                        <tr><td><strong>P95 Runtime</strong></td><td>${formatDuration(em.P95_execution)}</td></tr>
                        <tr><td><strong>Fastest / Slowest Runtime</strong></td><td>${formatDuration(em.fastest_task)} / ${formatDuration(em.slowest_task)}</td></tr>
                        <tr><td><strong>Workspace Reuse %</strong></td><td>${formatPct(reuse.workspace_reuse_rate)}</td></tr>
                        <tr><td><strong>Conversation Reuse %</strong></td><td>${formatPct(reuse.conversation_reuse_rate)}</td></tr>
                        <tr><td><strong>Git Success %</strong></td><td>${formatPct(succ.git_success_rate)}</td></tr>
                        <tr><td><strong>Verifier Success %</strong></td><td>${formatPct(succ.verifier_success_rate)}</td></tr>
                        <tr><td><strong>Reliability Retries / Expiries</strong></td><td>
                            Retries: ${rm.retry_count || 0} | Session Expiries: ${rm.session_expiry_count || 0} | Metrics Failures: ${rm.metrics_failures || 0}
                        </td></tr>
                        <tr><td><strong>Worker Uptime / Startup Count</strong></td><td>
                            Uptime: ${wm.worker_uptime || 0}s | Startups: ${wm.startup_count || 0}
                        </td></tr>
                    `;
                } else {
                    metricsTbody.innerHTML = `<tr><td colspan="2" style="text-align: center; color: var(--text-muted);">No metrics data available</td></tr>`;
                }
                // Render Operations Section (V3.2 S6) — consumes data already fetched, no extra API calls
                try {
                    // Worker panel
                    const wStatus = data.worker_status || 'OFFLINE';
                    document.getElementById('ops-w-status').innerText = wStatus;
                    document.getElementById('ops-w-status').style.color = wStatus === 'ONLINE' ? '#10b981' : '#ef4444';
                    document.getElementById('ops-w-uptime').innerText = data.uptime ? formatDuration(data.uptime) : '—';
                    const wm = (data.metrics && data.metrics.worker_metrics) ? data.metrics.worker_metrics : {};
                    document.getElementById('ops-w-id').innerText = wm.worker_id || '—';
                    document.getElementById('ops-w-startups').innerText = wm.startup_count || '—';

                    // Queue panel
                    document.getElementById('ops-q-pending').innerText = data.inbox || 0;
                    document.getElementById('ops-q-running').innerText = data.working || 0;
                    document.getElementById('ops-q-done').innerText = data.done || 0;
                    document.getElementById('ops-q-failed').innerText = data.failed || 0;
                    document.getElementById('ops-q-blocked').innerText = data.blocked || 0;

                    // Sessions panel
                    const sessions = data.sessions || [];
                    const activeSessions = sessions.filter(s => s.status === 'ACTIVE').length;
                    const rm = (data.metrics && data.metrics.reliability_metrics) ? data.metrics.reliability_metrics : {};
                    const reuse = (data.metrics && data.metrics.reuse_metrics) ? data.metrics.reuse_metrics : {};
                    document.getElementById('ops-s-active').innerText = activeSessions;
                    document.getElementById('ops-s-reused').innerText = reuse.conversation_reuses !== undefined ? reuse.conversation_reuses : '—';
                    document.getElementById('ops-s-expired').innerText = rm.session_expiry_count || 0;

                    // Git panel
                    const succ = (data.metrics && data.metrics.success_metrics) ? data.metrics.success_metrics : {};
                    document.getElementById('ops-git-rate').innerText = succ.git_success_rate !== undefined ? (succ.git_success_rate * 100).toFixed(1) + '%' : '—';
                    document.getElementById('ops-git-fail').innerText = rm.git_failures || 0;

                    // Backup panel
                    const bkm = (data.metrics && data.metrics.reliability_metrics) ? data.metrics.reliability_metrics : {};
                    document.getElementById('ops-bk-id').innerText = bkm.latest_backup_id || 'None';
                    document.getElementById('ops-bk-age').innerText = bkm.backup_age_seconds !== undefined ? (bkm.backup_age_seconds / 60).toFixed(0) + 'm ago' : '—';
                    document.getElementById('ops-bk-count').innerText = bkm.backup_count !== undefined ? bkm.backup_count : '—';
                    document.getElementById('ops-bk-fail').innerText = bkm.backup_failure_count || 0;

                    // Validator panel — best-effort from metrics
                    const vl = (data.metrics && data.metrics.validator_status) ? data.metrics.validator_status : {};
                    document.getElementById('ops-vl-score').innerText = vl.score !== undefined ? (vl.score * 100).toFixed(1) + '%' : '—';
                    document.getElementById('ops-vl-status').innerText = vl.status || '—';
                    document.getElementById('ops-vl-warn').innerText = vl.warnings !== undefined ? vl.warnings : '—';
                    document.getElementById('ops-vl-fail').innerText = vl.failures !== undefined ? vl.failures : '—';
                } catch(opsErr) {
                    console.warn('Ops section render error:', opsErr);
                }
            } catch (err) {
                console.error("Dashboard pull error:", err);
            }
        }

        // Initial fetch
        fetchData();
    </script>
</body>
</html>"""
    return html_content


@app.get("/dashboard")
def get_dashboard_data():
    try:
        # 1. Fetch worker status
        worker_record = []
        try:
            worker_record = _sb_get("tasks?task_id=eq.SYSTEM-WORKER-WORKER-MAIN")
        except Exception:
            pass
            
        worker_status = "OFFLINE"
        heartbeat = None
        current_task = None
        uptime = 0
        
        now = datetime.datetime.now(datetime.timezone.utc)
        if worker_record and isinstance(worker_record, list) and len(worker_record) > 0:
            rec = worker_record[0]
            heartbeat_str = rec.get("last_heartbeat_at")
            
            # Read context details
            context_str = rec.get("context") or "{}"
            try:
                context_data = json.loads(context_str) if isinstance(context_str, str) else context_str
                if not isinstance(context_data, dict):
                    context_data = {}
            except Exception:
                context_data = {}
                
            started_str = context_data.get("started_at")
            current_task = context_data.get("current_task_id")
            
            if heartbeat_str:
                heartbeat = heartbeat_str
                try:
                    hb = datetime.datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                    diff = (now - hb).total_seconds()
                    if diff <= 60.0:
                        worker_status = "ONLINE"
                except Exception:
                    pass
            
            if started_str:
                try:
                    started = datetime.datetime.fromisoformat(started_str.replace("Z", "+00:00"))
                    uptime = int((now - started).total_seconds())
                except Exception:
                    pass
        else:
            # Fallback to old worker_status table if tasks query is empty
            try:
                fallback_record = _sb_get("worker_status?worker_id=eq.worker-main")
                if fallback_record and isinstance(fallback_record, list) and len(fallback_record) > 0:
                    rec = fallback_record[0]
                    heartbeat_str = rec.get("last_heartbeat_at")
                    started_str = rec.get("started_at")
                    current_task = rec.get("current_task_id")
                    
                    if heartbeat_str:
                        heartbeat = heartbeat_str
                        try:
                            hb = datetime.datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
                            diff = (now - hb).total_seconds()
                            if diff <= 60.0:
                                worker_status = "ONLINE"
                        except Exception:
                            pass
                    
                    if started_str:
                        try:
                            started = datetime.datetime.fromisoformat(started_str.replace("Z", "+00:00"))
                            uptime = int((now - started).total_seconds())
                        except Exception:
                            pass
            except Exception:
                pass

        # 2. Fetch all tasks to aggregate counts and status groupings
        tasks = []
        try:
            # Sort by updated_at descending to keep latest showing first
            tasks = _sb_get("tasks?order=updated_at.desc")
        except Exception:
            pass

        inbox_count = 0
        working_count = 0
        done_count = 0
        blocked_count = 0
        failed_count = 0
        
        inbox_list = []
        working_list = []
        done_list = []
        blocked_list = []
        failed_list = []
        
        for t in tasks:
            # Skip system heartbeat tasks
            if t.get("project") == "system":
                continue
                
            s = t.get("status", "inbox").lower()
            if s == "inbox":
                inbox_count += 1
                inbox_list.append(t)
            elif s in ["claimed", "delegated"]:
                working_count += 1
                working_list.append(t)
            elif s == "done":
                done_count += 1
                done_list.append(t)
            elif s == "blocked":
                blocked_count += 1
                blocked_list.append(t)
            elif s == "failed":
                failed_count += 1
                failed_list.append(t)
                
        # Get latest completed tasks (limit 5)
        completed_statuses = ["done", "blocked", "failed"]
        latest_completed = [t for t in tasks if (t.get("status", "").lower() in completed_statuses) and (t.get("project") != "system")][:5]

        # 1.5 Fetch persistent project sessions
        sessions = []
        try:
            db_path = "state/task_checkpoints.db"
            if os.path.exists(db_path):
                import sqlite3
                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT project_id, conversation_id, workspace_path, repository_url, 
                               default_branch, current_branch, last_commit, last_activity, status, 
                               locked_by, locked_at, updated_at
                        FROM project_sessions
                    """)
                    for row in cursor.fetchall():
                        mem_size = 0
                        try:
                            cursor2 = conn.cursor()
                            cursor2.execute("SELECT architecture, pending_todos, known_bugs, recent_decisions, coding_style, framework, backend_notes, oracle_notes, design_rules, owner_instructions FROM project_memories WHERE project_id = ?", (row["project_id"],))
                            mem_row = cursor2.fetchone()
                            if mem_row:
                                mem_size = sum(1 for val in mem_row if val and str(val).strip())
                        except Exception:
                            pass
                            
                        sessions.append({
                            "project_id": row["project_id"],
                            "conversation_id": row["conversation_id"],
                            "workspace_path": row["workspace_path"],
                            "repository_url": row["repository_url"],
                            "default_branch": row["default_branch"],
                            "current_branch": row["current_branch"],
                            "last_commit": row["last_commit"],
                            "last_activity": row["last_activity"],
                            "status": row["status"],
                            "locked_by": row["locked_by"],
                            "locked_at": row["locked_at"],
                            "memory_size": mem_size,
                            "updated_at": row["updated_at"]
                        })
        except Exception as e:
            print(f"⚠️ Failed to fetch project sessions: {e}")

        from control.health_monitor import HealthMonitor
        from control.metrics_manager import metrics_manager
        
        monitor = HealthMonitor()
        health_data = monitor.get_system_health()
        metrics_data = metrics_manager.get_metrics_report()

        return {
            "worker_status": worker_status,
            "heartbeat": heartbeat,
            "current_task": current_task,
            "inbox": inbox_count,
            "working": working_count,
            "done": done_count,
            "blocked": blocked_count,
            "failed": failed_count,
            "latest_completed": latest_completed,
            "uptime": uptime,
            "sessions": sessions,
            "health": health_data,
            "metrics": metrics_data,
            "lists": {
                "inbox": inbox_list[:10],
                "working": working_list[:10],
                "done": done_list[:10],
                "blocked": blocked_list[:10],
                "failed": failed_list[:10],
            }
        }
    except Exception as e:
        return {
            "worker_status": "OFFLINE",
            "heartbeat": None,
            "current_task": None,
            "inbox": 0,
            "working": 0,
            "done": 0,
            "blocked": 0,
            "failed": 0,
            "latest_completed": [],
            "uptime": 0,
            "lists": {
                "inbox": [],
                "working": [],
                "done": [],
                "blocked": [],
                "failed": []
            },
            "error": str(e)
        }


@app.get("/diagnostics", include_in_schema=True)
def get_diagnostics():
    """
    Read-only system diagnostics snapshot.
    Returns cached/available runtime information only.
    Never touches Git (writes), creates sessions, runs validator, creates backups, or writes anything.
    """
    import sys
    import platform
    import datetime as _dt
    from control.metrics_manager import metrics_manager

    diag = {}

    # 1. Worker identity + metrics summary (read cached metrics report)
    try:
        m = metrics_manager.get_metrics_report()
        wm = m.get("worker_metrics", {})
        diag["worker"] = {
            "worker_id":      wm.get("worker_id", ""),
            "startup_count":  wm.get("startup_count", 0),
            "uptime_s":       wm.get("worker_uptime", 0),
            "last_heartbeat": wm.get("last_heartbeat", None),
        }
        diag["metrics_summary"] = {
            "total_tasks":           m.get("task_metrics", {}).get("total_tasks", 0),
            "completed":             m.get("task_metrics", {}).get("completed_tasks", 0),
            "failed":                m.get("task_metrics", {}).get("failed_tasks", 0),
            "blocked":               m.get("task_metrics", {}).get("blocked_tasks", 0),
            "git_success_rate":      m.get("success_metrics", {}).get("git_success_rate", 0),
            "verifier_success_rate": m.get("success_metrics", {}).get("verifier_success_rate", 0),
            "backup_failure_count":  m.get("reliability_metrics", {}).get("backup_failure_count", 0),
        }
    except Exception as e:
        diag["worker"] = {"error": str(e)}
        diag["metrics_summary"] = {}

    # 2. Configuration (read-only file reads)
    try:
        from control.config_manager import ConfigManager
        cm = ConfigManager()
        diag["configuration"] = {
            "version":         cm.get_version(),
            "active_projects": list(cm.projects_config.get("projects", {}).keys()),
        }
        diag["feature_flags"] = {
            flag: cm.get_feature_flag(flag)
            for flag in ["persistent_sessions", "structured_logging", "metrics",
                         "auto_push", "chaos_testing", "backup"]
        }
    except Exception as e:
        diag["configuration"] = {"error": str(e)}
        diag["feature_flags"] = {}

    # 3. Environment summary (read-only)
    diag["environment"] = {
        "python_version": sys.version,
        "platform":       platform.platform(),
        "cwd":            os.getcwd(),
        "timestamp":      _dt.datetime.utcnow().isoformat() + "Z",
    }

    # 4. Component versions
    diag["component_versions"] = {
        "bridge_server":        "3.2",
        "structured_logger":    "3.2",
        "audit_trail":          "3.2",
        "metrics_manager":      "3.2",
        "health_monitor":       "3.2",
        "backup_manager":       "3.2",
        "production_validator": "3.2",
        "telemetry":            "3.2",
    }

    # 5. Recent audit events (read-only SQLite SELECT)
    try:
        from control.audit_trail import audit_trail
        recent = audit_trail.get_recent(limit=10)
        diag["recent_audit_events"] = recent
    except Exception as e:
        diag["recent_audit_events"] = {"error": str(e)}

    # 6. Backup status (read-only directory scan + manifest reads)
    try:
        backup_dir = "state/backups"
        backups = []
        if os.path.exists(backup_dir):
            for entry in sorted(os.listdir(backup_dir), reverse=True)[:5]:
                manifest_path = os.path.join(backup_dir, entry, "manifest.json")
                if os.path.exists(manifest_path):
                    with open(manifest_path, "r", encoding="utf-8") as _f:
                        manifest = json.load(_f)
                    backups.append({
                        "backup_id":  manifest.get("backup_id", entry),
                        "created_at": manifest.get("created_at"),
                        "label":      manifest.get("label"),
                        "file_count": len(manifest.get("files", [])),
                    })
        diag["backup_status"] = {
            "total_backups":  len(os.listdir(backup_dir)) if os.path.exists(backup_dir) else 0,
            "recent_backups": backups,
        }
    except Exception as e:
        diag["backup_status"] = {"error": str(e)}

    # 7. Git status (read-only git commands — no writes)
    try:
        import subprocess
        branch_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3
        )
        commit_res = subprocess.run(
            ["git", "log", "-1", "--format=%h %s"],
            capture_output=True, text=True, timeout=3
        )
        diag["git_status"] = {
            "current_branch": branch_res.stdout.strip() if branch_res.returncode == 0 else "unknown",
            "last_commit":    commit_res.stdout.strip() if commit_res.returncode == 0 else "unknown",
        }
    except Exception as e:
        diag["git_status"] = {"error": str(e)}

    # 8. Validator cache (read cached result — never runs validator)
    try:
        validator_cache_path = "state/validator_cache.json"
        if os.path.exists(validator_cache_path):
            with open(validator_cache_path, "r", encoding="utf-8") as _f:
                diag["validator_status"] = json.load(_f)
        else:
            diag["validator_status"] = {
                "note": "No cached result. Run production_check.py to populate."
            }
    except Exception as e:
        diag["validator_status"] = {"error": str(e)}

    # 9. Configuration Precedence fields resolved by config_resolver
    try:
        from control.config_resolver import resolve_config
        cfg = resolve_config()
        diag.update(cfg)
    except Exception as e:
        diag["config_resolve_error"] = str(e)

    return diag


@app.get("/health")
def health():
    from control.health_monitor import HealthMonitor
    monitor = HealthMonitor()
    return monitor.get_system_health()



@app.get("/metrics")
def metrics():
    from control.metrics_manager import metrics_manager
    return metrics_manager.get_metrics_report()


@app.get("/openapi.json", include_in_schema=False)
def get_openapi_endpoint():
    return app.openapi()


# ── Custom OpenAPI operationIds ───────────────────────────────────────────────

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
        
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

    openapi_schema = get_openapi(
        title="Ashwani Agent Company Bridge API",
        version="1.0.0",
        description=instructions,
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
            elif route.path == "/tasks/{task_id}/artifacts":
                route.operation_id = "get_task_artifacts"
            elif route.path == "/tasks/{task_id}/artifacts/{artifact_name}":
                route.operation_id = "get_task_artifact_content"
            elif route.path == "/knowledge/search":
                route.operation_id = "search_knowledge"
            elif route.path == "/tasks/{task_id}/context":
                route.operation_id = "get_task_context"
            elif route.path == "/indexer/queue":
                route.operation_id = "get_indexer_queue"

                
    # Regenerate schema with correct operationIds
    openapi_schema = get_openapi(
        title="Ashwani Agent Company Bridge API",
        version="1.0.0",
        description=instructions,
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
