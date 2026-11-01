CREATE OR REPLACE VIEW analytics.jobs AS
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
              FROM analytics.job_roles x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS job_roles,
    COALESCE((SELECT jsonb_agg(x.seniority_level ORDER BY x.item_order)
              FROM analytics.job_seniority_levels x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS seniority_levels,
    j.location_text,
    COALESCE((SELECT jsonb_agg(x.city ORDER BY x.item_order)
              FROM analytics.job_cities x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS cities,
    j.work_mode,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                  'name', x.name, 'category', x.category)
                  ORDER BY x.item_order)
              FROM analytics.job_skills x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS skills,
    COALESCE((SELECT jsonb_agg(x.knowledge ORDER BY x.item_order)
              FROM analytics.job_required_knowledge x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS required_knowledge,
    j.experience_text,
    j.experience_min_years,
    j.experience_max_years,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                  'degree', e.degree,
                  'majors', COALESCE((SELECT jsonb_agg(m.major ORDER BY m.item_order)
                                      FROM analytics.job_education_majors m
                                      WHERE m.observation_id = e.observation_id
                                        AND m.education_order = e.item_order), '[]'::jsonb)) ORDER BY e.item_order)
              FROM analytics.job_education_requirements e
              WHERE e.observation_id = j.observation_id), '[]'::jsonb) AS education_requirements,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                  'language', x.language, 'level_text', x.level_text)
                  ORDER BY x.item_order)
              FROM analytics.job_foreign_languages x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS foreign_languages,
    COALESCE((SELECT jsonb_agg(x.soft_skill ORDER BY x.item_order)
              FROM analytics.job_soft_skills x WHERE x.observation_id = j.observation_id), '[]'::jsonb) AS soft_skills,
    j.salary_text,
    j.salary_min,
    j.salary_max,
    j.salary_currency,
    j.salary_period,
    j.salary_basis,
    j.salary_status
FROM analytics.job_observations j;

CREATE OR REPLACE VIEW analytics.latest_jobs AS
SELECT v.*
FROM analytics.jobs v
JOIN (
    SELECT DISTINCT ON (job_id) job_id, crawl_run_id
    FROM analytics.job_observations
    ORDER BY job_id, observed_at DESC, observation_id DESC
) latest USING (job_id, crawl_run_id);

-- Only fully validated observations are published by the explicit loader.
-- These views read persisted analytics rows, never the live staging gate.
CREATE OR REPLACE VIEW analytics.dwh_ready_jobs AS SELECT * FROM analytics.jobs;
CREATE OR REPLACE VIEW analytics.dwh_latest_jobs AS SELECT * FROM analytics.latest_jobs;
