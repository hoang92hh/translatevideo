from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from ..errors import UserFacingError
from .ffmpeg_service import FFmpegError, FFmpegService


ProgressCallback = Callable[[int, str], None]


class VideoRenderError(UserFacingError):
    pass


class VideoRenderService:
    def __init__(self) -> None:
        try:
            self.ffmpeg = FFmpegService()
        except FFmpegError as exc:
            raise VideoRenderError(
                "Không tìm thấy FFmpeg",
                str(exc),
                "Cài FFmpeg và bảo đảm ffmpeg/ffprobe có trong PATH rồi chạy lại Step 7.",
            ) from exc

    def render(
        self,
        source_video_path: str | Path,
        voice_track_path: str | Path,
        output_path: str | Path,
        background_path: str | Path | None,
        background_volume: float,
        subtitle_path: str | Path | None,
        burn_subtitle: bool,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        source_video = Path(source_video_path)
        voice_track = Path(voice_track_path)
        background = Path(background_path) if background_path else None
        subtitle = Path(subtitle_path) if subtitle_path else None
        if not source_video.is_file():
            raise VideoRenderError("Thiếu video nguồn", f"Không tìm thấy: {source_video}")
        if not voice_track.is_file():
            raise VideoRenderError("Thiếu voice track", f"Không tìm thấy: {voice_track}")
        if background is not None and not background.is_file():
            raise VideoRenderError("Thiếu background", f"Không tìm thấy: {background}")
        if burn_subtitle and (subtitle is None or not subtitle.is_file()):
            raise VideoRenderError(
                "Thiếu subtitle",
                "Đã chọn burn subtitle nhưng file SRT không tồn tại.",
            )

        duration = self.ffmpeg.probe_duration(source_video)
        if not duration or duration <= 0:
            raise VideoRenderError(
                "Không đọc được thời lượng video",
                f"FFprobe không xác định được thời lượng: {source_video}",
            )

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.stem}.part{output.suffix}")
        temporary.unlink(missing_ok=True)
        command = [
            self.ffmpeg.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_video),
            "-i",
            str(voice_track),
        ]
        if background is not None:
            command.extend(["-i", str(background)])
            audio_filter = (
                f"[1:a]volume=1.0[voice];[2:a]volume={background_volume:.4f}[background];"
                f"[voice][background]amix=inputs=2:duration=longest:normalize=0,"
                f"aresample=48000,apad,atrim=duration={duration:.6f}[audio]"
            )
        else:
            audio_filter = (
                f"[1:a]aresample=48000,apad,atrim=duration={duration:.6f}[audio]"
            )
        command.extend(["-filter_complex", audio_filter, "-map", "0:v:0", "-map", "[audio]"])
        if burn_subtitle and subtitle is not None:
            command.extend(["-vf", f"subtitles=filename={subtitle.name}:charenc=UTF-8"])
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                "-progress",
                "pipe:1",
                "-nostats",
                str(temporary),
            ]
        )
        if progress:
            progress(0, "Đang render video, voice và background…")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(output.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise VideoRenderError(
                "Không thể khởi động FFmpeg",
                "FFmpeg không thể bắt đầu render Step 7.",
                "Kiểm tra FFmpeg, đường dẫn và quyền truy cập thư mục output.",
                repr(exc),
            ) from exc
        assert process.stdout is not None
        for raw_line in process.stdout:
            key, _, value = raw_line.strip().partition("=")
            if key not in {"out_time_us", "out_time_ms"}:
                continue
            try:
                elapsed = int(value) / 1_000_000
            except ValueError:
                continue
            percent = min(99, max(0, round(elapsed / duration * 100)))
            if progress:
                progress(percent, f"Đang render video… {percent}%")
        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait()
        if return_code != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            temporary.unlink(missing_ok=True)
            raise VideoRenderError(
                "Không thể render video Step 7",
                "FFmpeg không tạo được video hoàn chỉnh.",
                "Mở chi tiết kỹ thuật để kiểm tra codec, subtitle hoặc file đầu vào.",
                stderr.strip() or f"FFmpeg exit code: {return_code}",
            )
        temporary.replace(output)
        measured = self.ffmpeg.probe_duration(output) or duration
        if progress:
            progress(100, "Đã render video hoàn chỉnh.")
        return {
            "output_path": str(output),
            "duration_seconds": measured,
            "background_used": background is not None,
            "background_volume": background_volume if background is not None else 0.0,
            "subtitle_created": subtitle is not None,
            "burned_subtitle": burn_subtitle,
            "video_codec": "H.264",
            "audio_codec": "AAC",
        }
