from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[int, str], None]


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractedAudio:
    output_path: str
    duration_seconds: float | None
    sample_rate: int
    channels: int
    file_size: int
    ffmpeg_path: str


class FFmpegService:
    def __init__(self, executable: str = "Auto") -> None:
        self.ffmpeg_path = self._resolve_executable(executable, "ffmpeg")
        self.ffprobe_path = self._resolve_ffprobe(self.ffmpeg_path)

    @staticmethod
    def _resolve_executable(configured: str, program: str) -> str:
        value = configured.strip()
        if not value or value.lower() == "auto":
            discovered = shutil.which(program)
            if discovered:
                return discovered
            raise FFmpegError(
                "Không tìm thấy FFmpeg trong PATH. Hãy cài FFmpeg hoặc chọn trực tiếp file ffmpeg.exe."
            )
        path = Path(value).expanduser()
        if path.is_dir():
            path = path / (f"{program}.exe" if os.name == "nt" else program)
        if not path.is_file():
            raise FFmpegError(f"Không tìm thấy executable: {path}")
        return str(path.resolve())

    @staticmethod
    def _resolve_ffprobe(ffmpeg_path: str) -> str | None:
        ffmpeg = Path(ffmpeg_path)
        sibling_name = "ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe"
        sibling = ffmpeg.with_name(sibling_name)
        if sibling.is_file():
            return str(sibling)
        return shutil.which("ffprobe")

    @staticmethod
    def _process_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def probe_duration(self, media_path: str | Path) -> float | None:
        if not self.ffprobe_path:
            return None
        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self._process_flags(),
            check=False,
        )
        if completed.returncode != 0:
            return None
        try:
            return float(completed.stdout.strip())
        except ValueError:
            return None

    def extract_audio(
        self,
        input_video: str | Path,
        output_audio: str | Path,
        sample_rate: int,
        channels: int,
        progress: ProgressCallback | None = None,
    ) -> ExtractedAudio:
        source = Path(input_video)
        output = Path(output_audio)
        if not source.is_file():
            raise FFmpegError(f"Video nguồn không tồn tại: {source}")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.stem}.part{output.suffix}")
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError as exc:
            raise FFmpegError(
                f"Không thể xóa file tạm đang bị khóa: {temporary}. Hãy đóng ứng dụng đang phát file này."
            ) from exc
        duration = self.probe_duration(source)
        if progress:
            progress(0, "Đang chuẩn bị FFmpeg…")

        command = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-progress",
            "pipe:1",
            "-nostats",
            str(temporary),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self._process_flags(),
        )
        assert process.stdout is not None
        for raw_line in process.stdout:
            key, _, value = raw_line.strip().partition("=")
            if not duration or key not in {"out_time_us", "out_time_ms"}:
                continue
            try:
                elapsed = int(value) / 1_000_000
            except ValueError:
                continue
            percent = min(99, max(0, round(elapsed / duration * 100)))
            if progress:
                progress(percent, f"Đang tách audio… {percent}%")

        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait()
        if return_code != 0:
            temporary.unlink(missing_ok=True)
            detail = stderr.strip() or f"FFmpeg kết thúc với mã lỗi {return_code}."
            raise FFmpegError(detail)
        if not temporary.is_file() or temporary.stat().st_size <= 44:
            temporary.unlink(missing_ok=True)
            raise FFmpegError("FFmpeg không tạo được file WAV hợp lệ. Video có thể không chứa audio.")

        try:
            temporary.replace(output)
        except PermissionError as exc:
            temporary.unlink(missing_ok=True)
            raise FFmpegError(
                f"Không thể thay thế file audio đang bị khóa: {output}. "
                "Hãy dừng trình phát hoặc đóng ứng dụng khác đang mở file."
            ) from exc
        output_duration = self.probe_duration(output) or duration
        if progress:
            progress(100, "Đã tách audio thành công.")
        return ExtractedAudio(
            output_path=str(output),
            duration_seconds=output_duration,
            sample_rate=sample_rate,
            channels=channels,
            file_size=output.stat().st_size,
            ffmpeg_path=self.ffmpeg_path,
        )
