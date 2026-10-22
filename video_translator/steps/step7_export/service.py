from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from ...errors import UserFacingError
from ...common.services.ffmpeg_service import FFmpegError, FFmpegService


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

    @staticmethod
    def _timing_edits(plan: dict[str, Any] | None, duration: float) -> list[dict[str, float | str]]:
        if not isinstance(plan, dict) or not bool(plan.get("enabled", False)):
            return []
        raw_edits = plan.get("edits", [])
        edits: list[dict[str, float | str]] = []
        for raw in raw_edits if isinstance(raw_edits, list) else []:
            if not isinstance(raw, dict):
                continue
            start = max(0.0, float(raw.get("source_start", 0.0)))
            end = min(duration, max(start, float(raw.get("source_end", start))))
            slow_duration = max(0.0, float(raw.get("slow_duration", 0.0)))
            freeze_duration = max(0.0, float(raw.get("freeze_duration", 0.0)))
            slow_edit: dict[str, float | str] | None = None
            if end - start > 0.002 and slow_duration > 0.002:
                slow_edit = {
                    "source_start": start,
                    "source_end": end,
                    "video_speed": max(
                        0.25,
                        min(1.0, float(raw.get("video_speed", 1.0))),
                    ),
                    "freeze_duration": 0.0,
                    "extension_duration": slow_duration,
                    "strategy": "slow_motion",
                }
                edits.append(slow_edit)
            if freeze_duration > 0.002:
                freeze_at = max(0.0, min(duration, float(raw.get("freeze_at_source", end))))
                if slow_edit is not None and freeze_at <= end + 0.002:
                    slow_edit["freeze_duration"] = freeze_duration
                    slow_edit["extension_duration"] = slow_duration + freeze_duration
                    slow_edit["strategy"] = "hybrid"
                    continue
                sample_start = max(0.0, freeze_at - 0.04)
                sample_end = min(duration, max(sample_start + 0.001, freeze_at))
                edits.append(
                    {
                        "source_start": sample_start,
                        "source_end": sample_end,
                        "video_speed": 1.0,
                        "freeze_duration": freeze_duration,
                        "extension_duration": freeze_duration,
                        "strategy": "freeze_frame",
                    }
                )

        edits.sort(key=lambda item: (float(item["source_start"]), float(item["source_end"])))
        return VideoRenderService._merge_overlapping_timing_edits(edits)

    @staticmethod
    def _merge_overlapping_timing_edits(
        edits: list[dict[str, float | str]],
    ) -> list[dict[str, float | str]]:
        if not edits:
            return []

        tolerance = 0.002
        merged: list[dict[str, float | str]] = []
        group: list[dict[str, float | str]] = []
        group_end = 0.0

        def finish_group() -> None:
            if not group:
                return
            if len(group) == 1:
                merged.append(dict(group[0]))
                return

            source_start = min(float(item["source_start"]) for item in group)
            source_end = max(float(item["source_end"]) for item in group)
            source_duration = max(0.001, source_end - source_start)
            freeze_duration = sum(float(item["freeze_duration"]) for item in group)
            slow_duration = sum(
                max(
                    0.0,
                    float(item["extension_duration"])
                    - float(item["freeze_duration"]),
                )
                for item in group
            )

            minimum_speed = 0.25
            maximum_slow_duration = source_duration / minimum_speed - source_duration
            applied_slow_duration = min(slow_duration, maximum_slow_duration)
            freeze_duration += max(0.0, slow_duration - applied_slow_duration)
            video_speed = (
                source_duration / (source_duration + applied_slow_duration)
                if applied_slow_duration > tolerance
                else 1.0
            )
            has_slow_motion = applied_slow_duration > tolerance
            has_freeze_frame = freeze_duration > tolerance
            strategy = (
                "hybrid"
                if has_slow_motion and has_freeze_frame
                else "slow_motion"
                if has_slow_motion
                else "freeze_frame"
            )
            merged.append(
                {
                    "source_start": source_start,
                    "source_end": source_end,
                    "video_speed": max(minimum_speed, min(1.0, video_speed)),
                    "freeze_duration": freeze_duration,
                    "extension_duration": slow_duration + sum(
                        float(item["freeze_duration"]) for item in group
                    ),
                    "strategy": strategy,
                }
            )

        for edit in edits:
            start = float(edit["source_start"])
            end = float(edit["source_end"])
            if group and start >= group_end - tolerance:
                finish_group()
                group = []
            group.append(edit)
            group_end = max(group_end if len(group) > 1 else end, end)
        finish_group()
        return merged

    @staticmethod
    def _atempo_filters(speed: float) -> list[str]:
        factors: list[float] = []
        remaining = max(0.25, min(1.0, speed))
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
        if remaining < 0.999999:
            factors.append(remaining)
        return [f"atempo={factor:.8f}" for factor in factors]

    def _video_timing_filters(
        self,
        edits: list[dict[str, float | str]],
        duration: float,
    ) -> tuple[list[str], str]:
        filters: list[str] = []
        pieces: list[tuple[float, float, float, float]] = []
        labels: list[str] = []
        cursor = 0.0

        def collect_piece(
            start: float,
            end: float,
            speed: float = 1.0,
            freeze: float = 0.0,
        ) -> None:
            if end - start <= 0.0001:
                return
            pieces.append((start, end, speed, freeze))

        for edit in edits:
            start = float(edit["source_start"])
            end = float(edit["source_end"])
            collect_piece(cursor, start)
            collect_piece(
                start,
                end,
                float(edit["video_speed"]),
                float(edit["freeze_duration"]),
            )
            cursor = end
        collect_piece(cursor, duration)

        source_labels = [f"vsrc{index}" for index in range(len(pieces))]
        if len(source_labels) > 1:
            outputs = "".join(f"[{label}]" for label in source_labels)
            filters.append(f"[0:v]split={len(source_labels)}{outputs}")

        for index, (start, end, speed, freeze) in enumerate(pieces):
            label = f"vt{index}"
            source = f"[{source_labels[index]}]" if len(source_labels) > 1 else "[0:v]"
            chain = (
                f"{source}trim=start={start:.6f}:end={end:.6f},"
                f"setpts={(1.0 / speed):.8f}*(PTS-STARTPTS)"
            )
            if freeze > 0.0001:
                chain += f",tpad=stop_mode=clone:stop_duration={freeze:.6f}"
            filters.append(f"{chain}[{label}]")
            labels.append(f"[{label}]")
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[video_timed]")
        return filters, "video_timed"

    def _audio_timing_filters(
        self,
        input_index: int,
        prefix: str,
        edits: list[dict[str, float | str]],
        duration: float,
    ) -> tuple[list[str], str]:
        filters: list[str] = []
        pieces: list[tuple[float, float, float, float]] = []
        labels: list[str] = []
        cursor = 0.0

        def collect_piece(
            start: float,
            end: float,
            speed: float = 1.0,
            freeze: float = 0.0,
        ) -> None:
            if end - start <= 0.0001:
                return

            pieces.append((start, end, speed, freeze))

        for edit in edits:
            start = float(edit["source_start"])
            end = float(edit["source_end"])
            collect_piece(cursor, start)
            collect_piece(
                start,
                end,
                float(edit["video_speed"]),
                float(edit["freeze_duration"]),
            )
            cursor = end
        collect_piece(cursor, duration)

        source_labels = [f"{prefix}src{index}" for index in range(len(pieces))]
        if len(source_labels) > 1:
            outputs = "".join(f"[{label}]" for label in source_labels)
            filters.append(f"[{input_index}:a]asplit={len(source_labels)}{outputs}")

        for index, (start, end, speed, freeze) in enumerate(pieces):
            label = f"{prefix}t{index}"
            piece_duration = (end - start) / speed + freeze
            source = (
                f"[{source_labels[index]}]"
                if len(source_labels) > 1
                else f"[{input_index}:a]"
            )
            chain = [
                f"{source}atrim=start={start:.6f}:end={end:.6f}",
                "asetpts=PTS-STARTPTS",
                *self._atempo_filters(speed),
            ]
            if freeze > 0.0001:
                chain.extend(
                    [
                        f"apad=pad_dur={freeze:.6f}",
                        f"atrim=duration={piece_duration:.6f}",
                    ]
                )
            chain.append("aresample=48000")
            filters.append(",".join(chain) + f"[{label}]")
            labels.append(f"[{label}]")
        output_label = f"{prefix}_timed"
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[{output_label}]")
        return filters, output_label

    def render(
        self,
        source_video_path: str | Path,
        voice_track_path: str | Path,
        output_path: str | Path,
        voice_volume: float,
        original_voice_path: str | Path | None,
        original_voice_volume: float,
        background_path: str | Path | None,
        background_volume: float,
        subtitle_path: str | Path | None,
        burn_subtitle: bool,
        video_timing_plan: dict[str, Any] | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        source_video = Path(source_video_path)
        voice_track = Path(voice_track_path)
        original_voice = Path(original_voice_path) if original_voice_path else None
        background = Path(background_path) if background_path else None
        subtitle = Path(subtitle_path) if subtitle_path else None
        if not source_video.is_file():
            raise VideoRenderError("Thiếu video nguồn", f"Không tìm thấy: {source_video}")
        if not voice_track.is_file():
            raise VideoRenderError("Thiếu voice track", f"Không tìm thấy: {voice_track}")
        if original_voice is not None and not original_voice.is_file():
            raise VideoRenderError("Thiếu voice gốc", f"Không tìm thấy: {original_voice}")
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
        edits = self._timing_edits(video_timing_plan, duration)
        output_duration = duration + sum(float(edit["extension_duration"]) for edit in edits)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.stem}.part{output.suffix}")
        filter_script = output.with_name(f".{output.stem}.filters.txt")
        temporary.unlink(missing_ok=True)
        filter_script.unlink(missing_ok=True)
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
        audio_inputs: list[tuple[int, str, float, bool]] = [(1, "voice_new", voice_volume, False)]
        next_input_index = 2
        if original_voice is not None:
            command.extend(["-i", str(original_voice)])
            audio_inputs.append((next_input_index, "voice_original", original_voice_volume, True))
            next_input_index += 1
        if background is not None:
            command.extend(["-i", str(background)])
            audio_inputs.append((next_input_index, "background", background_volume, True))

        filter_parts: list[str] = []
        video_label = ""
        if edits:
            video_filters, video_label = self._video_timing_filters(edits, duration)
            filter_parts.extend(video_filters)
        if burn_subtitle and subtitle is not None and edits:
            filter_parts.append(
                f"[{video_label}]subtitles=filename={subtitle.name}:charenc=UTF-8[video]"
            )
            video_label = "video"

        mixed_labels: list[str] = []
        for input_index, label, volume, follows_video in audio_inputs:
            source_label = f"{input_index}:a"
            if edits and follows_video:
                timing_filters, source_label = self._audio_timing_filters(
                    input_index,
                    label,
                    edits,
                    duration,
                )
                filter_parts.extend(timing_filters)
            filter_parts.append(f"[{source_label}]volume={volume:.4f}[{label}]")
            mixed_labels.append(f"[{label}]")
        labels = "".join(mixed_labels)
        if len(audio_inputs) > 1:
            filter_parts.append(
                f"{labels}amix=inputs={len(audio_inputs)}:duration=longest:normalize=0,"
                f"alimiter=limit=0.95:level=0:latency=1,aresample=48000,apad,"
                f"atrim=duration={output_duration:.6f}[audio]"
            )
        else:
            filter_parts.append(
                f"[{audio_inputs[0][1]}]aresample=48000,apad,"
                f"atrim=duration={output_duration:.6f}[audio]"
            )
        audio_filter = ";".join(filter_parts)
        video_map = f"[{video_label}]" if video_label else "0:v:0"
        if edits:
            filter_script.write_text(audio_filter, encoding="utf-8")
            command.extend(["-/filter_complex", str(filter_script)])
        else:
            command.extend(["-filter_complex", audio_filter])
        command.extend(["-map", video_map, "-map", "[audio]"])
        if burn_subtitle and subtitle is not None and not edits:
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
            filter_script.unlink(missing_ok=True)
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
            percent = min(99, max(0, round(elapsed / output_duration * 100)))
            if progress:
                progress(percent, f"Đang render video… {percent}%")
        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait()
        filter_script.unlink(missing_ok=True)
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
            "voice_volume": voice_volume,
            "original_voice_used": original_voice is not None,
            "original_voice_volume": original_voice_volume if original_voice is not None else 0.0,
            "background_used": background is not None,
            "background_volume": background_volume if background is not None else 0.0,
            "subtitle_created": subtitle is not None,
            "burned_subtitle": burn_subtitle,
            "video_timing_applied": bool(edits),
            "video_edit_count": len(edits),
            "source_video_duration": duration,
            "extended_duration": output_duration,
            "video_codec": "H.264",
            "audio_codec": "AAC",
        }



