# Dịch batch Step 3 — Google Gemini

{topic_content}

## B. Hồ sơ nhân vật và diễn biến phân đoạn trước đó

### 1. Hồ sơ nhân vật
{story_content}

### 2. Phân đoạn trước đó
{previous_segment_context}


## C. Dịch các câu trong phân đoạn mới

### 1. File JSON chứa nội dung cần dịch

File `{input_filename}` chứa `segments_to_translate`.


### 2. Dịch các giá trị `source_text` sang tiếng Việt

- Đọc toàn bộ `segments_to_translate`, hồ sơ nhân vật và diễn biến phân đoạn trước ở mục B (nếu có) để hiểu mạch hội thoại và sự kiện đang tiếp diễn.
- Dịch lần lượt từng `source_text`. Trước khi viết câu dịch, xác định người nói, người được nhắc đến, quan hệ và ý nghĩa câu nguồn; chọn cách xưng hô, tên riêng, danh xưng và thuật ngữ theo quy tắc chủ đề ở đầu prompt (mục A nếu có) và thông tin đã xác minh ở mục B.
- Dùng những từ ngữ đã chọn để tạo thành câu dịch đúng và đủ nghĩa. Giữ nguyên phủ định, câu hỏi, mệnh lệnh, hành động và sắc thái có căn cứ trong câu nguồn.
- Sau khi câu đã đúng nghĩa, điều chỉnh cách diễn đạt cho tự nhiên, phù hợp văn phong và toàn phân cảnh. Dùng diễn biến trước để giữ mạch câu chuyện, không thêm tình tiết mà câu nguồn không thể hiện.
- Hoàn thành từng câu theo trình tự trên trước khi chuyển sang câu tiếp theo. Cuối batch, kiểm tra lại tính nhất quán của toàn bộ bản dịch.

### 3. CHỈ được gộp các segment đích liền kề khi đồng thời đáp ứng tất cả điều kiện sau

- Các segment rõ ràng tạo thành một lời thoại liên tục của cùng một người nói.
- Giá trị `end_seconds` gốc của **MỌI segment đứng trước** phải bằng **CHÍNH XÁC** giá trị `start_seconds` của segment ngay sau.
- Cả hai segment đều thuộc batch hiện tại.

Bất kỳ khoảng trống thời gian, sự chồng lấn thời gian, lời thoại độc lập hoặc sự không chắc chắn về người nói đều không cho phép gộp.

Không bao giờ gộp với các segment chỉ dùng làm ngữ cảnh hoặc gộp qua ranh giới giữa các batch.

Khi việc gộp hợp lệ:

- Đặt toàn bộ bản dịch hoàn chỉnh vào ID đầu tiên.
- Trả về `translated_text` rỗng cho các ID đã được gộp vào ID đầu tiên.
- Giữ lại đầy đủ mọi đối tượng đầu ra và giá trị `source_text` gốc.

### 4. Tổng hợp lại nội dung của phân đoạn mới

## D. QUY ĐỊNH ĐỊNH DẠNG ĐẦU RA — OUTPUT CONTRACT

Trả về đúng MỘT JSON object có chính xác hai trường:

{"Noi_dung":"Tóm tắt diễn biến phân đoạn mới","segment_list":[{"id":1,"speaker_id":"SPEAKER_00","source_text":"原文","translated_text":"Bản dịch"}]}

- `Noi_dung` là chuỗi không rỗng, tóm tắt phân đoạn hiện tại.
- `segment_list` chứa mỗi ID cần dịch đúng một lần, theo thứ tự gốc. Mỗi đối tượng có đúng bốn trường `id`, `speaker_id`, `source_text`, `translated_text`.
- Giữ nguyên chính xác `id`, `source_text`; giữ `speaker_id` trừ khi có bằng chứng đáng tin cậy để sửa và chỉ dùng nhãn đã tồn tại trong dữ liệu đầu vào.
- Không trả ID chỉ dùng làm ngữ cảnh, timestamp, phân tích, giải thích hoặc trường bổ sung.
- Không bọc JSON trong Markdown.
