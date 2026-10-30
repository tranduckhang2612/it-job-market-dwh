# Quy tắc bàn giao code và báo cáo trên GitHub

- Khi người dùng chọn nhánh đang có, cập nhật đúng nhánh đó; chỉ tạo nhánh mới khi được yêu cầu. Giữ thay đổi local có sẵn.
- Chỉ stage danh sách file được kiểm tra; không dùng `git add .` khi có snapshot hoặc thông tin đăng nhập local.
- Commit code phụ thuộc, từ điển, SQL, test và hướng dẫn để bản checkout mới chạy độc lập.
- Bàn giao báo cáo Markdown UTF-8 với đầy đủ đoạn văn, bảng và code từ bản Word đã kiểm chứng.
- Dùng liên kết tương đối trong tài liệu GitHub; tách rõ kết quả đã đo với hạng mục còn cần duyệt.
- Giữ snapshot, database dump, cookie, mật khẩu và báo cáo nhị phân ở local.
- Chạy test từ bản export của Git index/commit, kiểm tra liên kết tài liệu và nội dung staged trước push.
- Kiểm thử rollback bằng revert trên bản clone riêng; không rollback database thật hoặc nhánh đang bàn giao.
- Push nhánh được chọn không force, xác minh hash remote trùng commit local và trả link nhánh cùng báo cáo.
