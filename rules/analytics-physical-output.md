# Ranh giới staging và analytics vật lý — 01/11/2026

- Quy tắc này thay thế phần analytics chỉ là view trong analytics-contract-1-2.md.
- Theo xác nhận phạm vi: phần 1 và 2 đã hoàn tất. Giữ nguyên byte mã, quy tắc và kiểm thử upstream tại commit 884b1dc (trước khi thêm loader analytics); không chỉnh/replay hai phần này trong đợt xuất analytics. Mốc 884b1dc không phải commit GitHub ban đầu.
- Bước 3 chỉ nhận đầu vào staging hiện có, chuẩn hóa bổ sung và kiểm định để xuất analytics theo hợp đồng 1.2. Các quy tắc đã có ở phần 1/2 không bị tháo bỏ hoặc chuyển chỗ.
- Bước ba dùng từ điển chuẩn hóa toàn diện, chỉ đọc staging đủ gate và ghi bảng thật trong analytics. Không UPDATE/DELETE/INSERT raw hoặc staging, không tạo schema normalize/normalized.
- Giữ nguyên staging lịch sử đã làm sạch từ trước; không replay raw để giả lập lại đầu vào cũ.
- Analytics có khóa quan sát (job_id,crawl_run_id), bảng con nguyên tử và trạng thái load theo hash; view phân tích đọc bảng analytics, không phụ thuộc staging khi truy vấn.
- Lần đầu nạp tất cả quan sát hợp lệ; chạy lại không đổi phải không ghi; batch mới chỉ thêm/cập nhật quan sát cần thiết, giữ lịch sử cũ. Batch lỗi và lỗi giữa transaction không xuất một phần.
- Thử read-only role trên raw/staging; kiểm thử đầu vào mới, sửa cùng quan sát, lỗi giữa transaction, lần lặp và rollback trên DB UUID.
- Snapshot/hash trước sửa; sửa bản copy rồi kiểm thử mới áp dụng local. Không push khi chưa có yêu cầu mới.
