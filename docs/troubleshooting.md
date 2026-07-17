# Troubleshooting & Recovery

This document describes common system issues, their causes, and how to recover from operational failures.

## Common Issues & Fixes

### 1. Database Locking issues
* **Symptom**: Tasks are marked as `BLOCKED` with logs indicating `Workspace lock acquisition failed`.
* **Cause**: A previous worker run crashed or exited abruptly without releasing the database lock.
* **Recovery**: Clear the locks inside SQLite:
  ```bash
  python -c "import sqlite3; conn=sqlite3.connect('state/task_checkpoints.db'); conn.execute('UPDATE persistent_sessions SET lock_holder=NULL, status=\'IDLE\''); conn.commit()"
  ```

### 2. Git Authentication Failures
* **Symptom**: Workspaces fail to clone or push; `production_check.py` reports Git failures.
* **Cause**: SSH keys are not loaded or lack repository access permissions.
* **Recovery**: Verify connection:
  ```bash
  ssh -T git@github.com
  ```
  Ensure `ssh-agent` is running and the correct key is added.

### 3. Missing BRIDGE_TOKEN
* **Symptom**: `production_check.py` fails with a Security warning.
* **Cause**: `BRIDGE_TOKEN` is not loaded in the active shell environment.
* **Recovery**: Export the variable before running the validation check:
  - **PowerShell**: `$env:BRIDGE_TOKEN="your-token"`
  - **Linux/bash**: `export BRIDGE_TOKEN="your-token"`

## Disaster Recovery Procedure

If the SQLite database or configuration files become corrupted:

1. **Locate Backups**: List all available backups in `state/backups/`.
2. **Verify Checksums**: Inspect the `manifest.json` file inside the target backup folder to verify integrity.
3. **Restore Files**:
   - Stop the worker and bridge processes.
   - Copy `task_checkpoints.db` from the backup folder back into `state/`.
   - Restore `projects.yaml`, `supabase.yaml`, and `feature_flags.yaml` configurations.
   - Restart the server.
4. **Verification**: Run `python production_check.py` to confirm the system returns to a healthy, validated state.
