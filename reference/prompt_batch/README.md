# Prompt dịch batch Step 3 theo story

Thư mục này nằm cùng cấp `prompt_template/`, dành riêng cho việc chuẩn bị prompt của nút
**Step 3** trong popup **Chạy batch theo step**. Không xuất hiện trong danh sách prompt dịch thường.

- `google_gemini/translation.md`: prompt cho Gemini API.
- `chatgpt_web/translation.md`: prompt cho ChatGPT Web, giữ contract trả file JSON tải xuống.

Ứng dụng đọc nguyên văn file rồi điền các biến:

- `{story_group}`, `{story_name}`, `{story_content}`: Group, tên và nội dung story được liên kết
  với video; nội dung được chốt khi bắt đầu batch.
- `{video_id}`, `{video_order}`, `{video_name}`, `{video_filename}`: video đang dịch và STT hiện tại.
- `{source_language}`, `{target_language}`: cặp ngôn ngữ trong project.
- `{batch_index}`, `{batch_total}`, `{segment_count}`, `{segment_start}`, `{segment_end}`: lượt dịch và ID đích.
- `{input_filename}`, `{output_filename}`: tên input/output theo cơ chế Step 3 hiện tại.

Danh sách `segments_to_translate`, ngữ cảnh trước/sau và các trường hỗ trợ vẫn nằm trong input JSON
đính kèm như luồng dịch thường. Story được điền trực tiếp trong prompt, không sao chép vào từng
segment. AI đọc toàn bộ story và đoạn hiện tại, phân tích nội bộ rồi chỉ trả bản dịch cuối cùng.
Các biến chỉ được thay một lần để giữ nguyên dấu ngoặc/biến xuất hiện trong story do người dùng sửa.

Chia segment vẫn tuân theo thiết lập project, gồm `Segments / batch` và tùy chọn dịch toàn bộ
segment trong một lượt. Không thêm request AI phân tích riêng. Việc gửi/nhận, retry, kiểm tra ID,
speaker, xử lý segment rỗng và lưu candidate dùng chung implementation Step 3.

Story mới của Step 2 ghi Group/STT/tên file/mã video nguồn. Group được tính từ STT chung và
trường **Số video mỗi Group** trong **Thiết lập project → Project & Input**. Step 3 tự lấy story
mới nhất đã liên kết với Group của từng video. Khi đổi thứ tự hoặc kích thước Group, liên kết cũ
được xóa để không dùng sai bối cảnh. Không tạo JSON ánh xạ câu.
