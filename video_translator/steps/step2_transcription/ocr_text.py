"""OCR text export from original video, independently of ASR timing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ...common.models import StepId
from ...config.step_defaults import with_defaults
from ...errors import UserFacingError
from .batch_session import release_batch_worker, run_batch_worker

ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class OcrTextResult:
    lines: tuple[str, ...]
    scan_count: int
    actual_device: str


class OcrTextService:
    def __init__(self, language: str, settings: dict[str, object], batch_session: str = "") -> None:
        self.language = language
        self.settings = with_defaults(StepId.STT, settings)
        self.batch_session = batch_session

    def preload(self) -> None:
        self._run({"type": "preload"}, None)

    def extract(self, video: str | Path, progress: ProgressCallback | None = None) -> OcrTextResult:
        source = Path(video)
        if not source.is_file():
            raise FileNotFoundError(f"Không tìm thấy video gốc để quét OCR: {source}")
        result = self._run({"input_video": str(source.resolve())}, progress)
        lines = result.get("lines")
        if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            raise UserFacingError("Kết quả OCR không hợp lệ", "Worker không trả về danh sách dòng text.")
        return OcrTextResult(tuple(lines), int(result["scan_count"]), str(result["actual_device"]))

    def _run(self, request: dict[str, object], progress: ProgressCallback | None) -> dict[str, object]:
        session_key = self.batch_session or f"ocr-single-{uuid4().hex}"
        request = {
            **{key: value for key, value in self.settings.items() if key.startswith("ocr_")},
            **request, "language": self.language,
            "requested_device": str(self.settings["device"]),
        }
        try:
            result, error, stderr, return_code = run_batch_worker(session_key, "ocr", request, progress)
            if error and request["requested_device"] == "Auto" and error.get("fallback_to_cpu"):
                if progress:
                    progress(2, "OCR GPU gặp lỗi, đang thử lại trên CPU…")
                result, error, stderr, return_code = run_batch_worker(
                    session_key, "ocr", {**request, "requested_device": "CPU"}, progress,
                )
            if error or result is None or return_code != 0:
                raise UserFacingError(
                    "OCR video thất bại",
                    str((error or {}).get("message", "Worker không trả về kết quả OCR.")),
                    "Kiểm tra EasyOCR, model ngôn ngữ, video gốc và cấu hình thiết bị.",
                    str((error or {}).get("technical_detail", "")) + "\n" + stderr,
                )
            return result
        finally:
            if not self.batch_session:
                release_batch_worker(session_key, "ocr")
