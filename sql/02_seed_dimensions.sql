-- =========================================================
-- 02_seed_dimensions.sql
-- Nạp bản ghi kỹ thuật (0, -1), Dim_Date và danh mục chuẩn
-- =========================================================

BEGIN;

-- 1. Dim_Date (Key 0: Unknown, Key -1: Invalid)
INSERT INTO dwh.dim_date
    (datekey, fulldate, day, month, monthname, quarter, year, weekofyear, isweekend)
VALUES
    (0,  '1900-01-01', 1, 1, 'Unknown', 1, 1900, 1, FALSE),
    (-1, '1900-01-02', 2, 1, 'Invalid', 1, 1900, 1, FALSE)
ON CONFLICT DO NOTHING;

-- Dữ liệu lịch thực tế (2023 - 2028) phục vụ nạp Fact
INSERT INTO dwh.dim_date
    (datekey, fulldate, day, month, monthname, quarter, year, weekofyear, isweekend)
SELECT
    TO_CHAR(d, 'YYYYMMDD')::INT,
    d::DATE,
    EXTRACT(DAY FROM d)::SMALLINT,
    EXTRACT(MONTH FROM d)::SMALLINT,
    TRIM(TO_CHAR(d, 'Month')),
    EXTRACT(QUARTER FROM d)::SMALLINT,
    EXTRACT(YEAR FROM d)::SMALLINT,
    EXTRACT(WEEK FROM d)::SMALLINT,
    CASE WHEN EXTRACT(ISODOW FROM d) IN (6, 7) THEN TRUE ELSE FALSE END
FROM generate_series('2023-01-01'::DATE, '2028-12-31'::DATE, '1 day'::INTERVAL) d
ON CONFLICT (datekey) DO NOTHING;

-- 2. Dim_Source
INSERT INTO dwh.dim_source (sourcekey, sourcecode, sourcename)
VALUES
    (0, 'UNKNOWN', 'Chưa xác định nguồn'),
    (-1, 'INVALID', 'Nguồn không hợp lệ')
ON CONFLICT DO NOTHING;

-- 3. Dim_Company
INSERT INTO dwh.dim_company (companykey, canonicalcompanyname, industry, companysize)
VALUES
    (0, 'Unknown Company', 'Unknown', 'Unknown'),
    (-1, 'Invalid Company', 'Invalid', 'Invalid')
ON CONFLICT DO NOTHING;

-- 4. Dim_Work_Mode
INSERT INTO dwh.dim_work_mode (workmodekey, workmodecode, description)
VALUES
    (0, 'unknown', 'Chưa xác định'),
    (-1, 'invalid', 'Không hợp lệ')
ON CONFLICT DO NOTHING;

-- 5. Dim_Salary_Context
INSERT INTO dwh.dim_salary_context (salarycontextkey, currency, period, basis, status)
VALUES
    (0, 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'unknown'),
    (-1, 'INVALID', 'INVALID', 'INVALID', 'invalid')
ON CONFLICT DO NOTHING;

-- 6. Dim_Job (SCD2 kỹ thuật)
INSERT INTO dwh.dim_job
    (jobkey, jobid, sourcejobid, title, description, validfrom, validto, iscurrent)
VALUES
    (0, 'UNKNOWN', NULL, 'Unknown Job', NULL, TIMESTAMPTZ '1900-01-01 00:00:00+00', NULL, TRUE),
    (-1, 'INVALID', NULL, 'Invalid Job', NULL, TIMESTAMPTZ '1900-01-02 00:00:00+00', NULL, TRUE)
ON CONFLICT DO NOTHING;

-- 7. Dim_Role
INSERT INTO dwh.dim_role (rolekey, canonicalrole, rolegroup)
VALUES
    (0, 'Unknown Role', 'Unknown'),
    (-1, 'Invalid Role', 'Invalid')
ON CONFLICT DO NOTHING;

-- 8. Dim_Seniority
INSERT INTO dwh.dim_seniority (senioritykey, senioritycode, description)
VALUES
    (0, 'UNKNOWN', 'Chưa xác định cấp độ'),
    (-1, 'INVALID', 'Cấp độ không hợp lệ')
ON CONFLICT DO NOTHING;

-- 9. Dim_Location
INSERT INTO dwh.dim_location (locationkey, canonicalcity, region, country)
VALUES
    (0, 'Unknown Location', 'Unknown', 'Unknown'),
    (-1, 'Invalid Location', 'Invalid', 'Invalid')
ON CONFLICT DO NOTHING;

-- 10. Dim_Skill
INSERT INTO dwh.dim_skill (skillkey, canonicalskill, category)
VALUES
    (0, 'Unknown Skill', 'Unknown'),
    (-1, 'Invalid Skill', 'Invalid')
ON CONFLICT DO NOTHING;

-- 11. Dim_Knowledge
INSERT INTO dwh.dim_knowledge (knowledgekey, canonicalknowledge)
VALUES
    (0, 'Unknown Knowledge'),
    (-1, 'Invalid Knowledge')
ON CONFLICT DO NOTHING;

-- 12. Dim_Education
INSERT INTO dwh.dim_education (educationkey, degree, major)
VALUES
    (0, 'Unknown Degree', 'Unknown Major'),
    (-1, 'Invalid Degree', 'Invalid Major')
ON CONFLICT DO NOTHING;

-- 13. Dim_Foreign_Language
INSERT INTO dwh.dim_foreign_language (languagekey, canonicallanguage)
VALUES
    (0, 'Unknown Language'),
    (-1, 'Invalid Language')
ON CONFLICT DO NOTHING;

-- 14. Dim_Soft_Skill
INSERT INTO dwh.dim_soft_skill (softskillkey, canonicalsoftskill)
VALUES
    (0, 'Unknown Soft Skill'),
    (-1, 'Invalid Soft Skill')
ON CONFLICT DO NOTHING;

-- Danh mục chuẩn nguồn tuyển dụng
INSERT INTO dwh.dim_source (sourcecode, sourcename)
VALUES
    ('itviec', 'ITviec'),
    ('topdev', 'TopDev'),
    ('vietnamworks', 'VietnamWorks')
ON CONFLICT (sourcecode) DO NOTHING;

-- Danh mục hình thức làm việc
INSERT INTO dwh.dim_work_mode (workmodecode, description)
VALUES
    ('onsite', 'Làm việc tại văn phòng'),
    ('hybrid', 'Làm việc kết hợp'),
    ('remote', 'Làm việc từ xa')
ON CONFLICT (workmodecode) DO NOTHING;

-- Danh mục cấp bậc chuẩn hóa
INSERT INTO dwh.dim_seniority (senioritycode, description)
VALUES
    ('All levels', 'Mọi cấp độ kinh nghiệm'),
    ('Internship', 'Thực tập sinh'),
    ('Intern', 'Thực tập sinh (tương đương Internship)'),
    ('Fresher', 'Mới tốt nghiệp / Chưa có kinh nghiệm'),
    ('Junior', 'Cấp độ Junior (dưới 2 năm)'),
    ('Middle', 'Cấp độ Middle (2 - 4 năm)'),
    ('Senior', 'Cấp độ Senior (từ 4-5 năm trở lên)'),
    ('Lead', 'Trưởng nhóm kỹ thuật (Tech Lead)'),
    ('Manager', 'Quản lý bộ phận / kỹ thuật'),
    ('Director', 'Giám đốc chuyên môn / kỹ thuật')
ON CONFLICT (senioritycode) DO NOTHING;

-- Danh mục ngữ cảnh lương
INSERT INTO dwh.dim_salary_context (currency, period, basis, status)
VALUES
    ('N/A', 'N/A', 'N/A', 'negotiable'),
    ('N/A', 'N/A', 'N/A', 'not_disclosed'),
    ('VND', 'monthly', 'gross', 'disclosed'),
    ('VND', 'monthly', 'net', 'disclosed'),
    ('USD', 'monthly', 'gross', 'disclosed'),
    ('USD', 'monthly', 'net', 'disclosed')
ON CONFLICT (currency, period, basis, status) DO NOTHING;

-- Danh mục học vấn mặc định
INSERT INTO dwh.dim_education (degree, major)
VALUES
    ('Not Specified', 'Not Specified')
ON CONFLICT (degree, major) DO NOTHING;

-- Đặt lại sequence sau khi chèn key thủ công 0/-1
SELECT setval(pg_get_serial_sequence('dwh.dim_source', 'sourcekey'),
              GREATEST(COALESCE((SELECT MAX(sourcekey) FROM dwh.dim_source), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_company', 'companykey'),
              GREATEST(COALESCE((SELECT MAX(companykey) FROM dwh.dim_company), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_work_mode', 'workmodekey'),
              GREATEST(COALESCE((SELECT MAX(workmodekey) FROM dwh.dim_work_mode), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_salary_context', 'salarycontextkey'),
              GREATEST(COALESCE((SELECT MAX(salarycontextkey) FROM dwh.dim_salary_context), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_job', 'jobkey'),
              GREATEST(COALESCE((SELECT MAX(jobkey) FROM dwh.dim_job), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_role', 'rolekey'),
              GREATEST(COALESCE((SELECT MAX(rolekey) FROM dwh.dim_role), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_seniority', 'senioritykey'),
              GREATEST(COALESCE((SELECT MAX(senioritykey) FROM dwh.dim_seniority), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_location', 'locationkey'),
              GREATEST(COALESCE((SELECT MAX(locationkey) FROM dwh.dim_location), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_skill', 'skillkey'),
              GREATEST(COALESCE((SELECT MAX(skillkey) FROM dwh.dim_skill), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_knowledge', 'knowledgekey'),
              GREATEST(COALESCE((SELECT MAX(knowledgekey) FROM dwh.dim_knowledge), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_education', 'educationkey'),
              GREATEST(COALESCE((SELECT MAX(educationkey) FROM dwh.dim_education), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_foreign_language', 'languagekey'),
              GREATEST(COALESCE((SELECT MAX(languagekey) FROM dwh.dim_foreign_language), 1), 1), TRUE);
SELECT setval(pg_get_serial_sequence('dwh.dim_soft_skill', 'softskillkey'),
              GREATEST(COALESCE((SELECT MAX(softskillkey) FROM dwh.dim_soft_skill), 1), 1), TRUE);

COMMIT;
