# Prompt Step 3 cho batch theo video

Mỗi video được chia thành các đoạn theo `video_story_group_size` trong thiết lập project. Đoạn cuối có số segment không quá 50% kích thước đặt trước sẽ được gộp vào đoạn liền trước.

Story của từng đoạn được tạo bằng toàn bộ nội dung template `.md` đang chọn tại
**Thiết lập project → Step 3**, lấy từ `../prompt_template/<provider>/`.
DeepSeek dùng chung template của Google Gemini. Chế độ người nói cũng dùng template
này, không có prompt story riêng. Template cần yêu cầu tạo hồ sơ câu chuyện; ứng dụng
nhận nội dung qua trường `story_markdown` và giữ story trong bộ nhớ khi batch chạy.
Một câu giới thiệu câu chuyện và nhân vật chính được lấy từ phần tổng quan của story
đoạn đầu, rồi dùng cho các đoạn tiếp theo trong cùng video.

`translation.md` (hoặc `translation_speaker.md` khi bật chế độ người nói) dịch các
phân cảnh trong đoạn. Prompt nhận `{story_content}` và `{story_intro_section}`
(trống ở đoạn đầu), không chèn lại nội dung template chủ đề. Kích thước phân cảnh
dùng `Segments / batch` của project. Khi chuyển video, story và câu giới thiệu được tạo lại.
