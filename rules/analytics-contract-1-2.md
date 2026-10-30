# Hợp đồng analytics phiên bản 1.2

- Yêu cầu mới ngày 30/10 thay thế quy tắc giữ nguyên cấu trúc lồng 1.1: vẫn 30 trường cấp cao, bỏ requirement_type ở kỹ năng, học vấn và ngoại ngữ.
- Raw giữ nguyên. Chuyển schema bi hiện có sang analytics trong transaction; không DROP CASCADE vượt schema hay tạo bản sao dữ liệu staging.
- Migration chỉ bỏ ba cột và thay view/hợp đồng. Không đọc lại toàn bộ raw để chuẩn hóa lần hai, không thay toàn bộ bảng con.
- Chỉ nâng gate từ 1.1 sang 1.2 cho batch đã hợp lệ và kiểm tra lại dữ liệu theo hợp đồng mới. Không biến batch lỗi thành thành công.
- Analytics là lớp view phục vụ Power BI, không tự nhận là DW vật lý. Sơ đồ phải phân biệt phần đang có và DW vật lý đề xuất.
- Snapshot/hashes trước sửa; thử lỗi giữa transaction, chạy lặp không ghi, rollback trên database UUID và kiểm thử pipeline trên DB mới.
- Không push tiếp khi người dùng chưa yêu cầu cho đợt này. Giữ báo cáo cũ như lịch sử, thêm tài liệu hiện hành để tránh hiểu nhầm.
