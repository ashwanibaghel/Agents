# Task Lifecycle

Tasks flow through a series of states starting from creation in the database (Supabase) to complete execution, validation, and final Git branch commit.

```
       [ INBOX ] (Queued on Supabase)
          │
          ▼
       [ CLAIMED ] (Acquired by Worker Daemon)
          │
          ▼
       [ WORKSPACE_PREPARED ] (Workspace prepared & branch checked out)
          │
          ▼
  ┌───────┴────────────────────────┐
  ▼                                ▼
[ SCRIPTED RUN ] (V3.1 fallback)  [ ANTIGRAVITY RUN ] (V3.2 delegated)
  │                                │
  └───────┬────────────────────────┘
          │
          ▼
       [ RUN_COMPLETED ] (Execution finishes, code modified)
          │
          ▼
       [ VERIFICATION_STARTED ] (Result Verifier runs tests)
          │
          ├───► Success ───► [ VERIFICATION_PASSED ] ───► [ GIT_PUSH ] ───► [ TASK_COMPLETED ] (DONE)
          │
          └───► Failure ───► [ VERIFICATION_FAILED ] ───► [ TASK_FAILED ] (FAILED)
```

## State Details

- **INBOX**: Task is created and queued.
- **CLAIMED**: The worker selects the task and locks it for execution.
- **WORKSPACE_PREPARED**: Workspace folder is checked and a feature branch (`task-<id>`) is checked out.
- **DELEGATED (ANTIGRAVITY_STARTED)**: Task execution is delegated to an active or new Antigravity session.
- **RUN_COMPLETED**: Code modification is finished and the worker begins clean-up.
- **VERIFICATION_STARTED**: The `ResultVerifier` executes configured validation commands inside the isolated workspace.
- **VERIFICATION_PASSED / FAILED**: The validator records the output and reports success or failure.
- **GIT_PUSH**: If successful, the branch is pushed to remote.
- **TASK_COMPLETED / FAILED / BLOCKED**: Final terminals. Releasing session locks.
