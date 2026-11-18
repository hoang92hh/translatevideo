# Dịch batch Step 3 — Google Gemini

{translation_rules_section}

## A. Tài liệu bối cảnh tổng thể và quy ước dịch

{story_content}

## B. Dịch các câu trong batch hiện tại

### 1. Dữ liệu cần dịch

File JSON `{input_filename}` chứa `segments_to_translate`.

### 2. Nguyên tắc dịch

Dịch từng `source_text` sang tiếng Việt chính xác, tự nhiên, ngắn gọn và phù hợp với thể loại, bối cảnh, sắc thái của câu chuyện.

**Lời thoại tiếng Trung là nguồn chính. Story là tài liệu tham chiếu để chuẩn hóa và giải quyết những điểm dễ nhầm, không phải nguồn để viết lại nội dung câu thoại.**

Áp dụng các quy tắc:

1. **Dịch theo nguyên văn và ngữ cảnh gần:** Hiểu câu thoại dựa trên `source_text`, các câu liền trước và sau, `context_before`, `context_after` và `previous_translations` nếu có. Giữ đúng ý nghĩa, chủ thể, đối tượng, phủ định, câu hỏi, hành động và sắc thái. Không thêm hoặc suy diễn nội dung từ Story.

2. **Thống nhất tên riêng và thuật ngữ:** Tham chiếu Story để sử dụng nhất quán tên nhân vật, địa danh, tổ chức, danh xưng và thuật ngữ chuyên biệt đã được xác nhận. Không tùy tiện thay đổi cách viết hoặc cách dịch giữa các segment và batch.

3. **Xưng hô đúng quan hệ:** Dịch đại từ và cách gọi theo người nói, người nghe, vai vế và tình huống hội thoại. Tham chiếu quy tắc xưng hô trong Story khi cần xác định hoặc kiểm tra cách gọi. Phân biệt tự xưng, gọi đối phương và nhắc đến người thứ ba. Giữ cách xưng hô nhất quán, chỉ thay đổi khi ngữ cảnh có căn cứ.

4. **Xử lý biến thể và lỗi ASR:** Đối chiếu những tên riêng, cách viết, từ ngữ và biến thể nghi lỗi ASR đã được ghi nhận trong Story. Nếu Story xác nhận cách hiểu hoặc cách thay thế và ngữ cảnh hiện tại phù hợp, sử dụng dạng chuẩn trong `translated_text`, nhưng giữ nguyên `source_text`. Không tự áp dụng những cách sửa còn được đánh dấu chưa xác định.

5. **Kiểm tra cảnh báo theo cụm segment:** Nếu segment hiện tại thuộc một cụm cảnh báo trong Story, đọc các câu liên quan và hướng xử lý để tránh sai xưng hô, nhân vật, đại từ hoặc ngữ nghĩa. Đối chiếu ID với nguyên văn và ngữ cảnh để tránh nhầm các ID trùng. Không mặc định cảnh báo là kết luận chắc chắn khi Story chưa đủ căn cứ.

6. **Xử lý mâu thuẫn:** Khi lời thoại có dấu hiệu mâu thuẫn với tên riêng, quan hệ, thuật ngữ hoặc quy ước đã xác nhận trong Story, kiểm tra lại ngữ cảnh và các biến thể ASR. Ưu tiên lời thoại gốc đáng tin cậy; sử dụng Story để giải quyết sự mơ hồ, không ép lời thoại phải khớp với một suy luận trong Story.

7. **Ngôn ngữ và văn phong:** Dịch tiếng Việt tự nhiên, đúng phong cách nhân vật và thế giới câu chuyện. Không dịch cứng từng chữ, không tự thêm đại từ không cần thiết, không sử dụng từ Hán Việt cầu kỳ hoặc ngôn ngữ hiện đại/cổ trang không phù hợp. Giữ các sắc thái đặc biệt và cách dùng từ quan trọng đã được ghi nhận trong Story.

8. **Nhất quán xuyên batch:** Tham khảo `previous_translations` để duy trì tên gọi, cách xưng hô và thuật ngữ đã sử dụng. Không kế thừa lỗi rõ ràng từ bản dịch trước nếu mâu thuẫn với nguồn hoặc thông tin đã xác nhận.

**Trước khi xuất kết quả:** Rà soát toàn batch, tập trung vào tên riêng, xưng hô, thuật ngữ, biến thể ASR và những segment có cảnh báo. Chỉ điều chỉnh khi có căn cứ; không tự viết lại những câu đã dịch đúng.

## C. QUY ĐỊNH ĐỊNH DẠNG ĐẦU RA — OUTPUT CONTRACT

Trả về đúng MỘT JSON object chỉ có trường `segment_list`:

{"segment_list":[{"id":1,"speaker_id":"SPEAKER_00","source_text":"原文","translated_text":"Bản dịch"}]}

- `segment_list` chứa mỗi ID cần dịch đúng một lần, theo thứ tự gốc. Mỗi đối tượng có đúng bốn trường `id`, `speaker_id`, `source_text`, `translated_text`.
- Giữ nguyên chính xác `id`, `source_text`; giữ `speaker_id` trừ khi có bằng chứng đáng tin cậy để sửa và chỉ dùng nhãn đã tồn tại trong dữ liệu đầu vào.
- Chỉ dịch nội dung thuộc từng segment, không gộp, tách hoặc chuyển nội dung sang segment khác.
- Không trả ID chỉ dùng làm ngữ cảnh, timestamp, phân tích, giải thích hoặc trường bổ sung.
- Không bọc JSON trong Markdown. Chỉ trả về JSON hợp lệ.
