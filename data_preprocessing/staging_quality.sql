-- Additive review views; the existing 30-field DWH contract is unchanged.
CREATE OR REPLACE VIEW staging.job_quality_flags AS
SELECT observation_id, job_id, crawl_run_id,
       NOT EXISTS (SELECT 1 FROM staging.job_cities c WHERE c.observation_id=o.observation_id) AS missing_cities,
       experience_min_years IS NULL AND experience_max_years IS NULL AS missing_experience_bounds,
       salary_min IS NULL AND salary_max IS NULL AS missing_salary_bounds,
       (
         lower(title) ~ '(hr intern|digital marketing|thu mua|thi công|thi cong|kỹ sư xây dựng|ky su xay dung|phiên dịch|phien dich|field sales)'
         AND lower(title) !~ '(developer|software|devops|data engineer|business analyst|kiểm thử|kiem thu|\mqa\M|tester)'
       ) AS likely_non_it,
       COALESCE(experience_text ~* '[0-9]+\s*[-–]\s*[0-9]+\s+kinh\s+nghiệm', false)
           AND experience_min_years IS NULL AS ambiguous_experience_unit
FROM staging.job_observations o;

-- This is a review-assisted candidate set, not a complete occupation classifier.
CREATE OR REPLACE VIEW staging.it_candidate_jobs AS
SELECT j.* FROM staging.dwh_latest_jobs j
JOIN staging.job_quality_flags q USING (job_id, crawl_run_id)
WHERE NOT q.likely_non_it;

COMMENT ON VIEW staging.job_quality_flags IS
    'Missing data and conservative non-IT title flags. Missing is not invalid; never deletes observations.';
COMMENT ON VIEW staging.it_candidate_jobs IS
    'Latest DWH-ready observations excluding explicit non-IT title flags; manual domain review is still required.';
