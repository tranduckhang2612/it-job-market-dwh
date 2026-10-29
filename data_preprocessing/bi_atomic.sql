-- Additive atomic views. The existing 30-field staging.jobs contract stays unchanged.
CREATE SCHEMA IF NOT EXISTS bi;
CREATE TABLE IF NOT EXISTS bi.canonical_values (
 domain TEXT NOT NULL, value_key TEXT NOT NULL, canonical_name TEXT NOT NULL,
 category TEXT NOT NULL, PRIMARY KEY(domain,value_key), UNIQUE(domain,canonical_name));
CREATE TABLE IF NOT EXISTS bi.value_aliases (
 domain TEXT NOT NULL, alias_key TEXT NOT NULL, value_key TEXT NOT NULL,
 PRIMARY KEY(domain,alias_key), FOREIGN KEY(domain,value_key) REFERENCES bi.canonical_values);
CREATE TABLE IF NOT EXISTS bi.standardization_metadata (
 version TEXT PRIMARY KEY, catalog_sha256 TEXT NOT NULL);
CREATE OR REPLACE FUNCTION bi.label_key(value TEXT) RETURNS TEXT LANGUAGE SQL IMMUTABLE STRICT AS $$
 SELECT lower(regexp_replace(btrim(normalize(value,NFKC)),'\s+',' ','g'))
$$;

CREATE OR REPLACE VIEW bi.fact_job_observations AS
SELECT o.observation_id,o.job_id,o.source,o.source_job_id,o.job_url,o.crawl_run_id,
 o.observed_at,o.published_at,(o.observed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS observed_date,
 o.title,o.company_name,o.work_mode,o.experience_min_years,o.experience_max_years,
 o.salary_min,o.salary_max,o.salary_currency,o.salary_period,o.salary_basis,o.salary_status
FROM staging.job_observations o JOIN staging.dwh_ready_jobs j USING(job_id,crawl_run_id);
CREATE OR REPLACE VIEW bi.fact_job_latest AS
SELECT DISTINCT ON(job_id) * FROM bi.fact_job_observations
ORDER BY job_id,observed_at DESC,crawl_run_id DESC,observation_id DESC;

CREATE OR REPLACE VIEW bi.attribute_source AS
SELECT observation_id,'skill'::text AS domain,name AS source_value,category,requirement_type,NULL::integer AS education_order FROM staging.job_skills
UNION ALL SELECT observation_id,'role',role,'role','unspecified',NULL FROM staging.job_roles
UNION ALL SELECT observation_id,'seniority',seniority_level,'seniority','unspecified',NULL FROM staging.job_seniority_levels
UNION ALL SELECT observation_id,'city',city,'city','unspecified',NULL FROM staging.job_cities
UNION ALL SELECT observation_id,'knowledge',knowledge,'knowledge','unspecified',NULL FROM staging.job_required_knowledge
UNION ALL SELECT observation_id,'soft_skill',soft_skill,'soft_skill','unspecified',NULL FROM staging.job_soft_skills
UNION ALL SELECT observation_id,'foreign_language',language,'foreign_language',requirement_type,NULL FROM staging.job_foreign_languages
UNION ALL SELECT observation_id,'degree',degree,'degree',requirement_type,item_order FROM staging.job_education_requirements WHERE degree IS NOT NULL
UNION ALL SELECT m.observation_id,'major',m.major,'major',e.requirement_type,m.education_order
 FROM staging.job_education_majors m JOIN staging.job_education_requirements e
 ON e.observation_id=m.observation_id AND e.item_order=m.education_order;

CREATE OR REPLACE VIEW bi.attribute_resolved AS
SELECT s.observation_id,s.domain,s.source_value,
 COALESCE(c.value_key,s.domain||':'||bi.label_key(s.source_value)) AS value_key,
 COALESCE(c.canonical_name,s.source_value) AS canonical_name,COALESCE(c.category,s.category) AS category,
 s.requirement_type,s.education_order,(c.value_key IS NOT NULL) AS is_mapped,
 (c.value_key IS NOT NULL OR s.source_value !~ '[,;|/\n]|\s&\s') AS is_atomic
FROM bi.attribute_source s
LEFT JOIN bi.value_aliases a ON a.domain=s.domain AND a.alias_key=bi.label_key(s.source_value)
LEFT JOIN bi.canonical_values c ON c.domain=a.domain AND c.value_key=a.value_key;

CREATE OR REPLACE VIEW bi.dim_attribute AS
SELECT domain,value_key,canonical_name,category,true AS is_mapped FROM bi.canonical_values
UNION ALL
SELECT r.domain,r.value_key,min(r.canonical_name),min(r.category),false
FROM bi.attribute_resolved r WHERE NOT r.is_mapped AND r.is_atomic
GROUP BY r.domain,r.value_key;

CREATE OR REPLACE VIEW bi.bridge_job_attributes AS
SELECT f.observation_id,f.job_id,r.domain,r.value_key,r.education_order,
 CASE max(CASE r.requirement_type WHEN 'required' THEN 2 WHEN 'preferred' THEN 1 ELSE 0 END)
 WHEN 2 THEN 'required' WHEN 1 THEN 'preferred' ELSE 'unspecified' END AS requirement_type
FROM bi.fact_job_observations f JOIN bi.attribute_resolved r USING(observation_id)
WHERE r.is_atomic
GROUP BY f.observation_id,f.job_id,r.domain,r.value_key,r.education_order;
CREATE OR REPLACE VIEW bi.bridge_job_attributes_latest AS
SELECT b.* FROM bi.bridge_job_attributes b JOIN bi.fact_job_latest f USING(observation_id,job_id);

CREATE OR REPLACE VIEW bi.dim_skills AS SELECT value_key AS skill_key,canonical_name,category,is_mapped FROM bi.dim_attribute WHERE domain='skill';
CREATE OR REPLACE VIEW bi.bridge_job_skills_latest AS SELECT observation_id,job_id,value_key AS skill_key,requirement_type FROM bi.bridge_job_attributes_latest WHERE domain='skill';
CREATE OR REPLACE VIEW bi.dim_cities AS SELECT value_key AS city_key,canonical_name,is_mapped FROM bi.dim_attribute WHERE domain='city';
CREATE OR REPLACE VIEW bi.bridge_job_cities_latest AS SELECT observation_id,job_id,value_key AS city_key FROM bi.bridge_job_attributes_latest WHERE domain='city';
CREATE OR REPLACE VIEW bi.dim_roles AS SELECT value_key AS role_key,canonical_name,is_mapped FROM bi.dim_attribute WHERE domain='role';
CREATE OR REPLACE VIEW bi.bridge_job_roles_latest AS SELECT observation_id,job_id,value_key AS role_key FROM bi.bridge_job_attributes_latest WHERE domain='role';
CREATE OR REPLACE VIEW bi.job_foreign_language_levels_latest AS
SELECT f.observation_id,f.job_id,l.language,trim(level.value) AS level_text,l.requirement_type
FROM bi.fact_job_latest f JOIN staging.job_foreign_languages l USING(observation_id)
CROSS JOIN LATERAL regexp_split_to_table(l.level_text,'\s*;\s*') AS level(value)
WHERE l.level_text IS NOT NULL AND btrim(level.value)<>'';
CREATE OR REPLACE VIEW bi.job_addresses_latest AS
SELECT f.observation_id,f.job_id,trim(a.value) AS address_text
FROM bi.fact_job_latest f JOIN staging.job_observations o USING(observation_id)
CROSS JOIN LATERAL regexp_split_to_table(o.location_text,E'\n+') AS a(value)
WHERE o.location_text IS NOT NULL AND btrim(a.value)<>'';
CREATE OR REPLACE VIEW bi.dim_observed_date AS
SELECT DISTINCT observed_date,extract(year from observed_date)::integer AS year,
 extract(month from observed_date)::integer AS month,extract(day from observed_date)::integer AS day
FROM bi.fact_job_observations;
CREATE OR REPLACE VIEW bi.review_queue AS
SELECT r.*,f.job_id,f.source FROM bi.attribute_resolved r
JOIN bi.fact_job_observations f USING(observation_id) WHERE NOT r.is_mapped;
CREATE OR REPLACE VIEW bi.programming_language_demand AS
SELECT d.canonical_name,count(DISTINCT b.job_id)::bigint AS distinct_jobs
FROM bi.bridge_job_skills_latest b JOIN bi.dim_skills d USING(skill_key)
WHERE d.category='programming_languages'
GROUP BY d.canonical_name;
COMMENT ON VIEW bi.fact_job_latest IS 'One latest eligible observation per source-qualified job_id; does not assert a vacancy is still active.';
COMMENT ON VIEW bi.bridge_job_attributes IS 'Atomic one observation/domain/value/education-context relationship. Never join independent child tables into a cartesian product.';
COMMENT ON VIEW bi.programming_language_demand IS 'Distinct latest observed jobs per programming language. Category counts overlap and must not be summed as total vacancies.';

CREATE OR REPLACE VIEW bi.attribute_demand_latest AS
SELECT b.domain,d.value_key,d.canonical_name,d.category,count(DISTINCT b.job_id)::bigint AS distinct_jobs
FROM bi.bridge_job_attributes_latest b JOIN bi.dim_attribute d USING(domain,value_key)
GROUP BY b.domain,d.value_key,d.canonical_name,d.category;
