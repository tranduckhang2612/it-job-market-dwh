# IT Job Market DWH

Thu thập tin tuyển dụng ITviec, chuẩn hóa dữ liệu và lưu vào PostgreSQL để phân tích thị trường việc làm IT. Crawler hỗ trợ chạy thủ công hoặc theo lịch với Airflow.

## Cấu trúc

- `itviec_crawler.py`: lấy trang danh sách và chi tiết, chuẩn hóa `JobPosting` JSON-LD, xuất JSON; tùy chọn nạp DB.
- `itviec_store.py`: nạp dữ liệu vào PostgreSQL theo khóa `(source_site, source_job_id)`.
- `sql/001_init.sql`: tạo bảng `itviec_jobs_current` và `itviec_crawl_runs`.
- `dags/itviec_daily.py`: chạy crawl và nạp DB hằng ngày bằng Airflow.

## Chạy thủ công

Cần Python 3.10+, PostgreSQL và `psql`. Từ thư mục gốc dự án:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ITVIEC_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME'
psql "$ITVIEC_DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/001_init.sql
python itviec_crawler.py --max-jobs 20 --pages 5 --delay 1.5 --output outputs/itviec_jobs.json --load-db
```

`--listing-url` mặc định là `https://itviec.com/it-jobs`; có thể truyền URL này kèm query để giới hạn tìm kiếm. `--max-jobs` giới hạn số tin crawl thành công, `--pages` giới hạn số trang danh sách, `--delay` là thời gian nghỉ giữa các request. Crawler dừng ở trang cuối theo liên kết phân trang của ITviec; trang rỗng bất thường trước đó làm đợt crawl thất bại để tránh nạp dữ liệu thiếu. Chạy `python itviec_crawler.py --help` để xem tất cả tùy chọn. Nếu chỉ muốn xuất JSON, bỏ `--load-db` và không cần kết nối PostgreSQL. Có thể dùng `--raw-html-dir` để giữ HTML chi tiết theo từng lần chạy phục vụ kiểm tra. 

Khóa duy nhất của DB là `(source_site, source_job_id)`: crawl lại cùng tin không tạo thêm hàng trong `itviec_jobs_current`. `last_seen_at` ghi lần quan sát mới nhất; nội dung và `content_changed_at` chỉ đổi khi dữ liệu nguồn đã chuẩn hóa đổi. `posted_at` là ngày đăng do nguồn cung cấp, khác với thời điểm crawl. Số trang/tin có giới hạn nên sự vắng mặt của một tin trong lần crawl không chứng minh tin đã đóng.

Kiểm tra kết quả:

```bash
psql "$ITVIEC_DATABASE_URL" -c 'SELECT count(*) FROM itviec_jobs_current;'
psql "$ITVIEC_DATABASE_URL" -c 'SELECT run_id, received_count, inserted_count, updated_count, unchanged_count, completed_at FROM itviec_crawl_runs ORDER BY completed_at DESC LIMIT 10;'
```

## Chạy với Airflow

Cài Airflow 2.4+ hoặc 3.x theo [hướng dẫn chính thức với constraints](https://airflow.apache.org/docs/apache-airflow/stable/installation/installing-from-pypi.html), sau đó cài phiên bản `apache-airflow-providers-postgres` tương thích với Airflow đang dùng và `pip install -r requirements.txt` trong môi trường scheduler/worker. Giữ toàn bộ repo ở cùng đường dẫn tương đối trên các worker: DAG nạp `itviec_crawler.py` và `itviec_store.py` từ thư mục cha của `dags/`. Chỉ chép riêng file DAG sẽ không đủ.

Trước khi bật DAG, chạy `sql/001_init.sql` trên database đích. Tạo Airflow Connection với **Conn Id** `jobs_postgres`, **Conn Type** `Postgres`, rồi điền host, port, database, user và password. DAG dùng Connection này; biến `ITVIEC_DATABASE_URL` chỉ dành cho CLI thủ công.

Các biến môi trường tùy chọn trên worker:

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `ITVIEC_LISTING_URL` | `https://itviec.com/it-jobs` | URL danh sách, có thể kèm query |
| `ITVIEC_MAX_JOBS` | `20` | Số tin crawl thành công tối đa |
| `ITVIEC_PAGES` | `5` | Số trang danh sách tối đa |
| `ITVIEC_DELAY` | `1.5` | Số giây nghỉ giữa request |
| `ITVIEC_TIMEOUT` | `20` | Timeout mỗi request, tính bằng giây |

DAG `itviec_daily` chạy lúc **02:00 mỗi ngày theo giờ Việt Nam**, không chạy bù lịch cũ và chỉ có một DAG run hoạt động tại một thời điểm. Task crawl và nạp DB trong cùng một process nên không đưa danh sách job qua XCom. Nếu nguồn lỗi, bị giới hạn truy cập hoặc DB lỗi, task thất bại để Airflow retry tối đa 3 lần với khoảng chờ tăng dần. Có thể chạy ngay bằng `airflow dags trigger itviec_daily` hoặc trong giao diện Airflow.
