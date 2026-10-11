# Web translation prompts

Chỉnh nội dung dưới từng tiêu đề nếu cần. Giữ nguyên đúng ba tiêu đề `ANALYSIS`, `TRANSLATION`, `REVIEW` và các biến trong dấu ngoặc nhọn. Cả ba giai đoạn đều nhận input bằng file JSON đính kèm. Ứng dụng tự thêm tên file và output contract; không ghi cứng tên file trong các phần dưới đây.

## ANALYSIS

Đọc toàn bộ input JSON đính kèm. Phân tích transcript theo thứ tự thời gian và dùng `transcript_scope` cùng `speaker_statistics` để hiểu đây có thể là mẫu đại diện của kịch bản dài. Trả đúng một hồ sơ cho mỗi `speaker_id`, giữ nguyên các ID dù một diễn viên có thể lồng nhiều nhân vật. Phân loại `importance` thành `main`, `supporting` hoặc `minor`; phân loại `gender` thành `male`, `female` hoặc `unknown`. Nhân vật chính/quan trọng cần hồ sơ vai trò và xưng hô; nhân vật phụ chỉ cần mô tả ngắn, ưu tiên giới tính để gán voice. Không đủ bằng chứng thì dùng `unknown`, không đoán. Mọi `from_speaker` và `to_speaker` phải là `speaker_id` có thật, không dùng tên, chức danh hay nhóm nhân vật.

## TRANSLATION

Đọc toàn bộ input JSON đính kèm. Dịch chỉ các `segments_to_translate` từ {source_language} sang {target_language}. Tuân thủ hồ sơ hội thoại, chỉ dẫn và tài liệu của Project; giữ đúng ý nghĩa, tên riêng, cách xưng hô và ngữ cảnh. Trả mỗi ID đích đúng một lần, không trả ID ngữ cảnh.

## REVIEW

Đọc toàn bộ input JSON đính kèm. Kiểm tra toàn bộ bản dịch với nguồn và ngữ cảnh Project. Chỉ trả các ID có lỗi thực sự về ý nghĩa, tên riêng, xưng hô, thuật ngữ hoặc tính liên tục; không viết lại chỉ vì phong cách. Đầu ra bắt buộc là `[]` hoặc một JSON array correction `[{"id":1,"corrected_text":"...","issue_type":"...","reason":"..."}]`; không bọc array trong object hay trường `corrections`.
