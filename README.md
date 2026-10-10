# IT Job Market DWH

Project thu thập tin tuyển dụng ITviec, lưu dữ liệu thô vào PostgreSQL, sau đó làm sạch và ánh xạ về cấu trúc chung gồm 30 trường.

**Luồng hiện tại:** crawl → JSON thô trong PostgreSQL → chuẩn hóa → các bảng staging → kiểm định → view đầu vào DWH.

Phạm vi hiện tại kết thúc ở dữ liệu staging đã kiểm định, sẵn sàng làm nguồn nạp DWH. Entry point duy nhất là `main_itviec.py`; sau khi thiết lập lần đầu, chạy `python main_itviec.py` để thực hiện toàn bộ luồng trên.

## Bước 1. Chuẩn bị công cụ

Cài các công cụ sau trên máy:

| Công cụ | Mục đích |
| --- | --- |
| Git | Clone source code |
| Python 3.10 trở lên, có pip và venv | Chạy crawler và chuẩn hóa dữ liệu |
| Docker và Docker Compose v2 | Chạy PostgreSQL và pgAdmin |
| Chrome hoặc trình duyệt đang đăng nhập ITviec | Lấy cookie để truy cập thông tin lương |

Trên Windows, cài và mở Docker Desktop trước khi chạy lệnh Docker. Trên Linux, cần Docker Engine đang chạy và tài khoản có quyền dùng Docker. Có thể làm theo [hướng dẫn cài Docker Compose chính thức](https://docs.docker.com/compose/install/).

Kiểm tra trong terminal:

```bash
git --version
docker --version
docker compose version
```

Kiểm tra Python bằng `python3 --version` trên Linux hoặc `py -3 --version` trên Windows. Nếu Windows chỉ có lệnh `python`, dùng `python` thay cho `py -3` ở bước tiếp theo.

## Bước 2. Clone project

```bash
git clone https://github.com/tranduckhang2612/it-job-market-dwh.git
cd it-job-market-dwh
```

Mở thư mục này trong VS Code hoặc editor của bạn. **Các lệnh bên dưới đều chạy từ thư mục gốc `it-job-market-dwh`**, nơi có `main_itviec.py` và `requirements.txt`.

Clone chỉ lấy source code. Mỗi người cần tự tạo virtualenv, cấu hình Docker, khởi tạo database và chuẩn bị cookie trên máy mình.

## Bước 3. Tạo môi trường Python và cài dependency

Virtualenv giúp các thư viện của project được cài riêng trên từng máy. Chọn lệnh theo hệ điều hành:

**Linux/macOS — Bash hoặc Zsh:**

```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows — PowerShell:**

```powershell
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
```

Nếu PowerShell chặn `Activate.ps1`, có thể mở **Command Prompt (CMD)** trong thư mục project và chạy `venv\Scripts\activate.bat`. Hoặc dùng trực tiếp `venv\Scripts\python.exe` thay cho `python` trong các lệnh tiếp theo. Xem thêm [hướng dẫn virtualenv của Python](https://docs.python.org/3/library/venv.html).

Sau khi kích hoạt môi trường, cài thư viện:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Tên file là **`requirements.txt`**. Thư mục `venv/` được tạo cục bộ và đã được bỏ qua bởi Git.

Chế độ mặc định dùng requests, nên **chưa cần chạy `python -m playwright install chromium`**. Chỉ cài Chromium nếu chọn chế độ browser ở phần tùy chọn bên dưới.

## Bước 4. Cấu hình PostgreSQL và pgAdmin

Hướng dẫn này dùng database mới tên `itviec`, tài khoản `itviec_user`. Trong VS Code, tạo file **`docker/.env`**, cùng thư mục với `docker-compose.yml`, rồi lưu nội dung sau:

```dotenv
POSTGRES_USER=itviec_user
POSTGRES_PASSWORD='THAY_BANG_MAT_KHAU_DATABASE_CUA_BAN'
POSTGRES_DB=itviec
POSTGRES_PORT=5432

PGADMIN_DEFAULT_EMAIL=admin@example.com
PGADMIN_DEFAULT_PASSWORD='THAY_BANG_MAT_KHAU_PGADMIN_CUA_BAN'
PGADMIN_PORT=5050
```

Thay hai giá trị mật khẩu bằng mật khẩu bạn tự chọn. Mật khẩu PostgreSQL dùng để kết nối database; mật khẩu pgAdmin dùng để đăng nhập giao diện quản lý. File phải có đúng tên `.env`, không phải `.env.txt`.

Docker Compose đọc file này để cấu hình các service. Khi chạy, `main_itviec.py` đọc cấu hình đã được Compose xử lý để lấy thông tin kết nối PostgreSQL; không cần nhập lại các giá trị trên trong code. Xem [quy tắc đọc `.env` của Docker Compose](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

Nếu đã có database/volume từ lần chạy trước, dùng lại thông tin đã khởi tạo. Đổi `POSTGRES_USER`, `POSTGRES_DB` hoặc `POSTGRES_PASSWORD` trong `.env` không tự thay tài khoản hay dữ liệu của volume cũ. Đây là [cách khởi tạo của image PostgreSQL](https://github.com/docker-library/docs/blob/master/postgres/README.md).

`.env` và cookie là cấu hình riêng của mỗi máy, đã được `.gitignore` loại khỏi Git.

## Bước 5. Khởi động PostgreSQL và pgAdmin

```bash
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
```

Lần đầu Docker sẽ tải image, nên có thể mất vài phút. Chờ service `postgres` có trạng thái **healthy** và `pgadmin` đang chạy rồi tiếp tục.

Với cấu hình ở bước 4:

| Nơi sử dụng | Địa chỉ |
| --- | --- |
| Python trên máy bạn kết nối PostgreSQL | `127.0.0.1:5432` |
| Trình duyệt mở giao diện pgAdmin | `http://localhost:5050` |
| pgAdmin trong Docker kết nối PostgreSQL | Host `postgres`, port `5432` |

Database được lưu trong Docker volume trên máy bạn. `main_itviec.py` cần PostgreSQL đang chạy; chương trình không tự khởi động Docker.

## Bước 6. Tạo schema và bảng trong database

File SQL tạo schema `raw`, `staging`, các bảng và view cần thiết. Bước này thực hiện lần đầu, hoặc khi cần áp dụng bản cập nhật schema.

Hai lệnh sau dùng được trên Linux và Windows PowerShell, không cần cài `psql` trên máy:

```bash
docker compose -f docker/docker-compose.yml cp data_preprocessing/itviec_db/itviec_schema.sql postgres:/tmp/itviec_schema.sql
docker compose -f docker/docker-compose.yml exec -T postgres psql -v ON_ERROR_STOP=1 -U itviec_user -d itviec -f /tmp/itviec_schema.sql
```

Lệnh đầu chép SQL vào container. Lệnh thứ hai thực thi SQL trong database `itviec`; nếu thành công, cuối output có `COMMIT`.

Nếu bạn chọn tên user/database khác ở bước 4, thay `itviec_user` và `itviec` sau `-U`, `-d` cho khớp. Nếu giữ cấu hình mặc định trong Compose và không tạo `.env`, user mặc định là `admin`, database là `postgres`.

Script có thể áp dụng lại để bổ sung cấu trúc hiện tại mà không nhân bản dữ liệu. Nó không tự crawl hoặc xử lý lại dữ liệu raw cũ. Chương trình main cũng không tự chạy file SQL này.

## Bước 7. Kiểm tra kết nối của project

Trong terminal đã kích hoạt virtualenv:

```bash
python main_itviec.py --check-db
```

Lệnh này kiểm tra kết nối PostgreSQL, bảng/cột, view và quyền truy cập. Chưa cần cookie ở bước này và chưa crawl website.

Kết quả mong đợi:

```text
INFO: Dùng cấu hình PostgreSQL trong docker/docker-compose.yml.
INFO: PostgreSQL đã kết nối; schema raw/staging, view dành cho DWH và quyền truy cập hợp lệ.
```

Nếu lỗi, xem phần xử lý lỗi ở cuối tài liệu trước khi crawl.

## Bước 8. Lấy và lưu cookie ITviec

Cookie cho phép crawler sử dụng phiên đăng nhập của bạn khi ITviec yêu cầu đăng nhập để xem lương. Mỗi người dùng cookie từ tài khoản của mình.

1. Mở Chrome thường dùng, vào [ITviec](https://itviec.com/it-jobs) và đăng nhập.
2. Mở một tin chi tiết, kiểm tra rằng bạn xem được lương nếu tin đó có công bố lương.
3. Nhấn `F12` để mở DevTools, chọn tab **Network**, rồi tải lại trang.
4. Chọn request tới `itviec.com` có loại **document**, tương ứng trang danh sách hoặc tin vừa mở.
5. Mở **Headers → Request Headers → Cookie** và sao chép toàn bộ **giá trị** của header Cookie.
6. Trong project, tạo file **`data_preprocessing/itviec_crawl_data/cookie_itviec.txt`** bằng editor.
7. Dán giá trị vừa lấy vào file, lưu dạng văn bản **UTF-8**, trên một dòng.

Định dạng minh họa; không dùng nguyên giá trị giả này:

```text
cookie_name=gia_tri_cookie; another_cookie=gia_tri_khac
```

File chứa giá trị header Cookie, không phải JSON, bảng cookie export hoặc toàn bộ Request Headers. Không chia một cookie thành nhiều dòng và không sửa các ký tự mã hóa trong giá trị.

Cookie đã được `.gitignore` loại khỏi Git. Giữ file này riêng trên máy; cookie cho phép truy cập phiên đăng nhập. Chế độ requests dùng lại file đã lưu, nhưng không tự đăng nhập lại khi phiên hết hạn. Khi cần, đăng nhập lại trên Chrome và cập nhật nội dung cùng file.

## Bước 9. Chạy toàn bộ pipeline

Sau khi hoàn tất các bước trên:

```bash
python main_itviec.py
```

Mặc định chương trình lấy **20 tin**, dùng cookie đã lưu ở bước 8 và thực hiện lần lượt:

1. Kiểm tra kết nối và cấu trúc database.
2. Crawl danh sách/tin chi tiết, lưu JSON thô vào `raw.job_postings`.
3. Đọc đúng batch raw vừa lưu, làm sạch và ánh xạ về 30 trường chung.
4. Ghi bảng chính và các bảng con trong `staging`.
5. Đọc lại kết quả để kiểm định; batch hợp lệ xuất hiện trong `staging.dwh_ready_jobs`.

Mỗi lần chạy có một `crawl_run_id` riêng. Log hiển thị hai bước chính `Bước 1/2` và `Bước 2/2`, kèm mã batch để đối chiếu. Mặc định dữ liệu được lưu trong PostgreSQL, không xuất CSV/JSON ra thư mục.

## Bước 10. Xem dữ liệu và xác nhận kết quả

Mở [pgAdmin cục bộ](http://localhost:5050), đăng nhập bằng email và mật khẩu `PGADMIN_DEFAULT_*` đã đặt trong `docker/.env`.

Đăng ký kết nối database:

1. Trong cây bên trái, nhấp phải **Servers → Register → Server**.
2. Tab **General**: đặt tên, ví dụ `ITviec local`.
3. Tab **Connection**: nhập các giá trị dưới đây rồi **Save**.

| Trường | Giá trị theo bước 4 |
| --- | --- |
| Host name/address | `postgres` |
| Port | `5432` |
| Maintenance database | `itviec` |
| Username | `itviec_user` |
| Password | Giá trị `POSTGRES_PASSWORD` bạn đã đặt |

Trong pgAdmin chạy bằng Docker, hostname DB là **`postgres`**. Chọn database `itviec`, mở **Query Tool** và chạy:

```sql
-- Trạng thái các lần crawl và kiểm định.
SELECT crawl_run_id, requested_count, collected_count, status,
       metadata #>> '{staging,status}' AS staging_status,
       metadata #>> '{staging,ready_for_dwh}' AS ready_for_dwh,
       metadata #>> '{staging,source_complete}' AS source_complete
FROM raw.crawl_runs
ORDER BY started_at DESC
LIMIT 10;

-- Xem JSON thô của các tin gần nhất.
SELECT job_id, crawl_run_id, raw_payload
FROM raw.job_postings
ORDER BY observed_at DESC
LIMIT 5;

-- Dữ liệu đã chuẩn hóa và qua kiểm định, dùng làm nguồn nạp DWH.
SELECT job_id, crawl_run_id, title, company_name, cities, skills,
       salary_text, salary_status
FROM staging.dwh_ready_jobs
ORDER BY observed_at DESC
LIMIT 20;

-- Quan sát hợp lệ gần nhất của mỗi tin.
SELECT * FROM staging.dwh_latest_jobs LIMIT 20;
```

Với lần chạy đầy đủ 20 tin, mong đợi `collected_count=20`, `status='succeeded'`, `staging_status='succeeded'` và `ready_for_dwh=true`.

`ready_for_dwh` cho biết các bản ghi đã thu thập qua kiểm định. `source_complete` cho biết crawler đạt số tin yêu cầu hay chưa. Một batch crawl thiếu tin vẫn có thể chứa dữ liệu hợp lệ, nên cần xem cả hai giá trị.

## Những lần chạy tiếp theo

Mở terminal tại thư mục project, kích hoạt lại virtualenv theo bước 3, bảo đảm Docker đang mở rồi chạy:

```bash
docker compose -f docker/docker-compose.yml up -d
python main_itviec.py
```

Không cần cài lại dependency hoặc tạo lại schema mỗi lần chạy. Cập nhật cookie khi phiên đăng nhập cần được làm mới.

Để dừng các container và giữ dữ liệu:

```bash
docker compose -f docker/docker-compose.yml stop
```

## Các lệnh tùy chọn

| Nhu cầu | Lệnh |
| --- | --- |
| Đổi số tin cần lấy | `python main_itviec.py --limit 50` |
| Dùng file cookie khác | `python main_itviec.py --cookie-file DUONG_DAN` |
| Chỉ kiểm tra DB | `python main_itviec.py --check-db` |
| Xử lý lại raw của một batch | `python main_itviec.py --process-run-id CRAWL_RUN_ID` |
| Lưu thêm JSON để đối chiếu | `python main_itviec.py --save-json --output-dir itviec_data/raw` |
| Lưu thêm HTML | `python main_itviec.py --save-html --output-dir itviec_data/raw` |
| Xem tất cả tùy chọn | `python main_itviec.py --help` |

Với `--process-run-id`, thay `CRAWL_RUN_ID` bằng mã lấy từ `raw.crawl_runs`. Lệnh đọc raw đã lưu, chuẩn hóa và kiểm định lại, không crawl lại. Cùng `(job_id, crawl_run_id)` được cập nhật thay vì tạo thêm quan sát; các danh sách con được thay trong transaction của từng tin. Thời điểm quan sát gốc `observed_at` được giữ nguyên.

Nếu dùng PostgreSQL bên ngoài Compose, có thể chỉ định:

```bash
python main_itviec.py --db-host 127.0.0.1 --db-port 5432 --db-name TEN_DATABASE --db-user TEN_USER --db-password-prompt
```

Database đích cũng cần áp dụng schema ở bước 6. `--db-*`, `DATABASE_URL` hoặc các biến kết nối `PG*` được ưu tiên so với Compose. `--no-compose-db` tắt việc lấy cấu hình từ Compose. Các bước crawl và chuẩn hóa dùng chung thông tin kết nối trong một lần chạy.

**Chế độ browser là tùy chọn:** cài browser bằng `python -m playwright install chromium`, sau đó dùng `python main_itviec.py --browser --login`. Lệnh cài Chromium dùng được trên Linux và Windows; Linux có thể cần thêm thư viện hệ thống theo [hướng dẫn Playwright](https://playwright.dev/python/docs/browsers). Chế độ này lưu phiên cục bộ trong `.itviec_auth.json`; chỉ `--browser` sẽ dùng lại phiên đó. Không kết hợp `--login` với `--cookie-file`. Nếu Google từ chối đăng nhập trong browser tự động hoặc ITviec trả 403, dùng lại luồng Chrome thường và requests ở bước 8–9 khi luồng đó truy cập được.

## Cấu trúc source code và dữ liệu

| File/thư mục | Vai trò |
| --- | --- |
| `main_itviec.py` | Điều phối toàn bộ pipeline và cấu hình kết nối |
| `data_preprocessing/itviec_crawl_data/crawl_itviec.py` | Tải danh sách, chi tiết tin và ghi JSON gốc |
| `data_preprocessing/itviec_normalize_data/normalize_itviec.py` | Làm sạch, ánh xạ và kiểm định dữ liệu |
| `data_preprocessing/itviec_db/itviec_schema.sql` | Tạo/cập nhật schema, bảng và view PostgreSQL |
| `docker/docker-compose.yml` | Chạy PostgreSQL 17 và pgAdmin |
| `requirements.txt` | Các dependency Python |
| `tests/` | Kiểm thử tự động, chạy riêng khi cần kiểm tra code |

| Bảng/view | Nội dung |
| --- | --- |
| `raw.crawl_runs` | Metadata, tiến độ và lỗi của từng lần crawl |
| `raw.job_postings` | JSON thô, một bản ghi cho mỗi `(job_id, crawl_run_id)` |
| `staging.job_observations` | 22 trường đơn đã chuẩn hóa và các cột kỹ thuật |
| Các bảng con `staging.job_*` | Tám trường danh sách; chuyên ngành học có bảng riêng |
| `staging.jobs` | Ghép đủ 30 trường cho mỗi tin/lần crawl, phục vụ đối chiếu |
| `staging.latest_jobs` | Quan sát đã xử lý gần nhất của mỗi tin |
| `staging.dwh_ready_jobs` | Lịch sử quan sát thuộc batch đạt kiểm định |
| `staging.dwh_latest_jobs` | Quan sát đạt kiểm định gần nhất của mỗi tin |

Các trường chung bao gồm định danh/thời gian, công ty/vị trí, mô tả, vai trò/cấp bậc, địa điểm/hình thức làm việc, kỹ năng, kinh nghiệm, học vấn, ngoại ngữ và lương. Trường đơn chưa biết là `NULL`; danh sách chưa trích xuất được là `[]`. Dữ liệu raw được giữ làm nguồn đối chiếu.

Bộ chuẩn hóa phiên bản `1.1` kiểm tra trường bắt buộc, URL/ID, datetime có múi giờ, kiểu dữ liệu, enum, số hữu hạn, cận min/max, trạng thái lương và phần tử trùng. Sau khi ghi, pipeline đọc lại đủ 30 trường để so sánh với kết quả ánh xạ và thông tin nguồn. Khi xử lý lại, batch tạm ngừng được công bố qua view DWH; nếu có lỗi, cả batch chưa được công bố qua view này. Lỗi nằm trong `raw.crawl_runs.metadata -> 'staging'`.

Kiểm định cấu trúc không chứng minh mọi cách diễn đạt tự nhiên đã được trích xuất đầy đủ. Thông tin nguồn không rõ vẫn giữ ở văn bản gốc; các trường số/nhãn tương ứng có thể để trống. Khi so sánh lương, cần cùng `salary_currency`, `salary_period`, `salary_basis` và `salary_status='disclosed'`. Với lịch sử DWH, dùng khóa `(job_id, crawl_run_id)`; khi đếm tin trong kỳ, dùng `COUNT(DISTINCT job_id)`.

## Kiểm thử — tùy chọn khi phát triển

Thư mục `tests/` kiểm tra ánh xạ lương/kinh nghiệm/kỹ năng, cấu trúc dữ liệu, entry point và lưu PostgreSQL. Các test không tự chạy khi crawl.

```bash
python -m unittest discover -s tests -v
```

Mặc định kiểm thử PostgreSQL được bỏ qua. Muốn chạy phần này, đặt biến môi trường `ITVIEC_TEST_DSN` trỏ đến tài khoản test có quyền tạo database. Harness tạo database riêng mang UUID và tự xóa sau kiểm tra. Không dùng DSN/mật khẩu trong source code hoặc commit vào Git.

## Xử lý lỗi thường gặp

| Hiện tượng | Cách kiểm tra/xử lý |
| --- | --- |
| `python` không tìm thấy hoặc `ModuleNotFoundError` | Kích hoạt đúng virtualenv ở bước 3, rồi cài lại bằng `python -m pip install -r requirements.txt`. Trên Linux, dùng `python3` để tạo virtualenv. |
| Linux báo thiếu `ensurepip`/`venv` | Trên Ubuntu/Debian, cài gói venv phù hợp bản Python, thường là `sudo apt install python3-venv`, rồi tạo lại môi trường. |
| PowerShell chặn `Activate.ps1` | Dùng CMD với `venv\Scripts\activate.bat`, hoặc gọi trực tiếp `.\venv\Scripts\python.exe`. |
| Không kết nối được Docker daemon | Mở Docker Desktop trên Windows, hoặc kiểm tra Docker service/quyền truy cập trên Linux. |
| Port `5432`/`5050` đã được sử dụng | Đổi `POSTGRES_PORT`/`PGADMIN_PORT` trong `docker/.env`, rồi chạy lại `up -d`. Main tự đọc port PostgreSQL được publish; pgAdmin vẫn kết nối DB nội bộ bằng `postgres:5432`. |
| DB chưa sẵn sàng | Chạy `docker compose -f docker/docker-compose.yml ps`; nếu cần xem lỗi bằng `docker compose -f docker/docker-compose.yml logs postgres`. |
| Thiếu bảng/cột/view hoặc lỗi schema | Thực hiện bước 6 trên đúng database, rồi chạy lại `--check-db`. |
| Sai mật khẩu hoặc project kết nối nhầm DB | Kiểm tra cấu hình ban đầu của volume và các biến `DATABASE_URL`/`PG*` đang có trong terminal; chúng có thể khiến main bỏ qua Compose. |
| pgAdmin không kết nối được PostgreSQL | Dùng hostname `postgres`, port `5432`, user/database ở bước 4 và mật khẩu PostgreSQL. |
| Không tìm thấy file cookie | Tạo đúng file ở bước 8; kiểm tra tên file không bị thêm `.txt` lần nữa. |
| Cookie không hợp lệ | Lưu một dòng giá trị header Cookie dạng UTF-8; không dùng JSON hoặc toàn bộ headers. |
| Lương vẫn hiện `Sign in to view salary` | Kiểm tra tin đó trong Chrome đã đăng nhập, rồi cập nhật cookie từ đúng request ITviec. Không phải mọi tin đều công bố số lương. |
| HTTP 403 | Kiểm tra quyền truy cập/xác minh trên Chrome thường. 403 có thể do website chặn hoặc yêu cầu xác minh, không đủ để kết luận cookie hết hạn. |
| Raw có dữ liệu nhưng view DWH chưa có batch | Xem lỗi và trạng thái trong truy vấn bên dưới; sau khi sửa nguyên nhân, xử lý lại bằng `--process-run-id`. |

```sql
SELECT crawl_run_id, status, collected_count, errors,
       metadata -> 'staging' AS processing
FROM raw.crawl_runs
ORDER BY started_at DESC
LIMIT 10;
```

Mã thoát của chương trình: `0` hoàn thành, `1` thiếu cookie mặc định/không có bản ghi hợp lệ, `2` xử lý một phần, `3` browser cần đăng nhập, `4` lỗi DB/schema, `130` người dùng dừng.
