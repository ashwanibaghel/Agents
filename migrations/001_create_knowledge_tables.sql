-- ===================================================
-- SUPABASE SQL MIGRATION: Create Knowledge Tables
-- Run this in the Supabase Dashboard SQL Editor
-- Project: xrimbjoxmwqxryvxdojz
-- ===================================================

-- 1. task_artifacts: stores worker-generated artifacts with indexing lifecycle
CREATE TABLE IF NOT EXISTS public.task_artifacts (
    id               BIGSERIAL PRIMARY KEY,
    task_id          TEXT NOT NULL,
    name             TEXT NOT NULL,
    path             TEXT NOT NULL DEFAULT '',
    type             TEXT NOT NULL DEFAULT 'markdown',
    size             INTEGER NOT NULL DEFAULT 0,
    summary          TEXT,
    content          TEXT NOT NULL DEFAULT '',
    indexing_status  TEXT NOT NULL DEFAULT 'PENDING',
    retry_count      INTEGER DEFAULT 0,
    indexing_error   TEXT,
    next_retry_at    TIMESTAMPTZ,
    lease_expiration TIMESTAMPTZ,
    indexed_by       TEXT,
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(task_id, name)
);

-- 2. task_knowledge: stores chunked text from indexed artifacts
CREATE TABLE IF NOT EXISTS public.task_knowledge (
    id              BIGSERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL,
    name            TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL DEFAULT 0,
    chunk_text      TEXT NOT NULL,
    embedding       JSONB DEFAULT '[]'::jsonb,
    promoted_level  TEXT DEFAULT 'TASK',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Indexes for fast lookup
CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id
    ON public.task_artifacts(task_id);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_status
    ON public.task_artifacts(indexing_status);

CREATE INDEX IF NOT EXISTS idx_task_knowledge_task_id
    ON public.task_knowledge(task_id);

-- 4. Enable Row Level Security (RLS) - permissive for service role
ALTER TABLE public.task_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_knowledge ENABLE ROW LEVEL SECURITY;

CREATE POLICY IF NOT EXISTS "service_role_task_artifacts"
    ON public.task_artifacts FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY IF NOT EXISTS "service_role_task_knowledge"
    ON public.task_knowledge FOR ALL
    USING (auth.role() = 'service_role');

-- 5. Verify (run this after the above to confirm)
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
