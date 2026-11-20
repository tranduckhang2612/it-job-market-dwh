-- Dimensional tables live in dbo; retain the script name for CLI compatibility.
-- Existing dw_v2 tables transfer in place; legacy dw objects are untouched.
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

    DECLARE @transfer_tables table (table_name sysname PRIMARY KEY);
    INSERT INTO @transfer_tables(table_name) VALUES
        (N'dim_date'),(N'dim_job'),(N'dim_source'),(N'dim_company'),
        (N'dim_crawl_run'),(N'dim_skill'),(N'dim_role'),(N'dim_city'),
        (N'dim_proficiency_level'),(N'fact_job_observation'),
        (N'fact_job_necessary_skill'),(N'fact_job_role_city');

    -- Reject mixed-schema collisions before transferring any object.
    IF EXISTS (
        SELECT 1 FROM @transfer_tables
        WHERE OBJECT_ID(N'dw_v2.' + QUOTENAME(table_name), N'U') IS NOT NULL
          AND OBJECT_ID(N'dbo.' + QUOTENAME(table_name)) IS NOT NULL
    )
        THROW 51101, 'A target dbo dimensional object already exists; no tables transferred.', 1;

    DECLARE @transfer_name sysname, @transfer_sql nvarchar(max);
    WHILE EXISTS (
        SELECT 1 FROM @transfer_tables
        WHERE OBJECT_ID(N'dw_v2.' + QUOTENAME(table_name), N'U') IS NOT NULL
    )
    BEGIN
        SELECT TOP (1) @transfer_name=table_name FROM @transfer_tables
        WHERE OBJECT_ID(N'dw_v2.' + QUOTENAME(table_name), N'U') IS NOT NULL
        ORDER BY table_name;
        SET @transfer_sql=N'ALTER SCHEMA dbo TRANSFER dw_v2.' + QUOTENAME(@transfer_name);
        EXEC sys.sp_executesql @transfer_sql;
        DELETE FROM @transfer_tables WHERE table_name=@transfer_name;
    END;

    IF SCHEMA_ID(N'dbo') IS NULL EXEC(N'CREATE SCHEMA dbo');

    IF OBJECT_ID(N'dbo.dim_date', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_date (
            date_key int NOT NULL CONSTRAINT PK_dw_v2_dim_date PRIMARY KEY,
            calendar_date date NOT NULL,
            day_of_week tinyint NOT NULL,
            day_name nvarchar(32) NOT NULL,
            month_no tinyint NOT NULL,
            month_name nvarchar(32) NOT NULL,
            quarter_no tinyint NOT NULL,
            year_no smallint NOT NULL,
            is_weekend bit NOT NULL,
            CONSTRAINT UQ_dw_v2_dim_date_calendar UNIQUE(calendar_date),
            CONSTRAINT CK_dw_v2_dim_date_key
                CHECK (date_key = CONVERT(int, CONVERT(char(8), calendar_date, 112))),
            CONSTRAINT CK_dw_v2_dim_date_day CHECK (day_of_week BETWEEN 1 AND 7),
            CONSTRAINT CK_dw_v2_dim_date_month CHECK (month_no BETWEEN 1 AND 12),
            CONSTRAINT CK_dw_v2_dim_date_quarter CHECK (quarter_no BETWEEN 1 AND 4)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_source', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_source (
            source_key int IDENTITY CONSTRAINT PK_dw_v2_dim_source PRIMARY KEY,
            source_code nvarchar(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
            source_name nvarchar(128) NOT NULL,
            is_active bit NOT NULL CONSTRAINT DF_dw_v2_dim_source_active DEFAULT (1),
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_source_created DEFAULT SYSUTCDATETIME(),
            updated_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_source_updated DEFAULT SYSUTCDATETIME(),
            CONSTRAINT UQ_dw_v2_dim_source_code UNIQUE(source_code)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_company', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_company (
            company_key bigint IDENTITY CONSTRAINT PK_dw_v2_dim_company PRIMARY KEY,
            company_name nvarchar(512) NOT NULL,
            normalized_name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_company_created DEFAULT SYSUTCDATETIME(),
            updated_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_company_updated DEFAULT SYSUTCDATETIME(),
            CONSTRAINT UQ_dw_v2_dim_company_name UNIQUE(normalized_name)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_job', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_job (
            job_key bigint IDENTITY CONSTRAINT PK_dw_v2_dim_job PRIMARY KEY,
            job_id nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
            source_job_id nvarchar(256) COLLATE Latin1_General_100_BIN2 NULL,
            canonical_job_url nvarchar(2048) NULL,
            first_seen_at_utc datetime2(6) NOT NULL,
            last_seen_at_utc datetime2(6) NOT NULL,
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_job_created DEFAULT SYSUTCDATETIME(),
            updated_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_job_updated DEFAULT SYSUTCDATETIME(),
            CONSTRAINT UQ_dw_v2_dim_job_id UNIQUE(job_id),
            CONSTRAINT CK_dw_v2_dim_job_seen CHECK (last_seen_at_utc >= first_seen_at_utc)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_crawl_run', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_crawl_run (
            crawl_run_key bigint IDENTITY CONSTRAINT PK_dw_v2_dim_crawl_run PRIMARY KEY,
            crawl_run_id nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
            source_scope nvarchar(128) NULL,
            pipeline_version nvarchar(64) NULL,
            started_at_utc datetime2(6) NOT NULL,
            finished_at_utc datetime2(6) NULL,
            run_status nvarchar(32) NOT NULL,
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_crawl_run_created DEFAULT SYSUTCDATETIME(),
            CONSTRAINT UQ_dw_v2_dim_crawl_run_id UNIQUE(crawl_run_id),
            CONSTRAINT CK_dw_v2_dim_crawl_run_status
                CHECK (run_status IN (N'running', N'succeeded', N'failed', N'partial')),
            CONSTRAINT CK_dw_v2_dim_crawl_run_time
                CHECK (finished_at_utc IS NULL OR finished_at_utc >= started_at_utc)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_skill', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_skill (
            skill_key bigint IDENTITY CONSTRAINT PK_dw_v2_dim_skill PRIMARY KEY,
            skill_type nvarchar(64) COLLATE Latin1_General_100_BIN2 NOT NULL,
            skill_name nvarchar(512) NOT NULL,
            normalized_name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            programming_language nvarchar(128) NULL,
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_skill_created DEFAULT SYSUTCDATETIME(),
            updated_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_skill_updated DEFAULT SYSUTCDATETIME(),
            CONSTRAINT CK_dw_v2_dim_skill_type CHECK (
                skill_type IN (
                    N'programming_language', N'framework', N'core_skill',
                    N'foreign_language', N'soft_skill', N'unknown'
                )
            ),
            CONSTRAINT UQ_dw_v2_dim_skill_natural
                UNIQUE(skill_type, normalized_name, category)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_role', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_role (
            role_key bigint IDENTITY CONSTRAINT PK_dw_v2_dim_role PRIMARY KEY,
            role_name nvarchar(512) NOT NULL,
            normalized_name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            role_category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_role_created DEFAULT SYSUTCDATETIME(),
            updated_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_role_updated DEFAULT SYSUTCDATETIME(),
            CONSTRAINT UQ_dw_v2_dim_role_natural UNIQUE(normalized_name, role_category)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_city', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_city (
            city_key int IDENTITY CONSTRAINT PK_dw_v2_dim_city PRIMARY KEY,
            city_name nvarchar(256) NOT NULL,
            normalized_name nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
            region_name nvarchar(256) NULL,
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_city_created DEFAULT SYSUTCDATETIME(),
            updated_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_city_updated DEFAULT SYSUTCDATETIME(),
            CONSTRAINT UQ_dw_v2_dim_city_name UNIQUE(normalized_name)
        );
    END;

    IF OBJECT_ID(N'dbo.dim_proficiency_level', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.dim_proficiency_level (
            proficiency_level_key int IDENTITY
                CONSTRAINT PK_dw_v2_dim_proficiency_level PRIMARY KEY,
            level_code nvarchar(64) COLLATE Latin1_General_100_BIN2 NULL,
            level_name nvarchar(256) NOT NULL,
            normalized_name nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_proficiency_created DEFAULT SYSUTCDATETIME(),
            updated_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_dim_proficiency_updated DEFAULT SYSUTCDATETIME(),
            CONSTRAINT UQ_dw_v2_dim_proficiency_name UNIQUE(normalized_name)
        );
    END;

    -- Unknown members keep optional dimensional references non-null in fact tables.
    IF NOT EXISTS (SELECT 1 FROM dbo.dim_source WHERE source_key = 0)
    BEGIN
        SET IDENTITY_INSERT dbo.dim_source ON;
        INSERT INTO dbo.dim_source(source_key, source_code, source_name, is_active)
        VALUES (0, N'unknown', N'Unknown', 1);
        SET IDENTITY_INSERT dbo.dim_source OFF;
    END;

    IF NOT EXISTS (SELECT 1 FROM dbo.dim_company WHERE company_key = 0)
    BEGIN
        SET IDENTITY_INSERT dbo.dim_company ON;
        INSERT INTO dbo.dim_company(company_key, company_name, normalized_name)
        VALUES (0, N'Unknown', N'unknown');
        SET IDENTITY_INSERT dbo.dim_company OFF;
    END;

    IF NOT EXISTS (SELECT 1 FROM dbo.dim_skill WHERE skill_key = 0)
    BEGIN
        SET IDENTITY_INSERT dbo.dim_skill ON;
        INSERT INTO dbo.dim_skill(
            skill_key, skill_type, skill_name, normalized_name, category, programming_language
        ) VALUES (0, N'unknown', N'Unknown', N'unknown', N'unknown', NULL);
        SET IDENTITY_INSERT dbo.dim_skill OFF;
    END;

    IF NOT EXISTS (SELECT 1 FROM dbo.dim_role WHERE role_key = 0)
    BEGIN
        SET IDENTITY_INSERT dbo.dim_role ON;
        INSERT INTO dbo.dim_role(role_key, role_name, normalized_name, role_category)
        VALUES (0, N'Unknown', N'unknown', N'unknown');
        SET IDENTITY_INSERT dbo.dim_role OFF;
    END;

    IF NOT EXISTS (SELECT 1 FROM dbo.dim_city WHERE city_key = 0)
    BEGIN
        SET IDENTITY_INSERT dbo.dim_city ON;
        INSERT INTO dbo.dim_city(city_key, city_name, normalized_name, region_name)
        VALUES (0, N'Unknown', N'unknown', NULL);
        SET IDENTITY_INSERT dbo.dim_city OFF;
    END;

    IF NOT EXISTS (
        SELECT 1 FROM dbo.dim_proficiency_level WHERE proficiency_level_key = 0
    )
    BEGIN
        SET IDENTITY_INSERT dbo.dim_proficiency_level ON;
        INSERT INTO dbo.dim_proficiency_level(
            proficiency_level_key, level_code, level_name, normalized_name
        ) VALUES (0, N'unknown', N'Unknown', N'unknown');
        SET IDENTITY_INSERT dbo.dim_proficiency_level OFF;
    END;

    IF OBJECT_ID(N'dbo.fact_job_observation', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.fact_job_observation (
            job_observation_key bigint IDENTITY
                CONSTRAINT PK_dw_v2_fact_job_observation PRIMARY KEY,
            observation_id nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            job_key bigint NOT NULL,
            date_key int NOT NULL,
            source_key int NOT NULL,
            company_key bigint NOT NULL,
            crawl_run_key bigint NOT NULL,
            observed_at_utc datetime2(6) NOT NULL,
            job_title_snapshot nvarchar(max) NULL,
            work_mode nvarchar(32) NULL,
            job_posting_count AS (CONVERT(int, 1)) PERSISTED,
            loaded_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_fact_job_observation_loaded DEFAULT SYSUTCDATETIME(),
            CONSTRAINT FK_dw_v2_fact_job_observation_job FOREIGN KEY(job_key)
                REFERENCES dbo.dim_job(job_key),
            CONSTRAINT FK_dw_v2_fact_job_observation_date FOREIGN KEY(date_key)
                REFERENCES dbo.dim_date(date_key),
            CONSTRAINT FK_dw_v2_fact_job_observation_source FOREIGN KEY(source_key)
                REFERENCES dbo.dim_source(source_key),
            CONSTRAINT FK_dw_v2_fact_job_observation_company FOREIGN KEY(company_key)
                REFERENCES dbo.dim_company(company_key),
            CONSTRAINT FK_dw_v2_fact_job_observation_run FOREIGN KEY(crawl_run_key)
                REFERENCES dbo.dim_crawl_run(crawl_run_key),
            CONSTRAINT UQ_dw_v2_fact_job_observation_id UNIQUE(observation_id),
            CONSTRAINT UQ_dw_v2_fact_job_observation_grain UNIQUE(job_key, crawl_run_key),
            CONSTRAINT CK_dw_v2_fact_job_observation_work_mode
                CHECK (work_mode IS NULL OR work_mode IN (N'onsite', N'hybrid', N'remote'))
        );
        CREATE INDEX IX_dw_v2_fact_job_observation_date_source
            ON dbo.fact_job_observation(date_key, source_key);
        CREATE INDEX IX_dw_v2_fact_job_observation_company
            ON dbo.fact_job_observation(company_key, date_key);
    END;

    IF COL_LENGTH(N'dbo.fact_job_observation', N'job_title_snapshot') <> -1
        ALTER TABLE dbo.fact_job_observation
            ALTER COLUMN job_title_snapshot nvarchar(max) NULL;

    IF OBJECT_ID(N'dbo.fact_job_necessary_skill', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.fact_job_necessary_skill (
            job_necessary_skill_key bigint IDENTITY
                CONSTRAINT PK_dw_v2_fact_job_necessary_skill PRIMARY KEY,
            observation_id nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            job_key bigint NOT NULL,
            date_key int NOT NULL,
            source_key int NOT NULL,
            crawl_run_key bigint NOT NULL,
            skill_key bigint NOT NULL,
            proficiency_level_key int NOT NULL
                CONSTRAINT DF_dw_v2_fact_skill_proficiency DEFAULT (0),
            context_no int NOT NULL,
            level_text_snapshot nvarchar(max) NULL,
            skill_mention_count AS (CONVERT(int, 1)) PERSISTED,
            loaded_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_fact_job_skill_loaded DEFAULT SYSUTCDATETIME(),
            CONSTRAINT FK_dw_v2_fact_job_skill_job FOREIGN KEY(job_key)
                REFERENCES dbo.dim_job(job_key),
            CONSTRAINT FK_dw_v2_fact_job_skill_date FOREIGN KEY(date_key)
                REFERENCES dbo.dim_date(date_key),
            CONSTRAINT FK_dw_v2_fact_job_skill_source FOREIGN KEY(source_key)
                REFERENCES dbo.dim_source(source_key),
            CONSTRAINT FK_dw_v2_fact_job_skill_run FOREIGN KEY(crawl_run_key)
                REFERENCES dbo.dim_crawl_run(crawl_run_key),
            CONSTRAINT FK_dw_v2_fact_job_skill_skill FOREIGN KEY(skill_key)
                REFERENCES dbo.dim_skill(skill_key),
            CONSTRAINT FK_dw_v2_fact_job_skill_level FOREIGN KEY(proficiency_level_key)
                REFERENCES dbo.dim_proficiency_level(proficiency_level_key),
            CONSTRAINT UQ_dw_v2_fact_job_skill_grain
                UNIQUE(job_key, crawl_run_key, skill_key, context_no),
            CONSTRAINT CK_dw_v2_fact_job_skill_context CHECK (context_no >= 0)
        );
        CREATE INDEX IX_dw_v2_fact_job_skill_skill_date
            ON dbo.fact_job_necessary_skill(skill_key, date_key);
        CREATE INDEX IX_dw_v2_fact_job_skill_observation
            ON dbo.fact_job_necessary_skill(observation_id);
    END;

    IF COL_LENGTH(N'dbo.fact_job_necessary_skill', N'level_text_snapshot') <> -1
        ALTER TABLE dbo.fact_job_necessary_skill
            ALTER COLUMN level_text_snapshot nvarchar(max) NULL;

    IF OBJECT_ID(N'dbo.fact_job_role_city', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.fact_job_role_city (
            job_role_city_key bigint IDENTITY
                CONSTRAINT PK_dw_v2_fact_job_role_city PRIMARY KEY,
            observation_id nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            job_key bigint NOT NULL,
            date_key int NOT NULL,
            source_key int NOT NULL,
            crawl_run_key bigint NOT NULL,
            role_key bigint NOT NULL CONSTRAINT DF_dw_v2_fact_role_key DEFAULT (0),
            city_key int NOT NULL CONSTRAINT DF_dw_v2_fact_city_key DEFAULT (0),
            combination_count AS (CONVERT(int, 1)) PERSISTED,
            loaded_at_utc datetime2(6) NOT NULL
                CONSTRAINT DF_dw_v2_fact_job_role_city_loaded DEFAULT SYSUTCDATETIME(),
            CONSTRAINT FK_dw_v2_fact_job_role_city_job FOREIGN KEY(job_key)
                REFERENCES dbo.dim_job(job_key),
            CONSTRAINT FK_dw_v2_fact_job_role_city_date FOREIGN KEY(date_key)
                REFERENCES dbo.dim_date(date_key),
            CONSTRAINT FK_dw_v2_fact_job_role_city_source FOREIGN KEY(source_key)
                REFERENCES dbo.dim_source(source_key),
            CONSTRAINT FK_dw_v2_fact_job_role_city_run FOREIGN KEY(crawl_run_key)
                REFERENCES dbo.dim_crawl_run(crawl_run_key),
            CONSTRAINT FK_dw_v2_fact_job_role_city_role FOREIGN KEY(role_key)
                REFERENCES dbo.dim_role(role_key),
            CONSTRAINT FK_dw_v2_fact_job_role_city_city FOREIGN KEY(city_key)
                REFERENCES dbo.dim_city(city_key),
            CONSTRAINT UQ_dw_v2_fact_job_role_city_grain
                UNIQUE(job_key, crawl_run_key, role_key, city_key)
        );
        CREATE INDEX IX_dw_v2_fact_job_role_city_role_date
            ON dbo.fact_job_role_city(role_key, date_key);
        CREATE INDEX IX_dw_v2_fact_job_role_city_city_date
            ON dbo.fact_job_role_city(city_key, date_key);
        CREATE INDEX IX_dw_v2_fact_job_role_city_observation
            ON dbo.fact_job_role_city(observation_id);
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
