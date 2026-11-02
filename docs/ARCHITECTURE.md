# Kiến trúc source code

Tài liệu này là điểm bắt đầu cho người tiếp nhận pipeline. Luồng đang chạy là:

```text
ITviec / TopDev
      │
      ▼
  crawlers ───────────────► PostgreSQL raw (giữ nguyên JSON nguồn)
                                  │
                                  ▼
                       role + language gate
                                  │
                                  ▼
                              normalizer
                                  │
                                  ▼
                     PostgreSQL normalize_data
                                  │
                                  ▼
                        staging / DWH sau này
```

## Ranh giới module

| Package | Trách nhiệm | Không nên chứa |
|---|---|---|
| `data_preprocessing.crawlers` | Gọi nguồn, tạo crawl run, ghi JSON raw | Rule làm sạch hoặc chuẩn hóa |
| `data_preprocessing.normalization.role_gate` | Quyết định tin có đúng role và ngôn ngữ được hỗ trợ | SQL hoặc ghi database |
| `data_preprocessing.normalization.contracts` | Field, version và validation của output | Rule suy luận dữ liệu |
| `data_preprocessing.normalization.normalizer` | Ánh xạ hai nguồn về hợp đồng chung và tạo metadata bằng chứng | HTTP, transaction hoặc CLI |
| `data_preprocessing.storage` | Schema/hợp đồng lưu trữ dùng chung | Rule nghiệp vụ theo nguồn |
| `data_preprocessing.cli` | Parse tham số, kết nối các module, log và exit code | Rule chuẩn hóa chi tiết |
| `data_preprocessing.sql` | DDL hiện tại và migration theo version | Logic crawler |

Các file `main_*.py` và `load_*.py` ở root chỉ là wrapper để lệnh cũ tiếp tục
chạy. Code mới phải import package thật, không import các wrapper này. Các package
`intermediate`, `itviec_crawl_data`, `topdev_crawl_data` và module
`raw_database.py` cũng chỉ còn là lớp tương thích tạm thời với import cũ.

## Hợp đồng dữ liệu

- `raw` là dữ liệu bất biến theo lần quan sát. Không làm sạch trực tiếp JSON tại đây.
- Gate chỉ trả quyết định giữ/loại; bản ghi bị loại vẫn còn trong `raw`.
- Normalizer không tự truy cập database. Input là raw record cùng gate decision;
  output là dictionary đúng thứ tự field của contract.
- `normalize_data.normalized_jobs` là output canonical hiện tại, version `5.1`.
- `staging.normalized_jobs` là hợp đồng 30 cột cũ, chỉ giữ để tương thích. Luồng
  chính không dual-write vào bảng này.
- DDL tạo mới nằm trong `data_preprocessing/sql`; thay đổi phá vỡ hợp đồng phải có
  migration mới và tăng normalization version.

## Điểm mở rộng

Thêm nguồn crawl mới theo thứ tự:

1. Tạo `data_preprocessing/crawlers/<source>.py`, chỉ ghi đúng raw contract.
2. Khai báo trường role/ngôn ngữ của nguồn trong `normalization/role_gate.py`.
3. Thêm nhánh ánh xạ nguồn trong `normalization/normalizer.py`; chỉ sửa
   `normalization/contracts.py` khi cấu trúc output thật sự thay đổi.
4. Thêm CLI mỏng trong `data_preprocessing/cli`.
5. Bổ sung fixture/unit test và PostgreSQL integration test cho nguồn mới.

Thay đổi rule chuẩn hóa:

1. Viết test mô tả dữ liệu lỗi hoặc case biên trước.
2. Sửa rule trong `normalization`, không sửa crawler hay loader để bù dữ liệu.
3. Nếu output persisted thay đổi, tăng `NORMALIZE_DATA_VERSION` và tạo migration.
4. Chạy toàn bộ test và `--dry-run` trước khi nạp lại dữ liệu.

## Kiểm tra trước khi bàn giao

```bash
python -m unittest discover -s tests -v
python main_itviec.py --check-db
python main_topdev.py --check-db
python load_normalize_data.py --dry-run
```

Không commit cookie, browser session, `.env` hoặc password database. Các file đó
đã được loại bởi `.gitignore`.

