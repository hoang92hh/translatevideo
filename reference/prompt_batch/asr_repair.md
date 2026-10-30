# Sửa transcript ASR tiếng Trung theo story

Đọc toàn bộ JSON đầu vào `{input_filename}`. `story_content` là bối cảnh câu chuyện của Group; `segments` là các câu nhận dạng giọng nói của một video, theo thứ tự thời gian. Mỗi segment có `id`, `start`, `end`, `source_text`.

Nhiệm vụ: sửa các chữ Hán bị ASR nhận sai từ giọng nói, dựa trên câu gốc, các câu kề nhau, timeline và story. Trả câu đã sửa bằng tiếng Trung trong `new_content`. Đây là chỉnh sửa bản chép lời, không phải dịch thuật. Không thêm lời thoại chỉ vì story nhắc đến tình tiết đó; nếu thiếu căn cứ để sửa, giữ nguyên `source_text`. Giữ cách viết chữ Hán và dấu câu phù hợp.

Sửa nội dung cho mọi segment, kể cả những câu có thể bị lặp; không tự đặt `new_content` rỗng. Ứng dụng sẽ đối chiếu các câu liền kề sau khi nhận phản hồi: nếu trùng trên 80% và câu dài hơn có trên 4 ký tự, ứng dụng giữ câu dài hơn và đặt `new_content` của câu còn lại thành chuỗi rỗng. Nếu dài bằng nhau, giữ câu đứng trước.

Trả đúng một JSON array theo thứ tự đầu vào, mỗi ID đúng một lần: `[{"id":1,"start":0.0,"end":1.0,"source_text":"原文","new_content":"中文"}]`. Sao chép chính xác `id`, `start`, `end`, `source_text` từ từng segment đầu vào; chỉ thêm `new_content`. Không thêm hay xóa phần tử, không kèm giải thích, Markdown hoặc bản dịch.
