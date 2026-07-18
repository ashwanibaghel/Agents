# Backup Manager

The Backup Manager automates local backups of critical system files to prevent data loss.

## Backup Scope
- SQLite Database: `state/task_checkpoints.db`
- Configuration files: `projects.yaml`, `supabase.yaml`, `feature_flags.yaml`
- Worker Identity: `state/worker_id.txt`

## Operations & Verification

### Timestamped Backups
Backups are written to folders in `state/backups/` using the naming pattern:
`backup_YYYYMMDD_HHMMSS_[label]`

### Checksum Verification
During a backup:
1. Every file copied has its SHA-256 checksum calculated.
2. Checksums are recorded in a `manifest.json` file inside the backup folder.
3. During a restore, the checksums are recalculated and verified against `manifest.json` to guarantee data integrity.

### Retention Policy
- A configurable retention limit (default: 10 backups) is enforced.
- When a backup succeeds, the manager checks the count of existing backups.
- Oldest backups are automatically pruned if the count exceeds the configured retention limit.
- Errors during cleanup or copy operations are logged but do not crash the system.
