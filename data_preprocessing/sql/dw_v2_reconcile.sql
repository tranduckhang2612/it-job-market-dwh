-- Phase 4: read-only reconciliation between the legacy dw snapshot and dbo.
-- This is a strict cutover check: every current v2 fact must correspond to legacy data.
SET XACT_ABORT ON;
SET NOCOUNT ON;
SET ANSI_NULLS ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @lock_result int;
    EXEC @lock_result = sys.sp_getapplock
        @Resource = N'it_job_market_dw:dw_v2:reconcile',
        @LockMode = N'Shared',
        @LockOwner = N'Transaction',
        @LockTimeout = 10000;
    IF @lock_result < 0
        THROW 53000, 'Could not acquire the dw_v2 reconciliation lock.', 1;

    IF OBJECT_ID(N'dw.fact_job', N'U') IS NULL
       OR OBJECT_ID(N'dw.fact_job_necessary_skill', N'U') IS NULL
       OR OBJECT_ID(N'dw.fact_job_role_city', N'U') IS NULL
        THROW 53001, 'Legacy facts are incomplete.', 1;
    IF OBJECT_ID(N'dbo.fact_job_observation', N'U') IS NULL
       OR OBJECT_ID(N'dbo.fact_job_necessary_skill', N'U') IS NULL
       OR OBJECT_ID(N'dbo.fact_job_role_city', N'U') IS NULL
        THROW 53002, 'dw_v2 facts are incomplete.', 1;
    IF OBJECT_ID(N'dw_v2_migration.map_job', N'U') IS NULL
       OR OBJECT_ID(N'dw_v2_migration.map_skill', N'U') IS NULL
       OR OBJECT_ID(N'dw_v2_migration.map_role', N'U') IS NULL
       OR OBJECT_ID(N'dw_v2_migration.map_city', N'U') IS NULL
        THROW 53003, 'Migration mappings are incomplete.', 1;

    IF (SELECT COUNT_BIG(*) FROM dw.fact_job)
        <> (SELECT COUNT_BIG(*) FROM dw_v2_migration.map_job)
        THROW 53010, 'Job mapping count mismatch.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.dim_necessary_skill)
        <> (SELECT COUNT_BIG(*) FROM dw_v2_migration.map_skill)
        THROW 53011, 'Skill mapping count mismatch.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.dim_role)
        <> (SELECT COUNT_BIG(*) FROM dw_v2_migration.map_role)
        THROW 53012, 'Role mapping count mismatch.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.dim_city)
        <> (SELECT COUNT_BIG(*) FROM dw_v2_migration.map_city)
        THROW 53013, 'City mapping count mismatch.', 1;

    IF (SELECT COUNT_BIG(*) FROM dw.fact_job)
        <> (SELECT COUNT_BIG(*) FROM dbo.fact_job_observation)
        THROW 53020, 'Observation row count mismatch.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.fact_job_necessary_skill)
        <> (SELECT COUNT_BIG(*) FROM dbo.fact_job_necessary_skill)
        THROW 53021, 'Skill fact row count mismatch.', 1;
    IF (SELECT COUNT_BIG(*) FROM dw.fact_job_role_city)
        <> (SELECT COUNT_BIG(*) FROM dbo.fact_job_role_city)
        THROW 53022, 'Role-city fact row count mismatch.', 1;

    IF (SELECT COALESCE(SUM(CONVERT(bigint, job_posting_count)), 0) FROM dw.fact_job)
        <> (SELECT COALESCE(SUM(CONVERT(bigint, job_posting_count)), 0)
            FROM dbo.fact_job_observation)
        THROW 53023, 'Observation measure mismatch.', 1;
    IF (SELECT COALESCE(SUM(CONVERT(bigint, skill_mention_count)), 0)
        FROM dw.fact_job_necessary_skill)
        <> (SELECT COALESCE(SUM(CONVERT(bigint, skill_mention_count)), 0)
            FROM dbo.fact_job_necessary_skill)
        THROW 53024, 'Skill measure mismatch.', 1;
    IF (SELECT COALESCE(SUM(CONVERT(bigint, combination_count)), 0)
        FROM dw.fact_job_role_city)
        <> (SELECT COALESCE(SUM(CONVERT(bigint, combination_count)), 0)
            FROM dbo.fact_job_role_city)
        THROW 53025, 'Role-city measure mismatch.', 1;

    IF EXISTS (
        SELECT job_key, crawl_run_key
        FROM dbo.fact_job_observation
        GROUP BY job_key, crawl_run_key
        HAVING COUNT_BIG(*) > 1
    )
        THROW 53030, 'Duplicate observation grain detected.', 1;
    IF EXISTS (
        SELECT job_key, crawl_run_key, skill_key, context_no
        FROM dbo.fact_job_necessary_skill
        GROUP BY job_key, crawl_run_key, skill_key, context_no
        HAVING COUNT_BIG(*) > 1
    )
        THROW 53031, 'Duplicate skill grain detected.', 1;
    IF EXISTS (
        SELECT job_key, crawl_run_key, role_key, city_key
        FROM dbo.fact_job_role_city
        GROUP BY job_key, crawl_run_key, role_key, city_key
        HAVING COUNT_BIG(*) > 1
    )
        THROW 53032, 'Duplicate role-city grain detected.', 1;

    IF EXISTS (
        SELECT 1
        FROM dbo.fact_job_necessary_skill skill
        LEFT JOIN dbo.fact_job_observation observation
          ON observation.observation_id = skill.observation_id
         AND observation.job_key = skill.job_key
         AND observation.crawl_run_key = skill.crawl_run_key
        WHERE observation.job_observation_key IS NULL
    )
        THROW 53033, 'Skill fact has no matching observation.', 1;
    IF EXISTS (
        SELECT 1
        FROM dbo.fact_job_role_city role_city
        LEFT JOIN dbo.fact_job_observation observation
          ON observation.observation_id = role_city.observation_id
         AND observation.job_key = role_city.job_key
         AND observation.crawl_run_key = role_city.crawl_run_key
        WHERE observation.job_observation_key IS NULL
    )
        THROW 53034, 'Role-city fact has no matching observation.', 1;

    IF EXISTS (
        SELECT
            legacy.job_id COLLATE Latin1_General_100_BIN2,
            legacy.source COLLATE Latin1_General_100_BIN2,
            COALESCE(NULLIF(LOWER(LTRIM(RTRIM(legacy.company_name))), N''), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(NULLIF(LTRIM(RTRIM(legacy.crawl_run_id)), N''), N'legacy:unknown')
                COLLATE Latin1_General_100_BIN2,
            legacy.observed_at_utc,
            ISNULL(legacy.title, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(legacy.job_url, N'') COLLATE Latin1_General_100_BIN2
        FROM dw.fact_job legacy
        EXCEPT
        SELECT
            job.job_id,
            source.source_code,
            company.normalized_name,
            run.crawl_run_id,
            fact.observed_at_utc,
            ISNULL(fact.job_title_snapshot, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(job.canonical_job_url, N'') COLLATE Latin1_General_100_BIN2
        FROM dbo.fact_job_observation fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_company company ON company.company_key = fact.company_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
    ) OR EXISTS (
        SELECT
            job.job_id,
            source.source_code,
            company.normalized_name,
            run.crawl_run_id,
            fact.observed_at_utc,
            ISNULL(fact.job_title_snapshot, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(job.canonical_job_url, N'') COLLATE Latin1_General_100_BIN2
        FROM dbo.fact_job_observation fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        JOIN dbo.dim_company company ON company.company_key = fact.company_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        EXCEPT
        SELECT
            legacy.job_id COLLATE Latin1_General_100_BIN2,
            legacy.source COLLATE Latin1_General_100_BIN2,
            COALESCE(NULLIF(LOWER(LTRIM(RTRIM(legacy.company_name))), N''), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(NULLIF(LTRIM(RTRIM(legacy.crawl_run_id)), N''), N'legacy:unknown')
                COLLATE Latin1_General_100_BIN2,
            legacy.observed_at_utc,
            ISNULL(legacy.title, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(legacy.job_url, N'') COLLATE Latin1_General_100_BIN2
        FROM dw.fact_job legacy
    )
        THROW 53040, 'Observation anti-join mismatch.', 1;

    IF EXISTS (
        SELECT
            job.job_id,
            run.crawl_run_id,
            skill.skill_type,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category,
            legacy.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(legacy.level_text))), N'')
                COLLATE Latin1_General_100_BIN2
        FROM dw.fact_job_necessary_skill legacy
        JOIN dw.fact_job job ON job.fact_job_key = legacy.fact_job_key
        JOIN dw.dim_necessary_skill skill
          ON skill.necessary_skill_key = legacy.necessary_skill_key
        JOIN dw_v2_migration.map_job job_map
          ON job_map.legacy_fact_job_key = legacy.fact_job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = job_map.crawl_run_key
        EXCEPT
        SELECT
            job.job_id,
            run.crawl_run_id,
            skill.skill_type,
            skill.normalized_name,
            skill.category,
            fact.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(fact.level_text_snapshot))), N'')
                COLLATE Latin1_General_100_BIN2
        FROM dbo.fact_job_necessary_skill fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_skill skill ON skill.skill_key = fact.skill_key
    ) OR EXISTS (
        SELECT
            job.job_id,
            run.crawl_run_id,
            skill.skill_type,
            skill.normalized_name,
            skill.category,
            fact.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(fact.level_text_snapshot))), N'')
                COLLATE Latin1_General_100_BIN2
        FROM dbo.fact_job_necessary_skill fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_skill skill ON skill.skill_key = fact.skill_key
        EXCEPT
        SELECT
            job.job_id,
            run.crawl_run_id,
            skill.skill_type,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category,
            legacy.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(legacy.level_text))), N'')
                COLLATE Latin1_General_100_BIN2
        FROM dw.fact_job_necessary_skill legacy
        JOIN dw.fact_job job ON job.fact_job_key = legacy.fact_job_key
        JOIN dw.dim_necessary_skill skill
          ON skill.necessary_skill_key = legacy.necessary_skill_key
        JOIN dw_v2_migration.map_job job_map
          ON job_map.legacy_fact_job_key = legacy.fact_job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = job_map.crawl_run_key
    )
        THROW 53041, 'Skill fact anti-join mismatch.', 1;

    IF EXISTS (
        SELECT
            job.job_id,
            run.crawl_run_id,
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(role.category, N'unknown') COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2
        FROM dw.fact_job_role_city legacy
        JOIN dw.fact_job job ON job.fact_job_key = legacy.fact_job_key
        LEFT JOIN dw.dim_role role ON role.role_key = legacy.role_key
        LEFT JOIN dw.dim_city city ON city.city_key = legacy.city_key
        JOIN dw_v2_migration.map_job job_map
          ON job_map.legacy_fact_job_key = legacy.fact_job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = job_map.crawl_run_key
        EXCEPT
        SELECT
            job.job_id,
            run.crawl_run_id,
            role.normalized_name,
            role.role_category,
            city.normalized_name
        FROM dbo.fact_job_role_city fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_role role ON role.role_key = fact.role_key
        JOIN dbo.dim_city city ON city.city_key = fact.city_key
    ) OR EXISTS (
        SELECT
            job.job_id,
            run.crawl_run_id,
            role.normalized_name,
            role.role_category,
            city.normalized_name
        FROM dbo.fact_job_role_city fact
        JOIN dbo.dim_job job ON job.job_key = fact.job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = fact.crawl_run_key
        JOIN dbo.dim_role role ON role.role_key = fact.role_key
        JOIN dbo.dim_city city ON city.city_key = fact.city_key
        EXCEPT
        SELECT
            job.job_id,
            run.crawl_run_id,
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(role.category, N'unknown') COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2
        FROM dw.fact_job_role_city legacy
        JOIN dw.fact_job job ON job.fact_job_key = legacy.fact_job_key
        LEFT JOIN dw.dim_role role ON role.role_key = legacy.role_key
        LEFT JOIN dw.dim_city city ON city.city_key = legacy.city_key
        JOIN dw_v2_migration.map_job job_map
          ON job_map.legacy_fact_job_key = legacy.fact_job_key
        JOIN dbo.dim_crawl_run run ON run.crawl_run_key = job_map.crawl_run_key
    )
        THROW 53042, 'Role-city fact anti-join mismatch.', 1;

    IF EXISTS (
        SELECT CAST(observed_at_utc AS date), source COLLATE Latin1_General_100_BIN2,
               COUNT_BIG(*) AS posting_count
        FROM dw.fact_job
        GROUP BY CAST(observed_at_utc AS date), source COLLATE Latin1_General_100_BIN2
        EXCEPT
        SELECT date.calendar_date, source.source_code,
               SUM(CONVERT(bigint, fact.job_posting_count))
        FROM dbo.fact_job_observation fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        GROUP BY date.calendar_date, source.source_code
    ) OR EXISTS (
        SELECT date.calendar_date, source.source_code,
               SUM(CONVERT(bigint, fact.job_posting_count))
        FROM dbo.fact_job_observation fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_source source ON source.source_key = fact.source_key
        GROUP BY date.calendar_date, source.source_code
        EXCEPT
        SELECT CAST(observed_at_utc AS date), source COLLATE Latin1_General_100_BIN2,
               COUNT_BIG(*)
        FROM dw.fact_job
        GROUP BY CAST(observed_at_utc AS date), source COLLATE Latin1_General_100_BIN2
    )
        THROW 53050, 'Dashboard reconciliation failed for jobs by date and source.', 1;

    IF EXISTS (
        SELECT
            CAST(job.observed_at_utc AS date),
            skill.skill_type,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category,
            COUNT_BIG(*)
        FROM dw.fact_job_necessary_skill fact
        JOIN dw.fact_job job ON job.fact_job_key = fact.fact_job_key
        JOIN dw.dim_necessary_skill skill
          ON skill.necessary_skill_key = fact.necessary_skill_key
        GROUP BY CAST(job.observed_at_utc AS date), skill.skill_type,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category
        EXCEPT
        SELECT date.calendar_date, skill.skill_type, skill.normalized_name, skill.category,
               SUM(CONVERT(bigint, fact.skill_mention_count))
        FROM dbo.fact_job_necessary_skill fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_skill skill ON skill.skill_key = fact.skill_key
        GROUP BY date.calendar_date, skill.skill_type, skill.normalized_name, skill.category
    ) OR EXISTS (
        SELECT date.calendar_date, skill.skill_type, skill.normalized_name, skill.category,
               SUM(CONVERT(bigint, fact.skill_mention_count))
        FROM dbo.fact_job_necessary_skill fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_skill skill ON skill.skill_key = fact.skill_key
        GROUP BY date.calendar_date, skill.skill_type, skill.normalized_name, skill.category
        EXCEPT
        SELECT
            CAST(job.observed_at_utc AS date),
            skill.skill_type,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category,
            COUNT_BIG(*)
        FROM dw.fact_job_necessary_skill fact
        JOIN dw.fact_job job ON job.fact_job_key = fact.fact_job_key
        JOIN dw.dim_necessary_skill skill
          ON skill.necessary_skill_key = fact.necessary_skill_key
        GROUP BY CAST(job.observed_at_utc AS date), skill.skill_type,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category
    )
        THROW 53051, 'Dashboard reconciliation failed for skill demand.', 1;

    IF EXISTS (
        SELECT
            CAST(job.observed_at_utc AS date),
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COUNT(DISTINCT legacy.fact_job_key)
        FROM dw.fact_job_role_city legacy
        JOIN dw.fact_job job ON job.fact_job_key = legacy.fact_job_key
        LEFT JOIN dw.dim_role role ON role.role_key = legacy.role_key
        LEFT JOIN dw.dim_city city ON city.city_key = legacy.city_key
        GROUP BY CAST(job.observed_at_utc AS date),
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2
        EXCEPT
        SELECT
            date.calendar_date,
            role.normalized_name,
            city.normalized_name,
            COUNT(DISTINCT fact.job_key)
        FROM dbo.fact_job_role_city fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_role role ON role.role_key = fact.role_key
        JOIN dbo.dim_city city ON city.city_key = fact.city_key
        GROUP BY date.calendar_date, role.normalized_name, city.normalized_name
    ) OR EXISTS (
        SELECT
            date.calendar_date,
            role.normalized_name,
            city.normalized_name,
            COUNT(DISTINCT fact.job_key)
        FROM dbo.fact_job_role_city fact
        JOIN dbo.dim_date date ON date.date_key = fact.date_key
        JOIN dbo.dim_role role ON role.role_key = fact.role_key
        JOIN dbo.dim_city city ON city.city_key = fact.city_key
        GROUP BY date.calendar_date, role.normalized_name, city.normalized_name
        EXCEPT
        SELECT
            CAST(job.observed_at_utc AS date),
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COUNT(DISTINCT legacy.fact_job_key)
        FROM dw.fact_job_role_city legacy
        JOIN dw.fact_job job ON job.fact_job_key = legacy.fact_job_key
        LEFT JOIN dw.dim_role role ON role.role_key = legacy.role_key
        LEFT JOIN dw.dim_city city ON city.city_key = legacy.city_key
        GROUP BY CAST(job.observed_at_utc AS date),
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown')
                COLLATE Latin1_General_100_BIN2
    )
        THROW 53052, 'Dashboard reconciliation failed for role-city demand.', 1;

    COMMIT TRANSACTION;

    SELECT check_name, legacy_value, v2_value, N'PASSED' AS status
    FROM (VALUES
        (N'job_observations',
            (SELECT COUNT_BIG(*) FROM dw.fact_job),
            (SELECT COUNT_BIG(*) FROM dbo.fact_job_observation)),
        (N'skill_facts',
            (SELECT COUNT_BIG(*) FROM dw.fact_job_necessary_skill),
            (SELECT COUNT_BIG(*) FROM dbo.fact_job_necessary_skill)),
        (N'role_city_facts',
            (SELECT COUNT_BIG(*) FROM dw.fact_job_role_city),
            (SELECT COUNT_BIG(*) FROM dbo.fact_job_role_city)),
        (N'jobs_by_date_source', CONVERT(bigint, 1), CONVERT(bigint, 1)),
        (N'skill_demand', CONVERT(bigint, 1), CONVERT(bigint, 1)),
        (N'role_city_demand', CONVERT(bigint, 1), CONVERT(bigint, 1))
    ) result(check_name, legacy_value, v2_value);
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
