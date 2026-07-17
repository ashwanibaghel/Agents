# ADR 004: Backup Strategy

## Status
Accepted

## Context
The system keeps critical state in local files (SQLite database containing audit trails, sessions, and task logs, as well as `projects.yaml`, `supabase.yaml`, and `feature_flags.yaml`). If these files are corrupted, deleted, or lost due to disk failure, worker crash, or manual error, recovery is extremely difficult. We need an automated backup manager to protect critical state files.

## Decision
We implemented a robust **Backup Manager** (`control/backup_manager.py`).
1. **Scope**: Backs up SQLite databases (`task_checkpoints.db`), configuration files (`projects.yaml`, `supabase.yaml`, `feature_flags.yaml`), and worker identities.
2. **Timestamped Folders**: Backups are written to unique folders formatted as `backup_YYYYMMDD_HHMMSS_[label]`.
3. **Retention Policy**: A configurable retention policy maintains a maximum number of backups (default: 10), automatically deleting the oldest backups when the threshold is exceeded.
4. **Checksum Verification**: SHA-256 checksums are calculated for every backed-up file, written to a `manifest.json` file inside the backup folder, and validated on restore to guarantee data integrity.
5. **No-Crash Guarantee**: Failures during backup generation (e.g., disk full, file locked) are logged as warnings and increment a failure counter, but never halt the main worker loop.

## Alternatives Considered
- **Standard Cron / OS-level Backup Scripts**: Rejected because file-level locking on Windows/Linux (especially active SQLite DBs) can lead to corrupted backups if copied while database transactions are in progress. Our manager uses safe SQLite connection copies or waits for locks.
- **Git-based Backup**: Rejected because storing binary databases and runtime environment configurations in the project Git repository is bad practice and balloons repo size.

## Consequences
- **Data Protection**: Zero data loss for state and configurations under failure.
- **Disk Usage**: Backups consume additional local disk space, which is managed and capped by the retention limits.
- **Auditability**: Manifests provide exact proof of what was backed up and when.

## Future Considerations
- Supporting cloud-storage backup upload (e.g., Supabase storage, AWS S3, Google Cloud Storage) for offsite disaster recovery.
