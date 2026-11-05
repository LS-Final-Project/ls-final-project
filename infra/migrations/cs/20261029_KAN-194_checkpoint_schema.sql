-- KAN-194: LangGraph checkpoint tables for the C/S processing graph, in checkpoint_cs.
-- Source: langgraph-checkpoint-postgres 3.2.0, langgraph/checkpoint/postgres/base.py
-- MIGRATIONS[0..9], written here in their final form so the app never runs setup():
--   * checkpoint_blobs.blob is nullable (MIGRATIONS[4]) and checkpoint_writes.task_path
--     exists (MIGRATIONS[9]) from the start.
--   * CREATE INDEX CONCURRENTLY (MIGRATIONS[6..8]) cannot run inside a transaction; the
--     tables are new and empty, so plain CREATE INDEX IF NOT EXISTS is used.
--   * checkpoint_migrations is created with v 0..9 so an accidental setup() is a no-op.
-- The saver only SELECTs and runs INSERT ... ON CONFLICT DO UPDATE / DO NOTHING. DELETE is
-- used only by delete_thread()/prune(), which the app does not call, so cs_app gets
-- SELECT, INSERT and UPDATE and never DELETE or TRUNCATE. The app connects with
-- options '-c search_path=checkpoint_cs' (the library uses unqualified table names).
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
-- 001 already creates the schema; this keeps databases without 001 (isolated tests) working.
CREATE SCHEMA IF NOT EXISTS checkpoint_cs AUTHORIZATION cs_migrator;
REVOKE ALL ON SCHEMA checkpoint_cs FROM PUBLIC;
GRANT USAGE ON SCHEMA checkpoint_cs TO cs_app;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS checkpoint_cs.checkpoint_migrations (
    v integer PRIMARY KEY
);
INSERT INTO checkpoint_cs.checkpoint_migrations (v)
SELECT v FROM generate_series(0, 9) AS v
ON CONFLICT (v) DO NOTHING;

CREATE TABLE IF NOT EXISTS checkpoint_cs.checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_cs.checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS checkpoint_cs.checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    task_path text NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx
    ON checkpoint_cs.checkpoints (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx
    ON checkpoint_cs.checkpoint_blobs (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx
    ON checkpoint_cs.checkpoint_writes (thread_id);

-- 001 default privileges also grant DELETE; the saver never deletes during a run.
GRANT SELECT, INSERT, UPDATE
    ON checkpoint_cs.checkpoints, checkpoint_cs.checkpoint_blobs, checkpoint_cs.checkpoint_writes
    TO cs_app;
REVOKE DELETE, TRUNCATE
    ON checkpoint_cs.checkpoints, checkpoint_cs.checkpoint_blobs, checkpoint_cs.checkpoint_writes
    FROM cs_app;
REVOKE ALL ON checkpoint_cs.checkpoint_migrations FROM cs_app;
COMMIT;
