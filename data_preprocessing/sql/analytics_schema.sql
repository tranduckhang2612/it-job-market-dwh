-- Atomic analytics model 1.0. Source: normalize_data.normalized_jobs v5.1.
-- No benefit tables. No foreign keys to upstream schemas. Run via load_analytics.py.
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE TABLE IF NOT EXISTS analytics.pipeline_state (
 singleton BOOLEAN PRIMARY KEY CHECK(singleton),
 model_name TEXT NOT NULL CHECK(model_name='normalize_atomic_v1'),
 model_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.job_observations (
 observation_key TEXT PRIMARY KEY CHECK(length(observation_key)=64),
 job_id TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('itviec','topdev')),
 source_job_id TEXT, job_url TEXT NOT NULL, crawl_run_id TEXT NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL, title TEXT NOT NULL, company_name TEXT,
 description TEXT NOT NULL, location_text TEXT,
 work_mode TEXT CHECK(work_mode IN ('onsite','hybrid','remote')),
 foreign_language_status TEXT NOT NULL CHECK(foreign_language_status IN ('known','unknown')),
 source_normalized_at TIMESTAMPTZ NOT NULL, source_normalization_version TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, output_sha256 TEXT NOT NULL, model_sha256 TEXT NOT NULL,
 loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(job_id,crawl_run_id), CHECK(job_id LIKE source||':%')
);
CREATE TABLE IF NOT EXISTS analytics.observation_issues (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 issue_key TEXT NOT NULL, field_name TEXT NOT NULL, source_value TEXT NOT NULL,
 reason TEXT NOT NULL, PRIMARY KEY(observation_key,issue_key)
);

CREATE TABLE IF NOT EXISTS analytics.dim_programming_language (
 programming_language_key TEXT PRIMARY KEY CHECK(length(programming_language_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_programming_language (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 programming_language_key TEXT NOT NULL REFERENCES analytics.dim_programming_language(programming_language_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,programming_language_key,context_key),
 CHECK (context_key='' AND level_text IS NULL)
);

CREATE TABLE IF NOT EXISTS analytics.dim_framework (
 framework_key TEXT PRIMARY KEY CHECK(length(framework_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_framework (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 framework_key TEXT NOT NULL REFERENCES analytics.dim_framework(framework_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,framework_key,context_key),
 CHECK (context_key='' AND level_text IS NULL)
);

CREATE TABLE IF NOT EXISTS analytics.dim_core_skill (
 core_skill_key TEXT PRIMARY KEY CHECK(length(core_skill_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_core_skill (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 core_skill_key TEXT NOT NULL REFERENCES analytics.dim_core_skill(core_skill_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,core_skill_key,context_key),
 CHECK (context_key='' AND level_text IS NULL)
);

CREATE TABLE IF NOT EXISTS analytics.dim_role (
 role_key TEXT PRIMARY KEY CHECK(length(role_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_role (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 role_key TEXT NOT NULL REFERENCES analytics.dim_role(role_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,role_key,context_key),
 CHECK (context_key='' AND level_text IS NULL)
);

CREATE TABLE IF NOT EXISTS analytics.dim_city (
 city_key TEXT PRIMARY KEY CHECK(length(city_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_city (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 city_key TEXT NOT NULL REFERENCES analytics.dim_city(city_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,city_key,context_key),
 CHECK (context_key='' AND level_text IS NULL)
);

CREATE TABLE IF NOT EXISTS analytics.dim_foreign_language (
 foreign_language_key TEXT PRIMARY KEY CHECK(length(foreign_language_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_foreign_language (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 foreign_language_key TEXT NOT NULL REFERENCES analytics.dim_foreign_language(foreign_language_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,foreign_language_key,context_key),
 CHECK (context_key<>'')
);

CREATE TABLE IF NOT EXISTS analytics.dim_level (
 level_key TEXT PRIMARY KEY CHECK(length(level_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_level (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 level_key TEXT NOT NULL REFERENCES analytics.dim_level(level_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,level_key,context_key),
 CHECK (context_key='' AND level_text IS NULL)
);

CREATE TABLE IF NOT EXISTS analytics.dim_soft_skill (
 soft_skill_key TEXT PRIMARY KEY CHECK(length(soft_skill_key)=64),
 name TEXT NOT NULL CHECK(btrim(name)<>''), category TEXT NOT NULL,
 UNIQUE(name,category)
);
CREATE TABLE IF NOT EXISTS analytics.bridge_job_soft_skill (
 observation_key TEXT NOT NULL REFERENCES analytics.job_observations ON DELETE CASCADE,
 soft_skill_key TEXT NOT NULL REFERENCES analytics.dim_soft_skill(soft_skill_key),
 context_key TEXT NOT NULL DEFAULT '',
 level_text TEXT,
 PRIMARY KEY(observation_key,soft_skill_key,context_key),
 CHECK (context_key='' AND level_text IS NULL)
);

CREATE OR REPLACE VIEW analytics.fact_job_latest AS
SELECT DISTINCT ON(job_id) * FROM analytics.job_observations
ORDER BY job_id,observed_at DESC,crawl_run_id DESC;
CREATE OR REPLACE VIEW analytics.review_queue AS
SELECT j.job_id,j.crawl_run_id,j.source,i.field_name,i.source_value,i.reason
FROM analytics.observation_issues i JOIN analytics.job_observations j USING(observation_key);

CREATE OR REPLACE VIEW analytics.bridge_job_programming_language_latest AS
SELECT b.* FROM analytics.bridge_job_programming_language b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.programming_language_demand_latest AS
SELECT d.programming_language_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_programming_language_latest b JOIN analytics.dim_programming_language d USING(programming_language_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.programming_language_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.bridge_job_framework_latest AS
SELECT b.* FROM analytics.bridge_job_framework b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.framework_demand_latest AS
SELECT d.framework_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_framework_latest b JOIN analytics.dim_framework d USING(framework_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.framework_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.bridge_job_core_skill_latest AS
SELECT b.* FROM analytics.bridge_job_core_skill b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.core_skill_demand_latest AS
SELECT d.core_skill_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_core_skill_latest b JOIN analytics.dim_core_skill d USING(core_skill_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.core_skill_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.bridge_job_role_latest AS
SELECT b.* FROM analytics.bridge_job_role b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.role_demand_latest AS
SELECT d.role_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_role_latest b JOIN analytics.dim_role d USING(role_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.role_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.bridge_job_city_latest AS
SELECT b.* FROM analytics.bridge_job_city b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.city_demand_latest AS
SELECT d.city_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_city_latest b JOIN analytics.dim_city d USING(city_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.city_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.bridge_job_foreign_language_latest AS
SELECT b.* FROM analytics.bridge_job_foreign_language b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.foreign_language_demand_latest AS
SELECT d.foreign_language_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_foreign_language_latest b JOIN analytics.dim_foreign_language d USING(foreign_language_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.foreign_language_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.bridge_job_level_latest AS
SELECT b.* FROM analytics.bridge_job_level b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.level_demand_latest AS
SELECT d.level_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_level_latest b JOIN analytics.dim_level d USING(level_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.level_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.bridge_job_soft_skill_latest AS
SELECT b.* FROM analytics.bridge_job_soft_skill b JOIN analytics.fact_job_latest f USING(observation_key);
CREATE OR REPLACE VIEW analytics.soft_skill_demand_latest AS
SELECT d.soft_skill_key,d.name,d.category,count(DISTINCT f.job_id)::bigint AS jobs_with_value,
 (SELECT count(*) FROM analytics.fact_job_latest)::bigint AS total_jobs,
 round(100.0*count(DISTINCT f.job_id)/NULLIF((SELECT count(*) FROM analytics.fact_job_latest),0),2) AS job_pct
FROM analytics.bridge_job_soft_skill_latest b JOIN analytics.dim_soft_skill d USING(soft_skill_key)
JOIN analytics.fact_job_latest f USING(observation_key)
GROUP BY d.soft_skill_key,d.name,d.category;

CREATE OR REPLACE VIEW analytics.job_skills_latest AS
SELECT b.observation_key,'programming_language'::text AS skill_group,d.programming_language_key AS skill_key,d.name,d.category FROM analytics.bridge_job_programming_language_latest b JOIN analytics.dim_programming_language d USING(programming_language_key)
UNION ALL
SELECT b.observation_key,'framework'::text AS skill_group,d.framework_key AS skill_key,d.name,d.category FROM analytics.bridge_job_framework_latest b JOIN analytics.dim_framework d USING(framework_key)
UNION ALL
SELECT b.observation_key,'core_skill'::text AS skill_group,d.core_skill_key AS skill_key,d.name,d.category FROM analytics.bridge_job_core_skill_latest b JOIN analytics.dim_core_skill d USING(core_skill_key);

CREATE OR REPLACE VIEW analytics.role_skill_demand_latest AS
WITH totals AS (
 SELECT r.role_key,count(DISTINCT f.job_id)::bigint AS total_role_jobs
 FROM analytics.bridge_job_role_latest r JOIN analytics.fact_job_latest f USING(observation_key)
 GROUP BY r.role_key
), mentions AS (
 SELECT r.role_key,s.skill_group,s.skill_key,s.name,s.category,
 count(DISTINCT f.job_id)::bigint AS jobs_with_skill
 FROM analytics.bridge_job_role_latest r JOIN analytics.fact_job_latest f USING(observation_key)
 JOIN analytics.job_skills_latest s USING(observation_key)
 GROUP BY r.role_key,s.skill_group,s.skill_key,s.name,s.category
)
SELECT d.name AS role_name,m.skill_group,m.skill_key,m.name AS skill_name,m.category,
 m.jobs_with_skill,t.total_role_jobs,
 round(100.0*m.jobs_with_skill/NULLIF(t.total_role_jobs,0),2) AS job_pct
FROM mentions m JOIN totals t USING(role_key) JOIN analytics.dim_role d USING(role_key);
