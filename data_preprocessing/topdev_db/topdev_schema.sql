-- Luồng dữ liệu TopDev: crawl -> raw JSONB -> làm sạch/ánh xạ cấu trúc chung -> staging.
-- Tương thích hoàn toàn với schema của ITviec; có thể áp dụng trên database riêng (topdev)
-- hoặc dùng chung database với ITviec mà không xung đột hay mất dữ liệu cũ.

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;

-- 1. BẢNG QUẢN LÝ LẦN CRAWL
CREATE TABLE IF NOT EXISTS raw.crawl_runs (
    crawl_run_id       TEXT PRIMARY KEY CHECK (btrim(crawl_run_id) <> ''),
    source             TEXT NOT NULL DEFAULT 'topdev' CHECK (source IN ('itviec', 'topdev')),
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ,
    requested_count    INTEGER NOT NULL CHECK (requested_count > 0),
    collected_count    INTEGER NOT NULL DEFAULT 0 CHECK (collected_count >= 0),
    status             TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'interrupted')),
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    errors             JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(errors) = 'array'),
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

-- 2. BẢNG LƯU TIN GỐC (RAW PAYLOAD)
CREATE TABLE IF NOT EXISTS raw.job_postings (
    job_id             TEXT NOT NULL CHECK (
                           (job_id LIKE 'topdev:%' AND length(job_id) > 7)
                           OR
                           (job_id LIKE 'itviec:%' AND length(job_id) > 7)
                       ),
    crawl_run_id       TEXT NOT NULL REFERENCES raw.crawl_runs(crawl_run_id) ON DELETE RESTRICT,
    source_job_id      TEXT,
    job_url            TEXT NOT NULL CHECK (btrim(job_url) <> ''),
    observed_at        TIMESTAMPTZ NOT NULL,
    raw_payload        JSONB NOT NULL CHECK (jsonb_typeof(raw_payload) = 'object'),
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (job_id, crawl_run_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_job_postings_run ON raw.job_postings(crawl_run_id);
CREATE INDEX IF NOT EXISTS idx_raw_job_postings_observed ON raw.job_postings(observed_at);

-- Cập nhật ràng buộc nếu bảng đã tồn tại từ trước (khi chạy chung database với ITviec)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'raw.crawl_runs'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%source%'
          AND pg_get_constraintdef(oid) NOT LIKE '%topdev%'
    ) THEN
        ALTER TABLE raw.crawl_runs DROP CONSTRAINT IF EXISTS crawl_runs_source_check;
        ALTER TABLE raw.crawl_runs ADD CONSTRAINT crawl_runs_source_check CHECK (source IN ('itviec', 'topdev'));
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'raw.job_postings'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%job_id%'
          AND pg_get_constraintdef(oid) NOT LIKE '%topdev%'
    ) THEN
        ALTER TABLE raw.job_postings DROP CONSTRAINT IF EXISTS job_postings_job_id_check;
        ALTER TABLE raw.job_postings ADD CONSTRAINT job_postings_job_id_check CHECK (
            (job_id LIKE 'topdev:%' AND length(job_id) > 7)
            OR
            (job_id LIKE 'itviec:%' AND length(job_id) > 7)
        );
    END IF;
END $$;

-- 3. BẢNG STAGING CHÍNH (JOB OBSERVATIONS)
CREATE TABLE IF NOT EXISTS staging.job_observations (
    observation_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id                TEXT NOT NULL,
    source                TEXT NOT NULL DEFAULT 'topdev' CHECK (source IN ('itviec', 'topdev')),
    source_job_id         TEXT,
    job_url               TEXT NOT NULL CHECK (btrim(job_url) <> ''),
    crawl_run_id          TEXT NOT NULL,
    observed_at           TIMESTAMPTZ NOT NULL,
    published_at          TIMESTAMPTZ,
    title                 TEXT NOT NULL CHECK (btrim(title) <> ''),
    company_name          TEXT,
    description           TEXT NOT NULL CHECK (btrim(description) <> ''),
    location_text         TEXT,
    work_mode             TEXT CHECK (work_mode IN ('onsite', 'hybrid', 'remote')),
    experience_text       TEXT,
    experience_min_years  NUMERIC,
    experience_max_years  NUMERIC,
    salary_text           TEXT,
    salary_min            NUMERIC,
    salary_max            NUMERIC,
    salary_currency       TEXT CHECK (salary_currency ~ '^[A-Z]{3}$'),
    salary_period         TEXT CHECK (btrim(salary_period) <> ''),
    salary_basis          TEXT CHECK (salary_basis IN ('gross', 'net')),
    salary_status         TEXT NOT NULL
                          CHECK (salary_status IN ('disclosed', 'negotiable', 'not_disclosed', 'unknown')),
    normalization_version TEXT NOT NULL DEFAULT '1.2' CHECK (btrim(normalization_version) <> ''),
    normalized_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (job_id, crawl_run_id),
    FOREIGN KEY (job_id, crawl_run_id)
        REFERENCES raw.job_postings(job_id, crawl_run_id) ON DELETE RESTRICT,
    CHECK (experience_min_years IS NULL OR
           (experience_min_years >= 0 AND experience_min_years < 'Infinity'::numeric)),
    CHECK (experience_max_years IS NULL OR
           (experience_max_years >= 0 AND experience_max_years < 'Infinity'::numeric)),
    CHECK (experience_min_years IS NULL OR experience_max_years IS NULL OR
           experience_min_years <= experience_max_years),
    CHECK (salary_min IS NULL OR (salary_min >= 0 AND salary_min < 'Infinity'::numeric)),
    CHECK (salary_max IS NULL OR (salary_max >= 0 AND salary_max < 'Infinity'::numeric)),
    CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max),
    CHECK (
        (salary_status = 'disclosed' AND (salary_min IS NOT NULL OR salary_max IS NOT NULL))
        OR
        (salary_status <> 'disclosed' AND salary_min IS NULL AND salary_max IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_job_observations_run ON staging.job_observations(crawl_run_id);
CREATE INDEX IF NOT EXISTS idx_job_observations_observed ON staging.job_observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_job_observations_latest
    ON staging.job_observations(job_id, observed_at DESC, observation_id DESC);

-- Cập nhật ràng buộc staging.job_observations cho topdev
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'staging.job_observations'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%source%'
          AND pg_get_constraintdef(oid) NOT LIKE '%topdev%'
    ) THEN
        ALTER TABLE staging.job_observations DROP CONSTRAINT IF EXISTS job_observations_source_check;
        ALTER TABLE staging.job_observations ADD CONSTRAINT job_observations_source_check CHECK (source IN ('itviec', 'topdev'));
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'staging.job_observations'::regclass
          AND conname = 'job_observations_ready_values'
          AND pg_get_constraintdef(oid) NOT LIKE '%topdev%'
    ) THEN
        ALTER TABLE staging.job_observations DROP CONSTRAINT IF EXISTS job_observations_ready_values;
        ALTER TABLE staging.job_observations ADD CONSTRAINT job_observations_ready_values CHECK (
            (
                (job_id LIKE 'topdev:%' AND length(job_id) > 7
                 AND (source_job_id IS NULL OR (btrim(source_job_id) <> '' AND job_id = 'topdev:' || source_job_id)))
                OR
                (job_id LIKE 'itviec:%' AND length(job_id) > 7
                 AND (source_job_id IS NULL OR (btrim(source_job_id) <> '' AND job_id = 'itviec:' || source_job_id)))
            )
            AND (company_name IS NULL OR btrim(company_name) <> '')
            AND (location_text IS NULL OR btrim(location_text) <> '')
            AND (experience_text IS NULL OR btrim(experience_text) <> '')
            AND (salary_text IS NULL OR btrim(salary_text) <> '')
            AND (salary_min IS NULL OR salary_min > 0)
            AND (salary_max IS NULL OR salary_max > 0)
        ) NOT VALID;
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'staging.job_observations'::regclass
          AND conname = 'job_observations_ready_values'
    ) THEN
        ALTER TABLE staging.job_observations ADD CONSTRAINT job_observations_ready_values CHECK (
            (
                (job_id LIKE 'topdev:%' AND length(job_id) > 7
                 AND (source_job_id IS NULL OR (btrim(source_job_id) <> '' AND job_id = 'topdev:' || source_job_id)))
                OR
                (job_id LIKE 'itviec:%' AND length(job_id) > 7
                 AND (source_job_id IS NULL OR (btrim(source_job_id) <> '' AND job_id = 'itviec:' || source_job_id)))
            )
            AND (company_name IS NULL OR btrim(company_name) <> '')
            AND (location_text IS NULL OR btrim(location_text) <> '')
            AND (experience_text IS NULL OR btrim(experience_text) <> '')
            AND (salary_text IS NULL OR btrim(salary_text) <> '')
            AND (salary_min IS NULL OR salary_min > 0)
            AND (salary_max IS NULL OR salary_max > 0)
        ) NOT VALID;
    END IF;
END $$;

-- 4. CÁC BẢNG STAGING CON (MẢNG DỮ LIỆU ĐƯỢC CHUẨN HOÁ)
CREATE TABLE IF NOT EXISTS staging.job_roles (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    role           TEXT NOT NULL CHECK (btrim(role) <> ''),
    PRIMARY KEY (observation_id, role),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS staging.job_seniority_levels (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    seniority_level TEXT NOT NULL CHECK (btrim(seniority_level) <> ''),
    PRIMARY KEY (observation_id, seniority_level),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS staging.job_cities (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    city           TEXT NOT NULL CHECK (btrim(city) <> ''),
    PRIMARY KEY (observation_id, city),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS staging.job_skills (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    name           TEXT NOT NULL CHECK (btrim(name) <> ''),
    category       TEXT NOT NULL CHECK (btrim(category) <> ''),
    PRIMARY KEY (observation_id, name),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS staging.job_required_knowledge (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    knowledge      TEXT NOT NULL CHECK (btrim(knowledge) <> ''),
    PRIMARY KEY (observation_id, knowledge),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS staging.job_education_requirements (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    degree         TEXT CHECK (btrim(degree) <> ''),
    PRIMARY KEY (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS staging.job_education_majors (
    observation_id BIGINT NOT NULL,
    education_order INTEGER NOT NULL,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    major          TEXT NOT NULL CHECK (btrim(major) <> ''),
    PRIMARY KEY (observation_id, education_order, major),
    UNIQUE (observation_id, education_order, item_order),
    FOREIGN KEY (observation_id, education_order)
        REFERENCES staging.job_education_requirements(observation_id, item_order) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS staging.job_foreign_languages (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    language       TEXT NOT NULL CHECK (btrim(language) <> ''),
    level_text     TEXT,
    PRIMARY KEY (observation_id, language),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS staging.job_soft_skills (
    observation_id BIGINT NOT NULL REFERENCES staging.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    soft_skill     TEXT NOT NULL CHECK (btrim(soft_skill) <> ''),
    PRIMARY KEY (observation_id, soft_skill),
    UNIQUE (observation_id, item_order)
);

CREATE INDEX IF NOT EXISTS idx_job_skills_name ON staging.job_skills(name, observation_id);
CREATE INDEX IF NOT EXISTS idx_job_cities_city ON staging.job_cities(city, observation_id);
CREATE INDEX IF NOT EXISTS idx_job_roles_role ON staging.job_roles(role, observation_id);

-- 5. VIEWS ÁNH XẠ ĐỦ 30 TRƯỜNG CHUNG CHO DWH
CREATE OR REPLACE VIEW staging.jobs AS
SELECT
    j.job_id,
    j.source,
    j.source_job_id,
    j.job_url,
    j.crawl_run_id,
    j.observed_at,
    j.published_at,
    j.title,
    j.company_name,
    j.description,
    COALESCE((SELECT jsonb_agg(x.role ORDER BY x.item_order)
              FROM staging.job_roles x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS job_roles,
    COALESCE((SELECT jsonb_agg(x.seniority_level ORDER BY x.item_order)
              FROM staging.job_seniority_levels x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS seniority_levels,
    j.location_text,
    COALESCE((SELECT jsonb_agg(x.city ORDER BY x.item_order)
              FROM staging.job_cities x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS cities,
    j.work_mode,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                  'name', x.name, 'category', x.category)
                  ORDER BY x.item_order)
              FROM staging.job_skills x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS skills,
    COALESCE((SELECT jsonb_agg(x.knowledge ORDER BY x.item_order)
              FROM staging.job_required_knowledge x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS required_knowledge,
    j.experience_text,
    j.experience_min_years,
    j.experience_max_years,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                  'degree', e.degree,
                  'majors', COALESCE((SELECT jsonb_agg(m.major ORDER BY m.item_order)
                                      FROM staging.job_education_majors m
                                      WHERE m.observation_id = e.observation_id
                                        AND m.education_order = e.item_order), '[]'::jsonb)) ORDER BY e.item_order)
              FROM staging.job_education_requirements e
              WHERE e.observation_id = j.observation_id), '[]'::jsonb) AS education_requirements,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                  'language', x.language, 'level_text', x.level_text)
                  ORDER BY x.item_order)
              FROM staging.job_foreign_languages x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS foreign_languages,
    COALESCE((SELECT jsonb_agg(x.soft_skill ORDER BY x.item_order)
              FROM staging.job_soft_skills x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS soft_skills,
    j.salary_text,
    j.salary_min,
    j.salary_max,
    j.salary_currency,
    j.salary_period,
    j.salary_basis,
    j.salary_status
FROM staging.job_observations j;

CREATE OR REPLACE VIEW staging.latest_jobs AS
SELECT v.*
FROM staging.jobs v
JOIN (
    SELECT DISTINCT ON (job_id) job_id, crawl_run_id
    FROM staging.job_observations
    ORDER BY job_id, observed_at DESC, observation_id DESC
) latest USING (job_id, crawl_run_id);

CREATE OR REPLACE VIEW staging.dwh_ready_jobs AS
SELECT v.*
FROM staging.jobs v
JOIN staging.job_observations o USING (job_id, crawl_run_id)
JOIN raw.job_postings r USING (job_id, crawl_run_id)
JOIN raw.crawl_runs c USING (crawl_run_id)
WHERE c.status <> 'running'
  AND c.metadata->'staging'->>'status' = 'succeeded'
  AND c.metadata->'staging'->>'ready_for_dwh' = 'true'
  AND c.metadata->'staging'->>'version' = o.normalization_version
  AND o.normalization_version = '1.2'
  AND c.metadata->'staging'->'quality'->>'error_count' = '0'
  AND c.metadata->'staging'->'quality'->>'validated_count' = c.metadata->'staging'->>'raw_count'
  AND o.source_job_id IS NOT DISTINCT FROM r.source_job_id
  AND o.job_url = r.job_url
  AND o.observed_at = r.observed_at;

CREATE OR REPLACE VIEW staging.dwh_latest_jobs AS
SELECT DISTINCT ON (job_id) *
FROM staging.dwh_ready_jobs
ORDER BY job_id, observed_at DESC, crawl_run_id DESC;

COMMENT ON VIEW staging.jobs IS
    'Exactly 30 common cleaned fields; one row per job_id/crawl_run_id.';
COMMENT ON VIEW staging.dwh_ready_jobs IS
    'DWH input: exactly 30 fields, complete validated staging batches at version 1.2; preserves observation history.';

COMMIT;
