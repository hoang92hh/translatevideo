# Prompt dịch batch Step 3 theo story

Thư mục này nằm cùng cấp `prompt_template/`, dành riêng cho việc chuẩn bị prompt của nút
**Step 3** trong popup **Chạy batch theo step**. Không xuất hiện trong danh sách prompt dịch thường.

- `google_gemini/translation.md`: prompt cho Gemini API.
- `chatgpt_web/translation.md`: prompt cho ChatGPT Web, giữ contract trả file JSON tải xuống.

Ứng dụng đọc nguyên văn file rồi điền các biến:

- `{story_name}`, `{story_content}`: tên và nội dung story đang chọn, được chốt khi bắt đầu batch.
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

Story mới của Step 2 ghi STT/tên file/mã video nguồn. STT lấy từ thứ tự chung trong
**Thiết lập project → Project & Input**, giống Videos, Outputs và Batch theo step.
STT có thể đổi khi người dùng sắp xếp lại tại màn hình thiết lập;
prompt yêu cầu đối chiếu danh tính video và nội dung, không tự gán tình huống khi story cũ
chưa đủ thông tin. Không tạo JSON ánh xạ câu.
