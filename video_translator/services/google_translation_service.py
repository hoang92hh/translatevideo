from __future__ import annotations

import json
from typing import Callable

from pydantic import BaseModel, Field

from ..config.gemini import GEMINI_PROVIDER_NAME, GEMINI_RECOMMENDED_MODEL_IDS
from ..errors import UserFacingError
from ..models import Segment
from .credential_service import GOOGLE_GEMINI, CredentialService
from .translation_provider import (
    DialogueAnalysisResponse,
    TranslationResponse,
    TranslationReviewResponse,
    register_translation_provider,
)


ProgressCallback = Callable[[int, str], None]


class TranslationItem(BaseModel):
    id: int
    translated_text: str


class SpeakerGuidance(BaseModel):
    speaker_id: str
    character_name: str = ""
    story_role: str
    gender: str = "unknown"
    age_group: str = "unknown"
    personality: str = ""
    default_self_reference: str
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
    corrected_text: str
    issue_type: str
    reason: str = ""


class GoogleTranslationService:
    def __init__(self, model_name: str, source_language: str, target_language: str) -> None:
        self.model_name = model_name
        self.source_language = source_language
        self.target_language = target_language

    @staticmethod
    def _client():
        credential = CredentialService.get(GOOGLE_GEMINI)
        if not credential.configured or not credential.value:
            raise UserFacingError(
                "Chưa cấu hình Google Gemini",
                "Ứng dụng chưa có Google API key dùng chung.",
                "Mở Cài đặt → API & Providers, lưu Google API key rồi chạy lại Step 3.",
            )
        try:
            from google import genai
        except ModuleNotFoundError as exc:
            raise UserFacingError(
                "Thiếu Google Gen AI SDK",
                "Không tìm thấy thư viện google-genai.",
                "Cài lại dependency bằng đúng Python của ứng dụng: python -m pip install -e .",
                repr(exc),
            ) from exc
        return genai.Client(api_key=credential.value), credential

    @classmethod
    def validate_connection(cls, model_name: str) -> str:
        client, credential = cls._client()
        try:
            client.models.get(model=model_name)
        except Exception as exc:
            raise cls._friendly_error(exc, credential.value) from exc
        finally:
            cls._close(client)
        return credential.source

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
        self,
        content: object,
        system_instruction: str,
        response_schema: object,
        temperature: float,
    ) -> tuple[object, str]:
        client, credential = self._client()
        try:
            from google.genai import types

            response = client.models.generate_content(
                model=self.model_name,
                contents=json.dumps(content, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=temperature,
                ),
            )
            if response.parsed is None:
                raise ValueError("Gemini không trả về structured output.")
            return response.parsed, credential.source
        except UserFacingError:
            raise
        except Exception as exc:
            raise self._friendly_error(exc, credential.value) from exc
        finally:
            self._close(client)

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

    def analyze_dialogue(
        self,
        segments: list[Segment],
        proper_name_policy: dict[str, object],
    ) -> DialogueAnalysisResponse:
        content = {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "proper_name_policy": proper_name_policy,
            "full_transcript": self._full_transcript(segments),
        }
        parsed, credential_source = self._generate_structured(
            content,
            (
                "Read full_transcript from beginning to end as one complete story before answering. Every line "
                "has [segment ID] SPEAKER_ID: source dialogue. Return exactly one speakers entry for every "
                "distinct SPEAKER_ID, with that character's identity, role in the whole story, personality, "
                "speech style, provider-neutral voice_description, and an exact default_self_reference written "
                "in target_language. Describe only voice traits supported by the dialogue (such as perceived "
                "age, pitch, energy, pace, and tone); do not choose a provider-specific voice or model. Infer directed "
                "addressing_rules only from story evidence. Every self_reference, direct_address, and "
                "third_person_reference must be an exact expression in target_language, never source-language "
                "text or an explanation in parentheses. Use from_segment/to_segment when a relationship or form "
                "of address changes during the story; zero means the entire story. Record uncertainty instead "
                "of inventing gender, age, status, or relationships. Build a stable terminology guide. "
                "Build one cumulative names list for people, places, organizations, and other proper names. "
                "For each source name, choose exactly one canonical_name by following proper_name_policy. "
                "Chinese personal names use Sino-Vietnamese readings only when the policy requests them; use "
                "Pinyin when requested. Restore a foreign name represented in Chinese characters only when "
                "confident, and record uncertainty otherwise. The profile will be the binding role and pronoun "
                "reference for all later translation batches, so make it complete and internally consistent."
            ),
            DialogueProfileOutput,
            0.1,
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
        parsed, credential_source = self._generate_structured(
            content,
            (
                "You are a professional audiovisual subtitle translator. Translate only segments_to_translate "
                f"from {self.source_language} to {self.target_language}. For each target ID, read its exact "
                "speaker_profile and applicable addressing_rules in speaker_context_for_targets before writing. "
                "Treat those roles and target-language forms of address as binding. Follow dialogue_profile "
                "consistently for pronouns, relationships, speech style, names, and "
                "terminology. For every proper name present in dialogue_profile.names, copy its canonical_name "
                "exactly and never romanize, translate, or reinterpret that name again inside a batch. For an "
                "unlisted proper name, follow proper_name_policy consistently. Use context_before, context_after, "
                "and previous_translations only as context. "
                "Treat duration_seconds as a soft timing target: prefer concise, natural spoken phrasing, but "
                "never lose essential meaning, negation, intent, names, numbers, or cause-and-effect. Do not "
                "invent a relationship when the profile marks it uncertain. Return every target ID exactly once. "
                "Do not return context IDs and do not merge, split, omit, explain, or add IDs."
            ),
            list[TranslationItem],
            0.2,
        )
        return TranslationResponse(self._translation_map(parsed), credential_source)

    def review_translation(
        self,
        segments: list[Segment],
        dialogue_profile: dict[str, object],
        proper_name_policy: dict[str, object],
    ) -> TranslationReviewResponse:
        content = {
            "dialogue_profile": dialogue_profile,
            "proper_name_policy": proper_name_policy,
            "speaker_context": self._speaker_context(segments, dialogue_profile),
            "full_translation": self._full_transcript(segments, include_translation=True),
        }
        parsed, credential_source = self._generate_structured(
            content,
            (
                "You are the final consistency auditor for a complete audiovisual translation. Read "
                "full_translation from beginning to end, compare every line with dialogue_profile and its "
                "speaker_context, and find only real errors in role, self-reference, direct address, third-person "
                "reference, names, terminology, meaning, or batch continuity. Return corrections only for IDs "
                "that must change; return an empty JSON list when no correction is needed. Do not rewrite correct "
                f"lines merely for style. Every corrected_text must be natural spoken {self.target_language} and "
                "preserve the source meaning and timing awareness. "
                "Replace every proper-name variant with the exact canonical_name from dialogue_profile.names; "
                "do not independently transliterate a listed name. Use proper_name_policy only for names that "
                "are not yet listed. Never add an ID that is absent from full_translation."
            ),
            list[TranslationCorrection],
            0.1,
        )
        if not isinstance(parsed, list):
            raise ValueError("Provider không trả về danh sách correction.")
        corrections: dict[int, str] = {}
        issues: list[dict[str, object]] = []
        for item in parsed:
            value = (
                item
                if isinstance(item, TranslationCorrection)
                else TranslationCorrection.model_validate(item)
            )
            if value.id in corrections or not value.corrected_text.strip():
                raise ValueError(f"Correction trùng hoặc trống: {value.id}")
            corrections[value.id] = value.corrected_text.strip()
            issues.append(value.model_dump(mode="json"))
        return TranslationReviewResponse(corrections, issues, credential_source)

    def translate(
        self,
        segments: list[Segment],
        batch_size: int,
        progress: ProgressCallback | None = None,
    ) -> TranslationResponse:
        if not segments:
            raise UserFacingError(
                "Transcript trống",
                "Step 3 không nhận được segment nào để dịch.",
                "Chọn lại output hợp lệ của Step 2.",
            )
        client, credential = self._client()
        translated: dict[int, str] = {}
        batches = [segments[index : index + batch_size] for index in range(0, len(segments), batch_size)]
        try:
            from google.genai import types

            for batch_index, batch in enumerate(batches, start=1):
                if progress:
                    percent = 5 + int((batch_index - 1) / len(batches) * 88)
                    progress(percent, f"Đang dịch batch {batch_index}/{len(batches)}…")
                content = json.dumps(
                    [
                        {
                            "id": segment.id,
                            "start_seconds": round(segment.start, 3),
                            "end_seconds": round(segment.end, 3),
                            "duration_seconds": round(segment.duration, 3),
                            "speaker_id": segment.speaker_id,
                            "text": segment.source_text,
                        }
                        for segment in batch
                    ],
                    ensure_ascii=False,
                )
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=content,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            "You are a professional audiovisual subtitle translator. "
                            f"Translate every segment from {self.source_language} to {self.target_language}. "
                            "Use adjacent segments for context and write clear, natural spoken language. "
                            "Treat duration_seconds as a soft timing target: prefer concise, idiomatic phrasing "
                            "that can be spoken within that duration at a natural pace, removing redundancy and "
                            "unnecessary filler when possible. Never omit or alter essential meaning, negation, "
                            "speaker intent, proper names, numbers, or cause-and-effect relationships. "
                            "Do not produce an unclear fragment merely to meet timing. If a complete and clear "
                            "translation cannot reasonably fit, preserve meaning and clarity even if it may run "
                            "longer than duration_seconds. Return every input ID exactly once. "
                            "Do not merge, split, omit, explain, or add IDs."
                        ),
                        response_mime_type="application/json",
                        response_schema=list[TranslationItem],
                        temperature=0.2,
                    ),
                )
                parsed = response.parsed
                if not isinstance(parsed, list):
                    raise UserFacingError(
                        "Kết quả Gemini không hợp lệ",
                        f"Batch {batch_index} không trả về danh sách bản dịch có cấu trúc.",
                        "Chạy lại Step 3 hoặc chọn model khác.",
                    )
                batch_result: dict[int, str] = {}
                for item in parsed:
                    value = item if isinstance(item, TranslationItem) else TranslationItem.model_validate(item)
                    text = value.translated_text.strip()
                    if value.id in batch_result or not text:
                        raise ValueError(f"ID trùng hoặc bản dịch trống: {value.id}")
                    batch_result[value.id] = text
                expected = {segment.id for segment in batch}
                if set(batch_result) != expected:
                    missing = sorted(expected - set(batch_result))
                    extra = sorted(set(batch_result) - expected)
                    raise UserFacingError(
                        "Gemini trả về thiếu segment",
                        f"Batch {batch_index} không giữ đúng danh sách segment.",
                        "Chạy lại Step 3 hoặc giảm số segment mỗi batch.",
                        f"Missing IDs: {missing}; extra IDs: {extra}",
                    )
                translated.update(batch_result)
        except UserFacingError:
            raise
        except Exception as exc:
            raise self._friendly_error(exc, credential.value) from exc
        finally:
            self._close(client)
        if progress:
            progress(95, "Đã dịch xong; đang tạo output…")
        return TranslationResponse(translated, credential.source)

    def rewrite_for_timing(
        self,
        items: list[dict[str, object]],
        batch_size: int = 30,
        progress: ProgressCallback | None = None,
    ) -> TranslationResponse:
        """Rút gọn các câu dịch đã có dựa trên thời lượng TTS đo được."""
        if not items:
            raise UserFacingError(
                "Chưa chọn segment",
                "Không có segment nào được chọn để AI chỉnh sửa.",
                "Đánh dấu ít nhất một checkbox rồi thử lại.",
            )
        client, credential = self._client()
        rewritten: dict[int, str] = {}
        size = max(1, batch_size)
        batches = [items[index : index + size] for index in range(0, len(items), size)]
        try:
            from google.genai import types

            for batch_index, batch in enumerate(batches, start=1):
                if progress:
                    percent = 5 + int((batch_index - 1) / len(batches) * 88)
                    progress(percent, f"AI đang rút gọn batch {batch_index}/{len(batches)}…")
                content = json.dumps(batch, ensure_ascii=False)
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=content,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            "You are revising existing audiovisual translations so their synthesized speech "
                            "fits a measured time window. For every item, rewrite current_translation in "
                            f"{self.target_language}; use source_text only to protect meaning and context. "
                            "The fields measured_tts_seconds, allowed_seconds, current_required_speed, "
                            "target_speed, and requested_reduction_ratio describe the real TTS result. "
                            "Make the sentence concise enough to fit at or below target_speed, using the "
                            "requested reduction as a practical minimum. Prefer natural spoken phrasing and "
                            "remove repetition, filler, and optional wording. Preserve essential meaning, "
                            "negation, speaker intent, proper names, numbers, and cause-and-effect. Do not add "
                            "facts, explanations, labels, or alternatives. Return every input ID exactly once "
                            "and do not merge, split, or omit IDs."
                        ),
                        response_mime_type="application/json",
                        response_schema=list[TranslationItem],
                        temperature=0.1,
                    ),
                )
                parsed = response.parsed
                if not isinstance(parsed, list):
                    raise UserFacingError(
                        "Kết quả Gemini không hợp lệ",
                        f"Batch {batch_index} không trả về danh sách nội dung rút gọn có cấu trúc.",
                        "Thử lại hoặc chọn model Gemini khác ở Step 3.",
                    )
                batch_result: dict[int, str] = {}
                for item in parsed:
                    value = item if isinstance(item, TranslationItem) else TranslationItem.model_validate(item)
                    text = value.translated_text.strip()
                    if value.id in batch_result or not text:
                        raise ValueError(f"ID trùng hoặc nội dung rút gọn trống: {value.id}")
                    batch_result[value.id] = text
                expected = {int(item["id"]) for item in batch}
                if set(batch_result) != expected:
                    missing = sorted(expected - set(batch_result))
                    extra = sorted(set(batch_result) - expected)
                    raise UserFacingError(
                        "Gemini trả về thiếu segment",
                        f"Batch {batch_index} không giữ đúng danh sách segment đã chọn.",
                        "Thử lại với ít segment hơn.",
                        f"Missing IDs: {missing}; extra IDs: {extra}",
                    )
                rewritten.update(batch_result)
        except UserFacingError:
            raise
        except Exception as exc:
            raise self._friendly_error(exc, credential.value) from exc
        finally:
            self._close(client)
        if progress:
            progress(95, "AI đã trả về nội dung rút gọn.")
        return TranslationResponse(rewritten, credential.source)

    @staticmethod
    def _close(client: object) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _friendly_error(error: Exception, api_key: str = "") -> UserFacingError:
        detail = repr(error).replace(api_key, "***") if api_key else repr(error)
        message = str(error).replace(api_key, "***") if api_key else str(error)
        lowered = message.lower()
        raw_code = getattr(error, "code", 0) or getattr(error, "status_code", 0) or 0
        try:
            code = int(raw_code)
        except (TypeError, ValueError):
            code = 0
        if code == 402 or any(token in lowered for token in ("payment_required", "prepay credit", "credit balance")):
            return UserFacingError(
                "Tài khoản Gemini hết credit",
                "Google Gemini yêu cầu bổ sung credit hoặc kích hoạt thanh toán.",
                "Kiểm tra Billing của Google project, nạp credit hoặc bật auto-reload rồi thử lại.",
                detail,
            )
        if code == 403 or "permission_denied" in lowered or "permission denied" in lowered:
            blocked_key = any(token in lowered for token in ("reported as leaked", "api key was blocked"))
            if blocked_key:
                return UserFacingError(
                    "Google API key đã bị chặn",
                    "Google đã chặn API key vì lý do bảo mật.",
                    "Tạo API key mới, xóa key cũ khỏi nơi công khai rồi lưu key mới trong Cài đặt.",
                    detail,
                )
            model_related = "model" in lowered or "models/" in lowered
            recommended = " hoặc ".join(GEMINI_RECOMMENDED_MODEL_IDS)
            return UserFacingError(
                "Không có quyền sử dụng model Gemini" if model_related else "Google Gemini từ chối quyền truy cập",
                (
                    "API key có thể hợp lệ nhưng Google project không được phép sử dụng model đã chọn."
                    if model_related
                    else "API key hoặc Google project không có quyền truy cập tài nguyên Gemini này."
                ),
                (
                    f"Chọn {recommended}; đồng thời kiểm tra Gemini API và giới hạn API key "
                    "trong Google Cloud Console."
                ),
                detail,
            )
        invalid_key_tokens = (
            "api key not valid",
            "api_key_invalid",
            "invalid api key",
            "unauthenticated",
            "authentication",
            "key expired",
        )
        if code == 401 or any(token in lowered for token in invalid_key_tokens):
            return UserFacingError(
                "Google API key không hợp lệ",
                "Google xác nhận API key đang thiếu, sai hoặc đã hết hạn.",
                "Mở Cài đặt → API & Providers, thay bằng key còn hiệu lực rồi kiểm tra kết nối.",
                detail,
            )
        if code == 404 or "model_not_found" in lowered:
            return UserFacingError(
                "Model Gemini không tồn tại hoặc không còn khả dụng",
                "Google không tìm thấy model hoặc endpoint đã chọn.",
                "Chọn model khác trong Step 3 và cập nhật danh sách model của ứng dụng nếu Google đã đổi tên.",
                detail,
            )
        if code == 429 or any(token in lowered for token in ("resource_exhausted", "quota", "rate limit")):
            return UserFacingError(
                "Đã vượt giới hạn Gemini",
                "Google Gemini đang giới hạn request hoặc quota của project API.",
                "Chờ rồi chạy lại, giảm batch size hoặc kiểm tra quota/billing trong Google AI Studio.",
                detail,
            )
        if code >= 500 or any(token in lowered for token in ("unavailable", "timeout", "connection")):
            return UserFacingError(
                "Không thể kết nối Google Gemini",
                "Dịch vụ Gemini tạm thời không phản hồi.",
                "Kiểm tra Internet rồi thử lại sau.",
                detail,
            )
        if "model" in lowered and any(token in lowered for token in ("not found", "unsupported", "invalid")):
            return UserFacingError(
                "Model Gemini không khả dụng",
                "Tài khoản hoặc API key hiện tại không truy cập được model đã chọn.",
                "Chọn model khác trong Step 3 hoặc kiểm tra quyền model trong Google AI Studio.",
                detail,
            )
        return UserFacingError(
            "Google Gemini không thể dịch",
            message or "Gemini API trả về lỗi không xác định.",
            "Mở chi tiết kỹ thuật, kiểm tra cấu hình provider rồi thử lại.",
            detail,
        )


register_translation_provider(
    GEMINI_PROVIDER_NAME,
    lambda model_name, source_language, target_language: GoogleTranslationService(
        model_name,
        source_language,
        target_language,
    ),
)
