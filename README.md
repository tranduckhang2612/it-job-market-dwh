# IT Job Market Crawl and Normalization Pipeline

## Bổ sung analytics nguyên tử — 02/11/2026

**Trạng thái DW:** đã chạy thử luồng normalize_data → analytics → SQL Server DW trên máy local. Xem [data dictionary và lệnh chạy tay](#từ-normalize-đến-sql-server-dw).

Luồng mới: **raw → check/chuẩn hóa đã có → normalize_data (5.1) → analytics nguyên tử → load DWH/phân tích**. Không sửa hoặc chạy lại rule upstream trong loader analytics. Bỏ benefit theo yêu cầu. Xem [bảng, cách nạp, SQL và kết quả kiểm thử](docs/ANALYTICS_ATOMIC.md).

```bash
python load_analytics.py --dry-run
python load_analytics.py --apply --out reports/analytics/run-001
```

Chọn đúng DB bằng PG*/DATABASE_URL/--db-*. DB demo thật của đợt này là **it_job_market_v5**; dữ liệu PostgreSQL không đi kèm khi clone repo. --apply đồng bộ cả xóa các observation đã bị rút khỏi nguồn trong phạm vi đã chọn.


Luồng dữ liệu hiện tại:

```text
ITviec / TopDev → crawler → raw → role/language gate → chuẩn hóa → normalize_data
                                                               ↓
                                                    analytics nguyên tử → DWH (loader riêng)
```

JSON lấy từ nguồn được giữ nguyên trong `raw.job_postings.raw_payload`; trạng thái
và lỗi của mỗi lần chạy nằm trong `raw.crawl_runs`. Loader chỉ đưa các tin đạt hai
lớp xử lý vào `normalize_data.normalized_jobs`. Schema `staging` hiện có được giữ
nguyên; loader mới không dual-write sang staging.

## Cài đặt

Yêu cầu Python 3.10 trở lên và Docker Compose.

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
docker compose -f docker/docker-compose.yml up -d postgres
```

Hai entrypoint tự đọc cấu hình PostgreSQL từ Compose khi không có `DATABASE_URL`,
biến `PG*` hoặc tùy chọn `--db-*`. Nếu database chưa có hai bảng raw, entrypoint tự
áp dụng [raw_schema.sql](data_preprocessing/sql/raw_schema.sql).

Kiểm tra kết nối và cấu trúc trước khi crawl:

```bash
python main_itviec.py --check-db
python main_topdev.py --check-db
```

## Crawl TopDev

```bash
python main_topdev.py
python main_topdev.py --limit 50
```

TopDev mặc định gọi API công khai và ghi trực tiếp từng tin đã thu thập vào
PostgreSQL.

## Crawl ITviec

Lưu header Cookie của phiên ITviec vào file sau:

```text
data_preprocessing/itviec_crawl_data/cookie_itviec.txt
```

File cookie được `.gitignore` bỏ qua. Sau đó chạy:

```bash
python main_itviec.py
python main_itviec.py --limit 50
```

Có thể dùng file khác với `--cookie-file DUONG_DAN`. Khi cần đăng nhập bằng trình
duyệt, dùng `--login --headed` sau khi đã cài Chromium cho Playwright.

## Cấu trúc raw

| Bảng | Nội dung |
|---|---|
| `raw.crawl_runs` | ID lần chạy, nguồn, thời gian, số lượng, trạng thái, metadata và errors |
| `raw.job_postings` | ID tin, lần crawl, URL, thời điểm quan sát và toàn bộ JSON nguồn |

Khóa chính `(job_id, crawl_run_id)` giữ được nhiều lần quan sát của cùng một tin.
Schema không khai báo `CREATE INDEX`; PostgreSQL vẫn tạo index bắt buộc cho khóa
chính.

## Lớp trung gian 1: role gate

[role_gate.py](data_preprocessing/normalization/role_gate.py) đọc danh sách role từ
[ds.md](ds.md). Một tin chỉ được chuyển tiếp khi ít nhất một trường role khớp danh
sách này:

- ITviec: `title`, `job_expertise`, `job_domains`.
- TopDev: `title`, `job_types_str`, `job_types_arr`, `job_types`.

Gate không dùng skills hoặc description để tránh nhận nhầm tin ngoài IT chỉ vì mô
tả có nhắc tới công nghệ. So khớp được chuẩn hóa chữ hoa, dấu tiếng Việt, dấu gạch
nối và ranh giới từ. Các biến thể phổ biến như `Backend Engineer`, `QA Automation
Engineer`, `SRE`, `DBA` được ánh xạ về role trong `ds.md`.

`detect_job_language()` kiểm tra metadata ngôn ngữ nếu nguồn cung cấp, sau đó kiểm
tra title và nội dung tin. Chỉ nội dung tiếng Việt (`vi`) hoặc tiếng Anh (`en`) được
đi tiếp. Tiếng Hàn, Nhật, Trung và ngôn ngữ không xác định bị loại. Một cụm như
`Korean Interpreter` viết bằng tiếng Anh vẫn được nhận là tiếng Anh; việc nhận diện
dựa vào ngôn ngữ nội dung, không dựa vào tên ngoại ngữ được nhắc tới.

`iter_normalization_candidates()` bỏ qua tin không khớp rồi tiếp tục tin kế tiếp. Tin bị
bỏ qua vẫn còn nguyên trong raw để kiểm tra lại; chỉ không được chuyển sang
`normalize_data`.
Quyết định trả về một trong các lý do:

- `matched_role_keyword`: được đi tiếp.
- `no_matching_role_keyword`: có role text nhưng không khớp.
- `missing_role_text`: nguồn không có trường role.
- `invalid_raw_payload`: JSON đầu vào không hợp lệ.
- `unsupported_language`: nội dung thuộc ngôn ngữ khác Anh/Việt.
- `unknown_language`: không đủ dữ liệu để xác định ngôn ngữ.

## Lớp trung gian 2: chuẩn hóa có bằng chứng

[normalizer.py](data_preprocessing/normalization/normalizer.py) ánh xạ hai
JSON nguồn về một cấu trúc chung. Hợp đồng nội bộ vẫn phân tích kinh nghiệm để suy
luận seniority, nhưng schema `normalize_data` chỉ lưu 17 cột nghiệp vụ. Schema này
không lưu các cột kinh nghiệm, học vấn, ngày đăng, required knowledge hoặc salary.

Giá trị đơn chưa xác định dùng `NULL`; trường danh sách chưa có dữ liệu dùng `[]`.
Thời gian luôn có timezone, URL được bỏ query/fragment và HTML được chuyển thành
text. Các rule quan trọng:

- `job_roles`: title trước, trường role của nguồn sau; xung đột được ghi metadata.
- `seniority_levels`: nguồn công bố rõ ràng → title → kinh nghiệm tổng quát. Quy
  tắc chi tiết nằm trong [levels.md](levels.md); khoảng `2–4 năm` được giữ thành
  hai giá trị `Junior` và `Mid-level` thay vì bỏ trống hoặc ép về một level.
- Kinh nghiệm chỉ được xử lý trong bộ nhớ để suy `seniority_levels`; số năm riêng
  cho Java/React/... không được dùng và kết quả kinh nghiệm không được persist.
- `cities`: địa chỉ có cấu trúc → city của nguồn → full address → title fallback.
- `work_mode`: chỉ nhận từ trường nguồn hoặc cụm rõ ràng trong mô tả, không mặc
  định `onsite`.
- `skills`: alias được chuẩn hóa, skill chưa biết vẫn giữ ở nhóm `other`; tên ngoại
  ngữ được chuyển sang `foreign_languages`.
- `foreign_languages.level_text`: giữ chứng chỉ/mức gốc như `TOEIC 650`, `JLPT N2`;
  không tự quy đổi sang CEFR. Nếu raw không công bố yêu cầu ngoại ngữ và không có
  lỗi chuẩn hóa, giá trị được lưu là `[{"language": "unknown", "level_text": null}]`.

## Load vào normalize_data

[normalize_data_schema.sql](data_preprocessing/sql/normalize_data_schema.sql) tạo bảng
`normalize_data.normalized_jobs`: 17 cột nghiệp vụ, `normalization_meta`, version và
thời điểm xử lý. Metadata chỉ giữ source/confidence/evidence/conflicts ngắn, không
lặp lại raw JSON. Các trường danh sách dùng JSONB và schema không tạo `CREATE INDEX`
phụ. Khóa `(job_id, crawl_run_id)` cùng câu lệnh upsert giúp chạy lại cùng batch mà
không nhân bản dữ liệu. Nếu payload chuẩn hóa không thay đổi, upsert giữ nguyên
`normalized_at`; nhờ đó luồng incremental sau này không phải đọc lại toàn bộ bảng.

Chạy toàn bộ raw đã kết thúc crawl:

```bash
python load_normalize_data.py
```

Các chế độ thường dùng:

```bash
python load_normalize_data.py --check-db
python load_normalize_data.py --dry-run
python load_normalize_data.py --source itviec
python load_normalize_data.py --crawl-run-id RUN_ID
```

Một tin không khớp role, không đạt ngôn ngữ hoặc không chuẩn hóa được sẽ bị bỏ qua;
loader tiếp tục tin kế tiếp. Tin bị bỏ qua vẫn còn trong raw. Nếu chạy lại sau khi
rule thay đổi, loader xóa dòng `normalize_data` cũ không còn hợp lệ. Loader trả exit
code `2` nếu có lỗi chuẩn hóa/ghi, `4` nếu lỗi database; việc loại bởi gate là hành
vi bình thường.

`staging.normalized_jobs` cũ không bị xóa hoặc cập nhật bởi lệnh trên. Loader analytics mới chỉ đọc `normalize_data` và ghi các bảng nguyên tử trong `analytics`. Loader DWH tiếp theo đọc fact/dimension/bridge từ analytics; không chạy lại crawler/gate/normalizer ở bước đó.

Kiểm tra dữ liệu:

```sql
SELECT crawl_run_id, source, status, requested_count, collected_count,
       started_at, finished_at
FROM raw.crawl_runs
ORDER BY started_at DESC;

SELECT job_id, crawl_run_id, job_url, observed_at, raw_payload
FROM raw.job_postings
ORDER BY ingested_at DESC
LIMIT 20;

SELECT job_id, source, title, seniority_levels, cities,
       normalization_version, normalized_at
FROM normalize_data.normalized_jobs
ORDER BY normalized_at DESC;
```

Nếu muốn xóa toàn bộ dữ liệu raw để crawl lại từ đầu:

```sql
TRUNCATE TABLE raw.job_postings, raw.crawl_runs CASCADE;
```

## Kết nối PostgreSQL khác

Có thể dùng `DATABASE_URL`, các biến chuẩn `PGHOST`, `PGPORT`, `PGDATABASE`,
`PGUSER`, `PGPASSWORD`, hoặc tùy chọn CLI:

```bash
python main_topdev.py \
  --no-compose-db \
  --db-host 127.0.0.1 \
  --db-port 5432 \
  --db-name it_job_market \
  --db-user admin \
  --db-password-prompt
```

## Kiểm thử

```bash
python -m unittest discover -s tests -v
```

Test PostgreSQL tích hợp chỉ chạy khi có `ITVIEC_TEST_DSN`. Test này tạo một
database tên ngẫu nhiên, kiểm tra cả hai nguồn rồi xóa database đó; không sửa
database project đang dùng.

## Tổ chức source code

Đọc [tài liệu kiến trúc](docs/ARCHITECTURE.md) trước khi thêm nguồn hoặc thay đổi
hợp đồng dữ liệu. Các file Python ở root chỉ giữ lại để tương thích với lệnh vận
hành cũ; logic thật nằm trong package `data_preprocessing`.

```text
main_itviec.py
main_topdev.py
load_staging.py
load_normalize_data.py
load_analytics.py
data_preprocessing/
├── analytics/              # tách nguyên tử và đồng bộ analytics mới
├── cli/                    # orchestration và command-line interface
├── crawlers/               # logic lấy dữ liệu theo nguồn
│   ├── itviec.py
│   └── topdev.py
├── normalization/          # role/language gate và chuẩn hóa
│   ├── contracts.py
│   ├── role_gate.py
│   └── normalizer.py
├── storage/                # hợp đồng truy cập PostgreSQL dùng chung
│   └── raw.py
└── sql/                    # DDL hiện tại và migration có version
    ├── raw_schema.sql
    ├── normalize_data_schema.sql
    ├── staging_schema.sql
    └── migrations/
docker/docker-compose.yml
tests/
```



### Data dictionary — PostgreSQL

| Bảng | Một dòng là gì / Khóa chính | Các trường chính |
|---|---|---|
| `raw.crawl_runs` | Một lần crawl; `crawl_run_id` | `source`: nguồn; `started_at`, `finished_at`: thời điểm chạy; `requested_count`, `collected_count`: số tin yêu cầu/thu được; `status`, `metadata`, `errors`: trạng thái, thông tin và lỗi; `ingested_at`: lúc ghi DB. |
| `raw.job_postings` | Một tin trong một lần crawl; `(job_id, crawl_run_id)` | `job_id` có tiền tố nguồn, `source_job_id` là ID tại nguồn, `job_url` là URL; `observed_at` là lúc quan sát, `ingested_at` là lúc ghi; `raw_payload` là JSONB gốc. |
| `normalize_data.normalized_jobs` | Một bản tin đã qua lọc và chuẩn hóa; `(job_id, crawl_run_id)` nối về raw | `source`, `source_job_id`, `job_url`, `observed_at`: truy xuất nguồn; `title`, `company_name`, `description`: nội dung; `location_text`, `work_mode`: địa điểm gốc/hình thức làm; `job_roles`, `seniority_levels`, `cities`, `skills`, `foreign_languages`, `soft_skills`: mảng JSONB; `normalization_meta`, `normalization_version`, `normalized_at`: dấu vết xử lý. |
| `staging.normalized_jobs` (legacy, nếu có) | Một tin chuẩn hóa theo hợp đồng cũ; `(job_id, crawl_run_id)` | Nhóm ID, nội dung, vai trò, cấp bậc, địa điểm, kỹ năng; thêm `published_at`, `required_knowledge`, `experience_*`, `education_requirements`, `salary_*` và metadata phiên bản. Xem [DDL staging](data_preprocessing/sql/staging_schema.sql). |
| `analytics.job_observations` | Một lần quan sát tin đã tách nguyên tử; `observation_key`, duy nhất `(job_id, crawl_run_id)` | Giữ ID, nguồn, URL, lần crawl, thời điểm, tiêu đề, công ty, mô tả, địa điểm, hình thức làm; `foreign_language_status`: biết/chưa biết; `source_normalized_at`, `source_normalization_version`, `source_sha256`, `output_sha256`, `model_sha256`, `loaded_at`: theo dõi đồng bộ. |
| `analytics.observation_issues` | Một mục cần rà soát của một lần quan sát; `(observation_key, issue_key)` | `field_name`, `source_value`, `reason`: trường, giá trị nguồn, lý do cần xem lại. |
| `analytics.pipeline_state` | Một dòng trạng thái mô hình; `singleton` | `model_name`, `model_sha256`: định danh và dấu vết phiên bản mô hình. |

`public` là schema mặc định của PostgreSQL; luồng này không dùng bảng nghiệp vụ ở đó. Xem [DDL analytics](data_preprocessing/sql/analytics_schema.sql) để tra kiểu dữ liệu, khóa và ràng buộc chính xác.

Trong `analytics`, mỗi `dim_<nhóm>` có khóa `<nhóm>_key`, `name`, `category`; một dòng là một giá trị chuẩn. Mỗi `bridge_job_<nhóm>` lưu một cặp **lần quan sát–giá trị** theo khóa `(observation_key, <nhóm>_key, context_key)`. Ngoại ngữ có thể có thêm ngữ cảnh trình độ ở `level_text`.

### Data dictionary — SQL Server DW

| Bảng | Một dòng là gì / Khóa chính | Các trường chính |
|---|---|---|
| `dw.fact_job` | Bản quan sát mới nhất của một tin; `job_key` (INT IDENTITY), `job_id` duy nhất | `source`, `title`, `company_name`, `job_url`, `crawl_run_id`, `observed_at_utc` (UTC). Bảng này không lưu toàn bộ lịch sử crawl. |
| Các bảng `dw.dim_<nhóm>` | Một giá trị danh mục; `dim_id` (INT IDENTITY), duy nhất `(name, category)` | `name`, `category`, `created_at_utc`, `updated_at_utc`. Bản hiện tại không dùng `row_sha256` hay `analytics_key` trong dim. |
| Các bảng `dw.bridge_job_<nhóm>` | Một liên kết tin–giá trị–ngữ cảnh; `(job_key, dim_id, context_no)` | `job_key` nối fact, `dim_id` nối dim cùng nhóm, `context_no` phân biệt các trình độ nếu có, `level_text` ghi trình độ ngoại ngữ nếu có. |

**Danh sách đủ 8 nhóm bảng** (tên bảng giống nhau ở hai schema; khóa và cấp độ dữ liệu khác nhau như trên):

| Nhóm | Bảng danh mục | Bảng liên kết | View SQL Server đã nối sẵn |
|---|---|---|---|
| Ngôn ngữ lập trình | `dim_programming_language` | `bridge_job_programming_language` | `dw.job_programming_language` |
| Framework | `dim_framework` | `bridge_job_framework` | `dw.job_framework` |
| Kỹ năng chuyên môn | `dim_core_skill` | `bridge_job_core_skill` | `dw.job_core_skill` |
| Vai trò | `dim_role` | `bridge_job_role` | `dw.job_role` |
| Thành phố | `dim_city` | `bridge_job_city` | `dw.job_city` |
| Ngoại ngữ | `dim_foreign_language` | `bridge_job_foreign_language` | `dw.job_foreign_language` |
| Cấp bậc | `dim_level` | `bridge_job_level` | `dw.job_level` |
| Kỹ năng mềm | `dim_soft_skill` | `bridge_job_soft_skill` | `dw.job_soft_skill` |

**View PostgreSQL:** `analytics.fact_job_latest` chọn bản quan sát mới nhất mỗi tin; 8 view `bridge_job_<nhóm>_latest` giữ liên kết của bản đó; 8 view `<nhóm>_demand_latest` thống kê số/tỷ lệ tin theo giá trị. `job_skills_latest` gộp các nhóm kỹ năng để phân tích; `role_skill_demand_latest` thống kê kỹ năng theo vai trò; `review_queue` hiển thị mục cần rà soát.

