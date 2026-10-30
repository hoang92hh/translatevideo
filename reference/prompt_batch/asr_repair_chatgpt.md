# Sửa transcript ASR tiếng Trung trong cuộc chat hiện tại

Đọc toàn bộ JSON đầu vào `{input_filename}`. Story của Group và topic_content đã được gửi trong cuộc chat này. `segments` là toàn bộ câu nhận dạng giọng nói của một video theo thứ tự thời gian; mỗi segment có `id`, `start`, `end`, `source_text`.

Xử lý lỗi văn phong khi dùng từ ngữ tiếng Trung, ASR. Sửa chữ Hán bị nhận sai và cách dùng từ chưa tự nhiên dựa trên câu gốc, các câu kề nhau, timeline, story và topic_content. Đây là chỉnh sửa bản chép lời tiếng Trung, không phải dịch thuật. Không thêm lời thoại chỉ vì story nhắc đến tình tiết đó; nếu thiếu căn cứ để sửa, giữ nguyên `source_text`.

Sửa nội dung cho mọi segment, kể cả câu có thể bị lặp; không tự đặt `new_content` rỗng. Ứng dụng sẽ đối chiếu các câu liền kề sau khi nhận phản hồi và xử lý câu trùng.

Trả ngay trong chat đúng một JSON array theo thứ tự đầu vào, mỗi ID đúng một lần: `[{"id":1,"start":0.0,"end":1.0,"source_text":"原文","new_content":"中文"}]`. Sao chép chính xác `id`, `start`, `end`, `source_text` từ từng segment đầu vào; chỉ thêm `new_content`. Không thêm hay xóa phần tử, không kèm giải thích, Markdown hoặc bản dịch.
