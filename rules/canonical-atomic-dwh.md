# Quy tắc chuẩn hóa toàn diện và dữ liệu nguyên tử

- Đọc và giữ nguyên thay đổi local hiện có; sao lưu mã, hash và PostgreSQL trước khi sửa.
- Dùng chung từ điển alias cho hai nguồn; không chỉ chuẩn hóa ngôn ngữ lập trình.
- Chuẩn hóa kỹ năng, vai trò, cấp bậc, địa danh, ngoại ngữ, học vấn, kỹ năng mềm, enum và kiểu số có bằng chứng.
- Từ 30/10 dùng hợp đồng 1.2 với 30 trường cấp cao và không requirement_type trong đầu ra; mảng trong view tương thích không phải bảng vật lý đa trị. Lớp analytics đọc fact và các bridge nguyên tử.
- Không trộn Java với JavaScript, C với C++, công nghệ với ngoại ngữ hay tiền tệ khác nhau.
- Chỉ tách nhãn ghép khi các thành phần có trong từ điển; giữ CI/CD, UI/UX và nhãn được định nghĩa là một khái niệm.
- Nhãn chưa biết giữ để rà soát, không fuzzy match, không đoán dữ liệu thiếu hoặc hợp nhất công ty khác pháp nhân.
- Dùng một cặp tin và thuộc tính trên mỗi dòng; giữ observation_id, job_id và crawl_run_id để phân biệt lịch sử với tin phân biệt.
- Kiểm thử unit, tính lặp lại, lỗi giữa transaction, rollback trên PostgreSQL UUID riêng rồi mới áp dụng dữ liệu thật.
- Báo cáo Word phải ghi số liệu đo thực, giới hạn, trường chưa chuẩn hóa và hướng dẫn Power BI; render và kiểm tra từng trang trước bàn giao.
