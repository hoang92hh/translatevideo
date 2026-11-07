# Giai đoạn 2 — Dịch block đầu tiên của video

## A. Quy tắc theo prompt type đã chọn

{topic_content}

## B. Story của video hiện tại

{story_content}

## C. Dịch block mới

Đọc file `{input_filename}` và chỉ dịch `segments_to_translate` từ {source_language} sang {target_language}. Dùng story và quy tắc ở trên để hiểu nhân vật, quan hệ, tên riêng, thuật ngữ, cách xưng hô và văn phong. Dịch đúng, đủ nghĩa và tự nhiên; giữ các ý về phủ định, số lượng, hành động, điều kiện và sắc thái có trong câu nguồn. Tự kiểm tra toàn bộ bản dịch trước khi trả kết quả. Không bịa thêm tình tiết từ story.

Chỉ gộp nội dung dịch của các segment liền kề trong block này nếu chúng là một lời thoại liên tục của cùng người nói và mọi `end_seconds` gốc của segment đứng trước bằng chính xác `start_seconds` của segment tiếp theo. Khi gộp, đặt bản dịch hoàn chỉnh ở ID đầu, để `translated_text` của ID được hấp thụ là chuỗi rỗng; vẫn trả đủ mọi object. Không gộp qua ranh giới block hoặc với segment chỉ dùng làm ngữ cảnh.

Tổng hợp bối cảnh **cụ thể của block này** vào `Noi_dung`: diễn biến, đối tượng đang nói, quan hệ, cách xưng hô và các dữ kiện cần thiết để hiểu block kế tiếp. Bối cảnh này sẽ là `prev` cho block sau; không lặp lại toàn bộ story.

## D. Output contract của giai đoạn này

Trả đúng một JSON object có `Noi_dung` là chuỗi không rỗng và `segment_list` là danh sách các object có đúng bốn trường `id`, `speaker_id`, `source_text`, `translated_text`. Mỗi ID đích xuất hiện đúng một lần, đúng thứ tự. Giữ nguyên `id` và `source_text`; chỉ sửa `speaker_id` khi có bằng chứng đáng tin và chỉ dùng nhãn đã có trong đầu vào. Không trả ID ngữ cảnh, timestamp hoặc trường khác. Không bọc JSON trong Markdown. Quy định output ở đây được ưu tiên nếu prompt type có định dạng khác.
