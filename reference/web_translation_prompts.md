# Web translation prompts

Chỉnh nội dung dưới từng tiêu đề nếu cần. Giữ nguyên đúng ba tiêu đề `ANALYSIS`, `TRANSLATION`, `REVIEW` và các biến trong dấu ngoặc nhọn.

## ANALYSIS

Đọc toàn bộ transcript, tuân thủ chỉ dẫn/tài liệu của Project và tạo hồ sơ hội thoại ngắn gọn: vai trò người nói, quan hệ/cách xưng hô, thuật ngữ lặp lại, tên riêng chuẩn và các điểm chưa chắc có ảnh hưởng đến bản dịch. Chỉ dùng bằng chứng có trong dữ liệu, không suy diễn.

## TRANSLATION

Dịch chỉ các `segments_to_translate` từ {source_language} sang {target_language}. Tuân thủ hồ sơ hội thoại, chỉ dẫn và tài liệu của Project; giữ đúng ý nghĩa, tên riêng, cách xưng hô và ngữ cảnh. Trả mỗi ID đích đúng một lần, không trả ID ngữ cảnh.

## REVIEW

Kiểm tra toàn bộ bản dịch với nguồn và ngữ cảnh Project. Chỉ trả các ID có lỗi thực sự về ý nghĩa, tên riêng, xưng hô, thuật ngữ hoặc tính liên tục; không viết lại chỉ vì phong cách. Đầu ra bắt buộc là `[]` hoặc một JSON array correction `[{"id":1,"corrected_text":"...","issue_type":"...","reason":"..."}]`; không bọc array trong object hay trường `corrections`.
