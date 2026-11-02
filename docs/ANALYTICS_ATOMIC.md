# Analytics nguyên tử từ normalize_data — 02/11/2026

## 1 Phạm vi mới

Dựa trên `main` **e0dc1d44208f386e9b319c9c7e08a8a2644d6baf**. Luồng được nhóm xây lại:

```text
crawl → raw → check/chuẩn hóa của nhóm → normalize_data.normalized_jobs (5.1)
                                                  ↓ CHỈ ĐỌC
                                          bộ tách nguyên tử mới
                                                  ↓
                                   analytics: fact + dimension + bridge
                                                  ↓
                                   báo cáo / loader DWH của thành viên khác
```

Không dùng loader staging→analytics cũ. Không sửa crawler, role gate, normalizer, loader normalize/staging hoặc DDL/migration của chúng. Theo yêu cầu bổ sung của user, đã chạy crawler TopDev và loader normalize nguyên bản của main mới trên DB riêng; bước analytics chỉ đọc kết quả đó. **Bỏ benefit hoàn toàn**, không tạo bảng hoặc trích quyền lợi từ raw/description.

Tên schema thực trong source là **normalize_data**, không phải `normalize`. Nguồn duy nhất của loader mới là `normalize_data.normalized_jobs`; loader không đọc lại raw, không ghi ngược normalize_data/staging, không tự tạo các schema nguồn.

## 2 Các bảng nguyên tử

Một dòng trong `analytics.job_observations` = một **(job_id, crawl_run_id)**. `observation_key` là SHA256 xác định từ cặp khóa này; hai nguồn có tiền tố job_id khác nhau và các lần crawl khác nhau không bị gộp lịch sử. Giữ title/company_name/description/location_text/work_mode và lineage; không đưa mảng hoặc JSON vào fact.

Mỗi nhóm có một bảng danh mục và một bridge nối observation với đúng một giá trị. Dòng bridge ngoại ngữ còn giữ `level_text` của chứng chỉ/yêu cầu, không suy ra CEFR. `level` trong nhóm riêng nghĩa là **seniority**, không phải trình độ ngoại ngữ.

| Nhóm | Bảng danh mục (TABLE) | Bảng nối (TABLE) | Số dòng danh mục / nối đã đo |
|---|---|---|---:|
| `programming_language` | `analytics.dim_programming_language` | `analytics.bridge_job_programming_language` | 4 / 5 |
| `framework` | `analytics.dim_framework` | `analytics.bridge_job_framework` | 4 / 4 |
| `core_skill` | `analytics.dim_core_skill` | `analytics.bridge_job_core_skill` | 20 / 24 |
| `role` | `analytics.dim_role` | `analytics.bridge_job_role` | 12 / 14 |
| `city` | `analytics.dim_city` | `analytics.bridge_job_city` | 3 / 9 |
| `foreign_language` | `analytics.dim_foreign_language` | `analytics.bridge_job_foreign_language` | 2 / 6 |
| `level` | `analytics.dim_level` | `analytics.bridge_job_level` | 4 / 8 |
| `soft_skill` | `analytics.dim_soft_skill` | `analytics.bridge_job_soft_skill` | 0 / 0 |

- **programming_language:** Java, Python, C++, C#…
- **framework:** framework/library/runtime như React, Spring, Node.js; tên nhóm thuận tiện cho dashboard, không khẳng định Node.js là framework về bản chất.
- **core_skill:** công nghệ/kiến thức còn lại, giữ category như database, cloud, devops… Không gọi mọi nhãn `other` là kỹ năng đã được duyệt.
- **foreign_language:** English, Japanese, Korean… riêng hoàn toàn với ngôn ngữ lập trình. Giá trị nguồn `unknown` không trở thành một ngoại ngữ thật; fact giữ foreign_language_status='unknown'.
- **soft_skill:** giữ riêng khi nguồn có; mẫu hiện tại nguồn xuất [] nên hai bảng trống, không tự suy diễn từ description.

Tổng cộng **19 bảng vật lý**: fact, 8 dimension, 8 bridge, pipeline_state và observation_issues. Các view latest/tổng hợp phục vụ query không thay thế bảng vật lý.

## 3 Tách nhãn và giữ dữ liệu thiếu

- Alias xác định dùng catalog mới của analytics, ví dụ cpp/Cplus→C++, ReactJS→React. Java không gộp JavaScript; C không gộp C++.
- Chỉ tách chuỗi ghép nếu mọi phần có trong từ điển. `C/C++`, `Java/React` tách được; `CI/CD`, `UI/UX` và role chuẩn `Software Developer / Engineer` giữ là tên khái niệm thống nhất.
- Nhãn `InternalFoo/Bar` chưa rõ không được nhét vào bridge như một giá trị đã nguyên tử; giữ nguyên trong `analytics.review_queue` để rà soát. Nhãn đơn chưa rõ giữ với category nguồn và cờ review phù hợp, không fuzzy-match.
- Role/ngoại ngữ/soft skill bị đặt nhầm trong skills chỉ chuyển nhóm khi khớp alias xác định. Banking & Finance/E-commerce được ghi nhận là lĩnh vực nghiệp vụ, không đếm như core skill; đợt này không tự thêm bảng domain.
- Không có requirement_type trong đầu ra. Các con số là **tỷ lệ tin nhắc đến**, không phải mức bắt buộc/ưu tiên.
- Không điền lương/kinh nghiệm/học vấn/benefit: hợp đồng nguồn 5.1 không lưu các nhóm đó.

## 4 Kết quả thật đã chạy local

Database **it_job_market_v5** trên PostgreSQL Docker đang có; database `postgres` cũ được giữ nguyên.

- Crawl TopDev: **20** dòng raw trong một run.
- Gate/normalize của main mới: **9** dòng normalize_data, 9 loại theo role, 2 loại theo ngôn ngữ, không lỗi chuẩn hóa/ghi.
- Analytics: **9 quan sát / 9 job phân biệt**; không có dữ liệu ITviec thật trong đợt chạy này. Hai nguồn được kiểm thử bằng fixture độc lập.
- Nạp lần đầu: inserted=9; chạy lại: inserted=updated=deleted=0, unchanged=9.
- Review có **19 mục**, gồm 10 thông tin role đã chuyển nhóm, 4 thông tin nguồn chưa công bố ngoại ngữ, 2 lĩnh vực không phải skill, 3 nhãn category cần duyệt. Không diễn giải cả 19 mục là lỗi tải dữ liệu.
- Mẫu nhỏ để kiểm tra luồng, không đại diện tỷ lệ nhu cầu tuyển dụng toàn thị trường.

## 5 Chạy từ checkout mới

Cài requirements, mở PostgreSQL và cấu hình `DATABASE_URL` hoặc `PG*` trỏ đúng DB. Loader cũng hỗ trợ cùng `--db-*` và Compose fallback như CLI hiện có. Không lưu password vào Git.

```bash
# Phần upstream có sẵn — chỉ chạy khi muốn crawl/chuẩn hóa batch mới
python main_topdev.py --limit 20
python load_normalize_data.py

# Phần mới — mặc định CHỈ xem trước, không tạo bảng/ghi DB
python load_analytics.py
python load_analytics.py --dry-run
python load_analytics.py --check-db

# Tạo bảng + nạp toàn bộ normalize_data hiện có; --out phải là thư mục mới
python load_analytics.py --apply --out reports/analytics/run-001

# Chỉ đồng bộ một nguồn hoặc một batch
python load_analytics.py --apply --source topdev --out reports/analytics/run-002
python load_analytics.py --apply --crawl-run-id RUN_ID --out reports/analytics/run-003
```

DB local của đợt này cần đặt **PGDATABASE=it_job_market_v5** cùng thông tin host/port/user/password đã cấu hình. Nếu dùng mặc định Compose đang trỏ DB khác, chọn `--db-name it_job_market_v5` hoặc cấu hình PG* đầy đủ. Host từ máy Windows là 127.0.0.1, port publish 5432; trong Docker network, hostname PostgreSQL theo Compose. Không chạy lệnh nạp mẫu mà bỏ qua cấu hình DB đích.

Loader chạy trong transaction + advisory lock. Nó kiểm tra version/kiểu dữ liệu trước khi ghi, upsert dimension trước bridge, đối soát đọc lại output và hash nguồn. Cùng input/rule không đổi thì không ghi cả loaded_at; thay đổi chỉ ghi lại observation liên quan. Loader so hash trên toàn bộ phạm vi đã chọn, **incremental ở phần ghi**, chưa phải CDC chỉ đọc delta.

**Đồng bộ thu hồi:** nếu một dòng đã bị loại khỏi normalize_data, lần apply tiếp theo sẽ xóa observation/bridge analytics tương ứng trong phạm vi source/run được chọn. Không xóa phạm vi khác. Vì vậy normalize_data phải là tập đầu vào chuẩn có thẩm quyền; không chỉ là buffer tạm bị dọn tùy ý. Dimension không còn được dùng có thể vẫn giữ làm danh mục; báo cáo chỉ đếm bridge đang có.

Không dùng `--limit` khi sync vì sẽ nhầm các dòng ngoài limit là đã bị thu hồi. Không tự ghi đè model analytics cũ khác hợp đồng; lệnh dừng và giữ nó để đối chiếu.

## 6 SQL cho phân tích và người làm DWH

```sql
-- Ngôn ngữ lập trình xuất hiện trong bao nhiêu tin mới nhất
SELECT name,jobs_with_value,total_jobs,job_pct
FROM analytics.programming_language_demand_latest
ORDER BY jobs_with_value DESC,name;

-- Kỹ năng theo role: bao gồm programming, framework, core skill
SELECT role_name,skill_group,skill_name,jobs_with_skill,total_role_jobs,job_pct
FROM analytics.role_skill_demand_latest
ORDER BY role_name,job_pct DESC,skill_name;

-- Đọc chi tiết ngoại ngữ, không lẫn với programming language
SELECT f.job_id,f.crawl_run_id,d.name AS language,b.level_text
FROM analytics.bridge_job_foreign_language_latest b
JOIN analytics.dim_foreign_language d USING(foreign_language_key)
JOIN analytics.fact_job_latest f USING(observation_key);

-- Mục cần duyệt / thông tin thiếu hoặc chuyển nhóm
SELECT * FROM analytics.review_queue ORDER BY reason,job_id;
```

Các view `<group>_demand_latest` có jobs_with_value/total_jobs/job_pct cho đủ 8 nhóm. job_pct lưu **0–100**; không định dạng như tỷ lệ 0–1 thêm lần nữa. Mẫu số gồm mọi job latest trong phạm vi view, kể cả không có skill hoặc ngoại ngữ chưa rõ. Tổng % của nhiều giá trị có thể trên 100% vì một job có nhiều giá trị.

Role_skill dùng mẫu số là mọi job latest thuộc role, kể cả job không có skill; distinct job_id ngăn nhân chéo bridge. View tổng hợp không tự thay mẫu số theo slicer Power BI; nếu cần slice ngày/city/source, xây measure động trên fact/bridge. Fact/bridge lịch sử dùng để load DWH; latest chỉ dùng khi muốn trạng thái quan sát mới nhất, không chứng minh tin còn tuyển.

DWH nên dùng khóa nghiệp vụ (job_id,crawl_run_id), lookup dimension trước, upsert fact rồi bridge theo observation, lưu trạng thái load sau commit. Không lấy mảng JSON từ normalize_data để nhét vào một ô fact, không chỉ lấy bảng % làm nguồn DW. **Chưa tạo schema DW hoặc PBIX trong đợt này.**

## 7 Kiểm thử và khôi phục

```bash
python -m unittest discover -s tests -v
# Bật ITVIEC_TEST_DSN cho PostgreSQL integration, tài khoản có CREATEDB/CREATEROLE.
```

Đã chạy **78 tests, không skip**, trong đó 25 test mới kiểm tra alias/tách nhóm, dữ liệu thiếu, role/domain không lẫn skill, lỗi giữa transaction, load lần đầu/lặp/batch mới/cập nhật/thu hồi có phạm vi, % 2/3=66.67%, không có benefit/JSON output, rollback, chặn sửa tay và role chỉ SELECT normalize_data (không có USAGE raw).

Mỗi apply lưu before.json, after.json, metrics.json ở thư mục --out local. Không commit snapshot vì có dữ liệu thực. Khôi phục analytics bằng:

```bash
python load_analytics.py --rollback reports/analytics/run-001
```

Rollback kiểm tra toàn bộ trạng thái output và fingerprint nguồn trước khi thực hiện; thay đổi sau load sẽ chặn nó. Không ghi normalize/raw/staging. Gói bàn giao có ROLLBACK.sh còn phục hồi source đúng main e0dc1d4 trên bản copy đã kiểm thử; database cũ không bị sửa, DB mới và raw/normalize của lần crawl được giữ lại.

## 8 File được thêm/chỉnh

- `data_preprocessing/analytics/atomic.py`, `catalog.json`: tách, phân nhóm và ánh xạ xác định.
- `data_preprocessing/analytics/storage.py`: transaction, upsert, sync và rollback.
- `data_preprocessing/sql/analytics_schema.sql`: 19 bảng + các view phân tích.
- `data_preprocessing/cli/load_analytics.py`, `load_analytics.py`: CLI và wrapper.
- `tests/test_analytics_atomic.py`, `tests/test_analytics_postgres.py`: test mới.
- README, ARCHITECTURE, tài liệu này và rules/normalize-to-analytics.md: hướng dẫn hiện hành.

Không sửa upstream contracts/normalizer/gate/crawler hoặc loader normalize/staging. Không tự push GitHub.

## Trạng thái bàn giao SQL Server DW — 02/11/2026

Kho đích nhóm chọn là **SQL Server**. Phần được bàn giao ở nhánh `feature/Analytics` là **normalize_data → analytics trên PostgreSQL**, không phải bộ nạp hoàn chỉnh sang SQL Server. Kết nối server/instance/database, kiểu xác thực, DDL dim/fact/bridge đích và mapping vẫn chờ nhóm cung cấp. Chưa có loader, ODBC/SQL Server connection hoặc kiểm thử end-to-end analytics → DW trong bản này. Commit message do user chỉ định không thay thế trạng thái kỹ thuật này.
