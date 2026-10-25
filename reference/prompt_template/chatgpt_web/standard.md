# Prompt tiêu chuẩn

## PROMPT

Read the complete attached input before answering. In this single request, first analyze the batch context, speakers, relationships, forms of address, names, terminology, tone, and continuity; then translate every segments_to_translate item; finally review and correct the translations internally. Return only the final reviewed translations required by the output contract. Do not expose analysis or draft translations.

Đọc toàn bộ input JSON đính kèm. Tự phân tích ngữ cảnh, vai trò, quan hệ, cách xưng hô, tên riêng và thuật ngữ; sau đó dịch chỉ các segments_to_translate từ {source_language} sang {target_language}, tự kiểm tra và sửa bản dịch trước khi xuất kết quả cuối. Tuân thủ proper_name_policy, speaker_context_for_targets, context_before, context_after và previous_translations. Thực hiện theo thứ tự: hiểu đầy đủ nguồn → dịch đủ nghĩa → diễn đạt tự nhiên → rút gọn khi không mất thông tin. Thời lượng chỉ là ưu tiên sau cùng. Trả mỗi ID đích đúng một lần; không gộp hoặc xóa object JSON, tách hay đánh lại ID.

### DIALOGUE CONTINUITY AND COMPLETE MEANING

- Mandatory timing condition for combining: for EVERY adjacent pair in the proposed utterance, the original end_seconds of the preceding segment must EXACTLY equal the start_seconds of the next segment. Any gap or overlap forbids combining. Use original input timestamps, never times inferred from empty translations. This is necessary in addition to semantic continuity.
- Read neighboring segments as dialogue, not isolated fragments. Use only supplied context; never invent story facts, character identities, or speech.
- Step 2 preserves Main/Recovery ASR text and provisional speaker labels. SPEAKER_RECOVERY is a placeholder, not evidence that the text is redundant or meaningless. A timing boundary alone does not prove a sentence boundary.
- You may combine the TRANSLATION CONTENT of consecutive target segments only when grammar, meaning, timing, and the dialogue support one continuous utterance by the same actual speaker. Check the supplied start/end times; adjacency of IDs or matching speaker labels alone is insufficient. Do not combine across a substantial pause, an independent utterance, or a real speaker exchange. If uncertain, translate separately.
- For a valid combined utterance, place its COMPLETE translation exactly once at the FIRST target ID; return translated_text="" for each following absorbed ID. Keep every original ID as its own response object. Do not combine with context-only IDs or across batch boundaries. Prefer short complete utterances rather than long monologues.
- For an uncombined segment, translate its own source meaning only. Context clarifies interpretation; it must not replace or add content outside the selected consecutive utterance.
- ASR may contain a wrong word or an incomplete phrase. Correct interpretation only when the supplied context provides sufficient evidence. Preserve every reliably understood part, including numbers and quantities, even if another part is uncertain. Do not discard an entire phrase just because it is awkward. Never replace a difficult phrase with a neighboring name or sentence.
- Preserve names, titles, forms of address, numbers, units, actions, negation, conditions, cause/effect, and sentence force. Keep questions, statements, commands, and exclamations distinct. Concision means fewer words with the SAME information, not a summary with fewer facts.
- Preserve intentional source repetitions. Similar wording or a recovery label alone does not justify deleting speech. Remove only repetition accidentally introduced by your translation.
- An empty translation is permitted for a fragment whose complete meaning is already present in its valid representative translation, or when no reliable meaning at all can be recovered. Never use an empty string to hide difficult but recoverable information. Do not infer that an empty neighbor permits moving speech across a pause.
- Before writing the final file, internally compare each translation against every source fragment it represents. Check that all supported facts, quantities, names, actions, negation, and conditions survive. Correct omissions and borrowed or duplicated content within this same response; do not request another review round.

Example, only if the three targets are one continuous question:
101="你", 102="到底想干什么", 103="啊"
Result: [{"id":101,"speaker_id":"SPEAKER_01","source_text":"你","translated_text":"Rốt cuộc ngươi muốn làm gì hả?"},{"id":102,"speaker_id":"SPEAKER_01","source_text":"到底想干什么","translated_text":""},{"id":103,"speaker_id":"SPEAKER_01","source_text":"啊","translated_text":""}]

Correct speaker_id only when grammar, dialogue continuity, and supplied context provide enough evidence, including a fragmented utterance with an incorrect provisional label. Use only speaker IDs present in the attached input. Do not invent character identities or new IDs. If uncertain, retain the original speaker_id; never force a speaker change merely to enable grouping.

The tool maps the response by ID, applies validated speaker corrections, collapses every empty translation to start=end, and extends a representative's end across following empty targets within its batch. Never return timestamps, merge_ids, confidence, or explanations. Keep each source_text unchanged even when translation content is combined.

### OUTPUT CONTRACT

A JSON array containing only objects of the form {"id": integer, "speaker_id": string, "source_text": string, "translated_text": string}. Copy id and source_text exactly from the same segments_to_translate item. Return every target ID exactly once, including empty translations, and select speaker_id only from IDs present in the attached input. Create a downloadable JSON file named exactly "{output_filename}". Put only the JSON required by the output contract in that file. Do not print, preview, or wrap the JSON in the chat response; return the file itself.

### ATTACHED INPUT

This is translation batch {batch_index}/{batch_total}, containing {segment_count} target segments from {segment_start} through {segment_end}. Read the complete input JSON from the attached file "{input_filename}". Do not answer before reading the file.
