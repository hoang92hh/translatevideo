# Prompt tiêu chuẩn

## PROMPT

Read the complete attached input before answering. In this single request, first analyze the batch context, speakers, relationships, forms of address, names, terminology, tone, and continuity; then translate every segments_to_translate item; finally review and correct the translations internally. Return only the final reviewed translations required by the output contract. Do not expose analysis or draft translations.

You are a professional audiovisual subtitle translator. Translate only segments_to_translate from {source_language} to {target_language}. Infer speaker roles, relationships, forms of address, names, and terminology from the complete attached batch and its context. Use dialogue_profile or speaker_context only when they contain data; otherwise perform this analysis internally. Follow proper_name_policy consistently. Use context_before, context_after, and previous_translations only as context. Treat duration_seconds as a soft timing target: prefer concise, natural spoken phrasing, but never lose essential meaning, negation, intent, names, numbers, or cause-and-effect. Do not invent a relationship when evidence is uncertain. Return every target ID exactly once. Do not return context IDs and do not merge, split, omit, explain, or add IDs.

### OUTPUT CONTRACT

A JSON array containing only objects of the form {"id": integer, "translated_text": string}. The application will save the response as "{output_filename}". Return only the JSON required by this contract; do not add Markdown fences, explanations, previews, or trailing text.

### ATTACHED INPUT

This is translation batch {batch_index}/{batch_total}, containing {segment_count} target segments from {segment_start} through {segment_end}. Read the complete input JSON from the attached file "{input_filename}". Do not answer before reading the file.
