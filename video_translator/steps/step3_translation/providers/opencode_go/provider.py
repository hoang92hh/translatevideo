from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, get_args, get_origin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, TypeAdapter

from .....config.opencode_go import OPENCODE_GO_API_URL, OPENCODE_GO_MODEL, OPENCODE_GO_PROVIDER_NAME
from .....config.batch_prompts import prepare_translation_prompt
from .....config.timing_rewrite_prompt import load_timing_rewrite_prompt
from .....config.prompt_templates import (
    DEFAULT_PROMPT_TEMPLATE_ID,
    step3_batch_filenames,
)
from .....errors import UserFacingError
from .....common.models import Segment
from .....common.services.credential_service import OPENCODE_GO, CredentialService
from .. import (
    DialogueAnalysisResponse,
    TranslationResponse,
    TranslationReviewResponse,
    register_translation_provider,
)


ProgressCallback = Callable[[int, str], None]
ANALYSIS_SEGMENT_LIMIT = 250


class TranslationItem(BaseModel):
    id: int
    translated_text: str


class StoryOutput(BaseModel):
    story_markdown: str = Field(min_length=1)


class AsrCorrectionItem(BaseModel):
    id: int
    start: float
    end: float
    source_text: str
    new_content: str


class SourceVerifiedTranslationItem(BaseModel):
    id: int
    speaker_id: str
    source_text: str
    translated_text: str


class BatchTranslationOutput(BaseModel):
    Noi_dung: str = Field(min_length=1)
    segment_list: list[SourceVerifiedTranslationItem]


class SpeakerGuidance(BaseModel):
    speaker_id: str
    character_name: str = ""
    story_role: str = ""
    importance: str = "minor"
    gender: str = "unknown"
    age_group: str = "unknown"
    personality: str = ""
    default_self_reference: str = ""
    speech_style: str = ""
    voice_description: str = ""
    confidence: str = "low"
    notes: str = ""


class AddressingRule(BaseModel):
    from_speaker: str
    to_speaker: str
    from_segment: int = 0
    to_segment: int = 0
    relationship: str = ""
    self_reference: str
    direct_address: str
    third_person_reference: str
    confidence: str = "low"


class TerminologyGuidance(BaseModel):
    source_term: str
    preferred_translation: str


class ProperNameGuidance(BaseModel):
    source_name: str
    name_type: str
    canonical_name: str
    confidence: str = "low"
    notes: str = ""


class DialogueProfileOutput(BaseModel):
    story_summary: str = ""
    global_style: str = "natural spoken dialogue"
    speakers: list[SpeakerGuidance] = Field(default_factory=list)
    addressing_rules: list[AddressingRule] = Field(default_factory=list)
    terminology: list[TerminologyGuidance] = Field(default_factory=list)
    names: list[ProperNameGuidance] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class TranslationCorrection(BaseModel):
    id: int
    speaker_id: str = ""
    corrected_text: str
    issue_type: str
    reason: str = ""


class OpenCodeReviewCorrection(TranslationCorrection):
    source_text: str


class OpenCodeGoTranslationService:
    provider_name = OPENCODE_GO_PROVIDER_NAME

    def __init__(
        self,
        model_name: str,
        source_language: str,
        target_language: str,
        options: dict[str, object] | None = None,
    ) -> None:
        if model_name != OPENCODE_GO_MODEL:
            raise ValueError(f"OpenCode Go only supports {OPENCODE_GO_MODEL}.")
        self.model_name = model_name
        self.session_id = str(uuid4())
        context = (options or {}).get("batch_prompt_context")
        self.batch_prompt_context = dict(context) if context is not None else None
        self.source_language = source_language
        self.target_language = target_language
        self.prompt_template = str(
            (options or {}).get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID)
        ).strip() or DEFAULT_PROMPT_TEMPLATE_ID
        diagnostic_root = str((options or {}).get("diagnostics_root", "")).strip()
        self.diagnostics_root = Path(diagnostic_root) if diagnostic_root else None

    @staticmethod
    def _record_diagnostic_error(
        directory: Path | None,
        stage: str,
        message: str,
        **details: object,
    ) -> None:
        if directory is None:
            return
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "error.json").write_text(
            json.dumps(
                {"stage": stage, "message": message, **details},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _stage_prompt(
        self,
        stage: str,
        segments: list[Segment],
        batch_index: int = 1,
        batch_total: int = 1,
        previous_segment_summary: str = "",
    ) -> tuple[str, str, str]:
        first_id = segments[0].id
        last_id = segments[-1].id
        input_filename, output_filename = step3_batch_filenames(
            stage,
            first_id,
            last_id,
        )
        template_id = "review" if stage == "REVIEW" else self.prompt_template
        prompt = prepare_translation_prompt(
            OPENCODE_GO_PROVIDER_NAME,
            template_id,
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
            previous_segment_summary=previous_segment_summary,
        )
        return prompt, input_filename, output_filename

    @staticmethod
    def _client():
        return OpenCodeGoTranslationService._credential()


    @classmethod
    def validate_connection(cls, model_name: str = OPENCODE_GO_MODEL) -> str:
        provider = cls(model_name, "Chinese", "Vietnamese")
        provider._request({"ping": "pong"}, "Return JSON: {\"ok\": true}", 16)
        return cls._credential().source


    @staticmethod
    def _segment_payload(segment: Segment, include_translation: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": segment.id,
            "start_seconds": round(segment.start, 3),
            "end_seconds": round(segment.end, 3),
            "duration_seconds": round(segment.duration, 3),
            "speaker_id": segment.speaker_id,
            "source_text": segment.source_text,
        }
        if include_translation:
            payload["current_translation"] = segment.translated_text
        return payload

    def _generate_structured(
        self, content: object, system_instruction: str, response_schema: object,
        temperature: float, input_filename: str, output_filename: str,
        diagnostic_dir: Path | None = None, expected_ids: list[int] | None = None,
        batch_index: int = 1, batch_total: int = 1,
    ) -> tuple[object, str]:
        is_list = get_origin(response_schema) is list
        schema = get_args(response_schema)[0] if is_list else response_schema
        if is_list:
            system_instruction += "\nFor this API response, wrap the requested array in a JSON object with exactly one key named items. This overrides any instruction to return a bare array."
            system_instruction += "\nEach items entry must match this JSON schema: " + json.dumps(
                TypeAdapter(schema).json_schema(), ensure_ascii=False,
            )
        else:
            system_instruction += "\nReturn a JSON object matching the requested output fields."
            system_instruction += "\nThe output object must match this JSON schema: " + json.dumps(
                TypeAdapter(schema).json_schema(), ensure_ascii=False,
            )
        if diagnostic_dir is not None:
            diagnostic_dir.mkdir(parents=True, exist_ok=True)
            (diagnostic_dir / "request.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            (diagnostic_dir / "prompt.txt").write_text(system_instruction, encoding="utf-8")
            (diagnostic_dir / "metadata.json").write_text(json.dumps({
                "provider": self.provider_name, "model": self.model_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expected_ids": expected_ids, "batch_index": batch_index,
                "batch_total": batch_total, "input_filename": input_filename,
                "output_filename": output_filename,
            }, ensure_ascii=False), encoding="utf-8")
        try:
            answer, source = self._request(content, system_instruction, 32768)
            if diagnostic_dir is not None:
                (diagnostic_dir / "response.json").write_text(answer, encoding="utf-8")
            parsed: Any = json.loads(answer)
            if is_list:
                if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
                    raise ValueError("OpenCode Go phải trả object có mảng items.")
                parsed = TypeAdapter(list[schema]).validate_python(parsed["items"])
            else:
                parsed = TypeAdapter(schema).validate_python(parsed)
            return parsed, source
        except Exception as exc:
            if diagnostic_dir is not None:
                (diagnostic_dir / "error.json").write_text(json.dumps({
                    "stage": "generate_response", "message": str(exc),
                }, ensure_ascii=False), encoding="utf-8")
            if isinstance(exc, UserFacingError):
                raise
            raise UserFacingError(
                "OpenCode Go trả về kết quả không hợp lệ", str(exc),
                "Thử lại với ít segment hơn hoặc kiểm tra prompt template.",
            ) from exc


    @staticmethod
    def _translation_map(parsed: object) -> dict[int, str]:
        if not isinstance(parsed, list):
            raise ValueError("Provider không trả về danh sách bản dịch.")
        result: dict[int, str] = {}
        for item in parsed:
            value = item if isinstance(item, TranslationItem) else TranslationItem.model_validate(item)
            text = value.translated_text.strip()
            if value.id in result or not text:
                raise ValueError(f"ID trùng hoặc bản dịch trống: {value.id}")
            result[value.id] = text
        return result

    @staticmethod
    def _full_transcript(segments: list[Segment], include_translation: bool = False) -> str:
        lines: list[str] = []
        for segment in segments:
            speaker_id = segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
            line = f"[{segment.id:04d}] {speaker_id}: {segment.source_text.strip()}"
            if include_translation:
                line += f"\n         TRANSLATION: {segment.translated_text.strip()}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _analysis_segments(segments: list[Segment]) -> list[Segment]:
        """Use at most 250 lines while retaining rare speakers and timeline coverage."""
        if len(segments) <= ANALYSIS_SEGMENT_LIMIT:
            return list(segments)

        selected_indexes: set[int] = set()
        by_speaker: dict[str, list[tuple[int, Segment]]] = {}
        for index, segment in enumerate(segments):
            speaker_id = segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
            by_speaker.setdefault(speaker_id, []).append((index, segment))

        representatives = sorted(
            (
                max(items, key=lambda pair: len(pair[1].source_text.strip()))
                for items in by_speaker.values()
            ),
            key=lambda pair: len(
                by_speaker[pair[1].speaker_id.strip() or "SPEAKER_UNKNOWN"]
            ),
        )
        for index, _segment in representatives[:ANALYSIS_SEGMENT_LIMIT]:
            selected_indexes.add(index)

        remaining = ANALYSIS_SEGMENT_LIMIT - len(selected_indexes)
        if remaining > 0:
            candidates = [index for index in range(len(segments)) if index not in selected_indexes]
            if remaining >= len(candidates):
                selected_indexes.update(candidates)
            elif remaining == 1:
                selected_indexes.add(candidates[len(candidates) // 2])
            else:
                last = len(candidates) - 1
                selected_indexes.update(
                    candidates[round(position * last / (remaining - 1))]
                    for position in range(remaining)
                )
        return [segments[index] for index in sorted(selected_indexes)]

    @staticmethod
    def _speaker_statistics(segments: list[Segment]) -> list[dict[str, object]]:
        counts = Counter(
            segment.speaker_id.strip() or "SPEAKER_UNKNOWN" for segment in segments
        )
        durations: Counter[str] = Counter()
        for segment in segments:
            durations[segment.speaker_id.strip() or "SPEAKER_UNKNOWN"] += segment.duration
        return [
            {
                "speaker_id": speaker_id,
                "segment_count": count,
                "total_duration_seconds": round(durations[speaker_id], 3),
            }
            for speaker_id, count in counts.items()
        ]

    @staticmethod
    def _speaker_context(
        segments: list[Segment],
        dialogue_profile: dict[str, object],
    ) -> list[dict[str, object]]:
        raw_speakers = dialogue_profile.get("speakers")
        speakers = raw_speakers if isinstance(raw_speakers, list) else []
        by_id = {
            str(item.get("speaker_id", "")): item
            for item in speakers
            if isinstance(item, dict)
        }
        raw_rules = dialogue_profile.get("addressing_rules")
        rules = raw_rules if isinstance(raw_rules, list) else []
        return [
            {
                "id": segment.id,
                "speaker_id": segment.speaker_id.strip() or "SPEAKER_UNKNOWN",
                "speaker_profile": by_id.get(
                    segment.speaker_id.strip() or "SPEAKER_UNKNOWN",
                    {},
                ),
                "addressing_rules": [
                    rule
                    for rule in rules
                    if isinstance(rule, dict)
                    and str(rule.get("from_speaker", ""))
                    == (segment.speaker_id.strip() or "SPEAKER_UNKNOWN")
                    and (
                        int(rule.get("from_segment", 0) or 0) <= segment.id
                        and (
                            int(rule.get("to_segment", 0) or 0) == 0
                            or segment.id <= int(rule.get("to_segment", 0) or 0)
                        )
                    )
                ],
            }
            for segment in segments
        ]

    def generate_story(
        self, content: object, prompt: str, input_filename: str, output_filename: str,
    ) -> str:
        parsed, _credential_source = self._generate_structured(
            content, prompt, StoryOutput, 0.1, input_filename, output_filename,
        )
        result = parsed if isinstance(parsed, StoryOutput) else StoryOutput.model_validate(parsed)
        return result.story_markdown

    def correct_asr(
        self, content: object, prompt: str, input_filename: str, output_filename: str,
    ) -> object:
        parsed, _credential_source = self._generate_structured(
            content, prompt, list[AsrCorrectionItem], 0.1, input_filename, output_filename,
        )
        return [
            item.model_dump(mode="json") if isinstance(item, AsrCorrectionItem)
            else AsrCorrectionItem.model_validate(item).model_dump(mode="json")
            for item in parsed
        ]

    def analyze_dialogue(
        self,
        segments: list[Segment],
        proper_name_policy: dict[str, object],
    ) -> DialogueAnalysisResponse:
        analysis_segments = self._analysis_segments(segments)
        content = {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "proper_name_policy": proper_name_policy,
            "transcript_scope": {
                "total_segment_count": len(segments),
                "analyzed_segment_count": len(analysis_segments),
                "is_sampled": len(analysis_segments) < len(segments),
            },
            "speaker_statistics": self._speaker_statistics(segments),
            "transcript": self._full_transcript(analysis_segments),
        }
        prompt, input_filename, output_filename = self._stage_prompt(
            "ANALYSIS",
            analysis_segments,
        )
        parsed, credential_source = self._generate_structured(
            content,
            prompt,
            DialogueProfileOutput,
            0.1,
            input_filename,
            output_filename,
        )
        profile = (
            parsed.model_dump(mode="json")
            if isinstance(parsed, DialogueProfileOutput)
            else DialogueProfileOutput.model_validate(parsed).model_dump(mode="json")
        )
        return DialogueAnalysisResponse(profile, credential_source)

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
        validation_feedback: str = "",
        previous_segment_summary: str = "",
    ) -> TranslationResponse:
        content = {
            "dialogue_profile": dialogue_profile,
            "proper_name_policy": proper_name_policy,
            "speaker_context_for_targets": self._speaker_context(segments, dialogue_profile),
            "context_before": [self._segment_payload(segment) for segment in context_before],
            "segments_to_translate": [self._segment_payload(segment) for segment in segments],
            "context_after": [self._segment_payload(segment) for segment in context_after],
            "previous_translations": previous_translations,
        }
        is_step_batch = (
            self.batch_prompt_context is not None
            and self.batch_prompt_context.get("batch_prompt_mode", "step") in {"step", "video"}
        )
        is_video_batch = (
            self.batch_prompt_context is not None
            and self.batch_prompt_context.get("batch_prompt_mode") == "video"
        )
        if is_video_batch:
            content = {"segments_to_translate": content["segments_to_translate"]}
        if is_step_batch and not is_video_batch and previous_segment_summary:
            content["Boi_canh_doan_truoc"] = previous_segment_summary
        prompt, input_filename, output_filename = self._stage_prompt(
            "TRANSLATION",
            segments,
            batch_index,
            batch_total,
            previous_segment_summary,
        )
        if not is_step_batch:
            prompt += (
                "\n\nFor every translated item, copy id and source_text exactly from the "
                "corresponding segments_to_translate item in the attached JSON. "
                "Return only objects with id, speaker_id, source_text, and translated_text. "
                "Keep id and source_text exactly unchanged. For speaker_id, choose the correct "
                "speaker from the speaker IDs present in the request, correcting diarization errors "
                "when the dialogue context clearly shows that another speaker is speaking. Never invent "
                "a new speaker ID or renumber IDs. Combine consecutive fragments only if each preceding "
                "original end_seconds EXACTLY equals the next start_seconds; any gap or overlap forbids "
                "combining. Semantic continuity is also required. Put one "
                "complete translated utterance at the first ID, with empty translated_text at the "
                "absorbed IDs. Never merge response objects or source_text fields. Every empty "
                "translation is skipped by the tool; do not return extra fields."
            )
        if validation_feedback:
            prompt += (
                "\n\nRETRY FEEDBACK: The previous response failed validation:\n"
                + validation_feedback
                + "\nCorrect the reported problem using the attached input. Keep the same JSON contract."
            )
        expected_ids = [segment.id for segment in segments]
        expected_source_by_id = {segment.id: segment.source_text for segment in segments}
        diagnostic_dir = (
            self.diagnostics_root / f"batch-{batch_index:03d}-{segments[0].id:04d}-{segments[-1].id:04d}"
            if self.diagnostics_root is not None else None
        )
        parsed, credential_source = self._generate_structured(
            content,
            prompt,
            BatchTranslationOutput if is_step_batch else list[SourceVerifiedTranslationItem],
            0.2,
            input_filename,
            output_filename,
            diagnostic_dir=diagnostic_dir,
            expected_ids=expected_ids,
            batch_index=batch_index,
            batch_total=batch_total,
        )
        segment_summary = ""
        if is_step_batch:
            try:
                output = BatchTranslationOutput.model_validate(parsed)
                segment_summary = output.Noi_dung.strip()
                if not segment_summary:
                    raise ValueError("Noi_dung trống.")
                parsed = output.segment_list
            except (ValidationError, TypeError, ValueError) as exc:
                self._record_diagnostic_error(
                    diagnostic_dir, "validate_response", "Thiếu Noi_dung hoặc segment_list.",
                    reason=str(exc),
                )
                raise UserFacingError(
                    "OpenCode Go trả về sai cấu trúc batch",
                    f"Batch {batch_index}/{batch_total}: cần object có Noi_dung và segment_list.",
                    "Kiểm tra response.json trong thư mục chẩn đoán.",
                    str(exc),
                ) from exc
        if not isinstance(parsed, list):
            self._record_diagnostic_error(
                diagnostic_dir, "validate_response", "Phản hồi không phải JSON array.",
                response_type=type(parsed).__name__,
            )
            raise UserFacingError(
                "OpenCode Go trả về sai cấu trúc",
                f"Batch {batch_index}/{batch_total}: phản hồi không phải danh sách bản dịch. Nhật ký: {diagnostic_dir / 'error.json' if diagnostic_dir else 'không có'}",
            )
        result: dict[int, str] = {}
        corrected_speakers: dict[int, str] = {}
        duplicate_ids: set[int] = set()
        source_text_mismatches: list[dict[str, object]] = []
        invalid_items: list[dict[str, object]] = []
        valid_items: list[SourceVerifiedTranslationItem] = []
        for position, item in enumerate(parsed, start=1):
            try:
                value = (
                    item if isinstance(item, SourceVerifiedTranslationItem)
                    else SourceVerifiedTranslationItem.model_validate(item)
                )
            except (ValidationError, TypeError, ValueError) as exc:
                invalid_items.append({"position": position, "reason": str(exc)})
                continue
            valid_items.append(value)
            if value.id in result:
                duplicate_ids.add(value.id)
            expected_source = expected_source_by_id.get(value.id)
            if expected_source is not None and value.source_text != expected_source:
                source_text_mismatches.append({
                    "position": position,
                    "id": value.id,
                    "expected_source_text": expected_source,
                    "received_source_text": value.source_text,
                })
            result[value.id] = value.translated_text.strip()
            corrected_speakers[value.id] = value.speaker_id.strip()
        expected = set(expected_ids)
        actual = set(result)
        missing_ids = sorted(expected - actual)
        extra_ids = sorted(actual - expected)
        count_difference = len(parsed) - len(segments)
        alignment_errors: list[dict[str, object]] = []
        skipped_id: int | None = None
        if count_difference == -1 and not invalid_items:
            expected_position = 0
            response_position = 0
            while response_position < len(valid_items):
                value = valid_items[response_position]
                segment = segments[expected_position]
                if value.id == segment.id and value.source_text == segment.source_text:
                    expected_position += 1
                    response_position += 1
                    continue
                next_position = expected_position + 1
                if (
                    skipped_id is None
                    and next_position < len(segments)
                    and value.id == segments[next_position].id
                    and value.source_text == segments[next_position].source_text
                ):
                    skipped_id = segment.id
                    expected_position = next_position
                    continue
                alignment_errors.append({
                    "position": response_position + 1,
                    "expected_id": segment.id,
                    "expected_source_text": segment.source_text,
                    "received_id": value.id,
                    "received_source_text": value.source_text,
                })
                break
            if not alignment_errors and skipped_id is None and expected_position == len(segments) - 1:
                skipped_id = segments[-1].id
                expected_position += 1
            if not alignment_errors:
                if skipped_id is not None and expected_position == len(segments):
                    result[skipped_id] = ""
                else:
                    alignment_errors.append({"reason": "Không xác định được duy nhất một segment bị thiếu."})
        if (
            count_difference not in (0, -1)
            or duplicate_ids
            or invalid_items
            or extra_ids
            or alignment_errors
            or (count_difference == 0 and missing_ids)
        ):
            issues = {
                "duplicate_ids": sorted(duplicate_ids),
                "missing_ids": missing_ids,
                "extra_ids": extra_ids,
                "source_text_mismatches": source_text_mismatches,
                "invalid_items": invalid_items,
                "alignment_errors": alignment_errors,
                "count_difference": count_difference,
                "skipped_id": skipped_id,
                "expected_count": len(expected_ids),
                "response_count": len(parsed),
            }
            self._record_diagnostic_error(
                diagnostic_dir, "validate_response", "Danh sách bản dịch OpenCode Go không hợp lệ.",
                **issues,
            )
            summary = (
                f"Batch {batch_index}/{batch_total}: ID trùng {sorted(duplicate_ids)}; "
                f"thiếu {missing_ids}; thừa {extra_ids}; "
                f"chênh lệch số mục {count_difference}; "
                f"không căn khớp được {len(alignment_errors)}; "
                f"mục sai cấu trúc {len(invalid_items)}."
            )
            raise UserFacingError(
                "Kết quả OpenCode Go không hợp lệ",
                f"{summary} Nhật ký: {diagnostic_dir / 'error.json' if diagnostic_dir else 'không có'}",
                "Mở request.json, response.json và error.json để đối chiếu.",
                json.dumps(issues, ensure_ascii=False, indent=2),
            )
        return TranslationResponse(result, credential_source, corrected_speakers, segment_summary)

    def review_translation(
        self,
        segments: list[Segment],
        dialogue_profile: dict[str, object],
        proper_name_policy: dict[str, object],
        batch_index: int = 1,
        batch_total: int = 1,
        *,
        context_segments: list[Segment] | None = None,
        quality_issues: list[dict[str, object]] | None = None,
        sentence_sources: dict[int, str] | None = None,
        validation_feedback: str = "",
    ) -> TranslationReviewResponse:
        context = context_segments if context_segments is not None else segments
        content = {
            "dialogue_profile": dialogue_profile,
            "proper_name_policy": proper_name_policy,
            "speaker_context": self._speaker_context(context, dialogue_profile),
            "full_translation": "\n".join(
                f"[{segment.id:04d}] {segment.speaker_id.strip() or 'SPEAKER_UNKNOWN'} "
                f"({segment.start:.3f}–{segment.end:.3f}s): {segment.source_text}\n"
                f"         TRANSLATION: {segment.translated_text}"
                for segment in context
            ),
        }
        prompt, input_filename, output_filename = self._stage_prompt(
            "REVIEW", segments, batch_index, batch_total,
        )
        expected = {segment.id: segment for segment in segments}
        prompt += (
            "\n\nRequested correction IDs: " + json.dumps(list(expected))
            + ". Edit each requested CURRENT translation independently. "
            "Only corrected_text may change. Keep the speaker, source, and sentence boundaries. "
            "All other IDs in full_translation are read-only context; never take their "
            "dialogue into a requested sentence, even when they share a speaker. "
            "Preserve the current names, titles, address, named objects, and sentence force. "
            "Fix real text errors first. Length is advisory; keep complete meaning rather "
            "than forcing a shorter or empty sentence.\n"
            "Fixed source scope for each requested sentence (not permission to regroup):\n"
            + "\n".join(
                f"[{segment.id:04d}] "
                + (sentence_sources or {}).get(segment.id, segment.source_text)
                for segment in segments
            )
            + "\nQuality checks for those same IDs:\n"
            + json.dumps(quality_issues or [], ensure_ascii=False)
        )
        if validation_feedback:
            prompt += "\nPrevious review validation error to correct:\n" + validation_feedback
        diagnostic_dir = (
            self.diagnostics_root / f"review-{batch_index:03d}-{segments[0].id:04d}-{segments[-1].id:04d}"
            if self.diagnostics_root is not None else None
        )
        parsed, credential_source = self._generate_structured(
            content, prompt, list[OpenCodeReviewCorrection], 0.1,
            input_filename, output_filename, diagnostic_dir=diagnostic_dir,
        )
        try:
            if not isinstance(parsed, list):
                raise ValueError("Provider không trả về danh sách correction.")
            corrections: dict[int, str] = {}
            corrected_speakers: dict[int, str] = {}
            issues: list[dict[str, object]] = []
            for item in parsed:
                value = (
                    item if isinstance(item, OpenCodeReviewCorrection)
                    else OpenCodeReviewCorrection.model_validate(item)
                )
                if value.id not in expected or value.id in corrections:
                    raise ValueError(f"Correction có ID trùng hoặc không được yêu cầu: {value.id}")
                # ID is the only mapping key. Review may edit text only;
                # echoed source/speaker and an empty correction cannot alter state.
                text = value.corrected_text.strip() or expected[value.id].translated_text
                corrections[value.id] = text
                corrected_speakers[value.id] = expected[value.id].speaker_id
                record = value.model_dump(mode="json")
                record["corrected_text"] = text
                record["speaker_id"] = expected[value.id].speaker_id
                record["source_text"] = expected[value.id].source_text
                issues.append(record)
            missing = set(expected) - set(corrections)
            if len(parsed) == len(segments) - 1 and len(missing) == 1:
                skipped_id = next(iter(missing))
                corrections[skipped_id] = expected[skipped_id].translated_text
                corrected_speakers[skipped_id] = expected[skipped_id].speaker_id
                self._record_diagnostic_error(
                    diagnostic_dir, "review_count_difference",
                    "Thiếu một correction; giữ nguyên bản dịch hiện tại, speaker và timeline.",
                    missing_ids=[skipped_id],
                )
            elif len(parsed) != len(segments) or missing:
                raise ValueError(
                    f"Review sai số lượng: expected={len(segments)}, received={len(parsed)}, "
                    f"missing={sorted(missing)}"
                )
        except (ValidationError, ValueError, TypeError) as exc:
            self._record_diagnostic_error(
                diagnostic_dir, "validate_review", str(exc),
            )
            raise UserFacingError(
                "Phản hồi review OpenCode Go không hợp lệ", str(exc),
                "Kiểm tra request/response/error trong thư mục diagnostics.",
            ) from exc
        return TranslationReviewResponse(corrections, issues, credential_source, corrected_speakers)

    def translate(self, segments: list[Segment], batch_size: int, progress: ProgressCallback | None = None) -> TranslationResponse:
        if not segments:
            raise UserFacingError("Empty transcript", "No segments to translate.")
        translated = {}
        source = ""
        size = max(1, batch_size)
        batches = [segments[i:i + size] for i in range(0, len(segments), size)]
        for index, batch in enumerate(batches, start=1):
            if progress:
                progress(5 + int((index - 1) / len(batches) * 88), f"Translating batch {index}/{len(batches)}")
            prompt = f"Translate all segments from {self.source_language} to {self.target_language}. Preserve meaning and return each ID once."
            parsed, source = self._generate_structured([self._segment_payload(s) for s in batch], prompt,
                list[TranslationItem], 0.2, f"batch-{index}.json", f"batch-{index}-result.json")
            result = {item.id: item.translated_text.strip() for item in parsed}
            if set(result) != {s.id for s in batch} or not all(result.values()):
                raise UserFacingError("Incomplete OpenCode Go response", f"Batch {index} returned missing or extra IDs.")
            translated.update(result)
        if progress:
            progress(95, "Translation complete")
        return TranslationResponse(translated, source)


    def rewrite_for_timing(self, items: list[dict[str, object]], batch_size: int = 30,
                           progress: ProgressCallback | None = None) -> TranslationResponse:
        if not items:
            raise UserFacingError("No segments selected", "Select at least one segment.")
        rewritten = {}
        source = ""
        size = max(1, batch_size)
        batches = [items[i:i + size] for i in range(0, len(items), size)]
        for index, batch in enumerate(batches, start=1):
            if progress:
                progress(5 + int((index - 1) / len(batches) * 88), f"Rewriting batch {index}/{len(batches)}")
            prompt = load_timing_rewrite_prompt(self.target_language)
            parsed, source = self._generate_structured(batch, prompt, list[TranslationItem], 0.1,
                f"timing-{index}.json", f"timing-{index}-result.json")
            result = {item.id: item.translated_text.strip() for item in parsed}
            if set(result) != {int(item["id"]) for item in batch} or not all(result.values()):
                raise UserFacingError("Incomplete OpenCode Go response", f"Batch {index} returned missing or extra IDs.")
            rewritten.update(result)
        if progress:
            progress(95, "Rewrite complete")
        return TranslationResponse(rewritten, source)


    @staticmethod
    def _close(client: object) -> None:
        return


    @staticmethod
    def _friendly_error(error: Exception, api_key: str = "") -> UserFacingError:
        detail = repr(error).replace(api_key, "***") if api_key else repr(error)
        return UserFacingError("OpenCode Go request failed", str(error).replace(api_key, "***") if api_key else str(error),
                               "Check OPENCODE_API_KEY, model access and network connection.", detail)


    @staticmethod
    def _credential():
        credential = CredentialService.get(OPENCODE_GO)
        if not credential.configured or not credential.value:
            raise UserFacingError(
                "Chưa cấu hình OpenCode Go", "Ứng dụng chưa có OpenCode Go API key.",
                "Nhập OPENCODE_API_KEY vào api_keys.env cạnh translatevideo.exe rồi mở lại ứng dụng.",
            )
        return credential

    def _request(self, content: object, prompt: str, max_tokens: int) -> tuple[str, str]:
        credential = self._credential()
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": prompt + "\nThe input JSON is in the user message; any reference to an attached file means that JSON. Return valid JSON only."},
                {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = Request(
            OPENCODE_GO_API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {credential.value}",
                "Content-Type": "application/json",
                "User-Agent": "TransLanguage/1.0",
                "x-opencode-session": self.session_id,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:
                result = json.load(response)
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise UserFacingError(
                "OpenCode Go API từ chối request",
                f"HTTP {exc.code} khi gọi OpenCode Go.",
                "Kiểm tra API key, số dư và quyền truy cập model rồi thử lại.",
                detail.replace(credential.value, "***"),
            ) from exc
        except URLError as exc:
            raise UserFacingError(
                "Không kết nối được OpenCode Go", str(exc.reason),
                "Kiểm tra kết nối mạng rồi thử lại.",
            ) from exc
        choices = result.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ValueError(f"OpenCode Go dừng trước khi hoàn tất: {choices[0].get('finish_reason') if choices else 'no choices'}")
        answer = choices[0].get("message", {}).get("content") or ""
        if not answer.strip():
            raise ValueError("OpenCode Go trả về JSON rỗng.")
        return answer, credential.source

register_translation_provider(
    OPENCODE_GO_PROVIDER_NAME,
    lambda model_name, source_language, target_language, options: OpenCodeGoTranslationService(
        model_name,
        source_language,
        target_language,
        options,
    ),
)




