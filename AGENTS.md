# AGENTS.md

## Phạm vi áp dụng

Các quy tắc trong file này áp dụng cho toàn bộ repository, trừ khi một thư mục con có `AGENTS.md` với chỉ dẫn cụ thể hơn.

## Bối cảnh dự án

Đây là dự án ứng dụng phiên dịch video. Khi kiến trúc, công nghệ hoặc quy ước của dự án được xác định thêm, hãy cập nhật tài liệu dự án và tuân theo các quyết định đã được người dùng chấp thuận.

## Quy trình bắt buộc khi nhận yêu cầu phát triển

Khi người dùng giao một yêu cầu phát triển tính năng, sửa lỗi, tái cấu trúc hoặc thay đổi code:

1. Chưa được chỉnh sửa, tạo mới hoặc xóa code ngay.
2. Đọc và khảo sát repository ở chế độ chỉ đọc trong phạm vi cần thiết để hiểu hiện trạng.
3. Phản hồi bằng tiếng Việt, bao gồm:
   - Diễn giải lại yêu cầu và kết quả mong đợi để xác nhận cách hiểu.
   - Nêu hướng xử lý dự kiến, các khu vực/file có khả năng bị ảnh hưởng và những quyết định kỹ thuật quan trọng.
   - Liệt kê rõ mọi giả định, điểm chưa rõ, mâu thuẫn hoặc phương án còn phân vân.
   - Đặt câu hỏi xin người dùng xác nhận khi câu trả lời có thể làm thay đổi hành vi, phạm vi hoặc thiết kế.
4. Chờ người dùng đưa ra câu xác nhận rõ ràng trước khi bắt đầu chỉnh sửa code.
5. Chỉ triển khai đúng phạm vi đã được xác nhận. Nếu trong lúc triển khai phát hiện vấn đề mới có thể làm thay đổi đáng kể phạm vi hoặc hướng xử lý, dừng lại và xin xác nhận bổ sung.

Các câu như “được”, “ok”, “đồng ý”, “triển khai đi” hoặc nội dung tương đương được xem là xác nhận khi chúng trả lời trực tiếp cho phương án vừa đề xuất. Nếu người dùng thay đổi yêu cầu thay vì xác nhận, hãy cập nhật lại phần diễn giải và phương án rồi tiếp tục chờ xác nhận.

## Kiểm thử

- Không viết, tạo, sửa hoặc chạy test cho từng yêu cầu, trừ khi người dùng yêu cầu rõ ràng.
- Không tự động chạy toàn bộ test suite sau khi thay đổi code.
- Có thể thực hiện các kiểm tra tĩnh, build, lint, type-check hoặc kiểm tra cú pháp không phải test khi thật sự cần thiết; phải báo trước trong hướng xử lý nếu thao tác đó đáng kể hoặc có thể tốn thời gian.
- Khi bàn giao, nêu rõ rằng test không được thực hiện theo chỉ dẫn của dự án.

## Giao tiếp và phạm vi thay đổi

- Giao tiếp với người dùng bằng tiếng Việt, trừ khi họ yêu cầu ngôn ngữ khác.
- Không tự mở rộng phạm vi, thêm tính năng hoặc chọn một giả định có ảnh hưởng đáng kể khi chưa được xác nhận.
- Không thay đổi các file không liên quan và luôn giữ nguyên các thay đổi sẵn có của người dùng.
- Sau khi triển khai, tóm tắt ngắn gọn những gì đã thay đổi, các file chính bị ảnh hưởng và mọi lưu ý còn lại.
