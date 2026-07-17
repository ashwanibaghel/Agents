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
