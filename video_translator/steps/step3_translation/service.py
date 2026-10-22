from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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
        empty = sorted(segment_id for segment_id, text in normalized.items() if not text)
        if empty:
            raise UserFacingError(
                "AI trả về bản dịch trống",
                f"Giai đoạn {stage} trả về nội dung trống cho ID: {empty}",
                "Chạy lại Step 3 hoặc chọn provider/model khác.",
            )
        return normalized

    def run(
        self,
        segments: list[Segment],
        consistency_enabled: bool = True,
        proper_name_policy: dict[str, object] | None = None,
        progress: ProgressCallback | None = None,
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
            )
            batch_result = self._validate_translations(response.translations, batch, "dịch")
            translated.update(batch_result)
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
            review_issues=[],
        )


