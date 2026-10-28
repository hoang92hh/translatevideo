# Prompt cho batch theo step

Thư mục này nằm cùng cấp `prompt_template/`, chứa prompt tạo story và dịch Step 3
trong popup **Chạy batch theo step**. Các prompt này không xuất hiện trong danh sách prompt dịch thường.

- `google_gemini/story.md`: prompt tạo story bằng Gemini API.
- `chatgpt_web/story.md`: prompt tạo story bằng ChatGPT Web.
- `google_gemini/translation.md`: prompt cho Gemini API.
- `chatgpt_web/translation.md`: prompt cho ChatGPT Web, giữ contract trả file JSON tải xuống.

Batch Step 3 với Google Gemini điền `{topic_content}` từ phần `## PROMPT` của template
được chọn tại **Thiết lập project → Step 3** vào `google_gemini/translation.md`.
ChatGPT Web tiếp tục dùng prompt và định dạng phản hồi riêng.
Story mới chỉ lưu nội dung AI trả về; khi nạp story cũ, ứng dụng xóa khối thông tin
Group/video do phiên bản trước tự thêm vào đầu file để không gửi khối đó khi dịch.

Khi tạo story, ứng dụng đọc nguyên văn `story.md` của provider đã chọn và gửi transcript
của toàn bộ video trong Group. Story được lưu tại `stories/story_group_<group>.md`.

Khi dịch Step 3, ứng dụng đọc nguyên văn `translation.md` rồi điền các biến:

- `{story_group}`, `{story_name}`, `{story_content}`: Group, tên và nội dung story được liên kết
  với video; nội dung được chốt khi bắt đầu batch.
- `{video_id}`, `{video_order}`, `{video_name}`, `{video_filename}`: video đang dịch và STT hiện tại.
- `{source_language}`, `{target_language}`: cặp ngôn ngữ trong project.
- `{batch_index}`, `{batch_total}`, `{segment_count}`, `{segment_start}`, `{segment_end}`: lượt dịch và ID đích.
- `{input_filename}`, `{output_filename}`: tên input/output theo cơ chế Step 3 hiện tại.

Danh sách `segments_to_translate`, ngữ cảnh trước/sau và các trường hỗ trợ vẫn nằm trong input JSON
đính kèm như luồng dịch thường. Story được điền trực tiếp trong prompt, không sao chép vào từng
segment. AI đọc toàn bộ story và đoạn hiện tại, phân tích nội bộ rồi chỉ trả bản dịch cuối cùng.
Với Gemini, phản hồi batch là object gồm `Noi_dung` và `segment_list`. Sau khi
`segment_list` được xác thực, ứng dụng lưu `Noi_dung` vào metadata của candidate
và gửi nó trong prompt cùng input JSON của batch kế tiếp. Batch đầu không có phần
diễn biến trước. ChatGPT Web vẫn nhận mảng segment như trước.
Các biến chỉ được thay một lần để giữ nguyên dấu ngoặc/biến xuất hiện trong story do người dùng sửa.

Chia segment vẫn tuân theo thiết lập project, gồm `Segments / batch` và tùy chọn dịch toàn bộ
segment trong một lượt. Không thêm request AI phân tích riêng. Việc gửi/nhận, retry, kiểm tra ID,
speaker, xử lý segment rỗng và lưu candidate dùng chung implementation Step 3.

Story mới của Step 2 ghi Group/STT/tên file/mã video nguồn. Group được tính từ STT chung và
trường **Số video mỗi Group** trong **Thiết lập project → Project & Input**. Step 3 tự lấy story
mới nhất đã liên kết với Group của từng video. Khi đổi thứ tự hoặc kích thước Group, liên kết cũ
được xóa để không dùng sai bối cảnh. Không tạo JSON ánh xạ câu.
