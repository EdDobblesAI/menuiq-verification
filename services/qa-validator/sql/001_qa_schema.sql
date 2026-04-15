-- filename: sql/001_qa_schema.sql

BEGIN;

CREATE SCHEMA IF NOT EXISTS qa;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'qa_failure_reason'
          AND n.nspname = 'qa'
    ) THEN
        CREATE TYPE qa.qa_failure_reason AS ENUM (
            'not_a_menu',
            'wrong_venue',
            'stale_content',
            'no_beverage_content',
            'fetch_failed',
            'boilerplate_only',
            'aggregator_page',
            'pass'
        );
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS qa.qa_sample_runs (
    id BIGSERIAL PRIMARY KEY,
    run_type TEXT NOT NULL CHECK (run_type IN ('sample', 'full')),
    sample_size INTEGER NOT NULL CHECK (sample_size > 0),
    state_filter TEXT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    pass_rate DOUBLE PRECISION NULL CHECK (pass_rate >= 0 AND pass_rate <= 1),
    mean_composite_score DOUBLE PRECISION NULL CHECK (mean_composite_score >= 0 AND mean_composite_score <= 1),
    requested_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id)
);

CREATE TABLE IF NOT EXISTS qa.qa_run_items (
    run_id BIGINT NOT NULL REFERENCES qa.qa_sample_runs(id) ON DELETE CASCADE,
    seq_id BIGINT NOT NULL,
    venue_id BIGINT NOT NULL,
    state TEXT NULL,
    website_url TEXT NOT NULL,
    domain TEXT NULL,
    item_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (item_status IN ('pending', 'processing', 'completed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    processed_at TIMESTAMPTZ NULL,
    PRIMARY KEY (run_id, seq_id),
    UNIQUE (run_id, venue_id)
);

CREATE INDEX IF NOT EXISTS idx_qa_run_items_run_status_seq
    ON qa.qa_run_items(run_id, item_status, seq_id);

CREATE INDEX IF NOT EXISTS idx_qa_run_items_domain
    ON qa.qa_run_items(run_id, domain);

CREATE TABLE IF NOT EXISTS qa.qa_results (
    id BIGSERIAL PRIMARY KEY,
    venue_id BIGINT NOT NULL,
    sample_run_id BIGINT NOT NULL REFERENCES qa.qa_sample_runs(id) ON DELETE CASCADE,
    freshness_score DOUBLE PRECISION NOT NULL CHECK (freshness_score >= 0 AND freshness_score <= 1),
    venue_accuracy_score DOUBLE PRECISION NOT NULL CHECK (venue_accuracy_score >= 0 AND venue_accuracy_score <= 1),
    menu_validity_score DOUBLE PRECISION NOT NULL CHECK (menu_validity_score >= 0 AND menu_validity_score <= 1),
    beverage_relevance_score DOUBLE PRECISION NOT NULL CHECK (beverage_relevance_score >= 0 AND beverage_relevance_score <= 1),
    composite_score DOUBLE PRECISION NOT NULL CHECK (composite_score >= 0 AND composite_score <= 1),
    failure_reason qa.qa_failure_reason NOT NULL,
    validated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    live_fetch_status INTEGER NULL,
    live_url TEXT NULL,
    live_domain TEXT NULL,
    llm_used BOOLEAN NOT NULL DEFAULT FALSE,
    debug_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (sample_run_id, venue_id)
);

CREATE INDEX IF NOT EXISTS idx_qa_results_run
    ON qa.qa_results(sample_run_id);

CREATE INDEX IF NOT EXISTS idx_qa_results_validated_at
    ON qa.qa_results(validated_at DESC);

CREATE INDEX IF NOT EXISTS idx_qa_results_failure_reason
    ON qa.qa_results(failure_reason);

CREATE INDEX IF NOT EXISTS idx_qa_results_live_domain
    ON qa.qa_results(live_domain);

CREATE TABLE IF NOT EXISTS qa.qa_daily_reports (
    report_date DATE PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES qa.qa_sample_runs(id) ON DELETE CASCADE,
    report_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qa_daily_reports_run_id
    ON qa.qa_daily_reports(run_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_qa_single_active_run
    ON qa.qa_sample_runs ((status))
    WHERE status IN ('pending', 'running');

COMMIT;
