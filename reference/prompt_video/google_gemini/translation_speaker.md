# Dịch phân cảnh trong đoạn của video theo speaker


{story_intro_section}
## B. Story của đoạn hiện tại

{story_content}

## C. Dịch block mới

Đọc file `{input_filename}` và chỉ dịch `segments_to_translate` từ {source_language} sang {target_language}. Dùng story và quy tắc ở trên để hiểu nhân vật, quan hệ, tên riêng, thuật ngữ, cách xưng hô và văn phong. Dịch đúng, đủ nghĩa và tự nhiên; giữ các ý về phủ định, số lượng, hành động, điều kiện và sắc thái có trong câu nguồn. Tự kiểm tra toàn bộ bản dịch trước khi trả kết quả. Không bịa thêm tình tiết từ story.

Mỗi phần tử `segments_to_translate` có `speaker_id`. Đọc `speaker_id` cùng `source_text` của chính phần tử đó và tra ánh xạ một ID → một nhân vật trong story. Giữ ID của mỗi segment gắn với câu nguồn tương ứng, kể cả khi gộp nội dung dịch. Với câu có tên hoặc cách gọi trực tiếp, dùng đó để nhận ra người nghe. Khi hai lượt thoại liền kề đổi speaker, mặc định người nói sau đang đáp người nói trước, trừ khi nội dung cho thấy đang nói với người khác; ví dụ `SPEAKER_01` nói rồi `SPEAKER_00` nói thì mặc định `SPEAKER_00` đáp `SPEAKER_01`. Dùng quy tắc xưng hô theo cặp ID người nói → người nghe trong story. Khi nhắc đến nhân vật thứ ba, dùng cách gọi ngôi thứ ba theo ID và tên nhân vật đó, không nhầm với gọi trực tiếp.

`SPEAKER_UNKNOWN` không phải một nhân vật cố định. Nếu lượt trước là `SPEAKER_UNKNOWN`, dùng câu trước, tên được nhắc và ghi chú story để xác định người nghe. Dùng cách gọi mà story đã đề xuất theo thể loại, quan hệ, giới tính và nhóm tuổi có căn cứ; ưu tiên tên hoặc quan hệ thể hiện rõ trong lời thoại. Khi thiếu căn cứ, dùng cách diễn đạt trung tính, không tự đoán tuổi hoặc giới tính và không áp cách gọi hiện đại cho bối cảnh khác.

Chỉ gộp nội dung dịch của các segment liền kề trong block này nếu chúng là một lời thoại liên tục của cùng người nói và mọi `end_seconds` gốc của segment đứng trước bằng chính xác `start_seconds` của segment tiếp theo. Khi gộp, đặt bản dịch hoàn chỉnh ở ID đầu, để `translated_text` của ID được hấp thụ là chuỗi rỗng; vẫn trả đủ mọi object. Không gộp qua ranh giới block hoặc với segment chỉ dùng làm ngữ cảnh.

## D. QUY ĐỊNH ĐỊNH DẠNG ĐẦU RA — OUTPUT CONTRACT

Trả về đúng MỘT JSON object chỉ có trường `segment_list`:

{"segment_list":[{"id":1,"speaker_id":"SPEAKER_00","source_text":"原文","translated_text":"Bản dịch"}]}

- `segment_list` chứa mỗi ID cần dịch đúng một lần, theo thứ tự gốc. Mỗi đối tượng có đúng bốn trường `id`, `speaker_id`, `source_text`, `translated_text`.
- `speaker_id` phải có trong từng phần tử đầu ra và được xử lý cùng nội dung của segment tương ứng; không bỏ trường này khi dịch hoặc gộp câu.
- Giữ nguyên chính xác `id`, `source_text`; giữ `speaker_id` trừ khi có bằng chứng đáng tin cậy để sửa và chỉ dùng nhãn đã tồn tại trong dữ liệu đầu vào.
- Không trả ID chỉ dùng làm ngữ cảnh, timestamp, phân tích, giải thích hoặc trường bổ sung.
- Không bọc JSON trong Markdown.
