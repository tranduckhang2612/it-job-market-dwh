-- Phase 3: append legacy observations, skill facts, and role-city facts into dw_v2.
-- Both the legacy schema and existing v2 facts are immutable inputs to this backfill.
SET XACT_ABORT ON;
SET NOCOUNT ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @lock_result int;
    EXEC @lock_result = sys.sp_getapplock
        @Resource = N'it_job_market_dw:dw_v2:backfill_facts',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 0;
    IF @lock_result < 0
        THROW 52000, 'Another dw_v2 fact backfill is already running.', 1;

    IF OBJECT_ID(N'dw.fact_job', N'U') IS NULL
        THROW 52001, 'Missing legacy table dw.fact_job.', 1;
    IF OBJECT_ID(N'dw.fact_job_necessary_skill', N'U') IS NULL
        THROW 52002, 'Missing legacy table dw.fact_job_necessary_skill.', 1;
    IF OBJECT_ID(N'dw.fact_job_role_city', N'U') IS NULL
        THROW 52003, 'Missing legacy table dw.fact_job_role_city.', 1;
    IF OBJECT_ID(N'dw_v2_migration.map_job', N'U') IS NULL
        THROW 52004, 'Missing phase-two job mapping.', 1;
    IF OBJECT_ID(N'dw_v2_migration.map_skill', N'U') IS NULL
        THROW 52005, 'Missing phase-two skill mapping.', 1;
    IF OBJECT_ID(N'dw_v2_migration.map_role', N'U') IS NULL
        THROW 52006, 'Missing phase-two role mapping.', 1;
    IF OBJECT_ID(N'dw_v2_migration.map_city', N'U') IS NULL
        THROW 52007, 'Missing phase-two city mapping.', 1;
    IF OBJECT_ID(N'dw_v2.fact_job_observation', N'U') IS NULL
       OR OBJECT_ID(N'dw_v2.fact_job_necessary_skill', N'U') IS NULL
       OR OBJECT_ID(N'dw_v2.fact_job_role_city', N'U') IS NULL
        THROW 52008, 'Missing phase-one fact tables.', 1;

    DROP TABLE IF EXISTS #observation_source;
    DROP TABLE IF EXISTS #skill_source;
    DROP TABLE IF EXISTS #role_city_source;

    SELECT
        legacy.fact_job_key AS legacy_fact_job_key,
        mapping.observation_id,
        mapping.job_key,
        mapping.date_key,
        mapping.source_key,
        mapping.company_key,
        mapping.crawl_run_key,
        legacy.observed_at_utc,
        legacy.title AS job_title_snapshot
    INTO #observation_source
    FROM dw.fact_job legacy
    JOIN dw_v2_migration.map_job mapping
      ON mapping.legacy_fact_job_key = legacy.fact_job_key;

    IF (SELECT COUNT(*) FROM dw.fact_job)
        <> (SELECT COUNT(*) FROM #observation_source)
        THROW 52010, 'Observation source mapping is incomplete.', 1;

    INSERT INTO dw_v2.fact_job_observation(
        observation_id, job_key, date_key, source_key, company_key, crawl_run_key,
        observed_at_utc, job_title_snapshot, work_mode
    )
    SELECT
        source.observation_id,
        source.job_key,
        source.date_key,
        source.source_key,
        source.company_key,
        source.crawl_run_key,
        source.observed_at_utc,
        source.job_title_snapshot,
        NULL
    FROM #observation_source source
    WHERE NOT EXISTS (
        SELECT 1
        FROM dw_v2.fact_job_observation target
        WHERE target.job_key = source.job_key
          AND target.crawl_run_key = source.crawl_run_key
    );

    IF EXISTS (
        SELECT 1
        FROM #observation_source source
        LEFT JOIN dw_v2.fact_job_observation target
          ON target.job_key = source.job_key
         AND target.crawl_run_key = source.crawl_run_key
         AND target.observation_id = source.observation_id
         AND target.date_key = source.date_key
         AND target.source_key = source.source_key
         AND target.company_key = source.company_key
         AND target.observed_at_utc = source.observed_at_utc
         AND ISNULL(target.job_title_snapshot, N'') = ISNULL(source.job_title_snapshot, N'')
        WHERE target.job_observation_key IS NULL
    )
        THROW 52011, 'Observation fact reconciliation failed.', 1;

    IF EXISTS (
        SELECT mapping.legacy_necessary_skill_key
        FROM dw_v2_migration.map_skill mapping
        LEFT JOIN dw.dim_necessary_skill legacy
          ON legacy.necessary_skill_key = mapping.legacy_necessary_skill_key
        WHERE legacy.necessary_skill_key IS NULL
    )
        THROW 52012, 'Skill mapping refers to a missing legacy dimension row.', 1;

    SELECT
        legacy.fact_job_necessary_skill_key AS legacy_fact_skill_key,
        job.observation_id,
        job.job_key,
        job.date_key,
        job.source_key,
        job.crawl_run_key,
        skill.skill_key,
        COALESCE(level_map.proficiency_level_key, 0) AS proficiency_level_key,
        legacy.context_no,
        legacy.level_text AS level_text_snapshot
    INTO #skill_source
    FROM dw.fact_job_necessary_skill legacy
    JOIN dw_v2_migration.map_job job
      ON job.legacy_fact_job_key = legacy.fact_job_key
    JOIN dw_v2_migration.map_skill skill
      ON skill.legacy_necessary_skill_key = legacy.necessary_skill_key
    LEFT JOIN dw_v2_migration.map_proficiency level_map
      ON level_map.normalized_level = LOWER(LTRIM(RTRIM(legacy.level_text)))
          COLLATE Latin1_General_100_BIN2;

    IF (SELECT COUNT(*) FROM dw.fact_job_necessary_skill)
        <> (SELECT COUNT(*) FROM #skill_source)
        THROW 52013, 'Skill fact source mapping is incomplete.', 1;

    IF EXISTS (
        SELECT job_key, crawl_run_key, skill_key, context_no
        FROM #skill_source
        GROUP BY job_key, crawl_run_key, skill_key, context_no
        HAVING COUNT(*) > 1
    )
        THROW 52014, 'Skill mapping would collapse legacy occurrences.', 1;

    INSERT INTO dw_v2.fact_job_necessary_skill(
        observation_id, job_key, date_key, source_key, crawl_run_key,
        skill_key, proficiency_level_key, context_no, level_text_snapshot
    )
    SELECT
        source.observation_id,
        source.job_key,
        source.date_key,
        source.source_key,
        source.crawl_run_key,
        source.skill_key,
        source.proficiency_level_key,
        source.context_no,
        source.level_text_snapshot
    FROM #skill_source source
    WHERE NOT EXISTS (
        SELECT 1
        FROM dw_v2.fact_job_necessary_skill target
        WHERE target.job_key = source.job_key
          AND target.crawl_run_key = source.crawl_run_key
          AND target.skill_key = source.skill_key
          AND target.context_no = source.context_no
    );

    IF EXISTS (
        SELECT 1
        FROM #skill_source source
        LEFT JOIN dw_v2.fact_job_necessary_skill target
          ON target.job_key = source.job_key
         AND target.crawl_run_key = source.crawl_run_key
         AND target.skill_key = source.skill_key
         AND target.context_no = source.context_no
         AND target.observation_id = source.observation_id
         AND target.date_key = source.date_key
         AND target.source_key = source.source_key
         AND target.proficiency_level_key = source.proficiency_level_key
         AND ISNULL(target.level_text_snapshot, N'') = ISNULL(source.level_text_snapshot, N'')
        WHERE target.job_necessary_skill_key IS NULL
    )
        THROW 52015, 'Skill fact reconciliation failed.', 1;

    IF EXISTS (
        SELECT 1
        FROM dw.fact_job_role_city legacy
        LEFT JOIN dw_v2_migration.map_role role_map
          ON role_map.legacy_role_key = legacy.role_key
        WHERE legacy.role_key IS NOT NULL AND role_map.legacy_role_key IS NULL
    )
        THROW 52016, 'Role fact mapping is incomplete.', 1;

    IF EXISTS (
        SELECT 1
        FROM dw.fact_job_role_city legacy
        LEFT JOIN dw_v2_migration.map_city city_map
          ON city_map.legacy_city_key = legacy.city_key
        WHERE legacy.city_key IS NOT NULL AND city_map.legacy_city_key IS NULL
    )
        THROW 52017, 'City fact mapping is incomplete.', 1;

    SELECT
        legacy.fact_job_role_city_key AS legacy_fact_role_city_key,
        job.observation_id,
        job.job_key,
        job.date_key,
        job.source_key,
        job.crawl_run_key,
        COALESCE(role_map.role_key, 0) AS role_key,
        COALESCE(city_map.city_key, 0) AS city_key
    INTO #role_city_source
    FROM dw.fact_job_role_city legacy
    JOIN dw_v2_migration.map_job job
      ON job.legacy_fact_job_key = legacy.fact_job_key
    LEFT JOIN dw_v2_migration.map_role role_map
      ON role_map.legacy_role_key = legacy.role_key
    LEFT JOIN dw_v2_migration.map_city city_map
      ON city_map.legacy_city_key = legacy.city_key;

    IF (SELECT COUNT(*) FROM dw.fact_job_role_city)
        <> (SELECT COUNT(*) FROM #role_city_source)
        THROW 52018, 'Role-city fact source mapping is incomplete.', 1;

    IF EXISTS (
        SELECT job_key, crawl_run_key, role_key, city_key
        FROM #role_city_source
        GROUP BY job_key, crawl_run_key, role_key, city_key
        HAVING COUNT(*) > 1
    )
        THROW 52019, 'Role-city mapping would collapse legacy occurrences.', 1;

    INSERT INTO dw_v2.fact_job_role_city(
        observation_id, job_key, date_key, source_key, crawl_run_key, role_key, city_key
    )
    SELECT
        source.observation_id,
        source.job_key,
        source.date_key,
        source.source_key,
        source.crawl_run_key,
        source.role_key,
        source.city_key
    FROM #role_city_source source
    WHERE NOT EXISTS (
        SELECT 1
        FROM dw_v2.fact_job_role_city target
        WHERE target.job_key = source.job_key
          AND target.crawl_run_key = source.crawl_run_key
          AND target.role_key = source.role_key
          AND target.city_key = source.city_key
    );

    IF EXISTS (
        SELECT 1
        FROM #role_city_source source
        LEFT JOIN dw_v2.fact_job_role_city target
          ON target.job_key = source.job_key
         AND target.crawl_run_key = source.crawl_run_key
         AND target.role_key = source.role_key
         AND target.city_key = source.city_key
         AND target.observation_id = source.observation_id
         AND target.date_key = source.date_key
         AND target.source_key = source.source_key
        WHERE target.job_role_city_key IS NULL
    )
        THROW 52020, 'Role-city fact reconciliation failed.', 1;

    COMMIT TRANSACTION;

    SELECT
        (SELECT COUNT(*) FROM dw_v2.fact_job_observation) AS observations,
        (SELECT COUNT(*) FROM dw_v2.fact_job_necessary_skill) AS skill_facts,
        (SELECT COUNT(*) FROM dw_v2.fact_job_role_city) AS role_city_facts;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
