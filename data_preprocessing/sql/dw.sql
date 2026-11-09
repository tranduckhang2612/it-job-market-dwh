-- SQL Server: install or migrate the current job snapshot in the target database.
-- Each dw.dim_<group> row belongs directly to one job; labels are not unique.
SET XACT_ABORT ON;
SET NOCOUNT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF SCHEMA_ID(N'dw') IS NULL EXEC(N'CREATE SCHEMA dw');

    IF OBJECT_ID(N'dw.dim_date', N'U') IS NULL
        CREATE TABLE dw.dim_date (
            date_key int NOT NULL CONSTRAINT PK_dim_date PRIMARY KEY,
            calendar_date date NOT NULL,
            CONSTRAINT CK_dim_date_key
                CHECK (date_key = CONVERT(int, CONVERT(char(8), calendar_date, 112)))
        );

    IF OBJECT_ID(N'dw.fact_job', N'U') IS NULL
        CREATE TABLE dw.fact_job (
            fact_job_key int IDENTITY CONSTRAINT PK_fact_job PRIMARY KEY,
            job_id nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
            source nvarchar(128) NOT NULL,
            title nvarchar(max),
            company_name nvarchar(max),
            job_url nvarchar(max),
            crawl_run_id nvarchar(256),
            observed_at_utc datetime2(6) NOT NULL,
            observed_date_key int NOT NULL CONSTRAINT FK_fact_job_observed_date REFERENCES dw.dim_date(date_key),
            job_posting_count AS (CONVERT(int, 1)) PERSISTED
        );
    ELSE IF COL_LENGTH(N'dw.fact_job', N'job_key') IS NOT NULL
    BEGIN
        -- Preserve the existing fact IDs and their references during migration.
        EXEC sys.sp_rename N'dw.fact_job.job_key', N'fact_job_key', N'COLUMN';
        EXEC(N'ALTER TABLE dw.fact_job ADD observed_date_key int NULL,
                   job_posting_count AS (CONVERT(int, 1)) PERSISTED;');
        EXEC(N'
            INSERT INTO dw.dim_date(date_key, calendar_date)
            SELECT DISTINCT k.date_key, d.calendar_date
            FROM dw.fact_job f
            CROSS APPLY (VALUES (CAST(f.observed_at_utc AS date))) d(calendar_date)
            CROSS APPLY (VALUES (CONVERT(int, CONVERT(char(8), d.calendar_date, 112)))) k(date_key)
            WHERE NOT EXISTS (SELECT 1 FROM dw.dim_date x WHERE x.date_key = k.date_key);

            UPDATE dw.fact_job
            SET observed_date_key = CONVERT(int, CONVERT(char(8), CAST(observed_at_utc AS date), 112));

            ALTER TABLE dw.fact_job ALTER COLUMN observed_date_key int NOT NULL;
            ALTER TABLE dw.fact_job ADD CONSTRAINT FK_fact_job_observed_date
                FOREIGN KEY (observed_date_key) REFERENCES dw.dim_date(date_key);');
    END;

    DECLARE @groups table (n int IDENTITY PRIMARY KEY, name sysname NOT NULL,
        keep_category bit NOT NULL, keep_level bit NOT NULL, has_language bit NOT NULL);
    INSERT INTO @groups(name, keep_category, keep_level, has_language) VALUES
        (N'programming_language', 1, 0, 0), (N'framework', 1, 0, 1), (N'core_skill', 1, 0, 0),
        (N'role', 1, 0, 0), (N'city', 0, 0, 0), (N'foreign_language', 0, 1, 0),
        (N'level', 0, 0, 0), (N'soft_skill', 1, 1, 0);

    DECLARE @i int = 1, @group sysname, @dim nvarchar(300), @bridge nvarchar(300),
        @old_name sysname, @old_dim nvarchar(300), @key nvarchar(258), @view nvarchar(300),
        @migrate bit, @refresh bit, @keep_category bit, @keep_level bit, @has_language bit,
        @level_source nvarchar(300), @drop_columns nvarchar(100), @statement nvarchar(max);
    WHILE @i <= (SELECT COUNT(*) FROM @groups)
    BEGIN
        SELECT @group = name, @keep_category = keep_category, @keep_level = keep_level,
            @has_language = has_language FROM @groups WHERE n = @i;
        SET @dim = N'dw.' + QUOTENAME(N'dim_' + @group);
        SET @bridge = N'dw.' + QUOTENAME(N'bridge_job_' + @group);
        SET @old_name = N'dim_' + @group + N'_legacy';
        SET @old_dim = N'dw.' + QUOTENAME(@old_name);
        SET @key = QUOTENAME(@group + N'_key');
        SET @view = N'dw.' + QUOTENAME(N'job_' + @group);
        SET @migrate = CASE WHEN COL_LENGTH(@dim, N'dim_id') IS NOT NULL THEN 1 ELSE 0 END;
        SET @refresh = @migrate;

        -- Abort the transaction if a column selected for removal has acquired data.
        IF @keep_category = 0 AND COL_LENGTH(@dim, N'category') IS NOT NULL
        BEGIN
            SET @statement = N'IF EXISTS (SELECT 1 FROM ' + @dim + N' WHERE LEN(LTRIM(RTRIM(category))) > 0)
                THROW 50003, ''Nonempty category in dim_' + @group + N'; column retained.'', 1;';
            EXEC sys.sp_executesql @statement;
        END;
        SET @level_source = CASE WHEN @migrate = 1 THEN @bridge ELSE @dim END;
        IF @keep_level = 0 AND COL_LENGTH(@level_source, N'level_text') IS NOT NULL
        BEGIN
            SET @statement = N'IF EXISTS (SELECT 1 FROM ' + @level_source + N' WHERE level_text IS NOT NULL)
                THROW 50004, ''Non-NULL level_text in ' + @group + N'; column retained.'', 1;';
            EXEC sys.sp_executesql @statement;
        END;

        IF @migrate = 1
        BEGIN
            -- A catalog without associations cannot be assigned to jobs.
            IF OBJECT_ID(@bridge, N'U') IS NULL
            BEGIN
                SET @statement = N'IF EXISTS (SELECT 1 FROM ' + @dim + N')
                    THROW 50001, ''Legacy dimension has rows but no job bridge; load into a new database.'', 1;';
                EXEC sys.sp_executesql @statement;
            END;
            EXEC sys.sp_rename @dim, @old_name, N'OBJECT';
        END;

        IF OBJECT_ID(@dim, N'U') IS NULL
        BEGIN
            SET @statement = N'CREATE TABLE ' + @dim + N' (
                ' + @key + N' int IDENTITY PRIMARY KEY,
                fact_job_key int NOT NULL REFERENCES dw.fact_job(fact_job_key) ON DELETE CASCADE,
                name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,'
                + CASE WHEN @keep_category = 1 THEN N'category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,' ELSE N'' END
                + CASE WHEN @keep_level = 1 THEN N'level_text nvarchar(max),' ELSE N'' END
                + CASE WHEN @has_language = 1 THEN N'programming_language nvarchar(128) NULL,' ELSE N'' END + N'
                created_at_utc datetime2(6) NOT NULL DEFAULT SYSUTCDATETIME()
            );
            CREATE INDEX ' + QUOTENAME(N'IX_dim_' + @group + N'_fact_job_key') + N'
                ON ' + @dim + N'(fact_job_key);';
            EXEC sys.sp_executesql @statement;
        END;
        ELSE
        BEGIN
            SET @drop_columns =
                CASE WHEN @keep_category = 0 AND COL_LENGTH(@dim, N'category') IS NOT NULL THEN N',category' ELSE N'' END
                + CASE WHEN @keep_level = 0 AND COL_LENGTH(@dim, N'level_text') IS NOT NULL THEN N',level_text' ELSE N'' END;
            IF @drop_columns <> N''
            BEGIN
                SET @statement = N'ALTER TABLE ' + @dim + N' DROP COLUMN ' + STUFF(@drop_columns, 1, 1, N'') + N';';
                EXEC sys.sp_executesql @statement;
                SET @refresh = 1;
            END;
            IF @has_language = 1 AND COL_LENGTH(@dim, N'programming_language') IS NULL
            BEGIN
                SET @statement = N'ALTER TABLE ' + @dim + N' ADD programming_language nvarchar(128) NULL;';
                EXEC sys.sp_executesql @statement;
                SET @refresh = 1;
            END;
        END;

        IF @migrate = 1
        BEGIN
            IF OBJECT_ID(@bridge, N'U') IS NOT NULL
            BEGIN
                -- No DISTINCT: even identical requirement occurrences get separate IDs.
                SET @statement = N'INSERT INTO ' + @dim + N'(fact_job_key, name'
                    + CASE WHEN @keep_category = 1 THEN N', category' ELSE N'' END
                    + CASE WHEN @keep_level = 1 THEN N', level_text' ELSE N'' END + N', created_at_utc)
                    SELECT b.job_key, d.name'
                    + CASE WHEN @keep_category = 1 THEN N', d.category' ELSE N'' END
                    + CASE WHEN @keep_level = 1 THEN N', b.level_text' ELSE N'' END + N', d.created_at_utc
                    FROM ' + @bridge + N' b JOIN ' + @old_dim + N' d ON d.dim_id = b.dim_id;
                    DROP TABLE ' + @bridge + N';';
                EXEC sys.sp_executesql @statement;
            END;
            SET @statement = N'DROP TABLE ' + @old_dim + N';';
            EXEC sys.sp_executesql @statement;
        END
        ELSE IF OBJECT_ID(@bridge, N'U') IS NOT NULL
            THROW 50002, 'Legacy bridge found without its legacy dimension.', 1;

        -- Preserve view names and keep their projected columns in sync.
        IF @refresh = 1 OR OBJECT_ID(@view, N'V') IS NULL
        BEGIN
            SET @statement = N'CREATE OR ALTER VIEW ' + @view + N' AS
                SELECT j.job_id, j.source, j.title, j.company_name, j.observed_at_utc,
                       d.name'
                + CASE WHEN @keep_category = 1 THEN N', d.category' ELSE N'' END
                + CASE WHEN @keep_level = 1 THEN N', d.level_text' ELSE N'' END
                + CASE WHEN @has_language = 1 THEN N', d.programming_language' ELSE N'' END + N'
                FROM ' + @dim + N' d JOIN dw.fact_job j ON j.fact_job_key = d.fact_job_key;';
            EXEC sys.sp_executesql @statement;
        END;

        SET @i += 1;
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
