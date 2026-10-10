from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..errors import UserFacingError
from ..models import Segment
from .translation_provider import TranslationProvider

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
        actual: set[str] = set()
        invalid: list[str] = []
        for item in speakers:
            if not isinstance(item, dict):
                continue
            speaker_id = str(item.get("speaker_id", "")).strip()
            if speaker_id:
                actual.add(speaker_id)
            if not speaker_id or not str(item.get("story_role", "")).strip():
                invalid.append(speaker_id or "<trống>")
            if not str(item.get("default_self_reference", "")).strip():
                invalid.append(f"{speaker_id or '<trống>'}: thiếu cách tự xưng")
        if actual != expected or invalid:
            raise UserFacingError(
                "Hồ sơ speaker từ AI không hợp lệ",
                "Giai đoạn phân tích toàn truyện không trả đủ vai trò và cách tự xưng.",
                "Chạy lại Step 3 hoặc chọn model mạnh hơn.",
                (
                    f"Missing: {sorted(expected - actual)}; extra: {sorted(actual - expected)}; "
                    f"invalid: {invalid}"
                ),
            )
        raw_rules = profile.get("addressing_rules")
        rules = raw_rules if isinstance(raw_rules, list) else []
        invalid_rules: list[str] = []
        for item in rules:
            if not isinstance(item, dict):
                invalid_rules.append(repr(item))
                continue
            source = str(item.get("from_speaker", "")).strip()
            target = str(item.get("to_speaker", "")).strip()
            if source not in expected or target not in expected:
                invalid_rules.append(f"{source} → {target}")
        if invalid_rules:
            raise UserFacingError(
                "Ma trận xưng hô từ AI không hợp lệ",
                "Một hoặc nhiều quy tắc tham chiếu speaker không tồn tại.",
                "Chạy lại Step 3 hoặc chọn model mạnh hơn.",
                f"Invalid rules: {invalid_rules}",
            )
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

        if consistency_enabled:
            if progress:
                progress(3, "AI đang đọc toàn bộ hội thoại và xác định vai trò speaker…")
            response = self.provider.analyze_dialogue(
                segments,
                resolved_name_policy,
            )
            dialogue_profile = self._validate_dialogue_profile(response.profile, segments)
            credential_source = response.credential_source or credential_source

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
                base = 22 if consistency_enabled else 5
                span = 50 if consistency_enabled else 88
                percent = base + int((index - 1) / len(batches) * span)
                progress(percent, f"AI đang dịch batch {index}/{len(batches)}…")
            response = self.provider.translate_batch(
                batch,
                dialogue_profile,
                before,
                after,
                recent_translations,
                resolved_name_policy,
            )
            batch_result = self._validate_translations(response.translations, batch, "dịch")
            translated.update(batch_result)
            credential_source = response.credential_source or credential_source

        for segment in segments:
            segment.translated_text = translated[segment.id]

        review_count = 0
        review_issues: list[dict[str, object]] = []
        if consistency_enabled:
            if progress:
                progress(76, "AI đang kiểm duyệt toàn bộ bản dịch…")
            response = self.provider.review_translation(
                segments,
                dialogue_profile,
                resolved_name_policy,
            )
            valid_ids = {segment.id for segment in segments}
            extra = sorted(set(response.corrections) - valid_ids)
            empty = sorted(
                segment_id
                for segment_id, text in response.corrections.items()
                if not str(text).strip()
            )
            if extra or empty:
                raise UserFacingError(
                    "Kết quả kiểm duyệt không hợp lệ",
                    "AI trả về correction sai ID hoặc nội dung trống.",
                    "Chạy lại Step 3 hoặc chọn model mạnh hơn.",
                    f"Extra IDs: {extra}; empty IDs: {empty}",
                )
            for segment_id, text in response.corrections.items():
                translated[segment_id] = str(text).strip()
            for segment in segments:
                segment.translated_text = translated[segment.id]
            review_issues = response.issues
            credential_source = response.credential_source or credential_source
            review_count = 1

        if progress:
            progress(97, "Đã hoàn tất dịch và rà soát; đang tạo output…")
        return TranslationWorkflowResult(
            translations=translated,
            dialogue_profile=dialogue_profile,
            proper_name_policy=resolved_name_policy,
            credential_source=credential_source,
            analysis_batch_count=1 if consistency_enabled else 0,
            translation_batch_count=len(batches),
            review_batch_count=review_count,
            review_issues=review_issues,
        )
