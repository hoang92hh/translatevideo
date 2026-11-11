# Dịch phân cảnh trong đoạn của video


{topic_content}

{story_intro_section}
## B. Story của đoạn hiện tại

{story_content}

## C. Dịch block mới

Đọc file `{input_filename}` và chỉ dịch `segments_to_translate` từ {source_language} sang {target_language}. Dùng story và quy tắc ở trên để hiểu nhân vật, quan hệ, tên riêng, thuật ngữ, cách xưng hô và văn phong. Dịch đúng, đủ nghĩa và tự nhiên; giữ các ý về phủ định, số lượng, hành động, điều kiện và sắc thái có trong câu nguồn. Tự kiểm tra toàn bộ bản dịch trước khi trả kết quả. Không bịa thêm tình tiết từ story.

Chỉ gộp nội dung dịch của các segment liền kề trong block này nếu chúng là một lời thoại liên tục của cùng người nói và mọi `end_seconds` gốc của segment đứng trước bằng chính xác `start_seconds` của segment tiếp theo. Khi gộp, đặt bản dịch hoàn chỉnh ở ID đầu, để `translated_text` của ID được hấp thụ là chuỗi rỗng; vẫn trả đủ mọi object. Không gộp qua ranh giới block hoặc với segment chỉ dùng làm ngữ cảnh.

## D. QUY ĐỊNH ĐỊNH DẠNG ĐẦU RA — OUTPUT CONTRACT

Trả về đúng MỘT JSON object chỉ có trường `segment_list`:

{"segment_list":[{"id":1,"speaker_id":"SPEAKER_00","source_text":"原文","translated_text":"Bản dịch"}]}

- `segment_list` chứa mỗi ID cần dịch đúng một lần, theo thứ tự gốc. Mỗi đối tượng có đúng bốn trường `id`, `speaker_id`, `source_text`, `translated_text`.
- Giữ nguyên chính xác `id`, `source_text`; giữ `speaker_id` trừ khi có bằng chứng đáng tin cậy để sửa và chỉ dùng nhãn đã tồn tại trong dữ liệu đầu vào.
- Không trả ID chỉ dùng làm ngữ cảnh, timestamp, phân tích, giải thích hoặc trường bổ sung.
- Không bọc JSON trong Markdown.
