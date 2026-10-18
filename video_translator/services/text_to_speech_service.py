from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ..config.tts import MELO_OPENVOICE_PROVIDER, PIPER_PROVIDER
from ..errors import UserFacingError
from ..paths import code_root, runtime_python

ProgressCallback = Callable[[int, str], None]


class _PersistentTtsWorker:
    def __init__(
        self,
        command: list[str],
        environment: dict[str, str],
        signature: str,
        idle_timeout: float = 180.0,
    ) -> None:
        self.command = command
        self.environment = environment
        self.signature = signature
        self.idle_timeout = idle_timeout
        self._process: subprocess.Popen[str] | None = None
        self._stderr_chunks: list[str] = []
        self._lock = threading.RLock()
        self._idle_timer: threading.Timer | None = None
        self._idle_generation = 0
        self._closed = False
        self._close_requested = threading.Event()

    def execute(
        self,
        request: dict[str, object],
        progress: ProgressCallback | None,
    ) -> tuple[dict[str, object] | None, dict[str, object] | None, str]:
        with self._lock:
            if self._closed or self._close_requested.is_set():
                raise TextToSpeechError(
                    "Worker TTS đã đóng",
                    "Phiên tạo voice trong popup không còn hoạt động.",
                    "Mở lại popup và thử lại.",
                )
            self._cancel_idle_locked()
            process = self._ensure_process_locked()
            stderr_start = len(self._stderr_chunks)
            request_id = uuid4().hex
            payload = {**request, "type": "synthesize", "request_id": request_id}
            try:
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except OSError as exc:
                self._stop_locked()
                raise TextToSpeechError(
                    "Không thể gửi yêu cầu tới worker TTS",
                    "Worker giữ model đã dừng trước khi nhận yêu cầu mới.",
                    "Thử tạo lại voice; ứng dụng sẽ nạp lại model.",
                    repr(exc),
                ) from exc

            result: dict[str, object] | None = None
            worker_error: dict[str, object] | None = None
            while True:
                raw_line = process.stdout.readline()
                if not raw_line:
                    stderr = "".join(self._stderr_chunks[stderr_start:]).strip()
                    self._stop_locked()
                    raise TextToSpeechError(
                        "Worker TTS kết thúc bất thường",
                        "Tiến trình giữ model đã đóng mà không trả về kết quả.",
                        "Thử tạo lại voice để khởi động một worker mới.",
                        stderr or f"Worker exit code: {process.poll()}",
                    )
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if str(event.get("request_id", "")) != request_id:
                    continue
                event_type = str(event.get("type", ""))
                if event_type == "progress" and progress:
                    progress(int(event.get("value", 0)), str(event.get("message", "")))
                elif event_type == "result":
                    result = event
                    break
                elif event_type == "error":
                    worker_error = event
                    break
            stderr = "".join(self._stderr_chunks[stderr_start:]).strip()
            if self._close_requested.is_set():
                self._closed = True
                self._stop_locked()
            else:
                self._schedule_idle_locked()
            return result, worker_error, stderr

    def request_close(self) -> None:
        self._close_requested.set()
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._closed = True
            self._cancel_idle_locked()
            self._stop_locked()
        finally:
            self._lock.release()

    def _ensure_process_locked(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._stderr_chunks = []
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        process = self._process
        assert process.stderr is not None

        def drain_stderr() -> None:
            for chunk in process.stderr:
                self._stderr_chunks.append(chunk)

        threading.Thread(target=drain_stderr, daemon=True).start()
        return process

    def _cancel_idle_locked(self) -> None:
        self._idle_generation += 1
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_idle_locked(self) -> None:
        self._cancel_idle_locked()
        generation = self._idle_generation
        timer = threading.Timer(
            self.idle_timeout,
            lambda: self._expire_idle(generation),
        )
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _expire_idle(self, generation: int) -> None:
        with self._lock:
            if self._closed or generation != self._idle_generation:
                return
            self._idle_timer = None
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                process.stdin.flush()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


_PERSISTENT_WORKERS: dict[str, _PersistentTtsWorker] = {}
_PERSISTENT_WORKERS_LOCK = threading.Lock()


def _persistent_worker(
    session_key: str,
    command: list[str],
    environment: dict[str, str],
    signature: str,
) -> _PersistentTtsWorker:
    previous: _PersistentTtsWorker | None = None
    with _PERSISTENT_WORKERS_LOCK:
        worker = _PERSISTENT_WORKERS.get(session_key)
        if worker and worker.signature == signature:
            return worker
        previous = _PERSISTENT_WORKERS.pop(session_key, None)
        worker = _PersistentTtsWorker(command, environment, signature)
        _PERSISTENT_WORKERS[session_key] = worker
    if previous:
        previous.request_close()
    return worker


def release_tts_session(session_key: str) -> None:
    with _PERSISTENT_WORKERS_LOCK:
        worker = _PERSISTENT_WORKERS.pop(session_key, None)
    if worker:
        worker.request_close()


def shutdown_tts_sessions() -> None:
    with _PERSISTENT_WORKERS_LOCK:
        workers = list(_PERSISTENT_WORKERS.values())
        _PERSISTENT_WORKERS.clear()
    for worker in workers:
        worker.request_close()


atexit.register(shutdown_tts_sessions)


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
        return code_root()

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
            runtime_python(runtime_name),
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
        session_key: str = "",
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
        result: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None
        stdin_error: OSError | None = None
        if session_key:
            signature = json.dumps(
                {
                    "command": command,
                    "provider": self.provider,
                    "settings": self.settings,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            worker = _persistent_worker(session_key, command, environment, signature)
            result, worker_error, stderr = worker.execute(request, progress)
            return_code = 0
        else:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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

