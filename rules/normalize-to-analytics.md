# Luồng mới: normalize_data → analytics (02/11/2026)

- Luồng của user ngày 02/11 và main e0dc1d4 thay thế thiết kế staging→analytics cũ. Nguồn hiện hành: normalize_data.normalized_jobs phiên bản 5.1, 17 cột nghiệp vụ.
- Chỉ thêm bộ tách nguyên tử và loader analytics; không sửa hoặc gọi lại crawler, role gate, normalizer, loader normalize/staging hay migration upstream.
- Programming language khác ngoại ngữ. Mỗi job/quan sát/giá trị một dòng trong bridge; giữ source, job_id, crawl_run_id, observed_at và source normalized_at.
- Nhóm programming/framework/core_skill/role/city/foreign_language/level có bảng thật; giữ soft_skill riêng. Không lấy thiếu dữ liệu làm phủ định yêu cầu.
- User xác nhận bỏ benefit: không tạo bảng, không trích xuất benefit.
- User yêu cầu chạy lại crawl + normalize bằng main mới trên DB riêng it_job_market_v5; chỉ chạy code upstream nguyên bản, không sửa rule.
- Không tách bừa nhãn đa nghĩa, không fuzzy-match; đưa nhãn ghép chưa xác định vào review. Không có requirement_type trong output.
- Loader chỉ SELECT nguồn, chỉ ghi analytics, transaction đầy đủ, lặp không ghi, đồng bộ thu hồi khóa nguồn đã xóa trong phạm vi chọn; run/source filter không xóa ngoài phạm vi.
- Snapshot/hash trước sửa, sửa bản copy; baseline/modified tests và rollback thực thi trên DB/mã copy. Không push khi chưa được yêu cầu.

- Bàn giao 02/11: user yêu cầu nhánh feature/Analytics, commit nguyên văn "done load vào dw chờ config của DW". Chỉ stage 14 file code/test/docs đã kiểm tra; không đẩy snapshot, dump, reports hoặc script tools cũ. Tài liệu phải ghi rõ loader SQL Server DW chưa có/chưa nghiệm thu, đang chờ cấu hình bảng đích/kết nối.
