# Quy tắc làm sạch staging

- Đọc schema, pipeline và dữ liệu thực tế trước khi chọn quy tắc.
- Giữ nguyên raw và giá trị nguồn; không suy diễn lương/kinh nghiệm khi thiếu bằng chứng.
- Chụp bản sao và SHA-256 trước khi đổi mã hoặc cập nhật dữ liệu.
- Áp dụng quy tắc chung cho ITviec và TopDev; giữ khóa liên kết và lineage.
- Dùng transaction, dry-run, báo cáo chất lượng trước/sau và kiểm thử idempotency.
- Không xóa bản ghi chỉ vì thiếu lương, mô tả hoặc tin xuất hiện trên cả hai nguồn.
- Ghi rõ số liệu đo được, quy tắc chưa xử lý và lệnh phục hồi đã thử nghiệm.
- Không ghi đè các thay đổi hoặc kiểm thử có sẵn của người dùng.
- Dùng `tools/clean_staging.py` cho dry-run/apply; mỗi apply chọn thư mục mới.
- Giữ schema 30 trường 1.1; ghi phiên bản quy tắc riêng qua CLEANING_VERSION.
- Rà soát `staging.job_quality_flags`; view ứng viên IT chỉ là heuristic.
- Chạy unittest và kiểm thử rollback trên database UUID riêng trước khi bàn giao.
