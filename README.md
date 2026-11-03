# IT Job Market DWH

## Cài đặt

Yêu cầu Python 3.10+ và Docker Compose. Chạy từ thư mục gốc dự án:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
docker compose -f docker/docker-compose.yml up -d postgres
```

Các giá trị `PG*` sau khớp cấu hình Docker Compose mặc định. Hãy đổi chúng nếu bạn dùng PostgreSQL khác; dùng cùng một bộ giá trị cho mọi lệnh dưới đây.

```bash
export PGHOST=127.0.0.1
export PGPORT=5432
export PGDATABASE=it_job_market_v12
export PGUSER=admin
export PGPASSWORD=12345678
```

`PGDATABASE` phải trùng với `POSTGRES_DB` mà Compose đang dùng. Project hiện cấu hình `it_job_market_v12`; có thể kiểm tra bằng `docker compose -f docker/docker-compose.yml config`.

Để nạp SQL Server DW, máy cần có ODBC Driver for SQL Server; `pyodbc` đã nằm trong `requirements.txt`.

## Crawl TopDev

```bash
python main_topdev.py
```

## Crawl ITviec

```bash
python main_itviec.py
```

Đặt Cookie tại `data_preprocessing/itviec_crawl_data/cookie_itviec.txt`, hoặc dùng `--login --headed` để đăng nhập qua trình duyệt.

## Nạp dữ liệu

Chạy toàn bộ luồng từ crawl hai nguồn đến `analytics`:

```bash
python pipeline/main.py --limit 50
```

```

Để nạp analytics vào SQL Server DW, đặt chuỗi kết nối của DB đích rồi chạy:

```bash
export DW_SQLSERVER_CONNECTION_STRING='DRIVER={ODBC Driver 18 for SQL Server};SERVER=HOST,1433;DATABASE=DB;UID=USER;PWD=PASSWORD;Encrypt=yes;TrustServerCertificate=yes'
python pipeline/load_to_dwh.py
```

Thay `HOST`, `DB`, `USER`, `PASSWORD` bằng thông tin SQL Server của bạn. Hai script dùng cùng PostgreSQL từ `PG*`, `DATABASE_URL` hoặc cấu hình Compose; lệnh cũ `load.py` vẫn dùng được.

## Data dictionary — PostgreSQL

| Bảng | Một dòng / khóa | Dữ liệu chính |
|---|---|---|
| `raw.crawl_runs` | Một lần crawl; PK `crawl_run_id` | Nguồn, thời gian bắt đầu/kết thúc, số tin yêu cầu/thu được, trạng thái, metadata và lỗi. |
| `raw.job_postings` | Một tin trong một lần crawl; PK `(job_id, crawl_run_id)` | ID và URL nguồn, `observed_at`, JSON gốc trong `raw_payload`. |
| `normalize_data.normalized_jobs` | Một tin đã qua lọc role/ngôn ngữ và chuẩn hóa; PK `(job_id, crawl_run_id)` | Tiêu đề, công ty, mô tả, vai trò, cấp bậc, thành phố, kỹ năng, ngoại ngữ, hình thức làm; `normalization_meta`, version và thời điểm chuẩn hóa. |
| `analytics.job_observations` | Một lần quan sát đã tách nguyên tử; PK `observation_key`, duy nhất `(job_id, crawl_run_id)` | Nội dung chính của tin, trạng thái ngoại ngữ, thời điểm/version nguồn, các hash kiểm tra đồng bộ. |
| `analytics.dim_<nhóm>` | Một giá trị danh mục; PK `<nhóm>_key` | `name`, `category`; duy nhất `(name, category)`. |
| `analytics.bridge_job_<nhóm>` | Một liên kết quan sát–giá trị; PK `(observation_key, <nhóm>_key, context_key)` | Khóa nối observation/dimension; `level_text` cho ngữ cảnh trình độ ngoại ngữ. |
| `analytics.observation_issues` | Một mục cần rà soát; PK `(observation_key, issue_key)` | Tên trường, giá trị nguồn và lý do cần xem lại. |
| `analytics.pipeline_state` | Một dòng trạng thái mô hình; PK `singleton` | Tên mô hình và hash phiên bản. |
| `staging.normalized_jobs` (cũ, nếu có) | Một tin theo hợp đồng staging cũ; PK `(job_id, crawl_run_id)` | Có thêm các trường kinh nghiệm, học vấn, ngày đăng và lương; không thuộc luồng nạp hiện tại. |

Tám `<nhóm>` trong analytics: `programming_language`, `framework`, `core_skill`, `role`, `city`, `foreign_language`, `level`, `soft_skill`. View `analytics.fact_job_latest` lấy lần quan sát mới nhất của từng tin; các view `*_demand_latest` dùng để thống kê nhu cầu.

## Data dictionary — SQL Server DW

| Bảng | Một dòng / khóa | Dữ liệu chính |
|---|---|---|
| `dw.fact_job` | Bản quan sát mới nhất của một tin; PK `job_key`, `job_id` duy nhất | Nguồn, tiêu đề, công ty, URL, lần crawl, `observed_at_utc`. Không lưu toàn bộ lịch sử crawl. |
| `dw.dim_<nhóm>` | Một giá trị danh mục; PK `dim_id`, duy nhất `(name, category)` | Tên, nhóm và thời điểm tạo/cập nhật. |
| `dw.bridge_job_<nhóm>` | Một liên kết tin–giá trị; PK `(job_key, dim_id, context_no)` | Khóa nối fact/dimension; `context_no` và `level_text` giữ ngữ cảnh trình độ nếu có. |

DW dùng cùng tám `<nhóm>` như analytics. Mỗi nhóm có view `dw.job_<nhóm>` đã nối fact, bridge và dimension để truy vấn trực tiếp.
