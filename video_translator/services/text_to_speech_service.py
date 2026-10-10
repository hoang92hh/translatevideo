from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config.tts import MELO_OPENVOICE_PROVIDER, PIPER_PROVIDER
from ..errors import UserFacingError

ProgressCallback = Callable[[int, str], None]


class TextToSpeechError(UserFacingError):
    pass


@dataclass(frozen=True, slots=True)
class SegmentSynthesisResult:
    id: int
    speaker_id: str
    provider: str
    model: str
    voice: str
    reference_voice: str
    actual_device: str
    settings: dict[str, object]


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    files: list[str]
    requested_device: str
    actual_device: str
    model: str
    voice: str
    segments: list[SegmentSynthesisResult]


class TextToSpeechService:
    def __init__(self, provider: str, settings: dict[str, object]) -> None:
        self.provider = provider
        self.settings = settings

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _python_executable(self) -> Path:
        if self.provider == MELO_OPENVOICE_PROVIDER:
            runtime_name = "melo"
            environment_name = "TRANSLANGUAGE_MELO_PYTHON"
            setup_script = "scripts/setup_melo_runtime.ps1"
            description = "MeloTTS/OpenVoice"
        elif self.provider == PIPER_PROVIDER:
            runtime_name = "piper"
            environment_name = "TRANSLANGUAGE_PIPER_PYTHON"
            setup_script = "scripts/setup_piper_runtime.ps1"
            description = "Piper TTS"
        else:
            return Path(sys.executable)
        configured = str(os.environ.get(environment_name, "")).strip()
        candidates = [
            Path(configured) if configured else Path("__missing__"),
            self._repo_root() / ".runtimes" / runtime_name / "Scripts" / "python.exe",
            self._repo_root() / ".runtimes" / runtime_name / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise TextToSpeechError(
            f"Chưa cài runtime {description}",
            "Provider này cần môi trường Python riêng để không xung đột dependency của các bước khác.",
            f"Chạy {setup_script}, hoặc đặt {environment_name} tới Python của runtime đã cài.",
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
        worker_path = self._repo_root() / "video_translator" / "services" / "tts_worker.py"
        command = [str(self._python_executable()), str(worker_path)]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=creation_flags,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        stderr_chunks: list[str] = []

        def drain_stderr() -> None:
            stderr_chunks.append(process.stderr.read())

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()
        stdin_error: OSError | None = None
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()
        except OSError as exc:
            stdin_error = exc
            try:
                process.stdin.close()
            except OSError:
                pass
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
        stderr_thread.join()
        stderr = "".join(stderr_chunks).strip()
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
            detail = stderr or f"Worker exit code: {return_code}"
            if stdin_error:
                detail = f"Không thể gửi request tới worker: {stdin_error!r}\n\n{detail}"
            raise TextToSpeechError(
                "Worker TTS kết thúc bất thường",
                "Tiến trình tạo giọng nói không trả về kết quả hợp lệ.",
                "Kiểm tra dependency/provider và mở chi tiết kỹ thuật.",
                detail,
            )
        files = [str(item) for item in result.get("files", [])]
        if len(files) != len(segments) or any(not Path(item).is_file() for item in files):
            raise TextToSpeechError(
                "Output TTS không đầy đủ",
                "Số file âm thanh tạo được không khớp số segment.",
                "Output cũ vẫn được giữ làm mặc định; kiểm tra provider rồi chạy lại.",
            )
        raw_segment_results = result.get("segment_results", [])
        if not isinstance(raw_segment_results, list) or len(raw_segment_results) != len(segments):
            raw_segment_results = [
                {
                    "id": item.get("id", index),
                    "speaker_id": item.get("speaker_id", ""),
                    "provider": self.provider,
                    "model": result.get("model", ""),
                    "voice": result.get("voice", ""),
                    "reference_voice": self.settings.get("reference_voice", ""),
                    "actual_device": result.get("actual_device", ""),
                    "settings": {},
                }
                for index, item in enumerate(segments, start=1)
            ]
        segment_results = [
            SegmentSynthesisResult(
                id=int(item.get("id", index)),
                speaker_id=str(item.get("speaker_id", "")),
                provider=str(item.get("provider", self.provider)),
                model=str(item.get("model", result.get("model", ""))),
                voice=str(item.get("voice", result.get("voice", ""))),
                reference_voice=str(item.get("reference_voice", "")),
                actual_device=str(item.get("actual_device", result.get("actual_device", ""))),
                settings=(
                    dict(item.get("settings", {}))
                    if isinstance(item.get("settings", {}), dict)
                    else {}
                ),
            )
            for index, item in enumerate(raw_segment_results, start=1)
            if isinstance(item, dict)
        ]
        return SynthesisResult(
            files=files,
            requested_device=str(result.get("requested_device", self.settings.get("device", "Auto"))),
            actual_device=str(result.get("actual_device", "")),
            model=str(result.get("model", "")),
            voice=str(result.get("voice", "")),
            segments=segment_results,
        )

