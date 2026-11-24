"""Run OCR against the source video and existing SRT cues."""

from __future__ import annotations

import re
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

    def update_srt(
        self, video: str | Path, srt: Path, speaker_srt: Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> OcrTextResult:
        result = self.extract(video, srt, progress)
        paths = [srt, speaker_srt] if speaker_srt else [srt]
        staged = []
        try:
            for path in paths:
                content = _updated_srt(path, result.lines, has_speakers=path == speaker_srt)
                draft = path.with_suffix(".srt.part")
                staged.append((draft, path))
                draft.write_text(content, encoding="utf-8")
            for draft, path in staged:
                draft.replace(path)
        finally:
            for draft, _ in staged:
                draft.unlink(missing_ok=True)
        return result

    def extract(
        self, video: str | Path, srt: Path, progress: ProgressCallback | None = None,
    ) -> OcrTextResult:
        source = Path(video)
        if not source.is_file():
            raise FileNotFoundError(f"Không tìm thấy video gốc để quét OCR: {source}")
        result = self._run({"input_video": str(source.resolve()), "input_srt": str(srt.resolve())}, progress)
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


def _updated_srt(path: Path, lines: tuple[str, ...], *, has_speakers: bool) -> str:
    original = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n[ \t]*\n+", original.rstrip("\n"))
    if len(blocks) != len(lines):
        raise ValueError(f"Số mục OCR không khớp với {path.name}.")
    updated = []
    for block, text in zip(blocks, lines, strict=True):
        number, timing, *original_text = block.split("\n")
        if has_speakers and original_text:
            prefix = re.match(r"^\[[^\]\n]+\] ", original_text[0])
            if prefix and text:
                text = prefix.group() + text
        updated.append(f"{number}\n{timing}\n{text}\n")
    return "\n".join(updated)
