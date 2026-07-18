# Sequence Diagrams

This document contains Mermaid diagrams describing the sequence flows of key system operations.

## Task Execution Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Operator
    participant WD as Worker Daemon (main.py)
    participant DP as Dispatcher
    participant WM as Workspace Manager
    participant AW as Antigravity Worker
    participant AC as Antigravity Client
    participant DB as SQLite DB
    participant TS as Supabase Task Source

    WD->>TS: Claim Task (INBOX)
    TS-->>WD: Return claimed task info
    WD->>DP: execute_task(task)
    DP->>WM: prepare_workspace(project)
    WM-->>DP: Return workspace_info
    DP->>AW: dispatch_task(task, workspace_info)
    
    rect rgb(30, 30, 40)
        note right of AW: Check Active Session
        AW->>DB: Query persistent session
        DB-->>AW: Return session metadata (conv_id)
        AW->>AC: Check session status (conv_id)
        AC-->>AW: Active/Expired
    end

    alt Session Active
        AW->>AC: send_message(conv_id, prompt)
    else Session Expired/None
        AW->>AC: new_conversation(full_prompt)
        AC-->>AW: Return new conv_id
        AW->>DB: Save session metadata
    end

    AW->>DB: Save delegation state (checkpoint)
    AW->>TS: Update status to delegated
    AW-->>DP: Return DELEGATED result
    DP-->>WD: Return execution status
```

## Worker Restart Sequence

```mermaid
sequenceDiagram
    autonumber
    participant WD as Worker Daemon (main.py)
    participant CM as Config Manager
    participant DB as SQLite DB
    participant TS as Supabase Task Source
    participant BM as Backup Manager
    participant PV as Production Validator

    WD->>CM: Load configs (projects.yaml, supabase.yaml)
    CM-->>WD: Configs loaded
    WD->>DB: Connect to database & init schema
    WD->>BM: Run auto-backup
    BM->>DB: Copy DB file
    BM->>BM: Calculate checksums & prune old backups
    BM-->>WD: Backup completed
    WD->>PV: Run validation checks
    PV-->>WD: Report validation score
    Note over WD: If validation score >= 90%, proceed
    WD->>WD: Register shutdown signal handlers
    WD->>WD: Start boot polling loop
    loop Every 5-10s
        WD->>TS: Poll claimed/delegated tasks
    end
```

## Recovery Flow Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant BM as Backup Manager
    participant DB as SQLite DB
    participant FS as Local Filesystem
    participant PV as Production Validator

    Admin->>FS: Inspect backup folder manifest.json
    Admin->>BM: Verify backup checksums
    BM->>FS: Recalculate file hashes
    FS-->>BM: Return calculated hashes
    BM->>BM: Compare with manifest.json
    alt Checksums Match
        BM-->>Admin: Verification SUCCESS
        Admin->>FS: Stop worker & bridge services
        Admin->>FS: Restore task_checkpoints.db
        Admin->>FS: Restore config files (*.yaml)
        Admin->>FS: Restart services
        Admin->>PV: Run production_check.py
        PV-->>Admin: Show 100% Persistence/Recovery health
    else Checksums Mismatch
        BM-->>Admin: Verification FAILED (corrupted backup)
    end
```
