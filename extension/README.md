# TransLanguage Web Bridge Extension

Extension kết nối TransLanguage với phiên đăng nhập hiện có trên **ChatGPT Web** và **Claude Web**.
Extension chỉ nhận job từ bridge nội bộ `http://127.0.0.1:8765`; không đọc hoặc lưu cookie đăng nhập.

## Cài đặt trên Chrome

1. Mở `chrome://extensions`.
2. Bật **Developer mode**.
3. Chọn **Load unpacked** và trỏ tới thư mục `extension` này.
4. Sau mỗi lần cập nhật source, nhấn **Reload** tại thẻ extension rồi tải lại tab ChatGPT/Claude.

## Sử dụng

1. Mở TransLanguage để bridge localhost khởi động.
2. Đăng nhập `https://chatgpt.com` hoặc `https://claude.ai`.
3. Mở Project đã thiết lập chỉ dẫn/file, chỉ để một tab của provider sắp dùng và bảo đảm ô nhập đang trống.
4. Dán URL Project vào provider tương ứng tại Step 3 rồi chạy.

Model của web provider được chọn trực tiếp trên trang. Mỗi lần chạy Step 3 bắt đầu một cuộc trò chuyện
mới từ URL Project; các request phân tích, dịch theo batch và kiểm duyệt của lần chạy đó được gửi nối tiếp
trong cùng cuộc trò chuyện. Extension chỉ tiếp tục URL cuộc trò chuyện được ghi nhận từ request đầu;
nếu tab đã chuyển sang chat khác, nó quay lại URL Project.

Cả ba giai đoạn gửi input bằng file JSON đính kèm. Giai đoạn phân tích dùng
`step3-analysis.json`, mỗi batch dịch dùng tên theo khoảng ID như
`step3-translation-0121-0140.json`, và giai đoạn kiểm duyệt dùng `step3-review.json`. Extension tạo file
trong bộ nhớ và đính kèm vào đúng lượt chat; không thêm chúng vào Project Sources. Nếu file đính kèm
chưa được giao diện web xác nhận sẵn sàng, extension dừng job trước khi bấm Send.

Khi tắt chế độ chất lượng, mỗi batch dịch yêu cầu provider tạo file kết quả thật có tên dạng
`step3-translation-result-0121-0140.json`. Extension chỉ nhận file nằm trong phản hồi mới nhất,
tải nội dung file và gửi về ứng dụng; batch kế tiếp chỉ được gửi sau khi file trước hợp lệ.
Với ChatGPT, extension ghép đúng resource card bằng tên file của batch hiện tại, đăng ký theo dõi
download trước khi bấm nút **Download file**, rồi chỉ chấp nhận download mới có đúng basename đó
(hoặc hậu tố `(1)`, `(2)` do Chrome tránh ghi đè). File của batch khác không được nhận thay thế.
Nếu sự kiện tạo download xuất hiện trước khi Chrome xác định tên file, extension đối soát lại các
download phát sinh sau thời điểm bắt đầu batch và chỉ trả đường dẫn của đúng file batch hiện tại.

Nếu Step 3 báo chưa kết nối, tải lại tab provider sau khi nạp extension và chờ vài giây. Có thể xem
Service worker console tại `chrome://extensions` để kiểm tra log `[JOB]`.

## Phạm vi hiện tại

- ChatGPT Web: gửi prompt, chờ phản hồi, đọc nội dung; các thao tác ChatGPT Project sẵn có vẫn được giữ nguyên.
- Claude Web: gửi prompt, chờ phản hồi và đọc nội dung; chưa tự thay đổi model trên giao diện Claude.
- Bridge dùng extension session header để ràng buộc kết quả với đúng worker đã nhận job.
