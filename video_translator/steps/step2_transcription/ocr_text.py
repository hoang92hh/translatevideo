"""Run OCR on an existing SRT and write the recognized text to a separate SRT."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ...common.models import StepId
from ...config.step_defaults import with_defaults
from ...errors import UserFacingError
from .batch_session import release_batch_worker, run_batch_worker
from .ocr_segment_matcher import read_srt

ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class OcrTextResult:
    lines: tuple[str, ...]
    scan_count: int
    actual_device: str


class OcrTextService:
    def __init__(self, settings: dict[str, object], batch_session: str = "") -> None:
        self.settings = with_defaults(StepId.STT, settings)
        self.batch_session = batch_session

    def create_srt(
        self, video: Path, srt: Path, output: Path, progress: ProgressCallback | None = None,
    ) -> OcrTextResult:
        if not video.is_file():
            raise FileNotFoundError(f"Không tìm thấy video gốc để quét OCR: {video}")
        cues = read_srt(srt)
        result = self._run({"input_video": str(video), "input_srt": str(srt)}, progress)
        lines = result.get("lines")
        if not isinstance(lines, list) or len(lines) != len(cues) or not all(isinstance(line, str) for line in lines):
            raise UserFacingError("Kết quả OCR không hợp lệ", f"Kết quả OCR phải có đủ {len(cues)} mục SRT.")
        ocr_result = OcrTextResult(tuple(lines), int(result["scan_count"]), str(result["actual_device"]))
        draft = output.with_name(f"{output.name}.{uuid4().hex}.part")
        try:
            content = "\n\n".join(
                f"{cue.index}\n{cue.timeline}\n{text}".rstrip("\n")
                for cue, text in zip(cues, ocr_result.lines, strict=True)
            )
            draft.write_text(content + "\n", encoding="utf-8")
            draft.replace(output)
        finally:
            draft.unlink(missing_ok=True)
        return ocr_result

    def _run(self, request: dict[str, object], progress: ProgressCallback | None) -> dict[str, object]:
        session_key = self.batch_session or f"ocr-single-{uuid4().hex}"
        request = {
            **{key: value for key, value in self.settings.items() if key.startswith("ocr_")},
            **request,
        }
        try:
            result, error, stderr, return_code = run_batch_worker(session_key, "ocr", request, progress)
            if error or result is None or return_code != 0:
                raise UserFacingError(
                    "OCR video thất bại",
                    str((error or {}).get("message", "Worker không trả về kết quả OCR.")),
                    "Kiểm tra RapidOCR, ONNX Runtime GPU, CUDA/cuDNN và video gốc.",
                    str((error or {}).get("technical_detail", "")) + "\n" + stderr,
                )
            return result
        finally:
            if not self.batch_session:
                release_batch_worker(session_key, "ocr")
