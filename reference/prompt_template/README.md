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
