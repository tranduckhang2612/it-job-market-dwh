-- Phase 2: backfill conformed dimensions and legacy-to-v2 key mappings.
-- The legacy dw schema is read-only in this migration. No facts are loaded yet.
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
        @Resource = N'it_job_market_dw:dw_v2:backfill_dimensions',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 0;
    IF @lock_result < 0
        THROW 51000, 'Another dw_v2 dimension backfill is already running.', 1;

    IF OBJECT_ID(N'dw.fact_job', N'U') IS NULL
        THROW 51001, 'Missing legacy table dw.fact_job.', 1;
    IF OBJECT_ID(N'dw.dim_necessary_skill', N'U') IS NULL
        THROW 51002, 'Missing legacy table dw.dim_necessary_skill.', 1;
    IF OBJECT_ID(N'dw.dim_role', N'U') IS NULL
        THROW 51003, 'Missing legacy table dw.dim_role.', 1;
    IF OBJECT_ID(N'dw.dim_city', N'U') IS NULL
        THROW 51004, 'Missing legacy table dw.dim_city.', 1;
    IF OBJECT_ID(N'dw.fact_job_necessary_skill', N'U') IS NULL
        THROW 51005, 'Missing legacy table dw.fact_job_necessary_skill.', 1;
    IF OBJECT_ID(N'dbo.fact_job_observation', N'U') IS NULL
        THROW 51006, 'Missing phase-one schema. Run dw_v2.sql first.', 1;

    DROP TABLE IF EXISTS #legacy_dates;
    DROP TABLE IF EXISTS #legacy_sources;
    DROP TABLE IF EXISTS #legacy_companies;
    DROP TABLE IF EXISTS #legacy_runs;
    DROP TABLE IF EXISTS #legacy_skills;
    DROP TABLE IF EXISTS #legacy_roles;
    DROP TABLE IF EXISTS #legacy_cities;
    DROP TABLE IF EXISTS #legacy_levels;

    IF SCHEMA_ID(N'dw_v2_migration') IS NULL
        EXEC(N'CREATE SCHEMA dw_v2_migration');

    IF OBJECT_ID(N'dw_v2_migration.map_job', N'U') IS NULL
        CREATE TABLE dw_v2_migration.map_job (
            legacy_fact_job_key int NOT NULL
                CONSTRAINT PK_dw_v2_migration_map_job PRIMARY KEY,
            observation_id nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            job_key bigint NOT NULL,
            date_key int NOT NULL,
            source_key int NOT NULL,
            company_key bigint NOT NULL,
            crawl_run_key bigint NOT NULL,
            CONSTRAINT UQ_dw_v2_migration_map_job_observation UNIQUE(observation_id),
            CONSTRAINT FK_dw_v2_migration_map_job_job FOREIGN KEY(job_key)
                REFERENCES dbo.dim_job(job_key),
            CONSTRAINT FK_dw_v2_migration_map_job_date FOREIGN KEY(date_key)
                REFERENCES dbo.dim_date(date_key),
            CONSTRAINT FK_dw_v2_migration_map_job_source FOREIGN KEY(source_key)
                REFERENCES dbo.dim_source(source_key),
            CONSTRAINT FK_dw_v2_migration_map_job_company FOREIGN KEY(company_key)
                REFERENCES dbo.dim_company(company_key),
            CONSTRAINT FK_dw_v2_migration_map_job_run FOREIGN KEY(crawl_run_key)
                REFERENCES dbo.dim_crawl_run(crawl_run_key)
        );

    IF OBJECT_ID(N'dw_v2_migration.map_skill', N'U') IS NULL
        CREATE TABLE dw_v2_migration.map_skill (
            legacy_necessary_skill_key int NOT NULL
                CONSTRAINT PK_dw_v2_migration_map_skill PRIMARY KEY,
            skill_key bigint NOT NULL,
            CONSTRAINT FK_dw_v2_migration_map_skill_skill FOREIGN KEY(skill_key)
                REFERENCES dbo.dim_skill(skill_key)
        );

    IF OBJECT_ID(N'dw_v2_migration.map_role', N'U') IS NULL
        CREATE TABLE dw_v2_migration.map_role (
            legacy_role_key int NOT NULL
                CONSTRAINT PK_dw_v2_migration_map_role PRIMARY KEY,
            role_key bigint NOT NULL,
            CONSTRAINT FK_dw_v2_migration_map_role_role FOREIGN KEY(role_key)
                REFERENCES dbo.dim_role(role_key)
        );

    IF OBJECT_ID(N'dw_v2_migration.map_city', N'U') IS NULL
        CREATE TABLE dw_v2_migration.map_city (
            legacy_city_key int NOT NULL
                CONSTRAINT PK_dw_v2_migration_map_city PRIMARY KEY,
            city_key int NOT NULL,
            CONSTRAINT FK_dw_v2_migration_map_city_city FOREIGN KEY(city_key)
                REFERENCES dbo.dim_city(city_key)
        );

    IF OBJECT_ID(N'dw_v2_migration.map_proficiency', N'U') IS NULL
        CREATE TABLE dw_v2_migration.map_proficiency (
            normalized_level nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL
                CONSTRAINT PK_dw_v2_migration_map_proficiency PRIMARY KEY,
            proficiency_level_key int NOT NULL,
            CONSTRAINT FK_dw_v2_migration_map_proficiency_level
                FOREIGN KEY(proficiency_level_key)
                REFERENCES dbo.dim_proficiency_level(proficiency_level_key)
        );

    SELECT DISTINCT CAST(observed_at_utc AS date) AS calendar_date
    INTO #legacy_dates
    FROM dw.fact_job;

    INSERT INTO dbo.dim_date(
        date_key, calendar_date, day_of_week, day_name,
        month_no, month_name, quarter_no, year_no, is_weekend
    )
    SELECT
        CONVERT(int, CONVERT(char(8), d.calendar_date, 112)),
        d.calendar_date,
        parts.day_of_week,
        CHOOSE(parts.day_of_week,
            N'Monday', N'Tuesday', N'Wednesday', N'Thursday', N'Friday', N'Saturday', N'Sunday'),
        DATEPART(month, d.calendar_date),
        CHOOSE(DATEPART(month, d.calendar_date),
            N'January', N'February', N'March', N'April', N'May', N'June',
            N'July', N'August', N'September', N'October', N'November', N'December'),
        DATEPART(quarter, d.calendar_date),
        DATEPART(year, d.calendar_date),
        CASE WHEN parts.day_of_week IN (6, 7) THEN 1 ELSE 0 END
    FROM #legacy_dates d
    CROSS APPLY (
        VALUES (CONVERT(tinyint, ((DATEDIFF(day, CONVERT(date, '19000101'), d.calendar_date) % 7 + 7) % 7) + 1))
    ) parts(day_of_week)
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_date target WHERE target.calendar_date = d.calendar_date
    );

    SELECT
        source COLLATE Latin1_General_100_BIN2 AS source_code,
        CASE source WHEN N'itviec' THEN N'ITviec' WHEN N'topdev' THEN N'TopDev' ELSE source END
            AS source_name
    INTO #legacy_sources
    FROM dw.fact_job
    GROUP BY source;

    INSERT INTO dbo.dim_source(source_code, source_name, is_active)
    SELECT source_code, source_name, 1
    FROM #legacy_sources s
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_source target WHERE target.source_code = s.source_code
    );

    UPDATE target
    SET source_name = s.source_name, is_active = 1, updated_at_utc = SYSUTCDATETIME()
    FROM dbo.dim_source target
    JOIN #legacy_sources s ON s.source_code = target.source_code
    WHERE target.source_name <> s.source_name OR target.is_active <> 1;

    SELECT
        MIN(LTRIM(RTRIM(company_name))) AS company_name,
        LOWER(LTRIM(RTRIM(company_name))) COLLATE Latin1_General_100_BIN2 AS normalized_name
    INTO #legacy_companies
    FROM dw.fact_job
    WHERE NULLIF(LTRIM(RTRIM(company_name)), N'') IS NOT NULL
    GROUP BY LOWER(LTRIM(RTRIM(company_name))) COLLATE Latin1_General_100_BIN2;

    INSERT INTO dbo.dim_company(company_name, normalized_name)
    SELECT company_name, normalized_name
    FROM #legacy_companies c
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_company target WHERE target.normalized_name = c.normalized_name
    );

    UPDATE target
    SET company_name = c.company_name, updated_at_utc = SYSUTCDATETIME()
    FROM dbo.dim_company target
    JOIN #legacy_companies c ON c.normalized_name = target.normalized_name
    WHERE target.company_name <> c.company_name;

    SELECT
        COALESCE(NULLIF(LTRIM(RTRIM(crawl_run_id)), N''), N'legacy:unknown')
            COLLATE Latin1_General_100_BIN2 AS crawl_run_id,
        CASE WHEN COUNT(DISTINCT source) = 1 THEN MIN(source) ELSE N'multi' END AS source_scope,
        MIN(observed_at_utc) AS started_at_utc,
        MAX(observed_at_utc) AS finished_at_utc
    INTO #legacy_runs
    FROM dw.fact_job
    GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(crawl_run_id)), N''), N'legacy:unknown');

    INSERT INTO dbo.dim_crawl_run(
        crawl_run_id, source_scope, pipeline_version,
        started_at_utc, finished_at_utc, run_status
    )
    SELECT crawl_run_id, source_scope, N'legacy', started_at_utc, finished_at_utc, N'succeeded'
    FROM #legacy_runs r
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_crawl_run target WHERE target.crawl_run_id = r.crawl_run_id
    );

    UPDATE target
    SET source_scope = r.source_scope,
        pipeline_version = COALESCE(target.pipeline_version, N'legacy'),
        started_at_utc = CASE WHEN r.started_at_utc < target.started_at_utc
            THEN r.started_at_utc ELSE target.started_at_utc END,
        finished_at_utc = CASE WHEN target.finished_at_utc IS NULL
                                   OR r.finished_at_utc > target.finished_at_utc
            THEN r.finished_at_utc ELSE target.finished_at_utc END,
        run_status = N'succeeded'
    FROM dbo.dim_crawl_run target
    JOIN #legacy_runs r ON r.crawl_run_id = target.crawl_run_id;

    INSERT INTO dbo.dim_job(
        job_id, source_job_id, canonical_job_url, first_seen_at_utc, last_seen_at_utc
    )
    SELECT
        legacy.job_id,
        CASE WHEN CHARINDEX(N':', legacy.job_id) > 0
            THEN SUBSTRING(legacy.job_id, CHARINDEX(N':', legacy.job_id) + 1, 256)
            ELSE legacy.job_id END,
        legacy.job_url,
        legacy.observed_at_utc,
        legacy.observed_at_utc
    FROM dw.fact_job legacy
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_job target WHERE target.job_id = legacy.job_id
    );

    UPDATE target
    SET source_job_id = CASE WHEN CHARINDEX(N':', legacy.job_id) > 0
            THEN SUBSTRING(legacy.job_id, CHARINDEX(N':', legacy.job_id) + 1, 256)
            ELSE legacy.job_id END,
        canonical_job_url = legacy.job_url,
        first_seen_at_utc = CASE WHEN legacy.observed_at_utc < target.first_seen_at_utc
            THEN legacy.observed_at_utc ELSE target.first_seen_at_utc END,
        last_seen_at_utc = CASE WHEN legacy.observed_at_utc > target.last_seen_at_utc
            THEN legacy.observed_at_utc ELSE target.last_seen_at_utc END,
        updated_at_utc = SYSUTCDATETIME()
    FROM dbo.dim_job target
    JOIN dw.fact_job legacy ON legacy.job_id = target.job_id;

    CREATE TABLE #legacy_skills (
        skill_type nvarchar(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
        skill_name nvarchar(512) NOT NULL,
        normalized_name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
        category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        programming_language nvarchar(128) NULL
    );

    INSERT INTO #legacy_skills(
        skill_type, skill_name, normalized_name, category, programming_language
    )
    SELECT
        ns.skill_type,
        MIN(ns.name),
        LOWER(LTRIM(RTRIM(ns.name))) COLLATE Latin1_General_100_BIN2,
        ns.category,
        NULLIF(MAX(ISNULL(
            CASE WHEN ns.skill_type = N'framework' THEN framework.programming_language END,
            N''
        )), N'')
    FROM dw.dim_necessary_skill ns
    LEFT JOIN (
        SELECT
            LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2 AS normalized_name,
            category,
            NULLIF(MAX(ISNULL(programming_language, N'')), N'') AS programming_language
        FROM dw.dim_framework
        GROUP BY LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2, category
    ) framework
        ON framework.normalized_name = LOWER(LTRIM(RTRIM(ns.name))) COLLATE Latin1_General_100_BIN2
       AND framework.category = ns.category
    GROUP BY ns.skill_type,
        LOWER(LTRIM(RTRIM(ns.name))) COLLATE Latin1_General_100_BIN2,
        ns.category;

    INSERT INTO #legacy_skills(
        skill_type, skill_name, normalized_name, category, programming_language
    )
    SELECT
        N'soft_skill',
        MIN(name),
        LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2,
        category,
        NULL
    FROM dw.dim_soft_skill soft
    GROUP BY LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2, category
    HAVING NOT EXISTS (
        SELECT 1 FROM #legacy_skills existing
        WHERE existing.skill_type = N'soft_skill'
          AND existing.normalized_name = LOWER(LTRIM(RTRIM(soft.name))) COLLATE Latin1_General_100_BIN2
          AND existing.category = soft.category
    );

    INSERT INTO dbo.dim_skill(
        skill_type, skill_name, normalized_name, category, programming_language
    )
    SELECT skill_type, skill_name, normalized_name, category, programming_language
    FROM #legacy_skills s
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_skill target
        WHERE target.skill_type = s.skill_type
          AND target.normalized_name = s.normalized_name
          AND target.category = s.category
    );

    UPDATE target
    SET skill_name = s.skill_name,
        programming_language = s.programming_language,
        updated_at_utc = SYSUTCDATETIME()
    FROM dbo.dim_skill target
    JOIN #legacy_skills s
      ON s.skill_type = target.skill_type
     AND s.normalized_name = target.normalized_name
     AND s.category = target.category
    WHERE target.skill_name COLLATE Latin1_General_100_BIN2
            <> s.skill_name COLLATE Latin1_General_100_BIN2
       OR ISNULL(target.programming_language, N'') COLLATE Latin1_General_100_BIN2
            <> ISNULL(s.programming_language, N'') COLLATE Latin1_General_100_BIN2;

    SELECT
        MIN(name) AS role_name,
        LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2 AS normalized_name,
        category AS role_category
    INTO #legacy_roles
    FROM dw.dim_role
    GROUP BY LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2, category;

    INSERT INTO dbo.dim_role(role_name, normalized_name, role_category)
    SELECT role_name, normalized_name, role_category
    FROM #legacy_roles r
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_role target
        WHERE target.normalized_name = r.normalized_name
          AND target.role_category = r.role_category
    );

    UPDATE target
    SET role_name = r.role_name, updated_at_utc = SYSUTCDATETIME()
    FROM dbo.dim_role target
    JOIN #legacy_roles r
      ON r.normalized_name = target.normalized_name
     AND r.role_category = target.role_category
    WHERE target.role_name COLLATE Latin1_General_100_BIN2
        <> r.role_name COLLATE Latin1_General_100_BIN2;

    SELECT
        MIN(name) AS city_name,
        LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2 AS normalized_name
    INTO #legacy_cities
    FROM dw.dim_city
    GROUP BY LOWER(LTRIM(RTRIM(name))) COLLATE Latin1_General_100_BIN2;

    INSERT INTO dbo.dim_city(city_name, normalized_name, region_name)
    SELECT city_name, normalized_name, NULL
    FROM #legacy_cities c
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_city target WHERE target.normalized_name = c.normalized_name
    );

    UPDATE target
    SET city_name = c.city_name, updated_at_utc = SYSUTCDATETIME()
    FROM dbo.dim_city target
    JOIN #legacy_cities c ON c.normalized_name = target.normalized_name
    WHERE target.city_name COLLATE Latin1_General_100_BIN2
        <> c.city_name COLLATE Latin1_General_100_BIN2;

    SELECT
        MIN(LTRIM(RTRIM(level_text))) AS level_name,
        LOWER(LTRIM(RTRIM(level_text))) COLLATE Latin1_General_100_BIN2 AS normalized_name
    INTO #legacy_levels
    FROM dw.fact_job_necessary_skill
    WHERE NULLIF(LTRIM(RTRIM(level_text)), N'') IS NOT NULL
    GROUP BY LOWER(LTRIM(RTRIM(level_text))) COLLATE Latin1_General_100_BIN2;

    INSERT INTO dbo.dim_proficiency_level(level_code, level_name, normalized_name)
    SELECT NULL, level_name, normalized_name
    FROM #legacy_levels l
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.dim_proficiency_level target
        WHERE target.normalized_name = l.normalized_name
    );

    UPDATE target
    SET level_name = l.level_name, updated_at_utc = SYSUTCDATETIME()
    FROM dbo.dim_proficiency_level target
    JOIN #legacy_levels l ON l.normalized_name = target.normalized_name
    WHERE target.level_name <> l.level_name;

    DELETE FROM dw_v2_migration.map_job;
    DELETE FROM dw_v2_migration.map_skill;
    DELETE FROM dw_v2_migration.map_role;
    DELETE FROM dw_v2_migration.map_city;
    DELETE FROM dw_v2_migration.map_proficiency;

    INSERT INTO dw_v2_migration.map_job(
        legacy_fact_job_key, observation_id, job_key, date_key,
        source_key, company_key, crawl_run_key
    )
    SELECT
        legacy.fact_job_key,
        CONVERT(varchar(64), HASHBYTES(
            'SHA2_256',
            CONCAT(legacy.job_id, N'|',
                COALESCE(NULLIF(LTRIM(RTRIM(legacy.crawl_run_id)), N''), N'legacy:unknown'))
        ), 2),
        job.job_key,
        CONVERT(int, CONVERT(char(8), CAST(legacy.observed_at_utc AS date), 112)),
        source.source_key,
        COALESCE(company.company_key, 0),
        run.crawl_run_key
    FROM dw.fact_job legacy
    JOIN dbo.dim_job job ON job.job_id = legacy.job_id
    JOIN dbo.dim_source source
      ON source.source_code = legacy.source COLLATE Latin1_General_100_BIN2
    JOIN dbo.dim_crawl_run run
      ON run.crawl_run_id = COALESCE(
          NULLIF(LTRIM(RTRIM(legacy.crawl_run_id)), N''), N'legacy:unknown'
      ) COLLATE Latin1_General_100_BIN2
    LEFT JOIN dbo.dim_company company
      ON company.normalized_name = LOWER(LTRIM(RTRIM(legacy.company_name)))
          COLLATE Latin1_General_100_BIN2;

    INSERT INTO dw_v2_migration.map_skill(legacy_necessary_skill_key, skill_key)
    SELECT legacy.necessary_skill_key, target.skill_key
    FROM dw.dim_necessary_skill legacy
    JOIN dbo.dim_skill target
      ON target.skill_type = legacy.skill_type
     AND target.normalized_name = LOWER(LTRIM(RTRIM(legacy.name)))
          COLLATE Latin1_General_100_BIN2
     AND target.category = legacy.category;

    INSERT INTO dw_v2_migration.map_role(legacy_role_key, role_key)
    SELECT legacy.role_key, target.role_key
    FROM dw.dim_role legacy
    JOIN dbo.dim_role target
      ON target.normalized_name = LOWER(LTRIM(RTRIM(legacy.name)))
          COLLATE Latin1_General_100_BIN2
     AND target.role_category = legacy.category;

    INSERT INTO dw_v2_migration.map_city(legacy_city_key, city_key)
    SELECT legacy.city_key, target.city_key
    FROM dw.dim_city legacy
    JOIN dbo.dim_city target
      ON target.normalized_name = LOWER(LTRIM(RTRIM(legacy.name)))
          COLLATE Latin1_General_100_BIN2;

    INSERT INTO dw_v2_migration.map_proficiency(normalized_level, proficiency_level_key)
    SELECT legacy.normalized_name, target.proficiency_level_key
    FROM #legacy_levels legacy
    JOIN dbo.dim_proficiency_level target
      ON target.normalized_name = legacy.normalized_name;

    IF (SELECT COUNT(*) FROM dw.fact_job)
        <> (SELECT COUNT(*) FROM dw_v2_migration.map_job)
        THROW 51010, 'Job dimension reconciliation failed.', 1;
    IF (SELECT COUNT(*) FROM dw.dim_necessary_skill)
        <> (SELECT COUNT(*) FROM dw_v2_migration.map_skill)
        THROW 51011, 'Skill dimension reconciliation failed.', 1;
    IF (SELECT COUNT(*) FROM dw.dim_role)
        <> (SELECT COUNT(*) FROM dw_v2_migration.map_role)
        THROW 51012, 'Role dimension reconciliation failed.', 1;
    IF (SELECT COUNT(*) FROM dw.dim_city)
        <> (SELECT COUNT(*) FROM dw_v2_migration.map_city)
        THROW 51013, 'City dimension reconciliation failed.', 1;
    IF (SELECT COUNT(*) FROM #legacy_levels)
        <> (SELECT COUNT(*) FROM dw_v2_migration.map_proficiency)
        THROW 51014, 'Proficiency dimension reconciliation failed.', 1;

    COMMIT TRANSACTION;

    SELECT
        (SELECT COUNT(*) FROM dbo.dim_job) AS jobs,
        (SELECT COUNT(*) FROM dbo.dim_company) AS companies_including_unknown,
        (SELECT COUNT(*) FROM dbo.dim_skill) AS skills_including_unknown,
        (SELECT COUNT(*) FROM dbo.dim_role) AS roles_including_unknown,
        (SELECT COUNT(*) FROM dbo.dim_city) AS cities_including_unknown,
        (SELECT COUNT(*) FROM dw_v2_migration.map_job) AS mapped_legacy_jobs;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
