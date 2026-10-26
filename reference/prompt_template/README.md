# Thư viện Prompt Step 3

Prompt được quản lý độc lập theo provider trong ba thư mục:

- `google_gemini/`
- `chatgpt_web/`
- `claude_web/`

Mỗi file Markdown là một template có tên hiển thị ở tiêu đề cấp 1 và một phần prompt duy nhất:

```markdown
# Tên prompt

## PROMPT

Đọc file đính kèm, phân tích ngữ cảnh, dịch từ {source_language} sang
{target_language}, tự kiểm tra và chỉ trả kết quả cuối theo OUTPUT CONTRACT.
```

Phần `PROMPT` quản lý cả chỉ dẫn nghiệp vụ, `OUTPUT CONTRACT` và `ATTACHED INPUT`. Ứng dụng thay thế các biến sau khi chạy:

- `{source_language}`, `{target_language}`
- `{input_filename}`, `{output_filename}`
- `{segment_start}`, `{segment_end}`, `{segment_count}`
- `{batch_index}`, `{batch_total}`

Tên file được tạo từ ID đầu và cuối thực tế của từng batch. Code vẫn kiểm tra JSON schema và danh sách ID sau khi provider trả kết quả. Với Gemini API, ứng dụng lưu JSON response thành `{output_filename}`; với ChatGPT Web và Claude Web, template có thể yêu cầu provider trả file tải xuống đúng tên.

Template cũ có ba phần vẫn đọc được: ứng dụng lấy nội dung `TRANSLATION` làm prompt duy nhất. Khi lưu lại trong popup, file được chuyển sang định dạng `PROMPT` mới.

Mỗi project chỉ lưu provider và mã template đã chọn. Popup **Quản lý Prompt** trên màn hình chính là nơi thêm, sửa và xóa template; màn hình **Thiết lập project** chỉ lựa chọn template có sẵn.

## Prompt tạo story sau batch Step 2

Các prompt tiếng Việt dùng riêng cho tác vụ viết hồ sơ câu chuyện được lưu tại:

- `google_gemini/story/story.md`
- `chatgpt_web/story/story.md`

Các file này được đọc nguyên văn bởi tác vụ tạo story, không phải template dịch và không
xuất hiện trong danh sách prompt dịch của Step 3. Đầu vào chỉ gồm ngôn ngữ nguồn và các
video theo thứ tự, mỗi video có `video_order`, `video_id`, `video_filename` và danh sách `source_text`; không có speaker,
timestamp hoặc ánh xạ ID câu. AI trả nội dung Markdown tiếng Việt qua trường `story_markdown`,
ứng dụng lưu thành một file `stories/story_<min>_<max>.md` trong project, không lưu JSON ánh xạ.
Tác vụ dùng provider/model được chọn trong thiết lập Step 3 nhưng không chạy hay sửa luồng dịch.

Prompt dịch theo story trong popup batch được quản lý riêng tại `../prompt_batch/`.
