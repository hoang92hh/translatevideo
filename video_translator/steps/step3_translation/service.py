from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from ...config.gemini import GEMINI_PROVIDER_NAME
from ...config.web_translation import CHATGPT_WEB_PROVIDER_NAME
from ...errors import UserFacingError
from ...common.models import Segment
from .providers import TranslationProvider

ProgressCallback = Callable[[int, str], None]
CONTEXT_SEGMENT_COUNT = 3


@dataclass(frozen=True, slots=True)
class TranslationWorkflowResult:
    translations: dict[int, str]
    dialogue_profile: dict[str, object]
    proper_name_policy: dict[str, object]
    credential_source: str
    analysis_batch_count: int
    translation_batch_count: int
    review_batch_count: int
    review_issues: list[dict[str, object]]
    batch_summaries: list[dict[str, object]]


class TranslationWorkflow:
    """Điều phối dịch nhiều giai đoạn mà không phụ thuộc Gemini/ChatGPT/Claude."""

    def __init__(self, provider: TranslationProvider, batch_size: int) -> None:
        self.provider = provider
        self.batch_size = max(1, batch_size)

    @staticmethod
    def _batches(segments: list[Segment], size: int) -> list[list[Segment]]:
        return [segments[index : index + size] for index in range(0, len(segments), size)]

    @staticmethod
    def _validate_dialogue_profile(
        profile: dict[str, object],
        segments: list[Segment],
    ) -> dict[str, object]:
        expected = {
            segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
            for segment in segments
        }
        raw_speakers = profile.get("speakers")
        speakers = raw_speakers if isinstance(raw_speakers, list) else []
        normalized_speakers: list[dict[str, object]] = []
        actual: set[str] = set()
        for item in speakers:
            if not isinstance(item, dict):
                continue
            speaker_id = str(item.get("speaker_id", "")).strip()
            if not speaker_id or speaker_id not in expected or speaker_id in actual:
                continue
            importance = str(item.get("importance", "minor")).strip().lower()
            gender = str(item.get("gender", "unknown")).strip().lower()
            normalized = dict(item)
            normalized["speaker_id"] = speaker_id
            normalized["importance"] = (
                importance if importance in {"main", "supporting", "minor"} else "minor"
            )
            normalized["gender"] = gender if gender in {"male", "female", "unknown"} else "unknown"
            normalized.setdefault("story_role", "")
            normalized.setdefault("default_self_reference", "")
            normalized_speakers.append(normalized)
            actual.add(speaker_id)
        for speaker_id in sorted(expected - actual):
            normalized_speakers.append(
                {
                    "speaker_id": speaker_id,
                    "character_name": "",
                    "story_role": "",
                    "importance": "minor",
                    "gender": "unknown",
                    "default_self_reference": "",
                    "confidence": "low",
                    "notes": "Không đủ dữ liệu phân tích để phân loại.",
                }
            )
        profile["speakers"] = normalized_speakers
        raw_rules = profile.get("addressing_rules")
        rules = raw_rules if isinstance(raw_rules, list) else []
        valid_rules: list[dict[str, object]] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            source = str(item.get("from_speaker", "")).strip()
            target = str(item.get("to_speaker", "")).strip()
            if source in expected and target in expected:
                valid_rules.append(item)
        profile["addressing_rules"] = valid_rules
        return profile

    @staticmethod
    def _validate_translations(
        result: dict[int, str],
        expected_segments: list[Segment],
        stage: str,
    ) -> dict[int, str]:
        expected = {segment.id for segment in expected_segments}
        actual = set(result)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise UserFacingError(
                "AI trả về sai danh sách segment",
                f"Giai đoạn {stage} không giữ đúng danh sách ID.",
                "Chạy lại Step 3 hoặc chọn provider/model khác.",
                f"Missing IDs: {missing}; extra IDs: {extra}",
            )
        normalized = {segment_id: str(text).strip() for segment_id, text in result.items()}
        return normalized

    @staticmethod
    def _target_language_issues(
        result: dict[int, str],
        expected_segments: list[Segment],
        target_language: str,
    ) -> list[dict[str, object]]:
        source_by_id = {segment.id: segment.source_text.strip() for segment in expected_segments}
        issues: list[dict[str, object]] = []
        for segment_id, text in result.items():
            if not text.strip():
                continue
            reasons: list[str] = []
            if source_by_id.get(segment_id) and text.strip() == source_by_id[segment_id]:
                reasons.append("translation_unchanged_from_source")
            if target_language == "Vietnamese" and re.search(r"[\u3400-\u9fff]", text):
                reasons.append("source_language_leftovers")
            if reasons:
                issues.append({"id": segment_id, "issues": reasons})
        return issues

    @staticmethod
    def _validate_target_language(
        result: dict[int, str],
        expected_segments: list[Segment],
        target_language: str,
    ) -> None:
        source_by_id = {segment.id: segment.source_text.strip() for segment in expected_segments}
        unchanged = [segment_id for segment_id, text in result.items()
                     if source_by_id.get(segment_id) and text.strip() == source_by_id[segment_id]]
        cjk_leftovers = [segment_id for segment_id, text in result.items()
                         if target_language == "Vietnamese" and re.search(r"[\u3400-\u9fff]", text)]
        if unchanged or cjk_leftovers:
            raise UserFacingError(
                "Bản dịch chưa đạt ngôn ngữ đích",
                "Gemini trả lại segment nguyên văn hoặc còn ký tự ngôn ngữ nguồn.",
                "Step 3 sẽ chạy lại batch lỗi; nếu tiếp tục lỗi, đổi model hoặc kiểm tra prompt template.",
                f"Unchanged IDs: {unchanged}; source-language leftovers: {cjk_leftovers}",
            )

    @staticmethod
    def _validate_speakers(
        result: dict[int, str],
        expected_segments: list[Segment],
        allowed_speakers: set[str] | None = None,
    ) -> dict[int, str]:
        expected_ids = {segment.id for segment in expected_segments}
        known_speakers = {
            segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
            for segment in expected_segments
        }
        if allowed_speakers is not None:
            known_speakers = allowed_speakers
        if not result:
            return {}
        if set(result) != expected_ids:
            raise UserFacingError(
                "AI trả về sai danh sách speaker",
                "Kết quả hiệu chỉnh speaker không giữ đúng danh sách ID segment.",
                "Chạy lại Step 3 hoặc chọn provider/model khác.",
            )
        invalid = sorted({speaker for speaker in result.values() if speaker not in known_speakers})
        if invalid:
            raise UserFacingError(
                "AI trả về speaker không hợp lệ",
                "Gemini đã tạo speaker mới thay vì chọn một speaker có trong transcript.",
                "Kiểm tra transcript đầu vào hoặc chạy lại Step 3.",
                f"Invalid speakers: {invalid}",
            )
        return {segment_id: speaker.strip() for segment_id, speaker in result.items()}

    def run(
        self,
        segments: list[Segment],
        consistency_enabled: bool = True,
        proper_name_policy: dict[str, object] | None = None,
        progress: ProgressCallback | None = None,
        target_language: str = "",
    ) -> TranslationWorkflowResult:
        if not segments:
            raise UserFacingError(
                "Transcript trống",
                "Step 3 không nhận được segment nào để dịch.",
                "Chọn lại output hợp lệ của Step 2.",
            )

        batches = self._batches(segments, self.batch_size)
        resolved_name_policy = dict(proper_name_policy or {})
        dialogue_profile: dict[str, object] = {}
        credential_source = ""

        translated: dict[int, str] = {}
        previous_segment_summary = ""
        batch_summaries: list[dict[str, object]] = []
        review_issues: list[dict[str, object]] = []
        is_gemini = getattr(self.provider, "provider_name", "") == GEMINI_PROVIDER_NAME
        supports_speaker_corrections = getattr(self.provider, "provider_name", "") in {
            GEMINI_PROVIDER_NAME, CHATGPT_WEB_PROVIDER_NAME,
        }
        for index, batch in enumerate(batches, start=1):
            start = (index - 1) * self.batch_size
            end = start + len(batch)
            before = segments[max(0, start - CONTEXT_SEGMENT_COUNT) : start]
            after = segments[end : end + CONTEXT_SEGMENT_COUNT]
            recent_ids = {segment.id for segment in before}
            recent_translations = {
                segment_id: text
                for segment_id, text in translated.items()
                if segment_id in recent_ids
            }
            if progress:
                percent = 5 + int((index - 1) / len(batches) * 90)
                progress(
                    percent,
                    f"AI đang phân tích, dịch và kiểm tra batch {index}/{len(batches)}…",
                )
            last_error: UserFacingError | None = None
            for attempt in range(2):
                try:
                    retry_options = {}
                    if is_gemini and last_error is not None:
                        retry_options["validation_feedback"] = (
                            last_error.error_message.message + "\n"
                            + last_error.error_message.technical_detail
                        )
                    response = self.provider.translate_batch(
                        batch,
                        dialogue_profile,
                        before,
                        after,
                        recent_translations,
                        resolved_name_policy,
                        response_file=True,
                        batch_index=index,
                        batch_total=len(batches),
                        **({"previous_segment_summary": previous_segment_summary} if is_gemini else {}),
                        **retry_options,
                    )
                    batch_result = self._validate_translations(response.translations, batch, "dịch")
                    # Gemini content errors belong to segment review. Structural
                    # errors still use the existing batch validation/retry path.
                    if getattr(self.provider, "provider_name", "") not in {
                        GEMINI_PROVIDER_NAME, CHATGPT_WEB_PROVIDER_NAME,
                    }:
                        self._validate_target_language(batch_result, batch, target_language)
                    if supports_speaker_corrections:
                        # The provider's existing count-minus-one branch can
                        # fill a missing translation with "". Keep its original
                        # speaker rather than rejecting the accepted count case.
                        speaker_result = dict(response.corrected_speakers)
                        for segment in batch:
                            if segment.id not in speaker_result and not batch_result[segment.id]:
                                speaker_result[segment.id] = segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
                        corrected_speakers = self._validate_speakers(
                            speaker_result, batch,
                            {
                                segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
                                for segment in [*before, *batch, *after]
                            },
                        )
                    break
                except UserFacingError as exc:
                    last_error = exc
                    if attempt == 1:
                        raise
            else:
                assert last_error is not None
                raise last_error
            translated.update(batch_result)
            if is_gemini and getattr(self.provider, "batch_prompt_context", None) is not None:
                previous_segment_summary = response.segment_summary
                batch_summaries.append({"batch_index": index, "Noi_dung": previous_segment_summary})
            # Gemini automatic assessment/review is temporarily disabled.
            # if is_gemini:
            #     review_issues.extend(self._target_language_issues(
            #         batch_result, batch, target_language,
            #     ))
            if not supports_speaker_corrections:
                corrected_speakers = self._validate_speakers(response.corrected_speakers, batch)
            for segment in batch:
                if segment.id in corrected_speakers:
                    segment.speaker_id = corrected_speakers[segment.id]
            credential_source = response.credential_source or credential_source

        for segment in segments:
            segment.translated_text = translated[segment.id]

        if progress:
            progress(97, "Đã nhận đủ kết quả dịch; đang tạo output…")
        return TranslationWorkflowResult(
            translations=translated,
            dialogue_profile=dialogue_profile,
            proper_name_policy=resolved_name_policy,
            credential_source=credential_source,
            analysis_batch_count=0,
            translation_batch_count=len(batches),
            review_batch_count=0,
            review_issues=review_issues,
            batch_summaries=batch_summaries,
        )


