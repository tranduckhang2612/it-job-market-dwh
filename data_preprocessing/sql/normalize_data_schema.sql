-- Vùng dữ liệu canonical sau role/language gate và chuẩn hóa.
-- Không tạo index phụ; PRIMARY KEY vẫn tạo index bắt buộc của PostgreSQL.

BEGIN;

CREATE SCHEMA IF NOT EXISTS normalize_data;

CREATE TABLE IF NOT EXISTS normalize_data.normalized_jobs (
    job_id                  TEXT NOT NULL,
    source                  TEXT NOT NULL CHECK (source IN ('itviec', 'topdev')),
    source_job_id           TEXT,
    job_url                 TEXT NOT NULL CHECK (btrim(job_url) <> ''),
    crawl_run_id            TEXT NOT NULL,
    observed_at             TIMESTAMPTZ NOT NULL,
    title                   TEXT NOT NULL CHECK (btrim(title) <> ''),
    company_name            TEXT,
    description             TEXT NOT NULL CHECK (btrim(description) <> ''),
    job_roles               JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(job_roles) = 'array'),
    seniority_levels        JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(seniority_levels) = 'array'),
    location_text           TEXT,
    cities                  JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(cities) = 'array'),
    work_mode               TEXT CHECK (work_mode IN ('onsite', 'hybrid', 'remote')),
    skills                  JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(skills) = 'array'),
    foreign_languages       JSONB NOT NULL
                            DEFAULT '[{"language":"unknown","level_text":null}]'::jsonb
                            CHECK (jsonb_typeof(foreign_languages) = 'array')
                            CHECK (jsonb_array_length(foreign_languages) > 0),
    soft_skills             JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(soft_skills) = 'array'),
    normalization_meta      JSONB NOT NULL DEFAULT '{}'::jsonb
                            CHECK (jsonb_typeof(normalization_meta) = 'object'),
    normalization_version   TEXT NOT NULL DEFAULT '5.1',
    normalized_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (job_id, crawl_run_id),
    FOREIGN KEY (job_id, crawl_run_id)
        REFERENCES raw.job_postings(job_id, crawl_run_id) ON DELETE RESTRICT,
    CHECK (job_id LIKE source || ':%'),
    CHECK (jsonb_array_length(job_roles) > 0)
);

COMMENT ON SCHEMA normalize_data IS
    'Dữ liệu canonical đã vượt gate và chuẩn hóa, trước bước staging/DWH.';
COMMENT ON TABLE normalize_data.normalized_jobs IS
    '17 cột nghiệp vụ cùng metadata giải thích; một dòng cho một job trong một crawl run.';

COMMIT;
