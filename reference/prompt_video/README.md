# Prompt Step 3 cho batch tuần tự theo video

`google_gemini/story.md` tạo story riêng từ toàn bộ kịch bản của một video. Story chỉ được giữ trong bộ nhớ của lượt chạy.

`google_gemini/first_block.md` dịch block segment đầu tiên. `google_gemini/next_block.md` dịch các block tiếp theo. Mỗi block trả `Noi_dung` là bối cảnh cụ thể của chính block đó; giá trị này trở thành `prev` của block kế tiếp. Khi sang video khác, cả story và prev đều được tạo lại.

`{topic_content}` là nội dung prompt type đã chọn trong Thiết lập project → Step 3. Các prompt dịch nhận thêm `{story_content}`, `{previous_segment_summary}` và thông tin batch. Chỉ riêng luồng Google Gemini của popup chạy batch tuần tự sử dụng các file này.
