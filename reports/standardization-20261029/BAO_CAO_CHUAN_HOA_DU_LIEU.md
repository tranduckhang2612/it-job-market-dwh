# Chuẩn hóa dữ liệu tuyển dụng và thiết kế dữ liệu nguyên tử cho kho dữ liệu

> **Bản review trên GitHub — ngày 29/10/2026.** Đạt kiểm tra kỹ thuật để nạp DW theo schema hiện tại; vẫn cần duyệt tin có dấu hiệu ngoài IT và thống nhất cách sử dụng dữ liệu thiếu. Lớp `bi` hiện là view, không phải DW vật lý đã nạp xong.

**Dự án IT Job Market DWH**

Báo cáo bàn giao cho nhóm trưởng và nhóm phát triển dữ liệu<br>
Ngày thực hiện 29 tháng 10 năm 2026

## 1 Kết luận và dữ liệu thực tế

Đợt xử lý đã chuẩn hóa 20 trong 20 lượt quan sát của 13 tin tuyển dụng, giữ nguyên dữ liệu raw, mã tin, observation_id và lịch sử ba lần crawl. Quy tắc được tích hợp chung cho ITviec và TopDev, đồng thời bổ sung lớp BI nguyên tử để nạp vào kho dữ liệu và lập mô hình Power BI.

Từ điển phiên bản 2026.10.29.1 có 14 nhóm, 261 giá trị chuẩn và 576 khóa alias. Phạm vi không chỉ là ngôn ngữ lập trình: vai trò, cấp bậc, địa danh, ngoại ngữ, học vấn, kỹ năng mềm và các enum liên quan đều được xử lý khi có cơ sở ánh xạ.

| Chỉ tiêu | Trước | Sau |
| --- | --- | --- |
| Lượt quan sát staging | 20 | 20 |
| Mã tin phân biệt | 13 | 13 |
| Lần crawl | 3 | 3 |
| Liên kết kỹ năng ở staging | 96 | 74 |
| Tin latest đủ gate DWH | Chưa có lớp BI | 13 |
| Liên kết thuộc tính nguyên tử | Chưa có lớp BI | 178 |
| Nhãn chưa ánh xạ trong mẫu | Chưa có hàng đợi | 0 |
| Khóa quan sát trùng | 0 | 0 |

Dữ liệu thật hiện chỉ gồm TopDev với 20 lượt quan sát; ITviec có 0 dòng trong database này. Kiểm thử ITviec dùng fixture và database UUID riêng, không được diễn giải thành số liệu tuyển dụng thực tế. Báo cáo không khẳng định mẫu nhỏ này đại diện toàn thị trường.

## 2 Bộ quy tắc chuẩn hóa dùng chung

Ánh xạ dựa trên tên đầy đủ và từ điển có phiên bản. Unicode, HTML entity, khoảng trắng và ký tự vô hình được làm sạch nhưng các ký hiệu có nghĩa như C++, C#, .NET và List&lt;String&gt; được giữ lại.

| Nhóm | Quy tắc và ví dụ |
| --- | --- |
| Kỹ năng và công nghệ | Cplus, cpp, C++ về C++; Csharp về C#; JS về JavaScript; phân loại framework, database, công cụ và ngôn ngữ riêng. |
| Vai trò | Full-stack Developer về Fullstack Developer; tag vai trò đã nhận diện chuyển về job_roles, không giữ như kỹ năng kỹ thuật. |
| Cấp bậc | Sr. về Senior; Mid-level về Middle; Intern về Internship. Không suy số năm kinh nghiệm từ cấp bậc. |
| Địa danh | HCMC, TP.HCM về Hồ Chí Minh. Giữ tên địa phương chưa có ánh xạ; không tự đổi địa giới hoặc suy tỉnh từ phường. |
| Ngoại ngữ | Tiếng Anh về English, tiếng Hàn về Korean. Chuyển tag ngôn ngữ khỏi skills; không đoán mức độ thành thạo. |
| Bằng cấp và chuyên ngành | Đại học / Cử nhân về bachelor; CNTT và Công nghệ thông tin về Information Technology; giữ liên kết đúng yêu cầu học vấn. |
| Kỹ năng mềm và kiến thức | Làm việc nhóm về Teamwork; Giao tiếp về Communication; RESTful API về REST API; Agile/Scrum tách hai mục. |
| Chế độ làm việc | Trực tiếp về onsite; Từ xa về remote; Kết hợp về hybrid. Không tự gán khi nguồn chưa xác định. |
| Lương và kinh nghiệm | Giữ cận số hữu hạn, kiểu NUMERIC và đơn vị riêng. Không cộng khoảng, đoán tiền tệ, đổi gross/net hoặc thay thiếu bằng 0. |
| Ngày giờ và định danh | Giữ source, job_id, crawl_run_id và observed_at có múi giờ. published_at thiếu vẫn NULL; không thay bằng ngày crawl. |
| Văn bản và công ty | Chuẩn Unicode và khoảng trắng, giữ nội dung nguồn. Không fuzzy merge công ty khác pháp nhân hoặc tin giữa hai nguồn. |

Các cặp khác nghĩa được giữ tách biệt: Java và JavaScript; C và C++; .NET và .NET Core; Angular và AngularJS. Alias chưa biết không được tự suy thành một giá trị quen thuộc. Thay từ điển cần chạy lại công cụ để đồng bộ bảng tham chiếu BI.

## 3 Cấu trúc dữ liệu nguyên tử

View staging.jobs giữ nguyên hợp đồng 30 trường để tương thích phần mềm hiện tại. Mảng JSON trong view là lớp trình bày; dữ liệu vật lý đã nằm trong các bảng con. Lớp BI chỉ công bố cột scalar có kiểu rõ ràng, không yêu cầu Power BI tách chuỗi danh sách.

| Bảng hoặc view | Đơn vị của một dòng |
| --- | --- |
| bi.fact_job_observations | Một observation_id, gồm khóa tin và lần crawl; giữ toàn bộ lịch sử đủ gate. |
| bi.fact_job_latest | Một job_id có tiền tố nguồn, chọn quan sát mới nhất đủ gate; hiện có 13 dòng. |
| bi.dim_attribute | Một giá trị chuẩn của một domain; khóa TEXT ổn định theo domain và tên, không dùng số thứ tự thay đổi theo lần chạy. |
| bi.bridge_job_attributes | Một observation, domain, giá trị và ngữ cảnh học vấn; hiện có 178 liên kết. |
| Các bridge latest | Chỉ các thuộc tính của observation đang được chọn; kỹ năng, thành phố và vai trò có view riêng. |
| Các view địa chỉ và mức ngoại ngữ | Một địa chỉ nguồn hoặc một bằng chứng trình độ trên một dòng, không đoán cấu phần chưa có. |
| bi.review_queue | Nhãn chưa ánh xạ để rà soát; nhãn ghép chưa xác định không được đẩy vào bridge nguyên tử. |

### Ví dụ tách quan hệ

| Bảng | Khóa tin | Giá trị |
| --- | --- | --- |
| FactJobLatest | A | Một dòng cho tin A |
| BridgeJobSkillsLatest | A | C++ |
| BridgeJobSkillsLatest | A | Python |

Không nối phẳng skills, cities và roles thành một bảng rồi đếm dòng vì phép nối có thể tạo tích Descartes. C/C++ và Agile &amp; Scrum được tách khi các phần đều đã biết; CI/CD, UI/UX hoặc QA/QC Engineer được giữ khi từ điển định nghĩa chúng là một khái niệm. Atomic được xác định theo miền dữ liệu, không theo số từ hay dấu câu.

Lớp BI hiện là các view trên staging cùng bảng tham chiếu canonical. Đây chưa phải DWH vật lý có SCD, lịch ETL và cơ chế xác định tin đã hết hạn.

## 4 Đối chiếu thay đổi trên dữ liệu thật

| Trường thay đổi | Lượt quan sát |
| --- | --- |
| job_roles | 4 |
| skills | 13 |
| education_requirements | 13 |
| soft_skills | 13 |
| required_knowledge | 3 |
| foreign_languages | 2 |

Một lượt có thể thay đổi nhiều trường, nên không cộng bảng trên thành số tin. Lương, tiền tệ, thời điểm quan sát, dữ liệu raw và các khóa lineage được giữ nguyên trong đợt chuẩn hóa này.

| Giá trị trước trong dữ liệu thật | Giá trị sau |
| --- | --- |
| C/C++ trong skills | C và C++, gộp với mục đã có; giữ required nếu nguồn có yêu cầu bắt buộc. |
| Agile &amp; Scrum hoặc Agile/Scrum | Hai giá trị Agile và Scrum, không tạo hai bản sao cùng kỹ năng trong một tin. |
| English hoặc Korean trong skills | Chuyển sang quan hệ ngoại ngữ, giữ bằng chứng và mức yêu cầu có nguồn. |
| Đại học / Cử nhân và Công nghệ thông tin | bachelor và Information Technology. |
| Giao tiếp và Làm việc nhóm | Communication và Teamwork. |
| QA trong nhãn kỹ năng | Quality Assurance, nhóm methodology; không đưa vào ngôn ngữ lập trình. |

Liên kết skills giảm từ 96 xuống 74 do gộp alias, bỏ trùng và chuyển các tag ngoại ngữ/vai trò sang đúng quan hệ. Số lượt không có kỹ năng kỹ thuật tăng từ 6 lên 7; đây không phải mất tin vì các bằng chứng đã được giữ ở nhóm tương ứng và raw vẫn còn nguyên.

### Các khoảng trống được giữ đúng nghĩa

Trong 20 lượt, published_at còn thiếu 20; cities thiếu 2; cận kinh nghiệm tối thiểu thiếu 9; salary_basis thiếu 20. Có 10 lượt lương thỏa thuận. Các trường này không được điền bằng số trung bình, 0 hoặc ngày crawl.

Heuristic theo tiêu đề vẫn gắn cờ 7 trong 13 tin có dấu hiệu ngoài IT. Không có tin nào bị xóa tự động; nhóm cần duyệt phạm vi ngành trước khi công bố thống kê thị trường.

## 5 Kiểm thử và bảo toàn dữ liệu

| Kiểm tra thực chạy | Kết quả |
| --- | --- |
| Bộ kiểm thử trước sửa | 170 bài, 1 bài PostgreSQL chưa bật. |
| Bộ kiểm thử sau sửa | 186 bài đạt khi bật PostgreSQL; gồm 16 bài hồi quy mới. |
| Từ điển và alias | Đối chiếu 576 alias, kiểm tra xung đột, ánh xạ lặp lại và các cặp khác nghĩa. |
| Bản sao PostgreSQL UUID | Chuẩn hóa trên dữ liệu sao chép, kiểm tra cấu trúc nguyên tử và liên kết dimension. |
| Lỗi sau lần ghi thực | Transaction hoàn tác toàn bộ thay đổi; snapshot không đổi. |
| Chạy lại chuẩn hóa | 0 lượt thay đổi; snapshot lặp lại khớp và không nhân bản quan sát. |
| Rollback cũ sau chỉnh sửa khác | Precondition từ chối, giữ nguyên chỉnh sửa mới. |
| Rollback trên bản sao | Khôi phục đủ snapshot trước; schema BI mới được gỡ; database thật không bị rollback. |
| Khóa và dữ liệu nguồn | 20 lượt, 13 mã tin, 3 lần crawl; raw và observation_id giữ nguyên. |

Năm assertion cũ của TopDev được cập nhật đúng nhãn chuẩn mới cho REST API, Communication, Teamwork, bachelor và Information Technology; các kiểm tra khác và thay đổi local có sẵn được bảo toàn. Các bản gốc và hash đã được chụp trước khi sửa.

Đợt áp dụng gồm bước chuẩn hóa chính và bước rà soát bổ sung alias QA. Mỗi bước chạy trong transaction. Thư mục final-evidence giữ snapshot đầu đợt và cuối đợt để kiểm tra hoặc rollback toàn bộ đợt; các snapshot từng bước vẫn được giữ riêng.

### Vận hành lại

```powershell
py -3.10 tools/standardize_warehouse.py --out reports/next/dry-run
py -3.10 tools/standardize_warehouse.py --apply --out reports/next/applied
py -3.10 tools/standardize_warehouse.py --rollback reports/next/applied
```

Cấu hình kết nối bằng DATABASE_URL hoặc PG*. Mặc định là dry-run; mỗi apply dùng thư mục mới. Không lưu mật khẩu, cookie hoặc token vào báo cáo. Công cụ từ chối rollback nếu trạng thái database không còn khớp snapshot sau xử lý.

## 6 Hướng dẫn mô hình Power BI

Để thống kê kỹ năng trên dữ liệu mới nhất, nạp ba view tương ứng FactJobLatest, DimSkills và BridgeJobSkillsLatest. Mỗi đầu one phải có khóa duy nhất; đặt các khóa ở chế độ không tổng hợp.

| Quan hệ | Cardinality và hướng lọc |
| --- | --- |
| FactJobLatest.observation_id → BridgeJobSkillsLatest.observation_id | Một nhiều, hướng lọc đơn vào bridge. |
| DimSkills.skill_key → BridgeJobSkillsLatest.skill_key | Một nhiều, hướng lọc đơn vào bridge. |

Với cấu hình trên, measure theo kỹ năng phải đếm trên bridge để nhận đồng thời bộ lọc kỹ năng và tin. Measure đếm trực tiếp fact không tự nhận bộ lọc kỹ năng theo chiều ngược lại.

```dax
So tin theo ky nang =
    DISTINCTCOUNT(BridgeJobSkillsLatest[job_id])

Tong tin quan sat moi nhat =
    DISTINCTCOUNT(FactJobLatest[job_id])
```

Lọc category = programming_languages cho ngôn ngữ lập trình. SQL thuộc query_languages, HTML thuộc markup_languages, CSS thuộc stylesheet_languages. Không tự đưa React hoặc Node.js vào nhóm ngôn ngữ lập trình.

### View tổng hợp có thể đọc trực tiếp

```sql
SELECT * FROM bi.programming_language_demand
ORDER BY distinct_jobs DESC, canonical_name;
```

| Ngôn ngữ trong mẫu | Số tin phân biệt |
| --- | --- |
| C | 1 |
| C++ | 1 |
| JavaScript | 1 |
| TypeScript | 1 |

bi.attribute_demand_latest hỗ trợ tương tự cho mọi nhóm thuộc tính. Một tin có thể dùng nhiều ngôn ngữ, có nhiều thành phố hoặc nhiều cấp bậc; số đếm theo nhóm không có tính cộng. Khi kết hợp nhiều bridge làm slicer, cần cấu hình truyền bộ lọc hoặc measure TREATAS riêng, không nối chéo rồi COUNTROWS.

Lương chỉ được thống kê trong cùng tiền tệ, kỳ trả và gross/net phù hợp. Hướng dẫn mô hình và DAX được bàn giao bằng tài liệu; chưa tạo hoặc nghiệm thu tệp PBIX trên Power BI Desktop.

## Phụ lục 1 Từ điển 30 trường chung

Kiểu dưới đây mô tả dữ liệu vật lý và cách dùng. Các trường danh sách được biểu diễn bằng bảng con nguyên tử; view staging.jobs chỉ tổng hợp lại thành JSON để tương thích hợp đồng 1.1.

| Trường | Kiểu hoặc vị trí | Quy tắc |
| --- | --- | --- |
| job_id | TEXT | Khóa tin có tiền tố nguồn; không gộp tin giữa hai nguồn. |
| source | TEXT enum | Nguồn itviec hoặc topdev. |
| source_job_id | TEXT hoặc NULL | Mã nguồn giữ nguyên, không ép số làm mất số 0 đầu. |
| job_url | TEXT | URL nguồn giữ để truy vết. |
| crawl_run_id | TEXT | Khóa lần crawl, không thay bằng thời gian xử lý. |
| observed_at | TIMESTAMPTZ | Thời điểm quan sát nguồn, giữ múi giờ và lịch sử. |
| published_at | TIMESTAMPTZ hoặc NULL | Ngày đăng theo nguồn; thiếu vẫn NULL. |
| title | TEXT | Văn bản đơn được làm sạch, không viết lại chức danh. |
| company_name | TEXT hoặc NULL | Chuẩn Unicode/khoảng trắng; không tự hợp nhất pháp nhân. |
| description | TEXT | Nội dung nguồn nhiều dòng, không dùng làm danh sách dimension. |
| job_roles | Bảng con | Một vai trò chuẩn trên một dòng liên kết. |
| seniority_levels | Bảng con | Một cấp bậc chuẩn trên một dòng liên kết. |
| location_text | TEXT nguồn | Giữ nguyên ranh giới địa chỉ; BI có view từng dòng địa chỉ. |
| cities | Bảng con | Một tỉnh/thành trên một dòng; không suy đoán địa giới. |
| work_mode | TEXT enum hoặc NULL | onsite, hybrid, remote hoặc chưa xác định. |

observation_id BIGINT là khóa kỹ thuật của một lượt quan sát, không nằm trong 30 trường nghiệp vụ. Cặp job_id và crawl_run_id vẫn duy nhất; khi đếm thị trường theo tin, dùng job_id phân biệt và chọn grain lịch sử hoặc latest nhất quán.

## Phụ lục 2 Từ điển 30 trường chung

Kiểu dưới đây mô tả dữ liệu vật lý và cách dùng. Các trường danh sách được biểu diễn bằng bảng con nguyên tử; view staging.jobs chỉ tổng hợp lại thành JSON để tương thích hợp đồng 1.1.

| Trường | Kiểu hoặc vị trí | Quy tắc |
| --- | --- | --- |
| skills | Bảng con | Một kỹ năng chuẩn, nhóm và mức yêu cầu trên một dòng. |
| required_knowledge | Bảng con | Một mục kiến thức hoặc khái niệm trên một dòng. |
| experience_text | TEXT hoặc NULL | Bằng chứng nguồn, không dùng thay cận số. |
| experience_min_years | NUMERIC hoặc NULL | Cận dưới số năm; 0 chỉ khi nguồn có bằng chứng. |
| experience_max_years | NUMERIC hoặc NULL | Cận trên số năm; giữ khoảng mở. |
| education_requirements | Bảng con | Một yêu cầu bằng cấp, giữ đúng ngữ cảnh item_order. |
| foreign_languages | Bảng con | Một ngôn ngữ và bằng chứng mức độ, không trộn skills. |
| soft_skills | Bảng con | Một kỹ năng mềm theo tên chuẩn. |
| salary_text | TEXT hoặc NULL | Bằng chứng lương nguồn để đối chiếu. |
| salary_min | NUMERIC hoặc NULL | Cận dưới dương, hữu hạn; không dùng 0 làm thiếu. |
| salary_max | NUMERIC hoặc NULL | Cận trên không nhỏ hơn cận dưới, giữ khoảng mở. |
| salary_currency | TEXT hoặc NULL | Mã tiền tệ, không đoán hoặc tự quy đổi. |
| salary_period | TEXT hoặc NULL | month, year, week, day, hour theo bằng chứng. |
| salary_basis | TEXT hoặc NULL | gross, net hoặc chưa xác định. |
| salary_status | TEXT enum | disclosed, negotiable, not_disclosed, unknown. |

### Tài liệu tham khảo

[1] Microsoft Learn. Understand star schema and the importance for Power BI.<br>
<https://learn.microsoft.com/en-us/power-bi/guidance/star-schema>

[2] Microsoft Learn. Many-to-many relationship guidance.<br>
<https://learn.microsoft.com/en-us/power-bi/guidance/relationships-many-to-many>

[3] PostgreSQL 17. String Functions and Operators.<br>
<https://www.postgresql.org/docs/17/functions-string.html>

Nguồn số liệu: PostgreSQL cục bộ của dự án và snapshot trước/sau trong reports/standardization-20261029/final-evidence. Tham khảo phương pháp không thay thế số liệu kiểm tra thực tế của dự án.

## Checklist nghiệm thu của leader

- [ ] Duyệt 7 trong 13 tin bị heuristic gắn cờ có thể ngoài IT, không coi cờ tự động là nhãn đã xác nhận.
- [ ] Chấp thuận quy ước NULL và công bố độ bao phủ từng trường trước khi phân tích lương, kinh nghiệm hoặc ngày đăng.
- [ ] Chốt grain lịch sử hay latest và dùng DISTINCTCOUNT theo job_id để tránh đếm lặp lần crawl.
- [ ] Review [từ điển chuẩn hóa](../../data_preprocessing/standardization_catalog.json), [SQL lớp BI](../../data_preprocessing/bi_atomic.sql) và [test hồi quy](../../tests/test_canonical_atomic.py).
- [ ] Chạy test trên checkout mới và dry-run trên database phù hợp trước khi apply.
- [ ] Thiết kế bước nạp DW vật lý và kiểm tra mô hình Power BI riêng nếu nhóm triển khai tiếp.

**Tệp giữ local:** snapshot PostgreSQL, database dump, thông tin kết nối và bản Word/PDF không được đưa vào nhánh. Các đường dẫn snapshot được nhắc trong báo cáo là vị trí bằng chứng tại máy thực hiện, không phải tài liệu đi kèm GitHub.
