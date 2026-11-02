-- Migration phá hủy dữ liệu theo yêu cầu: bỏ ngày đăng, required knowledge và salary.

BEGIN;

ALTER TABLE normalize_data.normalized_jobs
    DROP COLUMN IF EXISTS published_at,
    DROP COLUMN IF EXISTS required_knowledge,
    DROP COLUMN IF EXISTS salary_text,
    DROP COLUMN IF EXISTS salary_min,
    DROP COLUMN IF EXISTS salary_max,
    DROP COLUMN IF EXISTS salary_currency,
    DROP COLUMN IF EXISTS salary_period,
    DROP COLUMN IF EXISTS salary_basis,
    DROP COLUMN IF EXISTS salary_status,
    ALTER COLUMN normalization_version SET DEFAULT '5.0';

COMMENT ON TABLE normalize_data.normalized_jobs IS
    '17 cột nghiệp vụ cùng metadata giải thích; một dòng cho một job trong một crawl run.';

COMMIT;
