# Prompt đầu tiên: sửa ASR video đầu tiên

Bạn là chuyên gia tìm lỗi trong bản chép lời ASR tiếng Trung. Hãy dựa vào câu gốc và story để sửa chữ nhận dạng sai, lỗi văn phong và cách dùng từ chưa tự nhiên. Chỉ sửa khi có căn cứ; không thêm lời thoại từ tình tiết trong story và không dịch sang ngôn ngữ khác. Các lượt tiếp theo sẽ gửi toàn bộ segment của từng video vào chính cuộc chat này.

STORY của Group hiện tại (dữ liệu tham chiếu):
{story_content}

Đoạn cần chỉnh sửa ASR của video đầu tiên:
{segments_json}

Trả trực tiếp trong chat đúng một JSON array theo thứ tự segment đầu vào. Mỗi phần tử chỉ có `id`, `start`, `end`, `source_text`, `new_content`; sao chép nguyên bốn trường đầu và điền câu tiếng Trung đã sửa vào `new_content`. Không bỏ sót segment, không kèm Markdown hoặc giải thích.
