from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..errors import UserFacingError

ProgressCallback = Callable[[int, str], None]


class SpeechToTextError(UserFacingError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class Transcription:
    segments: list[TranscriptSegment]
    language: str
    language_probability: float | None
    duration_seconds: float | None
    requested_device: str
    actual_device: str
    compute_type: str
    device_selection_reason: str


class SpeechToTextService:
    def __init__(
        self,
        model_name: str,
        device: str,
        language: str | None,
        vad_filter: bool,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.language = language
        self.vad_filter = vad_filter

    @staticmethod
    def model_cache_dir() -> Path:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".cache"
        path = root / "TransLanguage" / "faster-whisper-models"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def transcribe(
        self,
        input_audio: str | Path,
        progress: ProgressCallback | None = None,
    ) -> Transcription:
        source = Path(input_audio)
        if not source.is_file():
            raise SpeechToTextError(
                "Không tìm thấy audio đầu vào",
                f"File không tồn tại: {source}",
                "Chọn lại Voice hoặc Original Mix còn tồn tại từ Step 1.",
            )
        request = {
            "input_audio": str(source.resolve()),
            "model_name": self.model_name,
            "model_cache_dir": str(self.model_cache_dir()),
            "requested_device": self.device,
            "language": self.language,
            "vad_filter": self.vad_filter,
        }
        result = self._run_worker(request, progress)
        raw_segments = result.get("segments")
        if not isinstance(raw_segments, list):
            raise SpeechToTextError(
                "Kết quả Faster Whisper không hợp lệ",
                "Worker không trả về danh sách segment hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            )
        segments: list[TranscriptSegment] = []
        try:
            for item in raw_segments:
                segments.append(
                    TranscriptSegment(
                        start=float(item["start"]),
                        end=float(item["end"]),
                        text=str(item["text"]).strip(),
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechToTextError(
                "Kết quả Faster Whisper không hợp lệ",
                "Một hoặc nhiều segment thiếu timestamp hoặc nội dung hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            ) from exc
        if not segments:
            raise SpeechToTextError(
                "Không phát hiện lời nói",
                "Faster Whisper đã xử lý xong nhưng không tạo được segment nào.",
                "Thử chọn Original Mix, tắt Voice activity detection hoặc kiểm tra lại audio từ Step 1.",
            )
        probability = result.get("language_probability")
        duration = result.get("duration_seconds")
        return Transcription(
            segments=segments,
            language=str(result.get("language", "")),
            language_probability=float(probability) if probability is not None else None,
            duration_seconds=float(duration) if duration is not None else None,
            requested_device=str(result.get("requested_device", self.device)),
            actual_device=str(result.get("actual_device", "")),
            compute_type=str(result.get("compute_type", "default")),
            device_selection_reason=str(result.get("device_selection_reason", "")),
        )

    def _run_worker(
        self,
        request: dict[str, object],
        progress: ProgressCallback | None,
    ) -> dict[str, object]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        command = [sys.executable, "-m", "video_translator.services.faster_whisper_worker"]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None

        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=creation_flags,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()

            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "progress" and progress:
                    progress(int(event.get("value", 0)), str(event.get("message", "")))
                elif event_type == "result":
                    result = event
                elif event_type == "error":
                    worker_error = event

            return_code = process.wait()
            stderr_file.seek(0)
            stderr = stderr_file.read().strip()

        if worker_error:
            if self.device == "Auto" and bool(worker_error.get("fallback_to_cpu", False)):
                if progress:
                    progress(5, "CUDA không thể khởi tạo; Auto đang thử lại bằng CPU…")
                fallback_reason = str(worker_error.get("message", "CUDA không thể khởi tạo."))
                fallback_request = {
                    **request,
                    "requested_device": "CPU",
                    "original_request": "Auto",
                    "fallback_reason": fallback_reason,
                }
                fallback_result = self._run_worker(fallback_request, progress)
                fallback_result["requested_device"] = "Auto"
                return fallback_result
            detail = str(worker_error.get("technical_detail", ""))
            if stderr:
                detail = f"{detail}\n\nWorker stderr:\n{stderr}".strip()
            raise SpeechToTextError(
                str(worker_error.get("title", "Faster Whisper không thể nhận dạng")),
                str(worker_error.get("message", "Worker Faster Whisper gặp lỗi.")),
                str(worker_error.get("suggestion", "")),
                detail,
            )
        if return_code != 0 or result is None:
            raise SpeechToTextError(
                "Worker Faster Whisper kết thúc bất thường",
                "Tiến trình nhận dạng không trả về kết quả hợp lệ.",
                "Kiểm tra dependency, model và cấu hình CPU/GPU rồi thử lại.",
                stderr or f"Worker exit code: {return_code}",
            )
        return result
