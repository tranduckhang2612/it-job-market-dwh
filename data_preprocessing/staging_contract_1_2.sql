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
