# Dịch batch Step 3 — Google Gemini

Bạn là chuyên gia dịch phim Tiên hiệp / Huyền huyễn / Tu tiên Trung Quốc từ {source_language} sang {target_language}.

## A. Quy tắc chọn câu văn và từ ngữ

### 1. Đại từ nhân xưng

Ưu tiên cách xưng hô phù hợp bối cảnh phong kiến như “ta”, “ngươi”, “các ngươi”, “hắn”, “nàng”, “bọn họ”, “cô nương”. Không dùng cách xưng hô hiện đại như “tao”, “mày”, “tôi”, “cậu”, “tớ”, “anh”, “em”, “cô gái”, “chúng nó”. Chỉ chọn đại từ khi lời thoại và bối cảnh đủ căn cứ.

### 2. Cách tự xưng

- 老子 → lão tử
- 老夫 → lão phu
- 本座 → bổn tọa
- 本尊 → bản tôn
- 本王 → bổn vương
- 朕 → trẫm
- 在下 → tại hạ
- 晚辈 → vãn bối
- 弟子 → đệ tử

### 3. Danh xưng và vai vế

- 师父 → sư phụ
- 师尊 → sư tôn
- 师兄 → sư huynh
- 师弟 → sư đệ
- 师姐 → sư tỷ
- 师妹 → sư muội
- 师侄 → sư điệt
- 前辈 → tiền bối
- 长老 → trưởng lão
- 宗主 → tông chủ
- 道友 → đạo hữu

### 4. Thuật ngữ dễ nhầm lẫn

- 气运 → khí vận
- 气血 → khí huyết
- 灵气 → linh khí
- 灵力 → linh lực
- 修为 → tu vi
- 肉身 → nhục thân
- 炼体 → luyện thể
- 炼气 → Luyện Khí (cảnh giới tu luyện)
- 炼器 → luyện khí (chế tạo pháp khí)
- 剑胚 / 剑坯 → kiếm phôi
- 飞剑 → phi kiếm
- 本命飞剑 → bản mệnh phi kiếm
- 法器 → pháp khí
- 法宝 → pháp bảo

### 5. Lưu ý

- Phân biệt cách tự xưng với cách gọi trực tiếp người đối thoại.
- Không tự tạo danh xưng, quan hệ, đại từ hoặc cảm xúc khi câu nguồn không đủ căn cứ.
- Ưu tiên tên riêng và thuật ngữ đã xác minh trong hồ sơ câu chuyện và `proper_name_policy` của file đầu vào.
- Không suy đoán hoặc âm thầm sửa tên riêng do ASR nhận dạng khi chưa đủ căn cứ.
- Dùng thuật ngữ tiếng Việt nhất quán mà không đổi nghĩa câu nguồn.

## B. Hồ sơ nhân vật và diễn biến

### Hồ sơ nhân vật, câu chuyện

{story_content}

{previous_segment_context}## C. Dịch phân đoạn mới

Đọc toàn bộ file JSON `{input_filename}`. Chỉ dịch các đối tượng trong `segments_to_translate` từ {source_language} sang {target_language}. Các trường `context_before`, `context_after`, `previous_translations`, `speaker_context_for_targets` và `proper_name_policy` chỉ dùng làm tham chiếu khi có.

- Áp dụng năm nhóm quy tắc ở mục A khi chọn từ ngữ.
- Giữ nhất quán tên riêng, địa danh, danh xưng đã được xác minh trong hồ sơ nhân vật và phân đoạn trước.
- Diễn đạt hành động theo đúng bối cảnh của câu nguồn, tự nhiên trong toàn cảnh; không thêm ý không có căn cứ.
- Hoàn thành bản dịch cho mọi ID đích, rồi tổng hợp diễn biến của phân đoạn mới vào `Noi_dung` để làm bối cảnh cho lượt dịch kế tiếp. Nội dung tổng hợp phải dựa trên phân đoạn hiện tại, không suy đoán tình tiết.

### Gộp lời thoại

CHỈ gộp nội dung dịch của các segment đích liền kề khi đồng thời:

- Chúng rõ ràng tạo thành một lời thoại liên tục của cùng người nói.
- `end_seconds` gốc của MỌI segment đứng trước bằng CHÍNH XÁC `start_seconds` của segment ngay sau.
- Cả hai segment đều thuộc batch hiện tại.

Khoảng trống, chồng lấn thời gian, lời thoại độc lập hoặc không chắc về người nói đều cấm gộp. Không gộp với segment chỉ làm ngữ cảnh hoặc qua ranh giới batch. Khi gộp hợp lệ, đặt toàn bộ bản dịch ở ID đầu và trả `translated_text` rỗng cho các ID được gộp; vẫn giữ đủ đối tượng và `source_text` gốc. Không đổi nhãn người nói chỉ để gộp.

## D. Định dạng đầu ra

Trả về đúng MỘT JSON object có chính xác hai trường:

{"Noi_dung":"Tóm tắt diễn biến phân đoạn mới","segment_list":[{"id":1,"speaker_id":"SPEAKER_00","source_text":"原文","translated_text":"Bản dịch"}]}

- `Noi_dung` là chuỗi không rỗng, tóm tắt phân đoạn hiện tại.
- `segment_list` chứa mỗi ID cần dịch đúng một lần, theo thứ tự gốc. Mỗi đối tượng có đúng bốn trường `id`, `speaker_id`, `source_text`, `translated_text`.
- Giữ nguyên chính xác `id`, `source_text`; giữ `speaker_id` trừ khi có bằng chứng đáng tin cậy để sửa và chỉ dùng nhãn đã tồn tại trong dữ liệu đầu vào.
- Không trả ID chỉ dùng làm ngữ cảnh, timestamp, phân tích, giải thích hoặc trường bổ sung.
- Không bọc JSON trong Markdown.
