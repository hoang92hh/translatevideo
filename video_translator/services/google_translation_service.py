from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from ..config.gemini import GEMINI_RECOMMENDED_MODEL_IDS
from ..errors import UserFacingError
from ..models import Segment
from .credential_service import GOOGLE_GEMINI, CredentialService


ProgressCallback = Callable[[int, str], None]


class TranslationItem(BaseModel):
    id: int
    translated_text: str


@dataclass(frozen=True, slots=True)
class TranslationResponse:
    translations: dict[int, str]
    credential_source: str


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
                    [{"id": segment.id, "text": segment.source_text} for segment in batch],
                    ensure_ascii=False,
                )
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=content,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            "You are a professional audiovisual subtitle translator. "
                            f"Translate every segment from {self.source_language} to {self.target_language}. "
                            "Preserve meaning, tone, names, numbers, and continuity across adjacent segments. "
                            "Write natural spoken-language subtitles. Return every input ID exactly once. "
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
