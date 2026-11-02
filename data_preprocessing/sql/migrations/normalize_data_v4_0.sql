-- Migration phá hủy dữ liệu theo yêu cầu: bỏ các cột kinh nghiệm và học vấn.

BEGIN;

ALTER TABLE normalize_data.normalized_jobs
    DROP COLUMN IF EXISTS experience_text,
    DROP COLUMN IF EXISTS experience_min_years,
    DROP COLUMN IF EXISTS experience_max_years,
    DROP COLUMN IF EXISTS education_requirements,
    ALTER COLUMN normalization_version SET DEFAULT '4.0';

COMMENT ON TABLE normalize_data.normalized_jobs IS
    '26 cột nghiệp vụ cùng metadata giải thích; một dòng cho một job trong một crawl run.';

COMMIT;
