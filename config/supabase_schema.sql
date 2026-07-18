    -- ══════════════════════════════════════════════════════════════════
    -- Ashwani Agent Company — Supabase Tasks Table Schema
    -- Run this once in your Supabase SQL editor.
    -- ══════════════════════════════════════════════════════════════════

    -- 1. Create tasks table
    CREATE TABLE IF NOT EXISTS public.tasks (
        id                  BIGSERIAL PRIMARY KEY,
        task_id             TEXT NOT NULL UNIQUE,
        project             TEXT NOT NULL,
        task_type           TEXT NOT NULL,
        objective           TEXT NOT NULL,
        context             TEXT DEFAULT '',
        acceptance_criteria JSONB DEFAULT '[]',
        constraints         JSONB DEFAULT '[]',
        validation_commands JSONB DEFAULT '[]',
        autonomy_level      INTEGER DEFAULT 2,

        -- Lifecycle
        status              TEXT NOT NULL DEFAULT 'inbox'
                            CHECK (status IN ('inbox','claimed','delegated','done','blocked','failed')),
        worker_id           TEXT,
        claimed_at          TIMESTAMPTZ,
        last_heartbeat_at   TIMESTAMPTZ,

        -- Evidence
        summary             TEXT,
        evidence_paths      JSONB DEFAULT '[]',
        files_changed       JSONB DEFAULT '[]',
        validation_results  JSONB DEFAULT '[]',
        error_message       TEXT,

        -- Timestamps
        created_at          TIMESTAMPTZ DEFAULT now(),
        updated_at          TIMESTAMPTZ DEFAULT now()
    );

    -- 2. Index for worker polling
    CREATE INDEX IF NOT EXISTS idx_tasks_status   ON public.tasks (status);
    CREATE INDEX IF NOT EXISTS idx_tasks_worker   ON public.tasks (worker_id, status);
    CREATE INDEX IF NOT EXISTS idx_tasks_task_id  ON public.tasks (task_id);

    -- 3. Enable Row Level Security
    ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;

    -- 4. RLS Policies
    -- Only the service_role key (used by bridge_server.py and local workers) can do anything.
    -- Anonymous / authenticated users (ChatGPT JWT) are denied all direct access.

    -- Allow service_role full access (it bypasses RLS by default in Supabase, but be explicit)
    -- No anon policy = anon gets nothing by default.

    -- If you want to allow authenticated users (e.g. future dashboard), add policies here:
    -- CREATE POLICY "auth_read_own" ON public.tasks
    --   FOR SELECT USING (auth.role() = 'authenticated');

    -- 5. Trigger: auto-update updated_at
    CREATE OR REPLACE FUNCTION public.set_updated_at()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS tasks_updated_at ON public.tasks;
    CREATE TRIGGER tasks_updated_at
        BEFORE UPDATE ON public.tasks
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


    -- ══════════════════════════════════════════════════════════════════
    -- Version 2 Upgrades: task_events and worker_status
    -- ══════════════════════════════════════════════════════════════════

    -- 6. Create task_events table
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

    -- 7. Create worker_status table
    CREATE TABLE IF NOT EXISTS public.worker_status (
        worker_id         TEXT PRIMARY KEY,
        last_heartbeat_at TIMESTAMPTZ DEFAULT now(),
        started_at        TIMESTAMPTZ DEFAULT now(),
        current_task_id   TEXT
    );

    ALTER TABLE public.worker_status ENABLE ROW LEVEL SECURITY;


    -- ══════════════════════════════════════════════════════════════════
    -- Version 3.3 Upgrades: task_artifacts and task_knowledge
    -- ══════════════════════════════════════════════════════════════════

    -- 8. Create task_artifacts table
    CREATE TABLE IF NOT EXISTS public.task_artifacts (
        id                BIGSERIAL PRIMARY KEY,
        task_id           TEXT NOT NULL REFERENCES public.tasks(task_id) ON DELETE CASCADE,
        name              TEXT NOT NULL,
        path              TEXT NOT NULL,
        type              TEXT NOT NULL,
        size              INTEGER NOT NULL,
        summary           TEXT,
        content           TEXT NOT NULL,
        indexing_status   TEXT DEFAULT 'PENDING',
        indexing_error    TEXT,
        indexed_at        TIMESTAMPTZ,
        retry_count       INTEGER DEFAULT 0,
        last_retry_at     TIMESTAMPTZ,
        next_retry_at     TIMESTAMPTZ,
        claimed_by        TEXT,
        claimed_at        TIMESTAMPTZ,
        lease_expiration  TIMESTAMPTZ,
        created_at        TIMESTAMPTZ DEFAULT now(),
        updated_at        TIMESTAMPTZ DEFAULT now(),
        UNIQUE(task_id, name)
    );

    CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id ON public.task_artifacts (task_id);
    CREATE INDEX IF NOT EXISTS idx_task_artifacts_indexing_status ON public.task_artifacts (indexing_status);
    ALTER TABLE public.task_artifacts ENABLE ROW LEVEL SECURITY;

    -- 9. Create task_knowledge table for semantic search
    CREATE TABLE IF NOT EXISTS public.task_knowledge (
        id             BIGSERIAL PRIMARY KEY,
        task_id        TEXT NOT NULL REFERENCES public.tasks(task_id) ON DELETE CASCADE,
        name           TEXT NOT NULL,
        chunk_index    INTEGER NOT NULL,
        chunk_text     TEXT NOT NULL,
        embedding      JSONB NOT NULL,
        promoted_level TEXT DEFAULT 'TASK',
        created_at     TIMESTAMPTZ DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS idx_task_knowledge_task_id ON public.task_knowledge (task_id);
    CREATE INDEX IF NOT EXISTS idx_task_knowledge_promoted_level ON public.task_knowledge (promoted_level);
    ALTER TABLE public.task_knowledge ENABLE ROW LEVEL SECURITY;
