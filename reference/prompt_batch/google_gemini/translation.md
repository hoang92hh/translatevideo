# Dịch batch Step 3 — Google Gemini

{topic_content}

## B. Tài liệu bối cảnh tổng thể và quy ước dịch của câu chuyện

{story_content}

## C. Dịch các câu trong batch hiện tại

### 1. File JSON chứa nội dung cần dịch

File `{input_filename}` chứa `segments_to_translate`.


### 2. Dịch các giá trị `source_text` sang tiếng Việt

- Đọc toàn bộ tài liệu ở mục B trước khi dịch `segments_to_translate`. Đây là bối cảnh xuyên suốt câu chuyện, gồm tổng quan diễn biến, hồ sơ nhân vật và quan hệ, quy tắc xưng hô, từ điển tên riêng và thuật ngữ, ngữ cảnh quan trọng cùng các điểm chưa xác định hoặc nghi lỗi ASR. Đối chiếu từng câu cần dịch với những phần liên quan của tài liệu; không coi tài liệu này là lời thoại cần dịch.
- Với từng `source_text`, dùng diễn biến và quan hệ nhân vật trong tài liệu để hiểu ai đang nói, nói với ai, nhắc đến ai và ý định của câu. Dùng `context_before`, `context_after`, `previous_translations` và thông tin người nói trong JSON đầu vào (nếu có) để xác định tình huống cụ thể tại thời điểm câu xuất hiện. Các segment ngữ cảnh không phải là câu cần trả về.
- Áp dụng mục quy tắc xưng hô theo đúng quan hệ, vai vế, sắc thái và điều kiện sử dụng; phân biệt tự xưng, gọi đối phương và nhắc đến người thứ ba. Không áp dụng một cách xưng hô cố định khi quan hệ hoặc tình huống đã thay đổi.
- Đối chiếu tên nhân vật, bí danh, địa danh, tổ chức, chức danh và thuật ngữ với từ điển Trung–Việt trong tài liệu và quy tắc chủ đề ở đầu prompt (nếu có). Giữ một cách dịch nhất quán cho thực thể đã được xác nhận; không biến biến thể nghi lỗi ASR hoặc thông tin được đánh dấu chưa xác định thành tên hay nghĩa chắc chắn. Khi thiếu căn cứ, bám sát chữ Hán quan sát được và ngữ cảnh gần, không tự tạo tên hoặc địa danh.
- Dùng phần khái niệm và ngữ cảnh quan trọng để phân biệt các nghĩa dễ nhầm, nhưng lấy `source_text` làm căn cứ quyết định nội dung câu dịch. Giữ đúng phủ định, câu hỏi, mệnh lệnh, hành động, số lượng và sắc thái; không thêm tình tiết chỉ vì tài liệu bối cảnh có nhắc đến.
- Viết tiếng Việt tự nhiên, gọn và phù hợp giọng nói của nhân vật; tránh dịch từng chữ khi gây gượng. Trước khi trả kết quả, rà lại từng câu và tính nhất quán về xưng hô, tên riêng, địa danh và thuật ngữ trong toàn batch với tài liệu bối cảnh.

## D. QUY ĐỊNH ĐỊNH DẠNG ĐẦU RA — OUTPUT CONTRACT

Trả về đúng MỘT JSON object chỉ có trường `segment_list`:

{"segment_list":[{"id":1,"speaker_id":"SPEAKER_00","source_text":"原文","translated_text":"Bản dịch"}]}

- `segment_list` chứa mỗi ID cần dịch đúng một lần, theo thứ tự gốc. Mỗi đối tượng có đúng bốn trường `id`, `speaker_id`, `source_text`, `translated_text`.
- Giữ nguyên chính xác `id`, `source_text`; giữ `speaker_id` trừ khi có bằng chứng đáng tin cậy để sửa và chỉ dùng nhãn đã tồn tại trong dữ liệu đầu vào.
- Không trả ID chỉ dùng làm ngữ cảnh, timestamp, phân tích, giải thích hoặc trường bổ sung.
- Không bọc JSON trong Markdown.
