from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from pydantic import ValidationError
from .....config.batch_prompts import prepare_translation_prompt

from .....config.web_translation import (
    CHATGPT_EXTENSION_ID,
    CHATGPT_WEB_PROVIDER_NAME,
    CLAUDE_EXTENSION_ID,
    CLAUDE_WEB_PROVIDER_NAME,
    project_url_error,
)
from .....config.prompt_templates import (
    DEFAULT_PROMPT_TEMPLATE_ID,
    step3_batch_filenames,
)
from .....errors import UserFacingError
from .....common.models import Segment
from ..gemini.provider import (
    DialogueProfileOutput,
    GoogleTranslationService,
    TranslationCorrection,
    TranslationItem,
    SourceVerifiedTranslationItem,
    StoryOutput,
)
from .. import (
    DialogueAnalysisResponse,
    TranslationResponse,
    TranslationReviewResponse,
    register_translation_provider,
)
from .....infrastructure.web_bridge_service import web_bridge_service


ProgressCallback = Callable[[int, str], None]


class WebTranslationService:
    """TranslationProvider adapter backed by an authenticated provider website."""

    def __init__(
        self,
        provider_name: str,
        provider_id: str,
        model_name: str,
        source_language: str,
        target_language: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.provider_id = provider_id
        self.model_name = model_name
        context = (options or {}).get("batch_prompt_context")
        self.batch_prompt_context = dict(context) if context is not None else None
        self.source_language = source_language
        self.target_language = target_language
        self.project_url = str((options or {}).get("project_url", "")).strip()
        self.prompt_template = str(
            (options or {}).get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID)
        ).strip() or DEFAULT_PROMPT_TEMPLATE_ID
        self._conversation_started = False
        self._last_raw_response = ""

    def _stage_prompt(
        self,
        stage: str,
        segments: list[Segment],
        batch_index: int = 1,
        batch_total: int = 1,
    ) -> tuple[str, str, str]:
        first_id = segments[0].id
        last_id = segments[-1].id
        input_filename, output_filename = step3_batch_filenames(
            stage,
            first_id,
            last_id,
        )
        prompt = prepare_translation_prompt(
            self.provider_name,
            self.prompt_template,
            stage,
            batch_context=self.batch_prompt_context,
            source_language=self.source_language,
            target_language=self.target_language,
            input_filename=input_filename,
            output_filename=output_filename,
            segment_start=f"{first_id:04d}",
            segment_end=f"{last_id:04d}",
            segment_count=str(len(segments)),
            batch_index=str(batch_index),
            batch_total=str(batch_total),
        )
        return prompt, input_filename, output_filename

    @staticmethod
    def _normalize_smart_json_quotes(text: str) -> str:
        """Normalize typographic JSON delimiters without rewriting quoted prose."""
        result: list[str] = []
        in_string = False
        smart_delimiter = False
        escaped = False

        for index, character in enumerate(text):
            if not in_string:
                if character == '"':
                    in_string = True
                    smart_delimiter = False
                elif character in "“”":
                    result.append('"')
                    in_string = True
                    smart_delimiter = True
                    continue
                result.append(character)
                continue

            if escaped:
                result.append(character)
                escaped = False
                continue
            if character == "\\":
                result.append(character)
                escaped = True
                continue
            if not smart_delimiter:
                result.append(character)
                if character == '"':
                    in_string = False
                continue
            if character not in {'"', "“", "”"}:
                result.append(character)
                continue

            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            next_significant = text[lookahead] if lookahead < len(text) else None
            if next_significant is None or next_significant in ":,}]":
                result.append('"')
                in_string = False
                smart_delimiter = False
            else:
                result.append(character)

        return "".join(result)

    @classmethod
    def _parse_json(cls, text: str, *, normalize_smart_quotes: bool = False) -> object:
        cleaned = text.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
        if fenced:
            cleaned = fenced.group(1).strip()
        if normalize_smart_quotes and any(
            quote in cleaned for quote in ("\u201c", "\u201d")
        ):
            strict_starts = [
                position for position in (cleaned.find("["), cleaned.find("{"))
                if position >= 0
            ]
            if strict_starts:
                try:
                    parsed, _end = json.JSONDecoder().raw_decode(
                        cleaned[min(strict_starts) :]
                    )
                    return parsed
                except json.JSONDecodeError:
                    cleaned = cls._normalize_smart_json_quotes(cleaned)
        starts = [position for position in (cleaned.find("["), cleaned.find("{")) if position >= 0]
        if not starts:
            raise ValueError("Phản hồi không chứa JSON object hoặc array.")
        parsed, _end = json.JSONDecoder().raw_decode(cleaned[min(starts) :])
        return parsed

    def _request_json(
        self,
        instruction: str,
        payload: object,
        output_contract: str = "",
        *,
        require_project: bool = True,
        attachment_name: str = "",
        response_attachment_name: str = "",
    ) -> object:
        url_error = project_url_error(self.provider_name, self.project_url)
        if require_project and url_error:
            raise UserFacingError(
                "URL Project chưa hợp lệ",
                url_error,
                "Mở Project trên trang web, sao chép URL trên thanh địa chỉ rồi dán vào Step 3.",
            )
        serialized_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        input_section = (
            "ATTACHED INPUT\n"
            f'Read the complete input JSON from the attached file "{attachment_name}". '
            "Do not answer before reading the file."
            if attachment_name
            else f"INPUT JSON\n{serialized_payload}"
        )
        quote_requirement = (
            "Use only the ASCII double quote character U+0022 for every JSON key and string delimiter. "
            "Do not use typographic quotes U+201C or U+201D.\n"
            if self.provider_name == CLAUDE_WEB_PROVIDER_NAME
            else ""
        )
        response_instruction = (
            f'Create a downloadable JSON file named exactly "{response_attachment_name}". '
            "Put only the JSON required by the output contract in that file. "
            "Do not print, preview, or wrap the JSON in the chat response; return the file itself."
            if response_attachment_name
            else "Return JSON only. Do not use Markdown fences, commentary, prefaces, or trailing notes."
        )
        prompt = instruction
        if output_contract:
            prompt = (
                f"SYSTEM TASK\n{instruction}\n\n"
                "OUTPUT CONTRACT\n"
                f"{output_contract}\n"
                f"{quote_requirement}"
                f"{response_instruction}\n\n"
                f"{input_section}"
            )
        try:
            response = web_bridge_service.submit_prompt(
                self.provider_id,
                prompt,
                model=self.model_name,
                project_url=self.project_url,
                conversation_mode="continue" if self._conversation_started else "start",
                attachments=(
                    [{
                        "name": attachment_name,
                        "mime_type": "application/json",
                        "content": serialized_payload,
                    }]
                    if attachment_name else None
                ),
                response_file_name=response_attachment_name,
                timeout=660.0 if response_attachment_name else 240.0,
            )
            self._last_raw_response = response
            self._conversation_started = True
            return self._parse_json(
                response,
                normalize_smart_quotes=self.provider_name == CLAUDE_WEB_PROVIDER_NAME,
            )
        except UserFacingError:
            raise
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise UserFacingError(
                f"Kết quả {self.provider_name} không hợp lệ",
                "Web provider không trả về JSON đúng cấu trúc mà workflow yêu cầu.",
                "Mở cuộc trò chuyện mới, giữ ô nhập trống và chạy lại Step 3.",
                f"{exc!r}\n\nRaw response:\n{response[:4000] if 'response' in locals() else ''}",
            ) from exc

    @staticmethod
    def _translation_map(parsed: object, allow_empty: bool = False) -> dict[int, str]:
        if not isinstance(parsed, list):
            raise ValueError("Kết quả phải là một JSON array.")
        result: dict[int, str] = {}
        for item in parsed:
            value = TranslationItem.model_validate(item)
            text = value.translated_text.strip()
            if value.id in result or (not allow_empty and not text):
                raise ValueError(f"ID trùng hoặc nội dung trống: {value.id}")
            result[value.id] = text
        return result

    def generate_story(
        self, content: object, prompt: str, input_filename: str, output_filename: str,
    ) -> str:
        parsed = self._request_json(
            prompt, content,
            'Một JSON object có đúng trường "story_markdown" là toàn bộ tài liệu Markdown tiếng Việt.',
            attachment_name=input_filename,
        )
        return StoryOutput.model_validate(parsed).story_markdown

    def correct_asr(
        self, content: object, prompt: str, input_filename: str, output_filename: str,
    ) -> object:
        return self._request_json(
            prompt, content,
            'Một JSON array, mỗi phần tử có đúng các trường "id", "start", "end", "source_text", "new_content".',
            attachment_name=input_filename,
            response_attachment_name=output_filename,
        )

    def analyze_dialogue(
        self,
        segments: list[Segment],
        proper_name_policy: dict[str, object],
    ) -> DialogueAnalysisResponse:
        analysis_segments = GoogleTranslationService._analysis_segments(segments)
        payload = {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "proper_name_policy": proper_name_policy,
            "transcript_scope": {
                "total_segment_count": len(segments),
                "analyzed_segment_count": len(analysis_segments),
                "is_sampled": len(analysis_segments) < len(segments),
            },
            "speaker_statistics": GoogleTranslationService._speaker_statistics(segments),
            "transcript": GoogleTranslationService._full_transcript(analysis_segments),
        }
        prompt, input_filename, output_filename = self._stage_prompt(
            "ANALYSIS",
            analysis_segments,
        )
        parsed = self._request_json(
            prompt,
            payload,
            attachment_name=input_filename,
            response_attachment_name=output_filename,
        )
        profile = DialogueProfileOutput.model_validate(parsed).model_dump(mode="json")
        return DialogueAnalysisResponse(profile, f"Chrome extension · {self.provider_name}")

    def translate_batch(
        self,
        segments: list[Segment],
        dialogue_profile: dict[str, object],
        context_before: list[Segment],
        context_after: list[Segment],
        previous_translations: dict[int, str],
        proper_name_policy: dict[str, object],
        response_file: bool = False,
        batch_index: int = 1,
        batch_total: int = 1,
    ) -> TranslationResponse:
        payload = {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "dialogue_profile": dialogue_profile,
            "proper_name_policy": proper_name_policy,
            "speaker_context_for_targets": GoogleTranslationService._speaker_context(
                segments, dialogue_profile
            ),
            "context_before": [
                GoogleTranslationService._segment_payload(item) for item in context_before
            ],
            "segments_to_translate": [GoogleTranslationService._segment_payload(item) for item in segments],
            "context_after": [
                GoogleTranslationService._segment_payload(item) for item in context_after
            ],
            "previous_translations": previous_translations,
        }
        instruction, input_name, output_name = self._stage_prompt(
            "TRANSLATION",
            segments,
            batch_index,
            batch_total,
        )
        parsed = self._request_json(
            instruction,
            payload,
            attachment_name=input_name,
            response_attachment_name=output_name if response_file else "",
        )
        if self.provider_name == CHATGPT_WEB_PROVIDER_NAME:
            try:
                if not isinstance(parsed, list):
                    raise ValueError("Phản hồi phải là JSON array.")
                values = [SourceVerifiedTranslationItem.model_validate(item) for item in parsed]
                translations = {}
                speakers = {}
                sources = {segment.id: segment.source_text for segment in segments}
                for value in values:
                    if value.id in translations:
                        raise ValueError(f"ID trùng: {value.id}")
                    translations[value.id] = value.translated_text.strip()
                    speakers[value.id] = value.speaker_id.strip()
                    if value.id in sources and value.source_text != sources[value.id]:
                        logging.getLogger(__name__).warning(
                            "ChatGPT Web echoed different source_text for ID %s; retaining original source.",
                            value.id,
                        )
                expected = set(sources)
                missing = expected - set(translations)
                extra = set(translations) - expected
                difference = len(values) - len(segments)
                if difference == -1 and len(missing) == 1 and not extra:
                    # As with Gemini, use source text only to align the N-1 case.
                    remaining = [segment for segment in segments if segment.id not in missing]
                    if [(value.id, value.source_text) for value in values] != [
                        (segment.id, segment.source_text) for segment in remaining
                    ]:
                        raise ValueError("Không căn khớp được ID/source_text khi thiếu một segment.")
                    missing_id = next(iter(missing))
                    translations[missing_id] = ""
                    logging.getLogger(__name__).warning(
                        "ChatGPT Web returned one fewer segment; keeping ID %s with an empty translation.",
                        missing_id,
                    )
                elif difference != 0 or missing or extra:
                    raise ValueError(f"Sai danh sách ID: thiếu {sorted(missing)}, thừa {sorted(extra)}.")
            except (ValueError, TypeError, ValidationError) as exc:
                raise UserFacingError(
                    "Kết quả ChatGPT Web không hợp lệ",
                    "Không thể mapping các segment trong file JSON trả về.",
                    "Kiểm tra ID và các trường id, speaker_id, source_text, translated_text.",
                    str(exc),
                ) from exc
            return TranslationResponse(
                translations, f"Chrome extension · {self.provider_name}", speakers,
            )
        translations = self._translation_map(parsed, allow_empty=True)
        return TranslationResponse(
            translations,
            f"Chrome extension · {self.provider_name}",
        )

    def review_translation(
        self,
        segments: list[Segment],
        dialogue_profile: dict[str, object],
        proper_name_policy: dict[str, object],
        batch_index: int = 1,
        batch_total: int = 1,
    ) -> TranslationReviewResponse:
        payload = {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "dialogue_profile": dialogue_profile,
            "proper_name_policy": proper_name_policy,
            "speaker_context": GoogleTranslationService._speaker_context(segments, dialogue_profile),
            "full_translation": GoogleTranslationService._full_transcript(
                segments, include_translation=True
            ),
        }
        prompt, input_filename, output_filename = self._stage_prompt(
            "REVIEW",
            segments,
            batch_index,
            batch_total,
        )
        parsed = self._request_json(
            prompt,
            payload,
            attachment_name=input_filename,
            response_attachment_name=output_filename,
        )
        try:
            if isinstance(parsed, dict):
                if set(parsed) != {"corrections"} or not isinstance(parsed["corrections"], list):
                    raise ValueError(
                        "Review object chỉ được chứa một trường corrections có giá trị là JSON array."
                    )
                parsed = parsed["corrections"]
            if not isinstance(parsed, list):
                raise ValueError("Kết quả review phải là một JSON array.")
            corrections: dict[int, str] = {}
            issues: list[dict[str, Any]] = []
            for item in parsed:
                value = TranslationCorrection.model_validate(item)
                text = value.corrected_text.strip()
                if value.id in corrections or not text:
                    raise ValueError(f"Correction trùng hoặc trống: {value.id}")
                corrections[value.id] = text
                issues.append(value.model_dump(mode="json"))
        except (ValueError, ValidationError, KeyError) as exc:
            raise UserFacingError(
                f"Kết quả review từ {self.provider_name} không hợp lệ",
                "Provider không trả về danh sách correction đúng cấu trúc.",
                "Kiểm tra chỉ dẫn trong Project có yêu cầu một định dạng JSON khác hay không rồi chạy lại Step 3.",
                f"{exc!r}\n\nRaw response:\n{self._last_raw_response[:4000]}",
            ) from exc
        return TranslationReviewResponse(
            corrections,
            issues,
            f"Chrome extension · {self.provider_name}",
        )

    def rewrite_for_timing(
        self,
        items: list[dict[str, object]],
        batch_size: int = 30,
        progress: ProgressCallback | None = None,
    ) -> TranslationResponse:
        if not items:
            raise UserFacingError(
                "Chưa chọn segment",
                "Không có segment nào được chọn để AI chỉnh sửa.",
                "Đánh dấu ít nhất một checkbox rồi thử lại.",
            )
        rewritten: dict[int, str] = {}
        size = max(1, batch_size)
        batches = [items[index : index + size] for index in range(0, len(items), size)]
        for index, batch in enumerate(batches, start=1):
            if progress:
                progress(5 + int((index - 1) / len(batches) * 88), f"AI đang rút gọn batch {index}/{len(batches)}…")
            parsed = self._request_json(
                (
                    f"Rewrite current_translation in {self.target_language} so synthesized speech fits the "
                    "measured window at or below target_speed. Preserve essential meaning, negation, intent, "
                    "proper names, numbers, and causality. Return every input ID exactly once."
                ),
                batch,
                'A JSON array containing only objects of the form {"id": integer, "translated_text": string}.',
                require_project=False,
            )
            batch_result = self._translation_map(parsed)
            expected = {int(item["id"]) for item in batch}
            if set(batch_result) != expected:
                raise ValueError(
                    f"Web provider trả sai ID; thiếu {sorted(expected - set(batch_result))}, "
                    f"dư {sorted(set(batch_result) - expected)}"
                )
            rewritten.update(batch_result)
        if progress:
            progress(95, "AI đã trả về nội dung rút gọn.")
        return TranslationResponse(rewritten, f"Chrome extension · {self.provider_name}")


def _factory(provider_name: str, provider_id: str):
    return lambda model_name, source_language, target_language, options: WebTranslationService(
        provider_name,
        provider_id,
        model_name,
        source_language,
        target_language,
        options,
    )


register_translation_provider(
    CHATGPT_WEB_PROVIDER_NAME,
    _factory(CHATGPT_WEB_PROVIDER_NAME, CHATGPT_EXTENSION_ID),
)
register_translation_provider(
    CLAUDE_WEB_PROVIDER_NAME,
    _factory(CLAUDE_WEB_PROVIDER_NAME, CLAUDE_EXTENSION_ID),
)




