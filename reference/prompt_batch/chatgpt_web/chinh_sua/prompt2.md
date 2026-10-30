# Prompt tiếp theo: sửa ASR video kế tiếp

Tiếp tục bối cảnh ở trên và chỉnh sửa các câu tiếng Trung của video tiếp theo. Xử lý lỗi văn phong khi dùng từ ngữ tiếng Trung, ASR; chỉ sửa khi có căn cứ, không thêm lời thoại từ story và không dịch sang ngôn ngữ khác.

Đoạn cần chỉnh sửa ASR:
{segments_json}

Trả trực tiếp trong chat đúng một JSON array theo thứ tự segment đầu vào. Mỗi phần tử chỉ có `id`, `start`, `end`, `source_text`, `new_content`; sao chép nguyên bốn trường đầu và điền câu tiếng Trung đã sửa vào `new_content`. Không bỏ sót segment, không kèm Markdown hoặc giải thích.
