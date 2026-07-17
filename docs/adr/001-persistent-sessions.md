# ADR 001: Persistent Session Manager

## Status
Accepted

## Context
The worker system interacts with Antigravity via conversational AI agents. In previous versions, each task execution initiated a fresh conversation session. This introduced significant latency (initiating connections, reloading project context, warm-up times) and consumed a high volume of API tokens as the entire project history and system prompt had to be re-transmitted for every single task. Additionally, it prevented the model from maintaining continuity or "memory" of previous actions in a multi-step sequence.

## Decision
We implemented a database-backed **Persistent Session Manager** (`control/project_runtime.py` / `workers/antigravity_worker.py`).
1. **SQLite Backend**: Session states, active conversation IDs, and lock flags are persisted in `state/task_checkpoints.db`.
2. **Conversation Reuse**: If an active session exists for a project, the worker resumes that conversation rather than spawning a new one.
3. **Session Expiry**: Sessions automatically expire if inactive for a configurable duration (default: 24 hours).
4. **Concurrency Safety (Workspace Locking)**: A row-level database lock prevents multiple workers from concurrently writing to the same workspace or sending conflicting messages to the same conversation.

## Alternatives Considered
- **Stateless/Fresh Conversations Only**: Rejected due to high token cost and lack of context continuity.
- **In-Memory Session Store**: Rejected because worker crashes or restarts would lose all session references, orphaned conversations would leak on the server, and multi-process workers couldn't share session states safely.
- **Redis/External Database**: Rejected to keep the implementation lightweight, self-contained, and zero-dependency beyond Python's standard library.

## Consequences
- **Continuity**: The model remembers recent context, improving its ability to carry out multi-step edits and iterations.
- **Performance**: Reduced prompt overhead and network latency.
- **Complexity**: Added the need for active session locking, state synchronization, and expiration monitoring.

## Future Considerations
- Supporting distributed setups where the SQLite database is replaced by a shared Postgres instance.
- Implementing proactive session warming or pre-emptive keep-alives.
