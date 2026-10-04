from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config.tts import MELO_OPENVOICE_PROVIDER
from ..errors import UserFacingError

ProgressCallback = Callable[[int, str], None]


class TextToSpeechError(UserFacingError):
    pass


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    files: list[str]
    requested_device: str
    actual_device: str
    model: str
    voice: str


class TextToSpeechService:
    def __init__(self, provider: str, settings: dict[str, object]) -> None:
        self.provider = provider
        self.settings = settings

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _python_executable(self) -> Path:
        if self.provider != MELO_OPENVOICE_PROVIDER:
            return Path(sys.executable)
        configured = str(os.environ.get("TRANSLANGUAGE_MELO_PYTHON", "")).strip()
        candidates = [
            Path(configured) if configured else Path("__missing__"),
            self._repo_root() / ".runtimes" / "melo" / "Scripts" / "python.exe",
            self._repo_root() / ".runtimes" / "melo" / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise TextToSpeechError(
            "Chưa cài runtime MeloTTS/OpenVoice",
            "Provider này cần môi trường Python riêng để không xung đột dependency của Step 1–3.",
            "Chạy scripts/setup_melo_runtime.ps1, hoặc đặt TRANSLANGUAGE_MELO_PYTHON tới Python của runtime đã cài.",
        )

    def synthesize(
        self,
        segments: list[dict[str, object]],
        output_folder: str | Path,
        target_language: str,
        progress: ProgressCallback | None = None,
    ) -> SynthesisResult:
        request = {
            "provider": self.provider,
            "settings": self.settings,
            "segments": segments,
            "output_folder": str(Path(output_folder).resolve()),
            "target_language": target_language,
            "repo_root": str(self._repo_root()),
        }
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(self._repo_root()), environment.get("PYTHONPATH", "")) if value
        )
        command = [str(self._python_executable()), "-m", "video_translator.services.tts_worker"]
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
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()
            for raw_line in process.stdout:
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "progress" and progress:
                    progress(int(event.get("value", 0)), str(event.get("message", "")))
                elif event.get("type") == "result":
                    result = event
                elif event.get("type") == "error":
                    worker_error = event
            return_code = process.wait()
            stderr_file.seek(0)
            stderr = stderr_file.read().strip()
        if worker_error:
            detail = str(worker_error.get("technical_detail", ""))
            if stderr:
                detail = f"{detail}\n\nWorker stderr:\n{stderr}".strip()
            raise TextToSpeechError(
                str(worker_error.get("title", "Không thể tạo giọng nói")),
                str(worker_error.get("message", "Worker TTS gặp lỗi.")),
                str(worker_error.get("suggestion", "")),
                detail,
            )
        if return_code != 0 or result is None:
            raise TextToSpeechError(
                "Worker TTS kết thúc bất thường",
                "Tiến trình tạo giọng nói không trả về kết quả hợp lệ.",
                "Kiểm tra dependency/provider và mở chi tiết kỹ thuật.",
                stderr or f"Worker exit code: {return_code}",
            )
        files = [str(item) for item in result.get("files", [])]
        if len(files) != len(segments) or any(not Path(item).is_file() for item in files):
            raise TextToSpeechError(
                "Output TTS không đầy đủ",
                "Số file âm thanh tạo được không khớp số segment.",
                "Output cũ vẫn được giữ làm mặc định; kiểm tra provider rồi chạy lại.",
            )
        return SynthesisResult(
            files=files,
            requested_device=str(result.get("requested_device", self.settings.get("device", "Auto"))),
            actual_device=str(result.get("actual_device", "")),
            model=str(result.get("model", "")),
            voice=str(result.get("voice", "")),
        )

