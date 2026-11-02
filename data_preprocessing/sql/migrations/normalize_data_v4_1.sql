-- Cập nhật version cho rule seniority nhiều level từ khoảng kinh nghiệm.

BEGIN;

ALTER TABLE normalize_data.normalized_jobs
    ALTER COLUMN normalization_version SET DEFAULT '4.1';

COMMIT;
