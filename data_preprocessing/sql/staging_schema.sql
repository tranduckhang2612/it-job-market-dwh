-- Staging legacy: một cấu trúc 30 cột thống nhất cho ITviec và TopDev.
-- Các trường nhiều giá trị dùng JSONB; không tạo index phụ.

BEGIN;

CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS staging.normalized_jobs (
    job_id                  TEXT NOT NULL,
    source                  TEXT NOT NULL CHECK (source IN ('itviec', 'topdev')),
    source_job_id           TEXT,
    job_url                 TEXT NOT NULL CHECK (btrim(job_url) <> ''),
    crawl_run_id            TEXT NOT NULL,
    observed_at             TIMESTAMPTZ NOT NULL,
    published_at            TIMESTAMPTZ,
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
    required_knowledge      JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(required_knowledge) = 'array'),
    experience_text         TEXT,
    experience_min_years    NUMERIC,
    experience_max_years    NUMERIC,
    education_requirements  JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(education_requirements) = 'array'),
    foreign_languages       JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(foreign_languages) = 'array'),
    soft_skills             JSONB NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(soft_skills) = 'array'),
    salary_text             TEXT,
    salary_min              NUMERIC,
    salary_max              NUMERIC,
    salary_currency         TEXT CHECK (salary_currency IS NULL OR salary_currency ~ '^[A-Z]{3}$'),
    salary_period           TEXT CHECK (salary_period IS NULL OR salary_period IN ('hour', 'day', 'month', 'year')),
    salary_basis            TEXT CHECK (salary_basis IS NULL OR salary_basis IN ('gross', 'net')),
    salary_status           TEXT NOT NULL
                            CHECK (salary_status IN ('disclosed', 'negotiable', 'not_disclosed', 'unknown')),
    normalization_version   TEXT NOT NULL DEFAULT '3.0',
    normalized_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (job_id, crawl_run_id),
    FOREIGN KEY (job_id, crawl_run_id)
        REFERENCES raw.job_postings(job_id, crawl_run_id) ON DELETE RESTRICT,
    CHECK (job_id LIKE source || ':%'),
    CHECK (jsonb_array_length(job_roles) > 0),
    CHECK (experience_min_years IS NULL OR experience_min_years >= 0),
    CHECK (experience_max_years IS NULL OR experience_max_years >= 0),
    CHECK (experience_min_years IS NULL OR experience_max_years IS NULL
           OR experience_min_years <= experience_max_years),
    CHECK (salary_min IS NULL OR salary_min >= 0),
    CHECK (salary_max IS NULL OR salary_max >= 0),
    CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max),
    CHECK (
        (salary_status = 'disclosed' AND (salary_min IS NOT NULL OR salary_max IS NOT NULL))
        OR
        (salary_status <> 'disclosed' AND salary_min IS NULL AND salary_max IS NULL)
    )
);

COMMENT ON TABLE staging.normalized_jobs IS
    'Hợp đồng 30 cột tương thích; dữ liệu canonical mới được lưu ở normalize_data.';

COMMIT;
