-- Phase 6: validate the latest consumer snapshot and publish read-only BI/ML access.
-- Historical facts are intentionally not compared by total row count with the legacy snapshot.
SET XACT_ABORT ON;
SET NOCOUNT ON;
SET ANSI_NULLS ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @lock_result int;
    EXEC @lock_result = sys.sp_getapplock
        @Resource = N'it_job_market_dw:dw_v2:consumer_cutover',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 0;
    IF @lock_result < 0
        THROW 55000, 'Another dw_v2 consumer cutover is already running.', 1;

    IF OBJECT_ID(N'dw.fact_job', N'U') IS NULL
       OR OBJECT_ID(N'dw.fact_job_necessary_skill', N'U') IS NULL
       OR OBJECT_ID(N'dw.fact_job_role_city', N'U') IS NULL
        THROW 55001, 'Legacy consumer facts are incomplete.', 1;

    IF OBJECT_ID(N'semantic.job_observation', N'V') IS NULL
       OR OBJECT_ID(N'semantic.latest_job', N'V') IS NULL
       OR OBJECT_ID(N'semantic.job_skill', N'V') IS NULL
       OR OBJECT_ID(N'semantic.job_role_city', N'V') IS NULL
       OR OBJECT_ID(N'semantic.skill_demand_daily', N'V') IS NULL
       OR OBJECT_ID(N'semantic.role_city_demand_daily', N'V') IS NULL
       OR OBJECT_ID(N'dw_v2_compat.fact_job', N'V') IS NULL
       OR OBJECT_ID(N'dw_v2_compat.job_necessary_skill', N'V') IS NULL
       OR OBJECT_ID(N'dw_v2_compat.job_role_city', N'V') IS NULL
        THROW 55002, 'Phase-five semantic or compatibility views are incomplete.', 1;

    -- The compatibility layer must reproduce the current legacy snapshot. The
    -- historical semantic layer may contain more than one observation per job.
    IF (SELECT COUNT_BIG(*) FROM dw.fact_job)
       <> (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job)
        THROW 55010, 'Latest job snapshot row count mismatch.', 1;

    IF EXISTS (
        SELECT
            job_id COLLATE Latin1_General_100_BIN2,
            source COLLATE Latin1_General_100_BIN2,
            ISNULL(title, N'') COLLATE Latin1_General_100_BIN2,
            LOWER(LTRIM(RTRIM(ISNULL(company_name, N'')))) COLLATE Latin1_General_100_BIN2,
            ISNULL(job_url, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(crawl_run_id, N'') COLLATE Latin1_General_100_BIN2,
            observed_at_utc,
            observed_date_key,
            job_posting_count
        FROM dw.fact_job
        EXCEPT
        SELECT
            job_id, source,
            ISNULL(title, N'') COLLATE Latin1_General_100_BIN2,
            LOWER(LTRIM(RTRIM(ISNULL(company_name, N'')))) COLLATE Latin1_General_100_BIN2,
            ISNULL(job_url, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(crawl_run_id, N'') COLLATE Latin1_General_100_BIN2,
            observed_at_utc,
            observed_date_key,
            job_posting_count
        FROM dw_v2_compat.fact_job
    ) OR EXISTS (
        SELECT
            job_id, source,
            ISNULL(title, N'') COLLATE Latin1_General_100_BIN2,
            LOWER(LTRIM(RTRIM(ISNULL(company_name, N'')))) COLLATE Latin1_General_100_BIN2,
            ISNULL(job_url, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(crawl_run_id, N'') COLLATE Latin1_General_100_BIN2,
            observed_at_utc,
            observed_date_key,
            job_posting_count
        FROM dw_v2_compat.fact_job
        EXCEPT
        SELECT
            job_id COLLATE Latin1_General_100_BIN2,
            source COLLATE Latin1_General_100_BIN2,
            ISNULL(title, N'') COLLATE Latin1_General_100_BIN2,
            LOWER(LTRIM(RTRIM(ISNULL(company_name, N'')))) COLLATE Latin1_General_100_BIN2,
            ISNULL(job_url, N'') COLLATE Latin1_General_100_BIN2,
            ISNULL(crawl_run_id, N'') COLLATE Latin1_General_100_BIN2,
            observed_at_utc,
            observed_date_key,
            job_posting_count
        FROM dw.fact_job
    )
        THROW 55011, 'Latest job snapshot content mismatch.', 1;

    IF (SELECT COUNT_BIG(*) FROM dw.fact_job_necessary_skill)
       <> (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job_necessary_skill)
        THROW 55012, 'Latest skill snapshot row count mismatch.', 1;

    IF EXISTS (
        SELECT
            job.job_id COLLATE Latin1_General_100_BIN2,
            job.crawl_run_id COLLATE Latin1_General_100_BIN2,
            skill.skill_type COLLATE Latin1_General_100_BIN2,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category COLLATE Latin1_General_100_BIN2,
            fact.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(fact.level_text))), N'') COLLATE Latin1_General_100_BIN2,
            fact.skill_mention_count
        FROM dw.fact_job_necessary_skill fact
        JOIN dw.fact_job job ON job.fact_job_key = fact.fact_job_key
        JOIN dw.dim_necessary_skill skill
          ON skill.necessary_skill_key = fact.necessary_skill_key
        EXCEPT
        SELECT
            job.job_id,
            job.crawl_run_id,
            fact.skill_type,
            LOWER(LTRIM(RTRIM(fact.skill_name))) COLLATE Latin1_General_100_BIN2,
            fact.category,
            fact.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(fact.level_text))), N'') COLLATE Latin1_General_100_BIN2,
            fact.skill_mention_count
        FROM dw_v2_compat.job_necessary_skill fact
        JOIN dw_v2_compat.fact_job job ON job.fact_job_key = fact.fact_job_key
    ) OR EXISTS (
        SELECT
            job.job_id,
            job.crawl_run_id,
            fact.skill_type,
            LOWER(LTRIM(RTRIM(fact.skill_name))) COLLATE Latin1_General_100_BIN2,
            fact.category,
            fact.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(fact.level_text))), N'') COLLATE Latin1_General_100_BIN2,
            fact.skill_mention_count
        FROM dw_v2_compat.job_necessary_skill fact
        JOIN dw_v2_compat.fact_job job ON job.fact_job_key = fact.fact_job_key
        EXCEPT
        SELECT
            job.job_id COLLATE Latin1_General_100_BIN2,
            job.crawl_run_id COLLATE Latin1_General_100_BIN2,
            skill.skill_type COLLATE Latin1_General_100_BIN2,
            LOWER(LTRIM(RTRIM(skill.name))) COLLATE Latin1_General_100_BIN2,
            skill.category COLLATE Latin1_General_100_BIN2,
            fact.context_no,
            ISNULL(LOWER(LTRIM(RTRIM(fact.level_text))), N'') COLLATE Latin1_General_100_BIN2,
            fact.skill_mention_count
        FROM dw.fact_job_necessary_skill fact
        JOIN dw.fact_job job ON job.fact_job_key = fact.fact_job_key
        JOIN dw.dim_necessary_skill skill
          ON skill.necessary_skill_key = fact.necessary_skill_key
    )
        THROW 55013, 'Latest skill snapshot content mismatch.', 1;

    IF (SELECT COUNT_BIG(*) FROM dw.fact_job_role_city)
       <> (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job_role_city)
        THROW 55014, 'Latest role-city snapshot row count mismatch.', 1;

    IF EXISTS (
        SELECT
            job.job_id COLLATE Latin1_General_100_BIN2,
            job.crawl_run_id COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown') COLLATE Latin1_General_100_BIN2,
            COALESCE(role.category, N'unknown') COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown') COLLATE Latin1_General_100_BIN2,
            fact.combination_count
        FROM dw.fact_job_role_city fact
        JOIN dw.fact_job job ON job.fact_job_key = fact.fact_job_key
        LEFT JOIN dw.dim_role role ON role.role_key = fact.role_key
        LEFT JOIN dw.dim_city city ON city.city_key = fact.city_key
        EXCEPT
        SELECT
            job.job_id,
            job.crawl_run_id,
            LOWER(LTRIM(RTRIM(fact.role_name))) COLLATE Latin1_General_100_BIN2,
            fact.role_category,
            LOWER(LTRIM(RTRIM(fact.city_name))) COLLATE Latin1_General_100_BIN2,
            fact.combination_count
        FROM dw_v2_compat.job_role_city fact
        JOIN dw_v2_compat.fact_job job ON job.fact_job_key = fact.fact_job_key
    ) OR EXISTS (
        SELECT
            job.job_id,
            job.crawl_run_id,
            LOWER(LTRIM(RTRIM(fact.role_name))) COLLATE Latin1_General_100_BIN2,
            fact.role_category,
            LOWER(LTRIM(RTRIM(fact.city_name))) COLLATE Latin1_General_100_BIN2,
            fact.combination_count
        FROM dw_v2_compat.job_role_city fact
        JOIN dw_v2_compat.fact_job job ON job.fact_job_key = fact.fact_job_key
        EXCEPT
        SELECT
            job.job_id COLLATE Latin1_General_100_BIN2,
            job.crawl_run_id COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(role.name))), N'unknown') COLLATE Latin1_General_100_BIN2,
            COALESCE(role.category, N'unknown') COLLATE Latin1_General_100_BIN2,
            COALESCE(LOWER(LTRIM(RTRIM(city.name))), N'unknown') COLLATE Latin1_General_100_BIN2,
            fact.combination_count
        FROM dw.fact_job_role_city fact
        JOIN dw.fact_job job ON job.fact_job_key = fact.fact_job_key
        LEFT JOIN dw.dim_role role ON role.role_key = fact.role_key
        LEFT JOIN dw.dim_city city ON city.city_key = fact.city_key
    )
        THROW 55015, 'Latest role-city snapshot content mismatch.', 1;

    IF (SELECT COUNT_BIG(*) FROM semantic.job_observation)
       < (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job)
       OR (SELECT COUNT_BIG(*) FROM semantic.job_skill)
       < (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job_necessary_skill)
       OR (SELECT COUNT_BIG(*) FROM semantic.job_role_city)
       < (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job_role_city)
        THROW 55016, 'Historical semantic facts are smaller than the latest snapshot.', 1;

    IF DATABASE_PRINCIPAL_ID(N'bi_ml_reader') IS NULL
        CREATE ROLE [bi_ml_reader] AUTHORIZATION [dbo];

    GRANT SELECT, VIEW DEFINITION ON SCHEMA::[semantic] TO [bi_ml_reader];
    GRANT SELECT, VIEW DEFINITION ON SCHEMA::[dw_v2_compat] TO [bi_ml_reader];
    DENY SELECT ON SCHEMA::[dbo] TO [bi_ml_reader];

    COMMIT TRANSACTION;

    SELECT check_name, legacy_value, published_value, N'PASSED' AS status
    FROM (VALUES
        (N'latest_jobs',
            (SELECT COUNT_BIG(*) FROM dw.fact_job),
            (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job)),
        (N'latest_skills',
            (SELECT COUNT_BIG(*) FROM dw.fact_job_necessary_skill),
            (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job_necessary_skill)),
        (N'latest_role_city',
            (SELECT COUNT_BIG(*) FROM dw.fact_job_role_city),
            (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job_role_city)),
        (N'historical_observations',
            (SELECT COUNT_BIG(*) FROM dw.fact_job),
            (SELECT COUNT_BIG(*) FROM semantic.job_observation))
    ) result(check_name, legacy_value, published_value);
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
