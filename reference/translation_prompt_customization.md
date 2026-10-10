# Ghi chú chỉnh prompt và dữ liệu gửi AI

Tài liệu này chỉ ra đúng vị trí cần sửa khi thay đổi prompt, payload hoặc quy tắc
dịch ở Step 03 và chức năng AI rút gọn câu tại Step 05.

## 1. Bản đồ chỉnh sửa nhanh

| Muốn thay đổi | File và vị trí chính |
|---|---|
| Prompt phân tích speaker, quan hệ, cách xưng hô, thuật ngữ và tên riêng | `video_translator/services/google_translation_service.py` → `analyze_dialogue()` |
| Prompt dịch từng batch | `video_translator/services/google_translation_service.py` → `translate_batch()` |
| Prompt kiểm duyệt toàn bộ bản dịch | `video_translator/services/google_translation_service.py` → `review_translation()` |
| Prompt rút gọn câu theo thời lượng ở Step 05 | `video_translator/services/google_translation_service.py` → `rewrite_for_timing()` |
| Trường dữ liệu của một segment gửi AI | `video_translator/services/google_translation_service.py` → `_segment_payload()` |
| Cấu hình gọi Gemini, JSON response, schema và `temperature` | `video_translator/services/google_translation_service.py` → `_generate_structured()` và từng lời gọi hàm này |
| Schema hồ sơ hội thoại, speaker, quan hệ, thuật ngữ và tên | Các class Pydantic ở đầu `video_translator/services/google_translation_service.py` |
| Quy tắc Hán–Việt, Pinyin, chữ gốc và tự động theo cặp ngôn ngữ | `video_translator/config/translation.py` → `resolve_proper_name_policy()` |
| Số segment ngữ cảnh trước/sau | `video_translator/services/translation_workflow.py` → `CONTEXT_SEGMENT_COUNT` |
| Thứ tự ba giai đoạn, chia batch và kiểm tra hồ sơ | `video_translator/services/translation_workflow.py` → `TranslationWorkflow.run()` |
| Hợp đồng cho Gemini hoặc extension ChatGPT/Claude | `video_translator/services/translation_provider.py` → `TranslationProvider` |
| Dữ liệu Step 05 dùng để yêu cầu rút gọn | `video_translator/services/audio_sync_service.py` → `rewrite_sync_drafts()` |
| Model Gemini mặc định và danh sách model | `video_translator/config/gemini.py` |
| Các lựa chọn hiển thị trên giao diện Step 03 | `video_translator/ui/steps/translation.py` → `TranslationStepPage.SPEC` |
| Cách tạo policy, gọi workflow và ghi manifest Step 03 | `video_translator/pipeline/mock_steps/translation.py` → `execute()` |

## 2. Luồng xử lý hiện tại

`TranslationWorkflow` điều phối ba tác vụ AI theo thứ tự:

1. `analyze_dialogue()` đọc toàn bộ transcript một lần và tạo `dialogue_profile`.
2. `translate_batch()` dịch từng batch bằng hồ sơ chung và ngữ cảnh chồng lấn.
3. `review_translation()` đọc toàn bộ bản dịch một lần và chỉ trả các ID cần sửa.

Workflow là phần dùng chung, không chứa prompt riêng của Gemini. Prompt Gemini nằm
trong `GoogleTranslationService`. Adapter ChatGPT hoặc Claude sau này phải triển khai
cùng các method trong `TranslationProvider`, nhưng có thể dùng cú pháp prompt và cơ
chế structured output riêng của provider đó.

## 3. Payload của từng giai đoạn

### 3.1. Phân tích tổng quan

`analyze_dialogue()` gửi:

```json
{
  "source_language": "Chinese",
  "target_language": "Vietnamese",
  "proper_name_policy": {},
  "full_transcript": "[0001] SPEAKER_00: ...\n[0002] SPEAKER_01: ..."
}
```

- `proper_name_policy`: quy tắc tên được code giải quyết trước khi gọi AI.
- `full_transcript`: toàn bộ câu nguồn theo thứ tự thời gian, mỗi dòng gắn ID và
  `speaker_id`. Giai đoạn này chỉ gọi provider một lần.

Kết quả phải khớp `DialogueProfileOutput`, gồm:

- `story_summary`
- `global_style`
- `speakers`
- `addressing_rules`
- `terminology`
- `names`
- `uncertainties`

Mỗi phần tử trong `names` ánh xạ `source_name` sang đúng một `canonical_name`.
Mỗi speaker có `story_role`, `default_self_reference`, phong cách nói,
`voice_description` trung lập provider và các thuộc tính tham chiếu giọng. Mỗi quy tắc
xưng hô có `from_speaker`, `to_speaker`, khoảng segment
áp dụng, cách tự xưng, cách gọi trực tiếp và cách nhắc ở ngôi thứ ba.

### 3.2. Dịch từng batch

`translate_batch()` gửi:

```json
{
  "dialogue_profile": {},
  "proper_name_policy": {},
  "speaker_context_for_targets": [],
  "context_before": [],
  "segments_to_translate": [],
  "context_after": [],
  "previous_translations": {}
}
```

AI chỉ được trả kết quả cho `segments_to_translate`. `speaker_context_for_targets`
chứa hồ sơ và quy tắc xưng hô áp dụng cho từng ID mục tiêu. Các segment trong
`context_before` và `context_after` chỉ dùng làm ngữ cảnh.

### 3.3. Rà soát tổng thể

`review_translation()` gửi toàn bộ source text và `current_translation` trong:

```json
{
  "dialogue_profile": {},
  "proper_name_policy": {},
  "speaker_context": [],
  "full_translation": "[0001] SPEAKER_00: ...\n         TRANSLATION: ..."
}
```

AI chỉ trả các ID thật sự cần thay đổi; danh sách rỗng nghĩa là không cần sửa. Prompt
yêu cầu dùng nguyên `canonical_name` và sửa các biến thể tên, cách xưng hô hoặc thuật
ngữ không nhất quán, nhưng không viết lại câu đúng chỉ vì khác phong cách.

### 3.4. Rút gọn theo thời lượng ở Step 05

`rewrite_sync_drafts()` chuẩn bị các trường:

- `id`
- `source_text`
- `current_translation`
- `measured_tts_seconds`
- `allowed_seconds`
- `current_required_speed`
- `target_speed`
- `requested_reduction_ratio`

Prompt xử lý chúng nằm trong `GoogleTranslationService.rewrite_for_timing()`.

## 4. Schema trả về bắt buộc

Giai đoạn dịch và rút gọn trả danh sách theo `TranslationItem`:

```json
[
  {
    "id": 1,
    "translated_text": "Nội dung đã xử lý"
  }
]
```

Không thay đổi tên `id` hoặc `translated_text` nếu chưa cập nhật đồng bộ schema và
mọi adapter provider. Workflow kiểm tra cứng các điều kiện:

- Mỗi ID mục tiêu xuất hiện đúng một lần.
- Không thiếu ID.
- Không có ID ngữ cảnh hoặc ID tự tạo thêm.
- `translated_text` không được trống.

Vi phạm một trong các điều kiện trên làm toàn bộ lần chạy Step 03 thất bại và không
đăng ký candidate mới.

Giai đoạn kiểm duyệt trả danh sách `TranslationCorrection` và được phép rỗng:

```json
[
  {
    "id": 12,
    "corrected_text": "Nội dung đã sửa",
    "issue_type": "addressing",
    "reason": "Cách xưng hô chưa khớp hồ sơ speaker"
  }
]
```

Workflow chỉ chấp nhận ID có trong transcript và chỉ thay nội dung của các ID này.

## 5. Cấu hình gửi Gemini

`GoogleTranslationService._generate_structured()` là điểm gọi chung của ba giai đoạn:

- `contents`: payload được serialize thành JSON UTF-8.
- `system_instruction`: prompt của giai đoạn tương ứng.
- `response_mime_type`: luôn là `application/json`.
- `response_schema`: schema Pydantic bắt buộc.
- `temperature`: được truyền riêng bởi từng giai đoạn.

Giá trị hiện tại:

| Giai đoạn | Temperature |
|---|---:|
| Phân tích | `0.1` |
| Dịch | `0.2` |
| Rà soát | `0.1` |
| Rút gọn Step 05 | `0.1` |

Nếu thêm trường vào payload, prompt phải giải thích rõ trường đó dùng để làm gì. Nếu
thêm trường bắt buộc vào output, phải cập nhật class Pydantic tương ứng.

## 6. Quy tắc tên riêng

Không viết các nhánh Chinese → Vietnamese/English/Spanish trực tiếp vào prompt dịch.
Hãy sửa `resolve_proper_name_policy()` nếu muốn đổi cách chọn quy tắc theo cặp ngôn
ngữ. Prompt chỉ nên:

1. Tuân thủ `proper_name_policy` khi xây bảng tên.
2. Dùng chính xác `dialogue_profile.names[].canonical_name` khi dịch.
3. Sửa mọi biến thể về `canonical_name` trong giai đoạn rà soát.

Cách này giữ prompt gọn và cho phép adapter ChatGPT/Claude dùng chung một policy.

## 7. Vị trí dễ sửa nhầm

`GoogleTranslationService.translate()` là luồng dịch một lượt cũ, có một
`system_instruction` riêng. Workflow Step 03 hiện tại **không gọi method này**; nó gọi
`analyze_dialogue()`, `translate_batch()` và `review_translation()`. Vì vậy, sửa prompt trong
`translate()` sẽ không thay đổi kết quả của workflow ba giai đoạn.

`rewrite_for_timing()` vẫn được sử dụng tại Step 05 thông qua provider registry.

## 8. Checklist khi thay đổi prompt

- Giữ nguyên ID và schema trả về.
- Phân biệt rõ segment mục tiêu với segment ngữ cảnh.
- Không yêu cầu AI đổi timestamp, `speaker_id` hoặc `merge_parts`.
- Không đưa API key, token, cookie hoặc credential vào prompt/payload.
- Giữ prompt provider-specific trong adapter, không đặt vào `TranslationWorkflow`.
- Nếu đổi hợp đồng method, cập nhật `TranslationProvider` và mọi adapter.
- Nếu đổi manifest, giữ khả năng đọc candidate phiên bản cũ.
- Cập nhật tài liệu Step 03 khi hành vi người dùng nhìn thấy thay đổi.

