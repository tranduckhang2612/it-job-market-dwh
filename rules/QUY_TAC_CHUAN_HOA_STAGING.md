> Lịch sử quy ước 1.1. Từ 30/10/2026, cấu trúc lồng 1.2 bỏ requirement_type; xem [hướng dẫn hiện hành](../docs/KIEN_TRUC_DW_VA_ANALYTICS.md). Các ví dụ nhãn yêu cầu bên dưới không còn là hợp đồng đầu ra hiện tại.

# Quy tắc chuẩn hóa dữ liệu tuyển dụng trong staging

## 1. Mục đích và phạm vi

Tài liệu mô tả quy ước biểu diễn dữ liệu chung cho **ITviec** và **TopDev**, phục vụ bước nạp dữ liệu vào kho dữ liệu tuyển dụng.

- **Schema chung:** phiên bản `1.1`, gồm 30 trường đã có trong dự án.
- **Phiên bản quy tắc làm sạch bổ sung:** `2026.10.29.1`.
- **Luồng xử lý:** `raw → chuẩn hóa theo nguồn → làm sạch dùng chung → kiểm định → staging`.
- Giữ nguyên dữ liệu tin gốc trong `raw.job_postings` để truy vết và xử lý lại.
- Các ví dụ dưới đây minh họa quy tắc, không phải một bản ghi thực hoàn chỉnh.

## 2. Quy tắc chuẩn hóa chung

| Nhóm dữ liệu | Quy tắc | Ví dụ trước → sau |
|---|---|---|
| Văn bản | Đưa về Unicode NFC; giải mã HTML entity; bỏ ký tự vô hình và ký tự điều khiển; thu gọn khoảng trắng | `"  Backend   Developer "` → `"Backend Developer"` |
| Văn bản nhiều dòng | Giữ ranh giới dòng của mô tả, địa chỉ và bằng chứng kinh nghiệm; thu gọn dòng trắng dư | Nhiều địa chỉ được giữ thành các dòng riêng |
| Giá trị thiếu | Trường đơn tùy chọn dùng `NULL`; danh sách chưa có dữ liệu dùng `[]` | `"N/A"`, `"null"`, `"-"`, chuỗi rỗng ở trường tùy chọn → `NULL` |
| Trường bắt buộc | Chặn bản ghi có trường bắt buộc rỗng; không tự điền nội dung giả | Tiêu đề rỗng → lỗi kiểm định |
| Địa danh | Thống nhất alias đã biết; bỏ tiền tố `TP`, `Thành phố`, `Tỉnh`; giữ tên chưa có trong từ điển | `TP.HCM` → `Hồ Chí Minh`; `Tỉnh Bình Thuận` → `Bình Thuận` |
| Địa chỉ | Giữ toàn bộ địa chỉ có cấu trúc của TopDev thay vì chỉ lấy địa chỉ đầu tiên | `["Địa chỉ A", "Địa chỉ B"]` → hai dòng trong `location_text` |
| Tên kỹ năng | Các alias tương đương dùng chung một tên | `NodeJS` → `Node.js`; `ReactJS` → `React`; `Postgres` → `PostgreSQL` |
| Nhóm kỹ năng | Thống nhất các tên nhóm và phân loại đã có trong từ điển | `NoSQL` → `databases`; `Docker` → `cloud_devops` |
| Danh sách trùng | Gộp mục trùng sau khi làm sạch; giữ thứ tự xuất hiện đầu tiên | `ReactJS`, `React.js` → một mục `React` |
| Mức yêu cầu | Khi gộp cùng một kỹ năng, ưu tiên nhãn rõ hơn theo thứ tự `required > preferred > unspecified` | Một mục có nhãn `preferred`, mục còn lại có nhãn `required` → giữ `required` |
| Chế độ làm việc | Dùng `onsite`, `hybrid`, `remote`; chưa xác định thì dùng `NULL` | `Trực tiếp` → `onsite`; `Từ xa` → `remote` |
| Kinh nghiệm | Đưa thời lượng về số năm; giữ câu nguồn trong `experience_text` | `6 tháng kinh nghiệm` → `0.5` năm |
| Khoảng kinh nghiệm | Giữ đúng cận dưới, cận trên và khoảng mở | `2–4 năm` → min `2`, max `4`; `4+ years` → min `4`, max `NULL` |
| Lương | Lưu mức tiền dạng số, tách tiền tệ, kỳ trả lương và trạng thái công bố | `12–25 triệu đồng/tháng` → `12000000–25000000`, `VND`, `month` |
| Thời gian | Dùng thời điểm có múi giờ; giữ nguyên thời điểm quan sát của nguồn | `observed_at` lưu bằng `TIMESTAMPTZ`, không thay bằng ngày làm sạch |
| Định danh | Phân biệt mã tin theo nguồn; giữ lịch sử giữa các lần crawl | `topdev:2138283`; khóa duy nhất `(job_id, crawl_run_id)` |

**Không gộp các công nghệ khác nghĩa:** Java khác JavaScript; C khác C++; `.NET` khác `.NET Core`. Các ký hiệu như `C++`, `C#`, `.NET` và `List<String>` được giữ lại trong văn bản.

## 3. Các giá trị phân loại chung

### 3.1. Chế độ làm việc

| Giá trị | Ý nghĩa |
|---|---|
| `onsite` | Làm việc trực tiếp |
| `hybrid` | Kết hợp trực tiếp và từ xa |
| `remote` | Làm việc từ xa |
| `NULL` | Chưa xác định được |

### 3.2. Mức yêu cầu

Áp dụng cho kỹ năng, ngoại ngữ và yêu cầu học vấn.

| Giá trị | Ý nghĩa |
|---|---|
| `required` | Bắt buộc theo nhãn hoặc bằng chứng từ nguồn |
| `preferred` | Ưu tiên |
| `unspecified` | Nguồn chưa xác định rõ mức yêu cầu |

### 3.3. Trạng thái lương

| Giá trị | Ý nghĩa |
|---|---|
| `disclosed` | Có ít nhất một cận lương dạng số |
| `negotiable` | Nguồn ghi thỏa thuận hoặc thương lượng |
| `not_disclosed` | Nguồn chưa công bố mức tiền |
| `unknown` | Chưa xác định hoặc có thông tin mâu thuẫn |

### 3.4. Các thuộc tính lương khác

- `salary_currency`: mã tiền tệ viết hoa gồm ba chữ cái, ví dụ `VND`, `USD`, `EUR`; thiếu bằng chứng thì dùng `NULL`.
- `salary_period`: `month`, `year`, `week`, `day` hoặc `hour` khi nguồn có bằng chứng về kỳ trả lương.
- `salary_basis`: `gross`, `net` hoặc `NULL`.
- Không tự gán tiền tệ hoặc kỳ trả lương khi nguồn chưa thể hiện.
- Không tự quy đổi ngoại tệ, lương năm thành lương tháng hoặc gross thành net.

## 4. Quy tắc xử lý lương và kinh nghiệm

### 4.1. Lương

1. Đọc các trường lương có cấu trúc từ nguồn; phân tích văn bản lương và các cận số hợp lệ.
2. Với TopDev, nếu chưa có mức tiền hợp lệ, xét câu có nhãn lương hoặc thu nhập trong `benefits_original`, `description`, `benefits`.
3. Loại các câu về phụ cấp, thưởng, ngân sách học tập hoặc bảo hiểm khỏi nguồn trích xuất mức lương. Lương tháng 13 không được dùng làm mức tiền.
4. Nếu văn bản cho biết mức thu nhập rõ ràng, giữ cả câu nguồn; không diễn giải thu nhập tổng thành lương cơ bản cố định.
5. Nếu nhiều mức tiền mâu thuẫn, giữ trạng thái `unknown` và chưa gán cận số.
6. Nếu nguồn chỉ ghi thỏa thuận, giữ `salary_status = negotiable`; `salary_min` và `salary_max` vẫn là `NULL`.

**Điều kiện kiểm định:** cận lương phải dương, hữu hạn; cận dưới không vượt cận trên. Không dùng `0`, số âm, boolean, `NaN` hoặc `Infinity` thay cho mức lương thiếu. Không tự đảo một khoảng lương sai để biến thành khoảng hợp lệ.

### 4.2. Kinh nghiệm

- Ưu tiên phần yêu cầu ứng viên; với đầu vào TopDev cũ chưa có phần yêu cầu riêng, bộ xử lý mới xét trường mô tả.
- Không suy diễn kinh nghiệm từ chức danh, tuổi ứng viên, lịch sử công ty hoặc phần quyền lợi.
- Không nâng số năm ở câu ưu tiên thành yêu cầu bắt buộc.
- Đổi tháng sang năm; giữ khoảng mở khi chỉ có cận trên hoặc cận dưới.
- Nếu có nhiều yêu cầu bắt buộc đồng thời, chọn cận dưới mạnh nhất, không cộng dồn các khoảng kinh nghiệm chồng lặp.
- Dòng thiếu đơn vị như `Có 1-2 kinh nghiệm tại vị trí tương đương` được giữ làm bằng chứng, chưa được gán thành số năm.
- Số năm kinh nghiệm bằng `0` chỉ dùng khi nguồn có bằng chứng phù hợp; dữ liệu thiếu vẫn dùng `NULL`.

## 5. Cấu trúc 30 trường chung

Các trường danh sách được thể hiện thành mảng JSON trong view `staging.jobs`; dữ liệu vật lý được lưu ở các bảng staging con.

| STT | Trường | Nội dung |
|---|---|---|
| 1 | `job_id` | Mã tin có tiền tố nguồn |
| 2 | `source` | Nguồn `itviec` hoặc `topdev` |
| 3 | `source_job_id` | Mã tin tại nguồn, nếu có |
| 4 | `job_url` | Đường dẫn tin |
| 5 | `crawl_run_id` | Mã lần thu thập |
| 6 | `observed_at` | Thời điểm thu thập có múi giờ |
| 7 | `published_at` | Thời điểm đăng theo nguồn, nếu có |
| 8 | `title` | Tiêu đề tuyển dụng |
| 9 | `company_name` | Tên công ty |
| 10 | `description` | Nội dung tuyển dụng |
| 11 | `job_roles` | Danh sách vai trò công việc |
| 12 | `seniority_levels` | Danh sách cấp bậc |
| 13 | `location_text` | Văn bản địa chỉ |
| 14 | `cities` | Danh sách tỉnh hoặc thành phố |
| 15 | `work_mode` | Chế độ làm việc |
| 16 | `skills` | Danh sách kỹ năng kèm nhóm và mức yêu cầu |
| 17 | `required_knowledge` | Danh sách kiến thức yêu cầu |
| 18 | `experience_text` | Văn bản bằng chứng kinh nghiệm |
| 19 | `experience_min_years` | Cận dưới số năm kinh nghiệm |
| 20 | `experience_max_years` | Cận trên số năm kinh nghiệm |
| 21 | `education_requirements` | Danh sách yêu cầu học vấn |
| 22 | `foreign_languages` | Danh sách yêu cầu ngoại ngữ |
| 23 | `soft_skills` | Danh sách kỹ năng mềm |
| 24 | `salary_text` | Văn bản lương hoặc thu nhập |
| 25 | `salary_min` | Cận dưới mức tiền |
| 26 | `salary_max` | Cận trên mức tiền |
| 27 | `salary_currency` | Tiền tệ |
| 28 | `salary_period` | Kỳ trả lương |
| 29 | `salary_basis` | Gross hoặc net |
| 30 | `salary_status` | Trạng thái công bố lương |

## 6. Ví dụ cấu trúc dữ liệu

Đây là một phần bản ghi minh họa sau chuẩn hóa:

```json
{
  "cities": ["Hà Nội"],
  "work_mode": "hybrid",
  "skills": [
    {
      "name": "Node.js",
      "category": "frameworks_libraries",
      "requirement_type": "required"
    }
  ],
  "experience_text": "4+ years of experience",
  "experience_min_years": 4,
  "experience_max_years": null,
  "education_requirements": [
    {
      "degree": null,
    "majors": ["Information Technology"],
      "requirement_type": "unspecified"
    }
  ],
  "foreign_languages": [
    {
      "language": "English",
      "level_text": "TOEIC 650",
      "requirement_type": "preferred"
    }
  ],
  "salary_text": "Thu nhập: 12–25 triệu đồng/tháng.",
  "salary_min": 12000000,
  "salary_max": 25000000,
  "salary_currency": "VND",
  "salary_period": "month",
  "salary_basis": null,
  "salary_status": "disclosed"
}
```

## 7. Nguyên tắc giữ chất lượng dữ liệu

- Không lấy lương trung bình hoặc số tự đoán để điền vào dữ liệu lương nguồn.
- Không dùng ngày crawl thay ngày đăng bị thiếu.
- Không xóa tin chỉ vì thiếu lương, kinh nghiệm hoặc kỹ năng.
- Không xóa lịch sử khi cùng một tin xuất hiện ở nhiều lần crawl; đếm mã tin phân biệt khi phân tích số việc làm.
- Không tự gộp tin giữa hai nguồn chỉ dựa vào tiêu đề và công ty giống nhau.
- Không tự cập nhật địa giới hành chính hoặc suy ra tỉnh chỉ từ tên phường; địa danh ngoài từ điển được giữ để rà soát.
- Từ điển alias chưa bao phủ mọi kỹ năng, nghề nghiệp và địa danh. Các nhãn ngoài từ điển vẫn giữ theo nguồn để bổ sung dần.
- Chuẩn cấu trúc học vấn dùng chung không đồng nghĩa mọi tên bằng cấp đã được ánh xạ về một hệ phân loại đầy đủ.
- View `staging.job_quality_flags` hỗ trợ rà soát dữ liệu thiếu, thiếu đơn vị kinh nghiệm và tiêu đề có dấu hiệu ngoài IT.
- Cờ `likely_non_it` là heuristic để rà soát, không phải nhãn ngành đã được duyệt thủ công và không làm mất bản ghi staging.

## 8. Mã nguồn đối chiếu

- [Quy tắc làm sạch dùng chung](../data_preprocessing/staging_cleaning.py)
- [Normalizer ITviec](../data_preprocessing/itviec_normalize_data/normalize_itviec.py)
- [Normalizer TopDev](../data_preprocessing/topdev_normalize_data/normalize_topdev.py)
- [Các view rà soát chất lượng](../data_preprocessing/staging_quality.sql)

## 9 Chuẩn hóa toàn diện và lớp BI nguyên tử

Từ điển dùng chung hiện nằm trong data_preprocessing/standardization_catalog.json; canonicalization.py áp dụng cho hai nguồn. Xem docs/CHUAN_HOA_VA_DU_LIEU_NGUYEN_TU.md để biết các domain, quy tắc tách nhãn ghép, chuyển ngoại ngữ/vai trò về đúng bảng con và mô hình BI. View 30 trường giữ để tương thích; fact/bridge trong bi không có cột danh sách JSON. Nhãn chưa có bằng chứng ánh xạ được giữ và đưa vào hàng đợi rà soát, không fuzzy match.
