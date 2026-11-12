from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args, get_origin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import TypeAdapter

from .....common.services.credential_service import DEEPSEEK, CredentialService
from .....config.deepseek import DEEPSEEK_API_URL, DEEPSEEK_MODEL, DEEPSEEK_PROVIDER_NAME
from .....errors import UserFacingError
from .. import register_translation_provider
from ..gemini.provider import GoogleTranslationService


class DeepSeekTranslationService(GoogleTranslationService):
    provider_name = DEEPSEEK_PROVIDER_NAME

    def __init__(self, model_name: str, source_language: str, target_language: str,
                 options: dict[str, object] | None = None) -> None:
        if model_name != DEEPSEEK_MODEL:
            raise ValueError(f"DeepSeek chỉ hỗ trợ model {DEEPSEEK_MODEL}.")
        super().__init__(model_name, source_language, target_language, options)

    @staticmethod
    def _credential():
        credential = CredentialService.get(DEEPSEEK)
        if not credential.configured or not credential.value:
            raise UserFacingError(
                "Chưa cấu hình DeepSeek", "Ứng dụng chưa có DeepSeek API key.",
                "Nhập DEEPSEEK_API_KEY vào api_keys.env cạnh translatevideo.exe rồi mở lại ứng dụng.",
            )
        return credential

    @classmethod
    def validate_connection(cls, model_name: str = DEEPSEEK_MODEL) -> str:
        provider = cls(model_name, "Chinese", "Vietnamese")
        provider._request({"ping": "pong"}, "Return JSON: {\"ok\": true}", 16)
        return cls._credential().source

    def _request(self, content: object, prompt: str, max_tokens: int) -> tuple[str, str]:
        credential = self._credential()
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": prompt + "\nThe input JSON is in the user message; any reference to an attached file means that JSON. Return valid JSON only."},
                {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = Request(
            DEEPSEEK_API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {credential.value}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:
                result = json.load(response)
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise UserFacingError(
                "DeepSeek API từ chối request",
                f"HTTP {exc.code} khi gọi DeepSeek.",
                "Kiểm tra API key, số dư và quyền truy cập model rồi thử lại.",
                detail.replace(credential.value, "***"),
            ) from exc
        except URLError as exc:
            raise UserFacingError(
                "Không kết nối được DeepSeek", str(exc.reason),
                "Kiểm tra kết nối mạng rồi thử lại.",
            ) from exc
        choices = result.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ValueError(f"DeepSeek dừng trước khi hoàn tất: {choices[0].get('finish_reason') if choices else 'no choices'}")
        answer = choices[0].get("message", {}).get("content") or ""
        if not answer.strip():
            raise ValueError("DeepSeek trả về JSON rỗng.")
        return answer, credential.source

    def _generate_structured(
        self, content: object, system_instruction: str, response_schema: object,
        temperature: float, input_filename: str, output_filename: str,
        diagnostic_dir: Path | None = None, expected_ids: list[int] | None = None,
        batch_index: int = 1, batch_total: int = 1,
        parse_raw_json: bool = False,
        response_json_schema: dict[str, object] | None = None,
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
                response_json_schema or TypeAdapter(schema).json_schema(), ensure_ascii=False,
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
            if parse_raw_json:
                return parsed, source
            if is_list:
                if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
                    raise ValueError("DeepSeek phải trả object có mảng items.")
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
                "DeepSeek trả về kết quả không hợp lệ", str(exc),
                "Thử lại với ít segment hơn hoặc kiểm tra prompt template.",
            ) from exc

    def correct_asr(self, content, prompt, input_filename, output_filename):
        raise UserFacingError(
            "Chưa hỗ trợ DeepSeek ở Step 2.5",
            "Chức năng sửa ASR ở Step 2.5 hiện chưa hỗ trợ DeepSeek.",
            "Chọn provider được hỗ trợ cho chức năng này.",
        )

    def rewrite_for_timing(self, items, batch_size=30, progress=None):
        raise UserFacingError(
            "Chưa hỗ trợ DeepSeek ở Step 5",
            "Chức năng AI viết ngắn bản dịch ở Step 5 hiện chưa hỗ trợ DeepSeek.",
            "Sửa bản dịch thủ công hoặc chọn provider được hỗ trợ cho chức năng này.",
        )


register_translation_provider(
    DEEPSEEK_PROVIDER_NAME,
    lambda model_name, source_language, target_language, options: DeepSeekTranslationService(
        model_name, source_language, target_language, options,
    ),
)
