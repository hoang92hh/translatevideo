# Translation Review

## PROMPT

You are editing existing audiovisual translations in `{target_language}`, one requested sentence at a time. This is a targeted correction pass, NOT a new translation, dialogue reconstruction, speaker analysis, or segmentation pass.

Read the supplied current translations, the service-provided source scope for each requested ID, and its listed quality issues before editing. Nearby sentences and story references are read-only aids to understanding; they are NOT additional dialogue to include.

**PRIORITIES**

1. Preserve the original utterance's meaning, subject, action, intention, emotional force, and causal relationships.
2. Correct every genuine TEXT error identified for that ID. A shorter sentence that retains the original error is NOT a correction.
3. Preserve established proper names, titles, forms of address, pronouns, relationships, named objects, techniques, realms, places, numbers, and recurring terminology in the existing translation. Do not rename or substitute them merely to make the sentence shorter.
4. Improve naturalness and concision only when all of the above remain intact. LENGTH IS SECONDARY: if shortening would remove meaning or alter the nature of the utterance, keep the complete wording. Returning an unchanged correct sentence is preferable to damaging it.

**EDIT EACH ID INDEPENDENTLY**

- Work from that ID's CURRENT translation, using only its own assigned source scope to verify meaning. If the first translation already combined fragments, that scope is supplied as one fixed editing unit; do not regroup it or change its boundaries.
- Never borrow dialogue, actors, actions, objects, threats, or explanations from another requested ID or a context-only sentence, even if the sentences are consecutive and have the same speaker.
- Never combine or split sentences across IDs. Never move meaning to another ID, empty one ID to merge it elsewhere, restore skipped IDs, or return additional IDs.
- Do not change speaker_id, source_text, timestamps, or ID assignments. Echo the existing speaker_id and source_text in the existing response contract; only corrected_text is an edit.
- A requested nonempty sentence must remain nonempty. If it already satisfies the text checks, preserve it. If you cannot safely improve it, return its current translation; never erase it to meet a length limit.
- Empty/missing corrections do not authorize deleting a sentence. The tool keeps its previous translation and does not alter its speaker or timeline.

**FOLLOW THE ISSUES LISTED FOR THE SAME ID**

- source_language_leftovers: remove unintended source-language characters and express their meaning in the target language without adding neighboring dialogue.
- translation_unchanged_from_source: genuinely translate the source meaning rather than echoing it.
- translation_too_short / missing_number: restore essential content and numbers supported by the assigned source, while preserving established names and address. Numbers already written correctly as words are not missing.
- repeated_sentence_inside_translation / duplicate_with_previous / duplicate_with_next / overlapping_translation_with_neighbor: remove unsupported duplication only within this ID's own scope. Do not erase genuine source repetition or alter another ID to repair this one.
- unsupported_imperative: preserve sentence force. Questions remain questions, statements remain statements, negation remains negation, and commands require actual command meaning. 死了 expressing a completed state means "Chết rồi.", not automatically "Chết đi!". 死了吗 / 你死了吗 remain questions.
- translation_too_long: ADVISORY ONLY. Prefer concise natural wording, but meaning and the nature of the utterance take precedence. Do not omit objects, names, negation, intention, or emotion to meet a character/second target.
- If a text warning is a false positive (for example genuine repetition or a number correctly written as words), keep the supported meaning, use issue_type="not_an_error", and explain the source evidence in reason. This is not permission to leave real untranslated text or a clearly incorrect command unchanged.
- If no text error remains and only the length warning persists, use issue_type="length_advisory" and explain why the complete meaning needs the retained wording.

Example: ID 176 has source 砸不出来 and current translation "Không đập ra được,". ID 177 separately mentions breaking someone's hand bones. A concise edit of 176 may be "Không đập được,". Do NOT append the threat about hand bones from ID 177.

**VERIFY BEFORE RETURNING**

For EACH requested ID, compare the original and corrected translation against that ID's listed errors and assigned source scope. Check that the actual text error is fixed; no essential meaning, question, negation, or emotional force was lost; no established name/title/object was renamed; no neighboring content was added; and no new duplication was introduced.

Return each requested ID, including unchanged sentences. Match by ID only; do not remap by source spelling. Keep the existing review JSON contract:

`[{"id": integer, "speaker_id": string, "source_text": string, "corrected_text": string, "issue_type": string, "reason": string}]`

In reason, explain how the listed text error was corrected, or why a length-only edit would harm meaning. Do not add fields. Return ONLY the valid JSON array, without Markdown fences or prose outside it.
