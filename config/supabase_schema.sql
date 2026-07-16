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
