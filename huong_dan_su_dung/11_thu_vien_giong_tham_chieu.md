# Thư viện giọng tham chiếu

Nút **Giọng tham chiếu** trên thanh trên cùng mở một hộp thoại độc lập với pipeline, tương tự hộp thoại **Cài đặt**.

## Chọn thư mục và nghe thử

Thư mục mặc định là `reference/sample` bên trong repository. Thư mục này không nằm trong `.gitignore`, vì vậy các file WAV có thể được thêm vào Git khi cần. Có thể chọn thư mục khác; ứng dụng ghi nhớ lựa chọn gần nhất cho lần mở sau.

Danh sách hiển thị các file `.wav` trực tiếp trong thư mục đang chọn. Chọn một file để phát, tạm dừng, dừng hoặc tua bằng thanh thời gian. Nút **Mở thư mục** mở vị trí hiện tại bằng File Explorer.

## Thu file WAV mới

1. Chọn microphone.
2. Nhập tên file; phần mở rộng `.wav` được tự thêm nếu còn thiếu.
3. Bấm **Bắt đầu ghi**.
4. Bấm **Dừng và lưu WAV**.

Ứng dụng thu bằng định dạng microphone hỗ trợ rồi dùng FFmpeg chuẩn hóa thành WAV mono PCM 16-bit, 48 kHz. File hoàn chỉnh được lưu vào đúng thư mục đang chọn và xuất hiện ngay trong danh sách nghe thử. Nếu tên file đã tồn tại, ứng dụng yêu cầu xác nhận trước khi ghi đè.

Nếu đóng hộp thoại trong lúc đang ghi, ứng dụng hỏi xác nhận và hủy bản ghi tạm. Các file WAV do người dùng tạo không tự động được thêm vào Git; việc `git add` và commit vẫn do người dùng thực hiện.
