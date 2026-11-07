# Dịch và review segment — Batch cũ ChatGPT

Đọc hồ sơ phân tích của chính video và toàn bộ file JSON đầu vào trước khi trả lời. Dùng hồ sơ để giữ nhất quán chủ đề, nhân vật, tên riêng, thuật ngữ và cách xưng hô.

Trong cùng một tác vụ, hãy dịch toàn bộ `segments_to_translate` từ `{source_language}` sang `{target_language}`, sau đó tự review và sửa bản dịch: đúng nghĩa, đủ nghĩa, tự nhiên, đúng bối cảnh, sắc thái, nhân vật và không thêm nội dung không có trong nguồn.

Chỉ trả JSON array trực tiếp trong chat theo output contract hiện tại, mỗi ID đích đúng một lần, giữ nguyên `id`, `speaker_id` và `source_text`. Không trả phân tích, story, timestamp, Markdown hoặc trường khác.

Hồ sơ phân tích video:
{story_content}

Đọc file `{input_filename}` và trả JSON array trực tiếp trong chat.
