-- Cấu trúc duy nhất mà hai crawler ITviec và TopDev ghi vào.
-- File này không tạo index riêng; PRIMARY KEY vẫn tạo index bắt buộc của PostgreSQL.

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.crawl_runs (
    crawl_run_id       TEXT PRIMARY KEY CHECK (btrim(crawl_run_id) <> ''),
    source             TEXT NOT NULL CHECK (source IN ('itviec', 'topdev')),
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ,
    requested_count    INTEGER NOT NULL CHECK (requested_count > 0),
    collected_count    INTEGER NOT NULL DEFAULT 0 CHECK (collected_count >= 0),
    status             TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'interrupted')),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb
                       CHECK (jsonb_typeof(metadata) = 'object'),
    errors             JSONB NOT NULL DEFAULT '[]'::jsonb
                       CHECK (jsonb_typeof(errors) = 'array'),
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS raw.job_postings (
    job_id             TEXT NOT NULL CHECK (
                           (job_id LIKE 'itviec:%' AND length(job_id) > 7)
                           OR (job_id LIKE 'topdev:%' AND length(job_id) > 7)
                       ),
    crawl_run_id       TEXT NOT NULL
                       REFERENCES raw.crawl_runs(crawl_run_id) ON DELETE RESTRICT,
    source_job_id      TEXT,
    job_url            TEXT NOT NULL CHECK (btrim(job_url) <> ''),
    observed_at        TIMESTAMPTZ NOT NULL,
    raw_payload        JSONB NOT NULL CHECK (jsonb_typeof(raw_payload) = 'object'),
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (job_id, crawl_run_id)
);

COMMENT ON TABLE raw.crawl_runs IS
    'Metadata, trạng thái và lỗi của từng lần crawl ITviec hoặc TopDev.';
COMMENT ON TABLE raw.job_postings IS
    'JSON gốc của mỗi tin tại từng lần quan sát; chưa làm sạch hoặc chuyển đổi.';

COMMIT;
