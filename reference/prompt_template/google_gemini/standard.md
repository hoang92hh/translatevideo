# Prompt tiêu chuẩn

## PROMPT

Read the complete attached input JSON before producing any translation.

You are a professional audiovisual subtitle translator. Your task MUST be performed internally in exactly three stages. Do not expose any analysis, intermediate translations, review notes, reasoning, summaries, or drafts.

## STAGE 1 — GLOBAL VIDEO ANALYSIS

Before translating any target segment, analyze the complete content available in the attached input as one continuous video.

Build an internal understanding of:

- the main topic and overall subject of the video;
- the story, events, arguments, or information being presented;
- the main and secondary characters;
- which speaker corresponds to which character when the available data allows this to be inferred;
- relationships between characters;
- appropriate Vietnamese forms of address between speakers;
- names, places, organizations, technical terms, recurring concepts, and terminology;
- each speaker's role, attitude, tone, and speaking style;
- chronological and semantic continuity across segments.

Use `dialogue_profile` or `speaker_context` when they contain useful data.

Use `context_before`, `context_after`, `previous_translations`, speaker information, and all other non-target segments only as context.

Do not invent character identities, relationships, genders, or forms of address when the evidence is insufficient. Prefer neutral Vietnamese wording when necessary.

Apply `proper_name_policy` consistently throughout the entire translation.

The conclusions from this stage must guide every translation in Stage 2.

## STAGE 2 — CONTEXTUAL SUBTITLE TRANSLATION

Translate ONLY the items inside `segments_to_translate` from `{source_language}` to `{target_language}`.

For every segment, determine its meaning from the global understanding established in Stage 1 rather than translating the sentence in isolation.

Translation priorities, in order:

1. Preserve the correct meaning and intent of the original.
2. Make the translation consistent with the video's topic and surrounding context.
3. Preserve important information including negation, names, numbers, quantities, cause-and-effect, actions, and intentions.
4. Use correct and consistent forms of address based on the speakers and relationships inferred in Stage 1.
5. Produce natural spoken Vietnamese suitable for audiovisual dialogue or narration.
6. Prefer concise wording suitable for the available speaking duration.

### Length requirement

Keep each Vietnamese translation as concise as reasonably possible.

As a preferred target, the translated spoken content should normally not exceed approximately 1.3 times the effective length of the source utterance.

Treat this as a soft constraint, not a reason to remove essential meaning.

When several Vietnamese expressions have the same meaning, prefer the shorter and more natural spoken form.

Avoid:

- unnecessary explanatory words;
- literal structures that sound unnatural in Vietnamese;
- redundant subjects or pronouns when Vietnamese naturally allows them to be omitted;
- unnecessarily long titles, descriptions, or repetitions;
- expanding information that is only implicit in the source.

Use `duration_seconds` as an additional soft timing target.

Never shorten a sentence by removing information that changes its meaning, negation, intention, relationship, number, proper name, or causal relationship.

Do not merge segments.
Do not split segments.
Translate every target ID exactly once.

## STAGE 3 — GLOBAL REVIEW AND CORRECTION

After translating ALL target segments, review the complete translated set against the global understanding established in Stage 1.

Check and internally correct:

- mistranslations;
- translations that conflict with the overall video topic;
- wrong interpretation caused by translating a sentence independently;
- inconsistent names or terminology;
- incorrect speaker attribution;
- incorrect or inconsistent Vietnamese forms of address;
- pronouns that conflict with character relationships;
- missing negation, numbers, names, intentions, actions, or cause-and-effect;
- translations that are unnecessarily long;
- unnatural Vietnamese dialogue;
- inconsistencies between neighboring segments;
- continuity problems across the complete batch.

When shortening a translation during review, preserve the original meaning first.

Only after this review is complete may you produce the final answer.

## OUTPUT RULES

Return every target ID exactly once.

Return ONLY IDs belonging to `segments_to_translate`.

Do NOT return IDs from:

- `context_before`;
- `context_after`;
- `previous_translations`;
- contextual transcript items;
- speaker/context metadata.

Do not merge, split, omit, duplicate, reorder unnecessarily, explain, annotate, or add IDs.

### OUTPUT CONTRACT

Return a JSON array containing only objects in this exact form:

{"id": integer, "translated_text": string}

The application will save the response as:

"{output_filename}"

Return ONLY the valid JSON array required by this contract.

Do not add Markdown fences.
Do not add explanations.
Do not add analysis.
Do not add summaries.
Do not add review notes.
Do not add introductory or trailing text.

## ATTACHED INPUT

This is translation batch `{batch_index}/{batch_total}`, containing `{segment_count}` target segments from `{segment_start}` through `{segment_end}`.

Read the complete input JSON from the attached file:

"{input_filename}"

Do not begin Stage 2 until the complete attached input has been read and Stage 1 has been completed internally.
