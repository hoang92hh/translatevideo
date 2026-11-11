# Prompt Step 3 cho batch theo video

Mỗi video được chia thành các đoạn theo `video_story_group_size` trong thiết lập project. Đoạn cuối có số segment không quá 50% kích thước đặt trước sẽ được gộp vào đoạn liền trước.

`google_gemini/story.md` và `opencode_go/story.md` tạo story riêng cho từng đoạn, theo cấu trúc tham chiếu của `prompt_batch`. Một câu giới thiệu câu chuyện và nhân vật chính được lấy từ phần tổng quan của story đoạn đầu, rồi dùng cho các đoạn tiếp theo trong cùng video.

`translation.md` của từng provider dịch các phân cảnh trong đoạn. Prompt nhận `{topic_content}`, `{story_content}` và `{story_intro_section}` (trống ở đoạn đầu). Kích thước phân cảnh dùng `Segments / batch` của project. Khi chuyển video, story và câu giới thiệu được tạo lại.