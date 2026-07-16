# Version 2 — Boss Dashboard and Presence Upgrades Walkthrough

We have successfully built and deployed **Version 2** of the Ashwani Agent Company infrastructure. Below is a detailed walkthrough of all the features implemented, tested, and live-verified.

## Features Implemented

### 1. Boss Dashboard UI (`GET /`)
A highly optimized, dark-themed, glassmorphic dashboard built using **Vanilla HTML and CSS** (no heavy frameworks).
* **Location**: Served at the root path of the deployed API (`https://agents-x52u.onrender.com/`).
* **Visuals**: Curated dark blue/gray backgrounds, glowing status badges, custom subtle scrollbars, and dynamic modal overlays.
* **Auto-refresh**: Pulls updated telemetry from the backend every **5 seconds** without manual page reloads, accompanied by a visual countdown progress bar.
* **Task Modals**: Click on any card in the columns to open a rich modal showing complete details (Objective, Context, Acceptance Criteria, Constraints, Summary, and Error logs).

### 2. Dashboard API (`GET /dashboard`)
Exposes comprehensive operations data for the frontend to render:
* **Worker Status**: Tracks `ONLINE` vs `OFFLINE` by comparing the latest worker heartbeat against a 60-second threshold.
* **Current Task**: Displays the task ID currently claimed by the active worker (or shows `[IDLE]` if none).
* **Task Boards**: Groups all database tasks into respective columns (`inbox`, `working`, `done`, `blocked`, `failed`), returning details for up to 10 tasks per column.
* **Uptime**: Computes active worker runtime in seconds from the startup timestamp.
* **Recent Finished Tasks**: Returns the last 5 completed tasks.

### 3. Worker Heartbeat and Presence
* **Loop Heartbeats**: Updated the local worker loop (`main.py`) to periodically update its presence in the `tasks` table under a special `SYSTEM-WORKER-{worker_id}` record every **15 seconds** (while idle or actively working). This avoids 404/schema caching errors on new tables.
* **Offline Detection**: The API classifies the worker as `OFFLINE` if no heartbeat is received for more than **60 seconds**.

### 4. Auto Recovery
* **Lease Extension**: Set the default task lease timeout to **10 minutes (600 seconds)**.
* **Task Recovery**: If a task remains in `working` (`claimed`/`delegated`) status for over 10 minutes without receiving a heartbeat, the background worker automatically releases it back to the `inbox` queue and records the recovery details.

### 5. Boss Notifications (Event Logging)
* Added a new `task_events` table in Supabase to log chronological state change event records whenever a task changes status:
  * `INBOX` ➔ `WORKING` (Claimed)
  * `WORKING` ➔ `DONE`
  * `WORKING` ➔ `BLOCKED`

---

## Technical Verification

### 1. Database Schema Additions
The following tables are defined in [supabase_schema.sql](file:///e:/Projects/ashwani-agent-company/config/supabase_schema.sql) and should be created in your Supabase SQL editor:
```sql
-- Create task_events table
CREATE TABLE IF NOT EXISTS public.task_events (
    id          BIGSERIAL PRIMARY KEY,
    task_id     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    old_status  TEXT,
    new_status  TEXT,
    message     TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON public.task_events (task_id);
ALTER TABLE public.task_events ENABLE ROW LEVEL SECURITY;

-- Create worker_status table
CREATE TABLE IF NOT EXISTS public.worker_status (
    worker_id         TEXT PRIMARY KEY,
    last_heartbeat_at TIMESTAMPTZ DEFAULT now(),
    started_at        TIMESTAMPTZ DEFAULT now(),
    current_task_id   TEXT
);
ALTER TABLE public.worker_status ENABLE ROW LEVEL SECURITY;
```

> [!NOTE]
> **Defensive Coding**: The worker and bridge server code is designed defensively using `try/except` wrappers. If these tables do not exist in your Supabase database yet, the system will fallback and continue working normally without throwing errors or crashing.

### 2. Automated Unit Tests
* Extended [test_bridge_api.py](file:///e:/Projects/ashwani-agent-company/tests/test_bridge_api.py) to assert dashboard route validity and correct JSON fields return.
* Ran full test suite locally: **72 tests, 0 failures** (Passed successfully).

---

## Boss Dashboard Preview

Here is a visual mockup of the dark-themed Version 2 Boss Dashboard:

* [Boss Dashboard Mockup Image](file:///C:/Users/ashwa/.gemini/antigravity/brain/756dbbdb-e0aa-41d7-8bc1-c26547135e2e/boss_dashboard_v2_1784224429746.png)
