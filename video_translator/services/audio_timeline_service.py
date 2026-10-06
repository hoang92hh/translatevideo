from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..errors import UserFacingError
from .ffmpeg_service import FFmpegError, FFmpegService


ProgressCallback = Callable[[int, str], None]


class AudioTimelineError(UserFacingError):
    pass


class AudioTimelineService:
    def __init__(self) -> None:
        try:
            self.ffmpeg = FFmpegService()
        except FFmpegError as exc:
            raise AudioTimelineError(
                "Không tìm thấy FFmpeg",
                str(exc),
                "Cài FFmpeg và bảo đảm ffmpeg/ffprobe có trong PATH rồi chạy lại Step 6.",
            ) from exc

    def build(
        self,
        sync_manifest_path: str | Path,
        output_path: str | Path,
        sample_rate: int,
        output_format: str,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        manifest = Path(sync_manifest_path)
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AudioTimelineError(
                "Không thể mở input Step 6",
                str(manifest),
                "Chọn lại candidate Step 5 hoàn chỉnh.",
                str(exc),
            ) from exc
        if int(payload.get("error_count", 0)):
            raise AudioTimelineError(
                "Candidate Step 5 chưa hoàn chỉnh",
                "Vẫn còn segment chưa thể xếp vào timeline.",
                "Xử lý hết segment lỗi ở Step 5 rồi chạy lại Step 6.",
            )
        raw = payload.get("segments", [])
        segments = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        if not segments:
            raise AudioTimelineError("Timeline trống", "Step 6 không nhận được segment audio nào.")

        timeline: list[dict[str, Any]] = []
        previous_end = 0.0
        for item in segments:
            audio = Path(str(item.get("synced_audio_file", "")))
            if not audio.is_file():
                raise AudioTimelineError(
                    "Thiếu audio đã đồng bộ",
                    f"Không tìm thấy file của segment #{int(item.get('id', 0)):04d}: {audio}",
                )
            start = max(0.0, float(item.get("adjusted_start", item.get("start", 0.0))))
            duration = float(item.get("play_duration", 0.0))
            if duration <= 0:
                duration = self.ffmpeg.probe_duration(audio) or 0.0
            if duration <= 0:
                raise AudioTimelineError(
                    "Không đọc được thời lượng segment",
                    f"Segment #{int(item.get('id', 0)):04d} không có play_duration hợp lệ.",
                )
            end = start + duration
            if start < previous_end - 0.002:
                raise AudioTimelineError(
                    "Timeline bị chồng voice",
                    f"Segment #{int(item.get('id', 0)):04d} bắt đầu tại {start:.3f}s trước khi segment trước kết thúc tại {previous_end:.3f}s.",
                    "Chạy lại Step 5 để cân timeline.",
                )
            timeline.append({"id": int(item.get("id", 0)), "audio": audio, "start": start, "duration": duration, "end": end})
            previous_end = end

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.stem}.part{output.suffix}")
        temporary.unlink(missing_ok=True)
        filter_script = output.with_name(f".{output.stem}.filters.txt")
        filters: list[str] = []
        labels: list[str] = []
        for index, item in enumerate(timeline):
            label = f"a{index}"
            delay_ms = round(float(item["start"]) * 1000)
            filters.append(
                f"[{index}:a]atrim=duration={float(item['duration']):.6f},"
                f"asetpts=PTS-STARTPTS,adelay={delay_ms}:all=1[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=longest:normalize=0,aresample={sample_rate}[out]"
        )
        filter_script.write_text(";\n".join(filters), encoding="utf-8")
        command = [self.ffmpeg.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error"]
        for item in timeline:
            command.extend(["-i", str(item["audio"])])
        command.extend(["-filter_complex_script", str(filter_script), "-map", "[out]", "-ar", str(sample_rate), "-ac", "1"])
        if output_format.upper() == "AAC":
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            command.extend(["-c:a", "pcm_s16le"])
        command.extend(["-progress", "pipe:1", "-nostats", str(temporary)])
        if progress:
            progress(0, "Đang ghép audio theo timeline Step 5…")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdout is not None
        total_duration = max(float(item["end"]) for item in timeline)
        for raw_line in process.stdout:
            key, _, value = raw_line.strip().partition("=")
            if key not in {"out_time_us", "out_time_ms"} or total_duration <= 0:
                continue
            try:
                elapsed = int(value) / 1_000_000
            except ValueError:
                continue
            percent = min(99, max(0, round(elapsed / total_duration * 100)))
            if progress:
                progress(percent, f"Đang ghép audio… {percent}%")
        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait()
        filter_script.unlink(missing_ok=True)
        if return_code != 0 or not temporary.is_file():
            temporary.unlink(missing_ok=True)
            raise AudioTimelineError(
                "Không thể ghép audio Step 6",
                "FFmpeg không tạo được audio timeline.",
                "Mở chi tiết kỹ thuật để kiểm tra file segment hoặc FFmpeg.",
                stderr.strip() or f"FFmpeg exit code: {return_code}",
            )
        temporary.replace(output)
        measured = self.ffmpeg.probe_duration(output) or total_duration
        if progress:
            progress(100, "Đã ghép audio timeline hoàn chỉnh.")
        return {
            "output_path": str(output),
            "duration_seconds": measured,
            "segment_count": len(timeline),
            "sample_rate": sample_rate,
            "format": output_format.upper(),
        }
