-- Phase 7: publish dw_v2 without reading or updating the legacy dw schema.
-- Run only after the direct loader has committed successfully.
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
        @Resource = N'it_job_market_dw:dw_v2:direct_publish',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 0;
    IF @lock_result < 0
        THROW 57000, 'Another direct dw_v2 publish is already running.', 1;

    IF OBJECT_ID(N'dbo.fact_job_observation', N'U') IS NULL
       OR OBJECT_ID(N'dbo.fact_job_necessary_skill', N'U') IS NULL
       OR OBJECT_ID(N'dbo.fact_job_role_city', N'U') IS NULL
        THROW 57001, 'Direct dw_v2 fact tables are incomplete.', 1;

    IF OBJECT_ID(N'semantic.job_observation', N'V') IS NULL
       OR OBJECT_ID(N'semantic.latest_job', N'V') IS NULL
       OR OBJECT_ID(N'semantic.job_skill', N'V') IS NULL
       OR OBJECT_ID(N'semantic.job_role_city', N'V') IS NULL
       OR OBJECT_ID(N'dw_v2_compat.fact_job', N'V') IS NULL
        THROW 57002, 'Semantic or compatibility views are incomplete.', 1;

    IF NOT EXISTS (SELECT 1 FROM dbo.fact_job_observation)
        THROW 57003, 'Direct publish rejected an empty observation fact.', 1;

    -- Child facts deliberately do not have a fact-to-fact foreign key. Validate
    -- their shared degenerate lineage and conformed dimensional keys explicitly.
    IF EXISTS (
        SELECT 1
        FROM dbo.fact_job_necessary_skill child
        LEFT JOIN dbo.fact_job_observation parent
          ON parent.observation_id = child.observation_id
         AND parent.job_key = child.job_key
         AND parent.crawl_run_key = child.crawl_run_key
         AND parent.date_key = child.date_key
         AND parent.source_key = child.source_key
        WHERE parent.job_observation_key IS NULL
    )
        THROW 57010, 'Skill fact lineage does not match an observation.', 1;

    IF EXISTS (
        SELECT 1
        FROM dbo.fact_job_role_city child
        LEFT JOIN dbo.fact_job_observation parent
          ON parent.observation_id = child.observation_id
         AND parent.job_key = child.job_key
         AND parent.crawl_run_key = child.crawl_run_key
         AND parent.date_key = child.date_key
         AND parent.source_key = child.source_key
        WHERE parent.job_observation_key IS NULL
    )
        THROW 57011, 'Role-city fact lineage does not match an observation.', 1;

    -- Inner-join semantic views must expose every physical fact row. A mismatch
    -- indicates a broken dimension relationship or an invalid view contract.
    IF (SELECT COUNT_BIG(*) FROM semantic.job_observation)
       <> (SELECT COUNT_BIG(*) FROM dbo.fact_job_observation)
        THROW 57012, 'Semantic observation row count mismatch.', 1;

    IF (SELECT COUNT_BIG(*) FROM semantic.job_skill)
       <> (SELECT COUNT_BIG(*) FROM dbo.fact_job_necessary_skill)
        THROW 57013, 'Semantic skill row count mismatch.', 1;

    IF (SELECT COUNT_BIG(*) FROM semantic.job_role_city)
       <> (SELECT COUNT_BIG(*) FROM dbo.fact_job_role_city)
        THROW 57014, 'Semantic role-city row count mismatch.', 1;

    IF (SELECT COUNT_BIG(*) FROM semantic.latest_job)
       <> (SELECT COUNT_BIG(*) FROM dbo.dim_job)
        THROW 57015, 'Latest-job contract does not expose one row per job.', 1;

    IF (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job)
       <> (SELECT COUNT_BIG(*) FROM semantic.latest_job)
        THROW 57016, 'Compatibility latest-job row count mismatch.', 1;

    IF DATABASE_PRINCIPAL_ID(N'bi_ml_reader') IS NULL
        CREATE ROLE [bi_ml_reader] AUTHORIZATION [dbo];

    GRANT SELECT, VIEW DEFINITION ON SCHEMA::[semantic] TO [bi_ml_reader];
    GRANT SELECT, VIEW DEFINITION ON SCHEMA::[dw_v2_compat] TO [bi_ml_reader];
    DENY SELECT ON SCHEMA::[dbo] TO [bi_ml_reader];

    COMMIT TRANSACTION;

    SELECT check_name, physical_value, published_value, N'PASSED' AS status
    FROM (VALUES
        (N'observations',
            (SELECT COUNT_BIG(*) FROM dbo.fact_job_observation),
            (SELECT COUNT_BIG(*) FROM semantic.job_observation)),
        (N'skills',
            (SELECT COUNT_BIG(*) FROM dbo.fact_job_necessary_skill),
            (SELECT COUNT_BIG(*) FROM semantic.job_skill)),
        (N'role_city',
            (SELECT COUNT_BIG(*) FROM dbo.fact_job_role_city),
            (SELECT COUNT_BIG(*) FROM semantic.job_role_city)),
        (N'latest_jobs',
            (SELECT COUNT_BIG(*) FROM semantic.latest_job),
            (SELECT COUNT_BIG(*) FROM dw_v2_compat.fact_job))
    ) result(check_name, physical_value, published_value);
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
