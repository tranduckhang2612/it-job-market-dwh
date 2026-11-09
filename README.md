# IT Job Market DWH

## Cài đặt

Yêu cầu Python 3.10+ và Docker Compose. Chạy từ thư mục gốc dự án:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
docker compose -f docker/docker-compose.yml up -d postgres
```

Ví dụ cấu hình `PG*` cho PostgreSQL local. Thay mật khẩu bằng giá trị `POSTGRES_PASSWORD` bạn đã cấu hình; dùng cùng một bộ giá trị cho mọi lệnh dưới đây.

```bash
export PGHOST=127.0.0.1
export PGPORT=5432
export PGDATABASE=it_job_market_v12
export PGUSER=admin
export PGPASSWORD='YOUR_POSTGRES_PASSWORD'
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

Luồng mặc định: **crawl → PostgreSQL `raw` → `normalize_data` → SQL Server `dw`**.
Đặt chuỗi kết nối của DB đích rồi chạy:

```bash
export DW_SQLSERVER_CONNECTION_STRING='DRIVER={ODBC Driver 18 for SQL Server};SERVER=HOST,1433;DATABASE=DB;UID=USER;PWD=PASSWORD;Encrypt=yes;TrustServerCertificate=yes'
python pipeline/main.py --limit 50
```

Thay `HOST`, `DB`, `USER`, `PASSWORD` bằng thông tin SQL Server của bạn. Hai script dùng cùng PostgreSQL từ `PG*`, `DATABASE_URL` hoặc cấu hình Compose.

- Đã có dữ liệu chuẩn hóa: chạy `python pipeline/load_to_dwh.py` để nạp SQL Server, hoặc thêm `--preview` để chỉ xem số dòng.
- Chỉ crawl và chuẩn hóa: thêm `--normalize-only` cho `pipeline/main.py`.
- Loader lấy bản quan sát mới nhất theo `observed_at` của mỗi `job_id` (hòa thì lấy `crawl_run_id` lớn hơn), tách tám nhóm trong Python rồi nạp fact/dim. Khi crawl một nguồn, SQL Server vẫn nhận snapshot chuẩn hóa của tất cả nguồn.
- `analytics` là luồng cũ, chỉ chạy nếu chọn `--out` trên `pipeline/main.py` hoặc gọi `load.py analytics` riêng. Luồng DWH không đọc, tạo hay xóa schema này.

### Chuẩn hóa cấp bậc

Ưu tiên cấp bậc do nguồn công bố, rồi đến tiêu đề, sau đó mới suy từ số năm kinh nghiệm tổng quát. Các mốc là **Fresher** (0–dưới 1 năm), **Junior** (1–dưới 3), **Mid-level** (3–dưới 5), **Senior** (từ 5 năm). Khoảng giao hai dải liền kề giữ cả hai; giao từ ba dải thì để trống.

Chỉ dùng **Intern**, **Lead**, **Principal**, **Architect**, **Director**, **CTO**, **CIO** và **All levels** khi nguồn hoặc tiêu đề có dấu hiệu rõ. Không suy từ số năm gắn với một công nghệ (ví dụ “3 năm Java”) hay từ “Nhân viên”/“Chuyên viên” đứng riêng. `All levels` là một giá trị, không bung thành nhiều cấp.

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

| Bảng | Một dòng đại diện cho | Khóa và dữ liệu chính |
|---|---|---|
| **FactJob** (`dw.fact_job`) | Bản quan sát mới nhất của **một tin tuyển dụng**, dù tin yêu cầu nhiều kỹ năng hoặc có nhiều địa điểm. | PK `fact_job_key`; `job_id` duy nhất; FK `observed_date_key`; chỉ số `job_posting_count = 1`; nguồn, tiêu đề, công ty, URL, lần crawl và thời điểm quan sát UTC. |
| `dw.dim_date` | Một ngày quan sát theo UTC. | PK `date_key` dạng `YYYYMMDD`; `calendar_date`. |
| `dw.dim_<nhóm>` | Một lần xuất hiện của một giá trị trong **một tin**. | PK `<nhóm>_key` (ví dụ `programming_language_key`); FK `fact_job_key`; `name`, thời điểm tạo và thuộc tính riêng của từng nhóm. |

SQL Server `dw` không còn bảng bridge: `dw.fact_job` nối trực tiếp **1–N** với từng bảng `dw.dim_<nhóm>` qua `fact_job_key`. Hai tin cùng yêu cầu Python sẽ có hai dòng Python với hai `programming_language_key` khác nhau. Một tin yêu cầu cả Python và SQL vẫn chỉ có một dòng trong fact. Các view `dw.job_<nhóm>` giữ tên và dùng các thuộc tính tương ứng với từng bảng.

- `dim_framework.programming_language` ghi ngôn ngữ tiêu biểu của hệ sinh thái (ví dụ Spring Boot → Java, FastAPI → Python, Angular → TypeScript), không suy ra từ các kỹ năng khác trong tin. MVVM và tên chưa xác định có giá trị `NULL`.
- Các bảng `dim_city` và `dim_level` không có `category` hay `level_text`. `dim_foreign_language` giữ `level_text` để mô tả yêu cầu ngoại ngữ, nhưng không có `category`.

`dw.dim_role.category` nhận một trong năm nhóm: **Software Engineer**, **Data/AI**, **Security**, **Embbed**, **Web/App**. Nhóm được suy ra từ tên vai trò đã chuẩn hóa; vai trò chưa có nhóm cụ thể dùng **Software Engineer**, không dùng `other`. Khi đổi nhóm, loader cập nhật ngay trên dòng cũ và giữ nguyên `role_key`.

`dw.dim_core_skill.category` dùng quy tắc theo tên kỹ năng đã chuẩn hóa: LLM, NLP và Computer Vision thuộc `data_ai`; Kafka thuộc `data_streaming`. Tên chưa có quy tắc giữ category nguồn. Loader chỉ cập nhật category trên dòng cũ, giữ `core_skill_key` và các lần xuất hiện.

Đếm số tin từ fact bằng `SUM(job_posting_count)`. Khi lọc hoặc nhóm theo kỹ năng/địa điểm, dùng `COUNT(DISTINCT f.fact_job_key)` sau khi nối để tránh đếm một tin nhiều lần:

```sql
SELECT d.name AS programming_language, COUNT(DISTINCT f.fact_job_key) AS jobs
FROM dw.fact_job f
JOIN dw.dim_programming_language d ON d.fact_job_key = f.fact_job_key
GROUP BY d.name;
```

Khi nạp với `--apply`, loader chạy [DDL](data_preprocessing/sql/dw.sql) trong cùng transaction với dữ liệu. Với schema cũ, nó giữ ID của tin, chuyển từng dòng bridge sang dimension rồi mới xóa bridge cũ. Giá trị danh mục cũ không gắn với tin nào không thuộc snapshot mới. ID dimension cũ dùng chung cho nhiều tin, nên mỗi dòng sau chuyển đổi sẽ được cấp ID mới. Nạp lại cùng snapshot giữ nguyên các ID mới.

### Đọc một dòng fact

Chạy [fact_examples.sql](data_preprocessing/sql/fact_examples.sql) trong SSMS; đổi `@job_id` thành mã một tin có trong DW. Kết quả đầu đọc đúng một tin, kết quả thứ hai minh họa đúng một dòng bán hàng. Ví dụ:

- **FactJob:** Vào ngày 29/12/2020 (observed_date_key = 20201229), nguồn topdev (source) ghi nhận tin tuyển dụng Python Developer của công ty ABC (fact_job_key = 1), với số tin là 1 (job_posting_count).
- **FactInternetSales:** Vào ngày 29/12/2020 (OrderDateKey), khách hàng 21768 (CustomerKey) đã mua sản phẩm Adjustable Race có mã 310 (ProductKey) với tổng số tiền giao dịch là $3,578.27 (SalesAmount).

`FactInternetSales` chỉ là ví dụ so sánh grain (một dòng bán hàng), không phải bảng trong kho tuyển dụng.
