# Chuẩn hóa dữ liệu nguyên tử

Tài liệu hiện hành: [Kiến trúc DW và analytics](KIEN_TRUC_DW_VA_ANALYTICS.md).

Từ 30/10/2026, hợp đồng là 1.2: vẫn 30 trường cấp cao, bỏ requirement_type ở kỹ năng, học vấn và ngoại ngữ. Schema phục vụ phân tích là analytics, thay thế bi. Raw giữ nguyên; staging chuẩn hóa một lần theo pipeline batch; analytics đọc kết quả để tạo fact/dimension/bridge và tỷ lệ, không chạy normalizer raw thêm lần nữa.

Báo cáo và snapshot 29/10 giữ như lịch sử. Snapshot cũ cần script cũ đi kèm, không dùng lẫn migration 1.2.
