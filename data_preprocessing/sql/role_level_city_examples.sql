-- SQL Server. Run in the DW database after the loader has created the new fact.
-- These queries are read-only and never select a created_at column.
SET NOCOUNT ON;

-- Legacy names retained; the fact/view now contain role and city, without level.
SELECT TOP (50) fact_job_role_level_city_key, fact_job_key,
       role_key, city_key,
       role_name, role_category, city_name, combination_count
FROM dw.job_role_level_city
ORDER BY fact_job_role_level_city_key;

-- Combinations are not the number of postings or vacancies.
SELECT COUNT(*) AS combination_rows,
       COUNT(DISTINCT fact_job_key) AS distinct_job_postings
FROM dw.fact_job_role_level_city;

-- One readable business sentence per fact row.
SELECT TOP (5)
       CONCAT(N'Tin có fact_job_key = ', fact_job_key, N' ghi nhận vai trò ',
              COALESCE(role_name, N'(chưa nêu)'), N', tại ',
              COALESCE(city_name, N'(chưa nêu)'),
              N'. Đây là một tổ hợp thuộc cùng tin tuyển dụng.') AS fact_description
FROM dw.job_role_level_city
ORDER BY fact_job_role_level_city_key;
