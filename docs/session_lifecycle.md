# Persistent Session Lifecycle

Persistent sessions maintain conversational context across multiple tasks to reduce setup overhead, speed up response times, and allow conversational history to be preserved.

```
       Start Task
           │
           ▼
     Check Session
           │
     ┌─────┴───────────────┐
     ▼                     ▼
[ Active Session ]   [ Expired / None ]
     │                     │
     │ (Reuse)             │ (Spawn New)
     ▼                     ▼
Send Message         New Conversation
     │                     │
     └─────┬───────────────┘
           │
           ▼
     Update State
           │
           ▼
      Release Lock
```

## Session Lifecycle Stages

### 1. Verification & Selection
When a task is dispatched, the `AntigravityWorker` queries the `persistent_sessions` table in `task_checkpoints.db` for the active project.
- If a session exists, the worker calls the Bridge API to check its metadata status.
- If the conversation metadata is active and was updated within the last 24 hours (configurable), the session state is set to `ACTIVE`.

### 2. Session Reuse
- The worker bypasses system prompt setup.
- The task engineering prompt is sent as a user message directly to the existing conversation, reusing the active session.
- Metrics tracks this as a `conversation_reuse` event.

### 3. Spawn New
- If no session exists or the session metadata check fails/expires, the worker initiates a `new_conversation` command.
- The full project memory, task context, and system rules are compiled and transmitted as the starting conversation prompt.

### 4. Database Locking & Concurrency
- Only one worker can acquire the project-level database lock at a time.
- If a lock is held, other workers attempting to execute tasks in the same project block and return a `BLOCKED` state until the lock is released.
