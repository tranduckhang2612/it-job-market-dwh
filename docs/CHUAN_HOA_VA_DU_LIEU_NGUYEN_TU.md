# Chuẩn hóa dữ liệu và sử dụng lớp BI nguyên tử

## Phạm vi đã triển khai

Quy tắc dùng chung cho ITviec và TopDev nằm trong `data_preprocessing/canonicalization.py` và từ điển phiên bản `2026.10.29.1` trong `data_preprocessing/standardization_catalog.json`.

Từ điển bao phủ kỹ năng/công nghệ, vai trò, cấp bậc, địa danh, ngoại ngữ, bằng cấp, chuyên ngành, kỹ năng mềm, kiến thức, chế độ làm việc, tiền tệ, kỳ lương, gross/net và mức yêu cầu. Kiểu số, thời điểm, NULL và lineage tiếp tục được kiểm định bởi normalizer 30 trường. Schema vẫn là 1.1; không xóa lịch sử hoặc thay raw.

Các ví dụ quy tắc: Cplus/cpp/C++ -> C++; Csharp/C# -> C#; JS -> JavaScript; Sr. -> Senior; Mid-level -> Middle; Đại học / Cử nhân -> bachelor; CNTT/Công nghệ thông tin -> Information Technology; Làm việc nhóm -> Teamwork. Java khác JavaScript, C khác C++, AngularJS khác Angular, .NET khác .NET Core.

## Dữ liệu nguyên tử

`staging.jobs` vẫn là view 30 trường có mảng JSON để tương thích API. Dữ liệu vật lý của các mảng đã nằm ở bảng con; Power BI nên đọc lớp `bi`, không tách chuỗi JSON thủ công.

- `bi.fact_job_observations`: một observation_id trên một dòng, có lịch sử crawl và các cột scalar có kiểu rõ ràng.
- `bi.fact_job_latest`: một job_id có tiền tố nguồn trên một dòng, lấy quan sát mới nhất đủ gate DWH. Không chứng minh tin còn đang tuyển.
- `bi.dim_attribute`: một giá trị chuẩn trên một dòng, khóa TEXT ổn định gồm domain và tên chuẩn, có cờ is_mapped.
- `bi.bridge_job_attributes`: một observation, domain, giá trị và ngữ cảnh học vấn trên một dòng; không nối chéo các danh sách độc lập.
- `bi.bridge_job_attributes_latest`: chỉ các liên kết của observation mới nhất.
- Các view kỹ năng, thành phố và vai trò chuyên biệt hỗ trợ dựng quan hệ Power BI nhanh.
- `bi.job_foreign_language_levels_latest`: một bằng chứng mức ngoại ngữ trên một dòng; không tự quy đổi TOEIC/IELTS sang CEFR.
- `bi.job_addresses_latest`: một địa chỉ nguồn theo ranh giới dòng trên một dòng; không đoán quận/phường hoặc địa giới mới.

Ví dụ tin A yêu cầu C++ và Python: fact chỉ có một dòng A; bridge có hai dòng A-C++ và A-Python. Không lưu `C++,Python` trong ô dimension. Tên đã định nghĩa là một khái niệm, như CI/CD hoặc UI/UX, vẫn là một giá trị; atomic không có nghĩa tách mọi dấu gạch chéo.

Nhãn ghép chỉ tách khi mọi phần đều nhận dạng được trong từ điển: C/C++ -> C và C++; Agile & Scrum -> Agile và Scrum. Nhãn ghép chưa nhận dạng được được giữ ở staging và đưa vào `bi.review_queue`, không tự nâng thành liên kết BI nguyên tử. Nhãn đơn chưa có mapping được giữ với is_mapped=false để bổ sung sau.

Tag English/Korean bị gán nhầm kỹ năng kỹ thuật được chuyển sang quan hệ ngoại ngữ. Tag vai trò đã nhận diện được chuyển sang job_roles; giữ mức required/preferred của bằng chứng đúng nơi phù hợp, không suy ra trình độ ngoại ngữ.

## Chạy lại

Cấu hình DATABASE_URL hoặc các biến PG* trong terminal; không ghi mật khẩu vào báo cáo hoặc Git.

```powershell
py -3.10 tools/standardize_warehouse.py --out reports/standardization-next/dry-run
py -3.10 tools/standardize_warehouse.py --apply --out reports/standardization-next/applied
py -3.10 tools/standardize_warehouse.py --rollback reports/standardization-next/applied
```

Lệnh mặc định là dry-run. Mỗi apply dùng thư mục mới; công cụ ghi snapshot trước khi cập nhật, chạy transaction, giữ observation_id và kiểm tra raw không đổi. Rollback từ chối nếu dữ liệu đã có chỉnh sửa tiếp sau snapshot đích.

## Power BI

Mô hình kỹ năng tối thiểu gồm FactJobLatest, DimSkills và BridgeJobSkillsLatest. Tạo hai quan hệ một nhiều, hướng lọc đơn vào bridge:

- FactJobLatest[observation_id] 1 -> * BridgeJobSkillsLatest[observation_id].
- DimSkills[skill_key] 1 -> * BridgeJobSkillsLatest[skill_key].

Dùng measure đếm trên bridge cho biểu đồ theo kỹ năng, không dùng số dòng fact chưa nhận bộ lọc kỹ năng:

```dax
So tin theo ky nang = DISTINCTCOUNT(BridgeJobSkillsLatest[job_id])
Tong tin quan sat moi nhat = DISTINCTCOUNT(FactJobLatest[job_id])
```

Lọc DimSkills[category] = programming_languages để chỉ thống kê ngôn ngữ lập trình. SQL là query_languages, HTML là markup_languages, CSS là stylesheet_languages; không trộn vào nhóm lập trình mặc định.

Có thể dùng trực tiếp `bi.programming_language_demand` cho biểu đồ cơ bản. `bi.attribute_demand_latest` cung cấp số tin phân biệt theo mọi nhóm thuộc tính. Các nhóm có thể giao nhau nên tổng số tin theo từng kỹ năng/thành phố/cấp bậc không cộng thành tổng số việc làm. Khi kết hợp nhiều bridge làm slicer, cần thiết kế truyền bộ lọc hoặc measure TREATAS riêng; không nối phẳng các bridge rồi COUNTROWS.

Lương phải lọc theo currency, period và basis phù hợp trước khi tính thống kê. Không cộng VND với USD, không tự đổi năm sang tháng hoặc gross sang net. Trường thiếu giữ NULL, không thay bằng 0 hay ngày crawl. Không gộp pháp nhân công ty chỉ vì tên gần giống; không tự gộp một tin giữa hai nguồn khi thiếu khóa đối soát.

## Tài liệu phương pháp

- Microsoft Learn: https://learn.microsoft.com/en-us/power-bi/guidance/star-schema
- Microsoft Learn: https://learn.microsoft.com/en-us/power-bi/guidance/relationships-many-to-many
- PostgreSQL 17 Unicode normalization: https://www.postgresql.org/docs/17/functions-string.html

Đây là lớp view phục vụ BI trên staging cùng bảng tham chiếu canonical, chưa phải DWH vật lý có SCD và lịch ETL. Hướng dẫn mô hình Power BI và DAX được bàn giao bằng tài liệu; chưa tạo hoặc nghiệm thu tệp PBIX.
