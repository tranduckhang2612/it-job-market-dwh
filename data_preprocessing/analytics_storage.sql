-- Step 3 persistent output. No foreign keys to staging or raw.

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.job_observations (
    observation_id        BIGINT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS analytics.job_roles (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    role           TEXT NOT NULL CHECK (btrim(role) <> ''),
    PRIMARY KEY (observation_id, role),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.job_seniority_levels (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    seniority_level TEXT NOT NULL CHECK (btrim(seniority_level) <> ''),
    PRIMARY KEY (observation_id, seniority_level),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.job_cities (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    city           TEXT NOT NULL CHECK (btrim(city) <> ''),
    PRIMARY KEY (observation_id, city),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.job_skills (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    name           TEXT NOT NULL CHECK (btrim(name) <> ''),
    category       TEXT NOT NULL CHECK (btrim(category) <> ''),
    PRIMARY KEY (observation_id, name),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.job_required_knowledge (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    knowledge      TEXT NOT NULL CHECK (btrim(knowledge) <> ''),
    PRIMARY KEY (observation_id, knowledge),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.job_education_requirements (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    degree         TEXT CHECK (btrim(degree) <> ''),
    PRIMARY KEY (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.job_education_majors (
    observation_id BIGINT NOT NULL,
    education_order INTEGER NOT NULL,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    major          TEXT NOT NULL CHECK (btrim(major) <> ''),
    PRIMARY KEY (observation_id, education_order, major),
    UNIQUE (observation_id, education_order, item_order),
    FOREIGN KEY (observation_id, education_order)
        REFERENCES analytics.job_education_requirements(observation_id, item_order) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analytics.job_foreign_languages (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    language       TEXT NOT NULL CHECK (btrim(language) <> ''),
    level_text     TEXT,
    PRIMARY KEY (observation_id, language),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.job_soft_skills (
    observation_id BIGINT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
    item_order     INTEGER NOT NULL CHECK (item_order > 0),
    soft_skill     TEXT NOT NULL CHECK (btrim(soft_skill) <> ''),
    PRIMARY KEY (observation_id, soft_skill),
    UNIQUE (observation_id, item_order)
);

CREATE TABLE IF NOT EXISTS analytics.load_state (
    observation_id BIGINT PRIMARY KEY REFERENCES analytics.job_observations(observation_id) ON DELETE CASCADE,
    source_sha256 TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    transform_sha256 TEXT NOT NULL,
    transform_version TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS analytics_observations_run ON analytics.job_observations(crawl_run_id);
CREATE INDEX IF NOT EXISTS analytics_observations_latest ON analytics.job_observations(job_id,observed_at DESC,observation_id DESC);
CREATE INDEX IF NOT EXISTS analytics_skills_name ON analytics.job_skills(name,observation_id);

