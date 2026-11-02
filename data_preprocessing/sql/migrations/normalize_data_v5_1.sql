-- Dùng sentinel unknown khi nguồn không công bố yêu cầu ngoại ngữ.

BEGIN;

UPDATE normalize_data.normalized_jobs
SET foreign_languages = '[{"language":"unknown","level_text":null}]'::jsonb
WHERE jsonb_array_length(foreign_languages) = 0;

ALTER TABLE normalize_data.normalized_jobs
    DROP CONSTRAINT IF EXISTS normalized_jobs_foreign_languages_nonempty,
    ADD CONSTRAINT normalized_jobs_foreign_languages_nonempty
        CHECK (jsonb_array_length(foreign_languages) > 0),
    ALTER COLUMN foreign_languages SET DEFAULT
        '[{"language":"unknown","level_text":null}]'::jsonb,
    ALTER COLUMN normalization_version SET DEFAULT '5.1';

COMMIT;
