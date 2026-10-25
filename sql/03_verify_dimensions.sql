-- =========================================================
-- 03_verify_dimensions.sql
-- Kiểm tra tính toàn vẹn 14 Dimension sau khi seed
-- =========================================================

-- 1. Kiểm tra 14 Dimension đều có đủ 2 dòng mặc định 0 (Unknown) và -1 (Invalid)
SELECT 'Dim_Date' AS tablename, COUNT(*) AS default_rows FROM dwh.dim_date WHERE datekey IN (0, -1)
UNION ALL SELECT 'Dim_Source', COUNT(*) FROM dwh.dim_source WHERE sourcekey IN (0, -1)
UNION ALL SELECT 'Dim_Company', COUNT(*) FROM dwh.dim_company WHERE companykey IN (0, -1)
UNION ALL SELECT 'Dim_Work_Mode', COUNT(*) FROM dwh.dim_work_mode WHERE workmodekey IN (0, -1)
UNION ALL SELECT 'Dim_Salary_Context', COUNT(*) FROM dwh.dim_salary_context WHERE salarycontextkey IN (0, -1)
UNION ALL SELECT 'Dim_Job', COUNT(*) FROM dwh.dim_job WHERE jobkey IN (0, -1)
UNION ALL SELECT 'Dim_Role', COUNT(*) FROM dwh.dim_role WHERE rolekey IN (0, -1)
UNION ALL SELECT 'Dim_Seniority', COUNT(*) FROM dwh.dim_seniority WHERE senioritykey IN (0, -1)
UNION ALL SELECT 'Dim_Location', COUNT(*) FROM dwh.dim_location WHERE locationkey IN (0, -1)
UNION ALL SELECT 'Dim_Skill', COUNT(*) FROM dwh.dim_skill WHERE skillkey IN (0, -1)
UNION ALL SELECT 'Dim_Knowledge', COUNT(*) FROM dwh.dim_knowledge WHERE knowledgekey IN (0, -1)
UNION ALL SELECT 'Dim_Education', COUNT(*) FROM dwh.dim_education WHERE educationkey IN (0, -1)
UNION ALL SELECT 'Dim_Foreign_Language', COUNT(*) FROM dwh.dim_foreign_language WHERE languagekey IN (0, -1)
UNION ALL SELECT 'Dim_Soft_Skill', COUNT(*) FROM dwh.dim_soft_skill WHERE softskillkey IN (0, -1)
ORDER BY tablename;

-- 2. Kiểm tra dữ liệu lịch thực tế Dim_Date
SELECT 
    COUNT(*) AS total_dates,
    MIN(fulldate) AS min_date,
    MAX(fulldate) AS max_date,
    COUNT(*) FILTER (WHERE datekey > 0) AS real_date_count
FROM dwh.dim_date;

-- 3. Kiểm tra tính toàn vẹn SCD Type 2 cho Dim_Job (mỗi JobID tối đa 1 bản ghi current)
SELECT jobid, COUNT(*) AS active_current_count
FROM dwh.dim_job
WHERE iscurrent = TRUE
GROUP BY jobid
HAVING COUNT(*) > 1;

-- 4. Kiểm tra khoảng thời gian SCD2 (validto phải lớn hơn validfrom)
SELECT jobkey, jobid, validfrom, validto
FROM dwh.dim_job
WHERE validto IS NOT NULL AND validto <= validfrom;

-- 5. Tổng số lượng bản ghi của 14 Dimension
SELECT 'Dim_Date' AS tablename, COUNT(*) AS row_count FROM dwh.dim_date
UNION ALL SELECT 'Dim_Source', COUNT(*) FROM dwh.dim_source
UNION ALL SELECT 'Dim_Company', COUNT(*) FROM dwh.dim_company
UNION ALL SELECT 'Dim_Work_Mode', COUNT(*) FROM dwh.dim_work_mode
UNION ALL SELECT 'Dim_Salary_Context', COUNT(*) FROM dwh.dim_salary_context
UNION ALL SELECT 'Dim_Job', COUNT(*) FROM dwh.dim_job
UNION ALL SELECT 'Dim_Role', COUNT(*) FROM dwh.dim_role
UNION ALL SELECT 'Dim_Seniority', COUNT(*) FROM dwh.dim_seniority
UNION ALL SELECT 'Dim_Location', COUNT(*) FROM dwh.dim_location
UNION ALL SELECT 'Dim_Skill', COUNT(*) FROM dwh.dim_skill
UNION ALL SELECT 'Dim_Knowledge', COUNT(*) FROM dwh.dim_knowledge
UNION ALL SELECT 'Dim_Education', COUNT(*) FROM dwh.dim_education
UNION ALL SELECT 'Dim_Foreign_Language', COUNT(*) FROM dwh.dim_foreign_language
UNION ALL SELECT 'Dim_Soft_Skill', COUNT(*) FROM dwh.dim_soft_skill
ORDER BY tablename;
