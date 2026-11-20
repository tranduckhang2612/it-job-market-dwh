-- Phase 5: semantic views for the historical model and compatibility views for cutover.
SET XACT_ABORT ON;
SET NOCOUNT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.fact_job_observation', N'U') IS NULL
       OR OBJECT_ID(N'dbo.fact_job_necessary_skill', N'U') IS NULL
       OR OBJECT_ID(N'dbo.fact_job_role_city', N'U') IS NULL
        THROW 54001, 'Missing dw_v2 fact tables.', 1;

    IF SCHEMA_ID(N'semantic') IS NULL EXEC(N'CREATE SCHEMA semantic');
    IF SCHEMA_ID(N'dw_v2_compat') IS NULL EXEC(N'CREATE SCHEMA dw_v2_compat');

    EXEC(N'CREATE OR ALTER VIEW semantic.job_observation AS
        SELECT
            fact.job_observation_key,
            fact.observation_id,
            fact.job_key,
            job.job_id,
            job.source_job_id,
            job.canonical_job_url AS job_url,
            fact.source_key,
            source.source_code,
            source.source_name,
            fact.company_key,
            company.company_name,
            company.normalized_name AS company_normalized_name,
            fact.crawl_run_key,
            run.crawl_run_id,
            run.pipeline_version,
            fact.date_key,
            date.calendar_date,
            date.month_no,
            date.quarter_no,
            date.year_no,
            fact.observed_at_utc,
            fact.job_title_snapshot AS job_title,
            fact.work_mode,
            fact.job_posting_count,
            fact.loaded_at_utc
        FROM dbo.fact_job_observation fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_company company ON company.company_key = fact.company_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_date date ON date.date_key = fact.date_key');

    EXEC(N'CREATE OR ALTER VIEW semantic.latest_job AS
        WITH ranked AS (
            SELECT observation.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY observation.job_key
                       ORDER BY observation.observed_at_utc DESC,
                                observation.crawl_run_key DESC,
                                observation.job_observation_key DESC
                   ) AS observation_rank
            FROM semantic.job_observation observation
        )
        SELECT * FROM ranked WHERE observation_rank = 1');

    EXEC(N'CREATE OR ALTER VIEW semantic.job_skill AS
        SELECT
            fact.job_necessary_skill_key,
            fact.observation_id,
            fact.job_key,
            job.job_id,
            fact.date_key,
            date.calendar_date,
            fact.source_key,
            source.source_code,
            fact.crawl_run_key,
            run.crawl_run_id,
            fact.skill_key,
            skill.skill_type,
            skill.skill_name,
            skill.normalized_name AS skill_normalized_name,
            skill.category AS skill_category,
            skill.programming_language,
            fact.proficiency_level_key,
            level.level_code,
            level.level_name,
            fact.context_no,
            fact.level_text_snapshot,
            fact.skill_mention_count,
            fact.loaded_at_utc
        FROM dbo.fact_job_necessary_skill fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_skill skill ON skill.skill_key = fact.skill_key
        JOIN dbo.dim_proficiency_level level
          ON level.proficiency_level_key = fact.proficiency_level_key');

    EXEC(N'CREATE OR ALTER VIEW semantic.job_role_city AS
        SELECT
            fact.job_role_city_key,
            fact.observation_id,
            fact.job_key,
            job.job_id,
            fact.date_key,
            date.calendar_date,
            fact.source_key,
            source.source_code,
            fact.crawl_run_key,
            run.crawl_run_id,
            fact.role_key,
            role.role_name,
            role.normalized_name AS role_normalized_name,
            role.role_category,
            fact.city_key,
            city.city_name,
            city.normalized_name AS city_normalized_name,
            city.region_name,
            fact.combination_count,
            fact.loaded_at_utc
        FROM dbo.fact_job_role_city fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_role role ON role.role_key = fact.role_key
        JOIN dbo.dim_city city ON city.city_key = fact.city_key');

    EXEC(N'CREATE OR ALTER VIEW semantic.skill_demand_daily AS
        SELECT
            fact.date_key,
            date.calendar_date,
            fact.source_key,
            source.source_code,
            fact.skill_key,
            skill.skill_type,
            skill.skill_name,
            skill.normalized_name AS skill_normalized_name,
            skill.category AS skill_category,
            SUM(CONVERT(bigint, fact.skill_mention_count)) AS skill_mention_count,
            COUNT_BIG(*) AS fact_row_count,
            COUNT(DISTINCT fact.job_key) AS distinct_job_count
        FROM dbo.fact_job_necessary_skill fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_skill skill ON skill.skill_key = fact.skill_key
        GROUP BY fact.date_key, date.calendar_date,
                 fact.source_key, source.source_code,
                 fact.skill_key, skill.skill_type, skill.skill_name,
                 skill.normalized_name, skill.category');

    EXEC(N'CREATE OR ALTER VIEW semantic.role_city_demand_daily AS
        SELECT
            fact.date_key,
            date.calendar_date,
            fact.source_key,
            source.source_code,
            fact.role_key,
            role.role_name,
            role.normalized_name AS role_normalized_name,
            role.role_category,
            fact.city_key,
            city.city_name,
            city.normalized_name AS city_normalized_name,
            SUM(CONVERT(bigint, fact.combination_count)) AS combination_count,
            COUNT_BIG(*) AS fact_row_count,
            COUNT(DISTINCT fact.job_key) AS distinct_job_count
        FROM dbo.fact_job_role_city fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_role role ON role.role_key = fact.role_key
        JOIN dbo.dim_city city ON city.city_key = fact.city_key
        GROUP BY fact.date_key, date.calendar_date,
                 fact.source_key, source.source_code,
                 fact.role_key, role.role_name, role.normalized_name, role.role_category,
                 fact.city_key, city.city_name, city.normalized_name');

    EXEC(N'CREATE OR ALTER VIEW dw_v2_compat.fact_job AS
        WITH ranked AS (
            SELECT
                fact.*,
                ROW_NUMBER() OVER (
                    PARTITION BY fact.job_key
                    ORDER BY fact.observed_at_utc DESC,
                             fact.crawl_run_key DESC,
                             fact.job_observation_key DESC
                ) AS observation_rank
            FROM dbo.fact_job_observation fact
        )
        SELECT
            ranked.job_key AS fact_job_key,
            job.job_id,
            source.source_code AS source,
            ranked.job_title_snapshot AS title,
            company.company_name,
            job.canonical_job_url AS job_url,
            run.crawl_run_id,
            ranked.observed_at_utc,
            ranked.date_key AS observed_date_key,
            ranked.job_posting_count
        FROM ranked
        JOIN dbo.dim_job job ON job.job_key = ranked.job_key
        JOIN dbo.dim_source source ON source.source_key = ranked.source_key
        JOIN dbo.dim_company company ON company.company_key = ranked.company_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = ranked.crawl_run_key
        WHERE ranked.observation_rank = 1');

    EXEC(N'CREATE OR ALTER VIEW dw_v2_compat.dim_necessary_skill AS
        SELECT
            skill.skill_key AS necessary_skill_key,
            skill.skill_type,
            skill.skill_name AS name,
            skill.category,
            skill.created_at_utc
        FROM dbo.dim_skill skill
        WHERE skill.skill_type IN (
            N''core_skill'', N''foreign_language'', N''framework'', N''programming_language''
        )');

    EXEC(N'CREATE OR ALTER VIEW dw_v2_compat.fact_job_necessary_skill AS
        WITH latest AS (
            SELECT
                observation.observation_id,
                ROW_NUMBER() OVER (
                    PARTITION BY observation.job_key
                    ORDER BY observation.observed_at_utc DESC,
                             observation.crawl_run_key DESC,
                             observation.job_observation_key DESC
                ) AS observation_rank
            FROM dbo.fact_job_observation observation
        )
        SELECT
            fact.job_necessary_skill_key AS fact_job_necessary_skill_key,
            fact.job_key AS fact_job_key,
            fact.skill_key AS necessary_skill_key,
            fact.context_no,
            fact.level_text_snapshot AS level_text,
            fact.skill_mention_count
        FROM dbo.fact_job_necessary_skill fact
        JOIN latest ON latest.observation_id = fact.observation_id
        WHERE latest.observation_rank = 1');

    EXEC(N'CREATE OR ALTER VIEW dw_v2_compat.job_necessary_skill AS
        WITH latest AS (
            SELECT
                observation.observation_id,
                ROW_NUMBER() OVER (
                    PARTITION BY observation.job_key
                    ORDER BY observation.observed_at_utc DESC,
                             observation.crawl_run_key DESC,
                             observation.job_observation_key DESC
                ) AS observation_rank
            FROM dbo.fact_job_observation observation
        )
        SELECT
            fact.job_necessary_skill_key AS fact_job_necessary_skill_key,
            fact.job_key AS fact_job_key,
            fact.skill_key AS necessary_skill_key,
            job.job_id,
            source.source_code AS source,
            observation.job_title_snapshot AS title,
            company.company_name,
            job.canonical_job_url AS job_url,
            run.crawl_run_id,
            observation.observed_at_utc,
            fact.date_key AS observed_date_key,
            skill.skill_type,
            skill.skill_name AS skill_name,
            skill.category,
            fact.context_no,
            fact.level_text_snapshot AS level_text,
            fact.skill_mention_count
        FROM dbo.fact_job_necessary_skill fact
        JOIN latest ON latest.observation_id = fact.observation_id
        JOIN dbo.fact_job_observation observation
          ON observation.observation_id = fact.observation_id
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_company company ON company.company_key = observation.company_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_skill skill ON skill.skill_key = fact.skill_key
        WHERE latest.observation_rank = 1');

    EXEC(N'CREATE OR ALTER VIEW dw_v2_compat.fact_job_role_city AS
        WITH latest AS (
            SELECT
                observation.observation_id,
                ROW_NUMBER() OVER (
                    PARTITION BY observation.job_key
                    ORDER BY observation.observed_at_utc DESC,
                             observation.crawl_run_key DESC,
                             observation.job_observation_key DESC
                ) AS observation_rank
            FROM dbo.fact_job_observation observation
        )
        SELECT
            fact.job_role_city_key AS fact_job_role_city_key,
            fact.job_key AS fact_job_key,
            fact.role_key,
            fact.city_key,
            fact.combination_count
        FROM dbo.fact_job_role_city fact
        JOIN latest ON latest.observation_id = fact.observation_id
        WHERE latest.observation_rank = 1');

    EXEC(N'CREATE OR ALTER VIEW dw_v2_compat.job_role_city AS
        WITH latest AS (
            SELECT
                observation.observation_id,
                ROW_NUMBER() OVER (
                    PARTITION BY observation.job_key
                    ORDER BY observation.observed_at_utc DESC,
                             observation.crawl_run_key DESC,
                             observation.job_observation_key DESC
                ) AS observation_rank
            FROM dbo.fact_job_observation observation
        )
        SELECT
            fact.job_role_city_key AS fact_job_role_city_key,
            fact.job_key AS fact_job_key,
            fact.role_key,
            fact.city_key,
            role.role_name AS role_name,
            role.role_category AS role_category,
            city.city_name AS city_name,
            fact.combination_count
        FROM dbo.fact_job_role_city fact
        JOIN latest ON latest.observation_id = fact.observation_id
        JOIN dbo.dim_role role ON role.role_key = fact.role_key
        JOIN dbo.dim_city city ON city.city_key = fact.city_key
        WHERE latest.observation_rank = 1');

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
