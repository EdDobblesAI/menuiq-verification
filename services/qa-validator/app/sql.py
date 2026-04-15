CREATE_SAMPLE_RUN_SQL = """
INSERT INTO qa.qa_sample_runs (run_type, sample_size, state_filter, status, requested_by)
VALUES ($1, $2, $3, 'pending', $4)
RETURNING id;
"""

ACTIVE_RUN_SQL = """
SELECT id, status
FROM qa.qa_sample_runs
WHERE status IN ('pending', 'running')
ORDER BY created_at DESC
LIMIT 1;
"""

MARK_RUN_RUNNING_SQL = """
UPDATE qa.qa_sample_runs
SET status = 'running', started_at = COALESCE(started_at, NOW())
WHERE id = $1;
"""

MARK_RUN_COMPLETED_SQL = """
UPDATE qa.qa_sample_runs
SET status = 'completed',
    completed_at = NOW(),
    pass_rate = $2,
    mean_composite_score = $3
WHERE id = $1;
"""

MARK_RUN_FAILED_SQL = """
UPDATE qa.qa_sample_runs
SET status = 'failed',
    completed_at = NOW()
WHERE id = $1;
"""

GET_RUN_SQL = """
SELECT id, run_type, sample_size, state_filter, status, started_at, completed_at
FROM qa.qa_sample_runs
WHERE id = $1;
"""

RUN_STATUS_COUNTS_SQL = """
SELECT
    COUNT(*) FILTER (WHERE item_status = 'completed') AS processed_count,
    COUNT(*) FILTER (WHERE item_status IN ('pending', 'processing', 'failed')) AS remaining_count
FROM qa.qa_run_items
WHERE run_id = $1;
"""

PASS_RATE_SO_FAR_SQL = """
SELECT
    AVG(CASE WHEN failure_reason = 'pass' THEN 1.0 ELSE 0.0 END) AS pass_rate
FROM qa.qa_results
WHERE sample_run_id = $1;
"""

REPORT_LATEST_SQL = """
SELECT report_date, run_id, report_json, created_at
FROM qa.qa_daily_reports
ORDER BY report_date DESC
LIMIT 1;
"""

REPORT_BY_DATE_SQL = """
SELECT report_date, run_id, report_json, created_at
FROM qa.qa_daily_reports
WHERE report_date = $1;
"""

LATEST_CAPTURE_SQL = """
SELECT
    v.id AS venue_id,
    v.name,
    v.address,
    v.city,
    v.state,
    v.website_url,
    ma.captured_text,
    ma.artifact_url,
    ma.captured_at,
    fmc.capture_status
FROM venues v
JOIN LATERAL (
    SELECT venue_id, captured_text, artifact_url, captured_at
    FROM menu_artifacts
    WHERE venue_id = v.id
      AND captured_text IS NOT NULL
      AND length(trim(captured_text)) > 0
    ORDER BY captured_at DESC, id DESC
    LIMIT 1
) ma ON TRUE
LEFT JOIN LATERAL (
    SELECT venue_id, capture_status, captured_at
    FROM analytics.fact_menu_captures
    WHERE venue_id = v.id
    ORDER BY captured_at DESC
    LIMIT 1
) fmc ON TRUE
WHERE v.id = $1;
"""

ELIGIBLE_FULL_RUN_ITEMS_SQL = """
WITH eligible AS (
    SELECT
        tv.venue_id,
        v.state,
        v.website_url,
        regexp_replace(split_part(regexp_replace(v.website_url, '^https?://', ''), '/', 1), '^www\\.', '') AS domain
    FROM analytics.trusted_venues tv
    JOIN venues v ON v.id = tv.venue_id
    JOIN LATERAL (
        SELECT 1
        FROM menu_artifacts ma
        WHERE ma.venue_id = v.id
          AND ma.captured_text IS NOT NULL
          AND length(trim(ma.captured_text)) > 0
        LIMIT 1
    ) cap ON TRUE
    WHERE v.url_classification = 'ok'
      AND v.website_url IS NOT NULL
      AND ($2::text IS NULL OR v.state = $2)
),
ordered AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY state NULLS LAST, venue_id) AS seq_id,
        venue_id,
        state,
        website_url,
        domain
    FROM eligible
)
INSERT INTO qa.qa_run_items (run_id, seq_id, venue_id, state, website_url, domain)
SELECT $1, seq_id, venue_id, state, website_url, domain
FROM ordered
ON CONFLICT (run_id, venue_id) DO NOTHING;
"""

STRATIFIED_SAMPLE_RUN_ITEMS_SQL = """
WITH eligible AS (
    SELECT
        tv.venue_id,
        v.state,
        v.website_url,
        regexp_replace(split_part(regexp_replace(v.website_url, '^https?://', ''), '/', 1), '^www\\.', '') AS domain
    FROM analytics.trusted_venues tv
    JOIN venues v ON v.id = tv.venue_id
    JOIN LATERAL (
        SELECT 1
        FROM menu_artifacts ma
        WHERE ma.venue_id = v.id
          AND ma.captured_text IS NOT NULL
          AND length(trim(ma.captured_text)) > 0
        LIMIT 1
    ) cap ON TRUE
    WHERE v.url_classification = 'ok'
      AND v.website_url IS NOT NULL
      AND ($3::text IS NULL OR v.state = $3)
),
state_counts AS (
    SELECT state, COUNT(*)::bigint AS n
    FROM eligible
    GROUP BY state
),
totals AS (
    SELECT SUM(n)::bigint AS total_n
    FROM state_counts
),
base_alloc AS (
    SELECT
        sc.state,
        sc.n,
        (($2::numeric * sc.n::numeric) / NULLIF(t.total_n, 0)) AS exact_alloc,
        FLOOR(($2::numeric * sc.n::numeric) / NULLIF(t.total_n, 0))::int AS base_take
    FROM state_counts sc
    CROSS JOIN totals t
),
remainder AS (
    SELECT GREATEST($2 - COALESCE(SUM(base_take), 0), 0)::int AS r
    FROM base_alloc
),
ranked_states AS (
    SELECT
        ba.state,
        ba.n,
        ba.base_take,
        (ba.exact_alloc - ba.base_take) AS frac_part,
        ROW_NUMBER() OVER (ORDER BY (ba.exact_alloc - ba.base_take) DESC, ba.n DESC, ba.state) AS rn
    FROM base_alloc ba
),
final_alloc AS (
    SELECT
        rs.state,
        LEAST(
            rs.n::int,
            rs.base_take + CASE WHEN rs.rn <= (SELECT r FROM remainder) THEN 1 ELSE 0 END
        ) AS take_n
    FROM ranked_states rs
),
sampled AS (
    SELECT
        e.state,
        e.venue_id,
        e.website_url,
        e.domain,
        ROW_NUMBER() OVER (PARTITION BY e.state ORDER BY random()) AS state_row
    FROM eligible e
),
picked AS (
    SELECT
        s.state,
        s.venue_id,
        s.website_url,
        s.domain
    FROM sampled s
    JOIN final_alloc fa ON fa.state = s.state
    WHERE s.state_row <= fa.take_n
),
ordered AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY state NULLS LAST, venue_id) AS seq_id,
        venue_id,
        state,
        website_url,
        domain
    FROM picked
)
INSERT INTO qa.qa_run_items (run_id, seq_id, venue_id, state, website_url, domain)
SELECT $1, seq_id, venue_id, state, website_url, domain
FROM ordered
ON CONFLICT (run_id, venue_id) DO NOTHING;
"""

GET_PENDING_ITEMS_SQL = """
SELECT run_id, seq_id, venue_id, state, website_url, domain
FROM qa.qa_run_items
WHERE run_id = $1
  AND seq_id > $2
ORDER BY seq_id
LIMIT $3;
"""

UPSERT_RESULTS_UNNEST_SQL = """
INSERT INTO qa.qa_results (
    venue_id,
    sample_run_id,
    freshness_score,
    venue_accuracy_score,
    menu_validity_score,
    beverage_relevance_score,
    composite_score,
    failure_reason,
    validated_at,
    live_fetch_status,
    live_url,
    live_domain,
    llm_used,
    debug_meta
)
SELECT *
FROM unnest(
    $1::bigint[],
    $2::bigint[],
    $3::double precision[],
    $4::double precision[],
    $5::double precision[],
    $6::double precision[],
    $7::double precision[],
    $8::qa.qa_failure_reason[],
    $9::timestamptz[],
    $10::integer[],
    $11::text[],
    $12::text[],
    $13::boolean[],
    $14::jsonb[]
)
ON CONFLICT (sample_run_id, venue_id)
DO UPDATE SET
    freshness_score = EXCLUDED.freshness_score,
    venue_accuracy_score = EXCLUDED.venue_accuracy_score,
    menu_validity_score = EXCLUDED.menu_validity_score,
    beverage_relevance_score = EXCLUDED.beverage_relevance_score,
    composite_score = EXCLUDED.composite_score,
    failure_reason = EXCLUDED.failure_reason,
    validated_at = EXCLUDED.validated_at,
    live_fetch_status = EXCLUDED.live_fetch_status,
    live_url = EXCLUDED.live_url,
    live_domain = EXCLUDED.live_domain,
    llm_used = EXCLUDED.llm_used,
    debug_meta = EXCLUDED.debug_meta;
"""

BULK_COMPLETE_RUN_ITEMS_SQL = """
UPDATE qa.qa_run_items q
SET item_status = x.item_status,
    attempts = q.attempts + 1,
    processed_at = x.processed_at
FROM (
    SELECT *
    FROM unnest(
        $1::bigint[],
        $2::bigint[],
        $3::text[],
        $4::timestamptz[]
    ) AS t(run_id, seq_id, item_status, processed_at)
) x
WHERE q.run_id = x.run_id
  AND q.seq_id = x.seq_id;
"""

# Adapted to the actual pipeline_job_progress schema, which uses a single
# job_name column (no separate run_id column). We encode the run_id into
# job_name as "{pipeline_name}:{run_id}" so each QA run has an isolated row.
# Assumes job_name carries a unique constraint (standard for this table).
UPSERT_CHECKPOINT_SQL = """
INSERT INTO pipeline_job_progress (job_name, status, last_processed_id, started_at, updated_at, metadata)
VALUES ($1, 'running', $2, NOW(), NOW(), $3::jsonb)
ON CONFLICT (job_name)
DO UPDATE SET
    last_processed_id = EXCLUDED.last_processed_id,
    updated_at = NOW(),
    metadata = EXCLUDED.metadata;
"""

GET_CHECKPOINT_SQL = """
SELECT last_processed_id
FROM pipeline_job_progress
WHERE job_name = $1;
"""

GENERATE_DAILY_REPORT_SQL = """
WITH target_run AS (
    SELECT id
    FROM qa.qa_sample_runs
    WHERE status = 'completed'
      AND started_at::date = $1::date
    ORDER BY completed_at DESC NULLS LAST, id DESC
    LIMIT 1
),
base AS (
    SELECT *
    FROM qa.qa_results
    WHERE sample_run_id = (SELECT id FROM target_run)
),
domains AS (
    SELECT
        COALESCE(live_domain, split_part(regexp_replace(live_url, '^https?://', ''), '/', 1), 'unknown') AS domain,
        COUNT(*) AS fail_count
    FROM base
    WHERE failure_reason <> 'pass'
    GROUP BY 1
    ORDER BY fail_count DESC, domain
    LIMIT 10
),
reason_dist AS (
    SELECT failure_reason::text AS reason, COUNT(*) AS cnt
    FROM base
    GROUP BY 1
)
SELECT jsonb_build_object(
    'pass_rate', COALESCE(AVG(CASE WHEN failure_reason = 'pass' THEN 1.0 ELSE 0.0 END), 0.0),
    'mean_score', COALESCE(AVG(composite_score), 0.0),
    'top_failure_domains', COALESCE((SELECT jsonb_agg(jsonb_build_object('domain', domain, 'count', fail_count)) FROM domains), '[]'::jsonb),
    'failure_reason_distribution', COALESCE((SELECT jsonb_object_agg(reason, cnt) FROM reason_dist), '{}'::jsonb),
    'stale_captures_count', COALESCE(SUM(CASE WHEN failure_reason = 'stale_content' THEN 1 ELSE 0 END), 0),
    'beverage_relevance_rate', COALESCE(AVG(CASE WHEN beverage_relevance_score >= 0.5 THEN 1.0 ELSE 0.0 END), 0.0),
    'sample_size', COUNT(*),
    'run_id', (SELECT id FROM target_run)
) AS report_json
FROM base;
"""

UPSERT_DAILY_REPORT_SQL = """
INSERT INTO qa.qa_daily_reports (report_date, run_id, report_json)
VALUES ($1, $2, $3::jsonb)
ON CONFLICT (report_date)
DO UPDATE SET
    run_id = EXCLUDED.run_id,
    report_json = EXCLUDED.report_json,
    created_at = NOW();
"""
