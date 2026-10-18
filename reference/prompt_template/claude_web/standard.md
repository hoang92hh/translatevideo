# Prompt tiêu chuẩn

## PROMPT

Read the complete attached input before answering. In this single request, first analyze the batch context, speakers, relationships, forms of address, names, terminology, tone, and continuity; then translate every segments_to_translate item; finally review and correct the translations internally. Return only the final reviewed translations required by the output contract. Do not expose analysis or draft translations.

Đọc toàn bộ input JSON đính kèm. Tự phân tích ngữ cảnh, vai trò, quan hệ, cách xưng hô, tên riêng và thuật ngữ; sau đó dịch chỉ các segments_to_translate từ {source_language} sang {target_language}, tự kiểm tra và sửa bản dịch trước khi xuất kết quả cuối. Tuân thủ proper_name_policy, context_before, context_after và previous_translations. Ưu tiên lời thoại tự nhiên, súc tích và phù hợp thời lượng nhưng không làm mất ý nghĩa, phủ định, chủ đích, tên riêng, con số hoặc quan hệ nhân quả. Trả mỗi ID đích đúng một lần; không gộp, tách, bỏ sót, giải thích hoặc thêm ID.

### OUTPUT CONTRACT

A JSON array containing only objects of the form {"id": integer, "translated_text": string}. Use only ASCII double quote U+0022 for JSON keys and string delimiters. Create a downloadable JSON file named exactly "{output_filename}". Put only the JSON required by the output contract in that file. Do not print, preview, or wrap the JSON in the chat response; return the file itself.

### ATTACHED INPUT

This is translation batch {batch_index}/{batch_total}, containing {segment_count} target segments from {segment_start} through {segment_end}. Read the complete input JSON from the attached file "{input_filename}". Do not answer before reading the file.
