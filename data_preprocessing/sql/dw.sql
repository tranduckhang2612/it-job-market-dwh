-- SQL Server: run inside the target database. Creates schema objects only.

SET XACT_ABORT ON;

BEGIN TRANSACTION;

IF SCHEMA_ID('dw') IS NULL EXEC(N'CREATE SCHEMA dw');

IF OBJECT_ID('dw.fact_job','U') IS NULL CREATE TABLE dw.fact_job(
        job_key int IDENTITY PRIMARY KEY, job_id nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
        source nvarchar(128) NOT NULL, title nvarchar(max), company_name nvarchar(max),
        job_url nvarchar(max), crawl_run_id nvarchar(256), observed_at_utc datetime2(6) NOT NULL);

IF OBJECT_ID('dw.dim_programming_language','U') IS NULL CREATE TABLE dw.dim_programming_language(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_programming_language_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_programming_language','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_programming_language DROP CONSTRAINT CK_dim_programming_language_hash, CK_dim_programming_language_key, UQ_dim_programming_language_analytics;
    ALTER TABLE dw.dim_programming_language DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_programming_language','U') IS NULL CREATE TABLE dw.bridge_job_programming_language(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_programming_language(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_programming_language AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_programming_language b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_programming_language d ON d.dim_id=b.dim_id');

IF OBJECT_ID('dw.dim_framework','U') IS NULL CREATE TABLE dw.dim_framework(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_framework_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_framework','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_framework DROP CONSTRAINT CK_dim_framework_hash, CK_dim_framework_key, UQ_dim_framework_analytics;
    ALTER TABLE dw.dim_framework DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_framework','U') IS NULL CREATE TABLE dw.bridge_job_framework(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_framework(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_framework AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_framework b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_framework d ON d.dim_id=b.dim_id');

IF OBJECT_ID('dw.dim_core_skill','U') IS NULL CREATE TABLE dw.dim_core_skill(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_core_skill_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_core_skill','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_core_skill DROP CONSTRAINT CK_dim_core_skill_hash, CK_dim_core_skill_key, UQ_dim_core_skill_analytics;
    ALTER TABLE dw.dim_core_skill DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_core_skill','U') IS NULL CREATE TABLE dw.bridge_job_core_skill(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_core_skill(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_core_skill AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_core_skill b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_core_skill d ON d.dim_id=b.dim_id');

IF OBJECT_ID('dw.dim_role','U') IS NULL CREATE TABLE dw.dim_role(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_role_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_role','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_role DROP CONSTRAINT CK_dim_role_hash, CK_dim_role_key, UQ_dim_role_analytics;
    ALTER TABLE dw.dim_role DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_role','U') IS NULL CREATE TABLE dw.bridge_job_role(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_role(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_role AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_role b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_role d ON d.dim_id=b.dim_id');

IF OBJECT_ID('dw.dim_city','U') IS NULL CREATE TABLE dw.dim_city(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_city_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_city','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_city DROP CONSTRAINT CK_dim_city_hash, CK_dim_city_key, UQ_dim_city_analytics;
    ALTER TABLE dw.dim_city DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_city','U') IS NULL CREATE TABLE dw.bridge_job_city(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_city(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_city AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_city b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_city d ON d.dim_id=b.dim_id');

IF OBJECT_ID('dw.dim_foreign_language','U') IS NULL CREATE TABLE dw.dim_foreign_language(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_foreign_language_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_foreign_language','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_foreign_language DROP CONSTRAINT CK_dim_foreign_language_hash, CK_dim_foreign_language_key, UQ_dim_foreign_language_analytics;
    ALTER TABLE dw.dim_foreign_language DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_foreign_language','U') IS NULL CREATE TABLE dw.bridge_job_foreign_language(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_foreign_language(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_foreign_language AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_foreign_language b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_foreign_language d ON d.dim_id=b.dim_id');

IF OBJECT_ID('dw.dim_level','U') IS NULL CREATE TABLE dw.dim_level(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_level_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_level','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_level DROP CONSTRAINT CK_dim_level_hash, CK_dim_level_key, UQ_dim_level_analytics;
    ALTER TABLE dw.dim_level DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_level','U') IS NULL CREATE TABLE dw.bridge_job_level(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_level(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_level AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_level b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_level d ON d.dim_id=b.dim_id');

IF OBJECT_ID('dw.dim_soft_skill','U') IS NULL CREATE TABLE dw.dim_soft_skill(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_soft_skill_natural UNIQUE(name,category));

IF COL_LENGTH('dw.dim_soft_skill','row_sha256') IS NOT NULL
BEGIN
    ALTER TABLE dw.dim_soft_skill DROP CONSTRAINT CK_dim_soft_skill_hash, CK_dim_soft_skill_key, UQ_dim_soft_skill_analytics;
    ALTER TABLE dw.dim_soft_skill DROP COLUMN row_sha256, analytics_key;
END;

IF OBJECT_ID('dw.bridge_job_soft_skill','U') IS NULL CREATE TABLE dw.bridge_job_soft_skill(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES dw.dim_soft_skill(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no));

EXEC(N'CREATE OR ALTER VIEW dw.job_soft_skill AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM dw.bridge_job_soft_skill b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN dw.dim_soft_skill d ON d.dim_id=b.dim_id');

COMMIT TRANSACTION;
