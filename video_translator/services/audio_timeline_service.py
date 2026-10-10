from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..errors import UserFacingError
from .ffmpeg_service import FFmpegError, FFmpegService


ProgressCallback = Callable[[int, str], None]


class AudioTimelineError(UserFacingError):
    pass


class AudioTimelineService:
    BATCH_SIZE = 40
    SAFE_COMMAND_LENGTH = 30_000

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
        source_video_path: str | Path,
        output_path: str | Path,
        sample_rate: int,
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

        source_video = Path(source_video_path)
        video_duration = self.ffmpeg.probe_duration(source_video) if source_video.is_file() else None
        if not video_duration or video_duration <= 0:
            raise AudioTimelineError(
                "Không đọc được thời lượng video",
                f"Không thể xác định thời lượng video nguồn: {source_video}",
                "Kiểm tra file video nguồn và FFprobe rồi chạy lại Step 6.",
            )

        timeline: list[dict[str, Any]] = []
        previous_end = 0.0
        for item in segments:
            source_start = float(item.get("start", 0.0))
            source_end = float(item.get("end", source_start))
            if source_end <= source_start:
                continue
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

        if not timeline:
            raise AudioTimelineError(
                "Timeline trống",
                "Step 6 không còn segment có thời lượng lớn hơn 0 để ghép.",
            )

        timeline_end = max(float(item["end"]) for item in timeline)
        if timeline_end > video_duration + 0.002:
            raise AudioTimelineError(
                "Voice vượt quá thời lượng video",
                f"Voice cuối kết thúc tại {timeline_end:.3f}s nhưng video dài {video_duration:.3f}s.",
                "Quay lại Step 5 để cân timeline trước khi ghép voice.",
            )

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f"{output.stem}.part{output.suffix}")
        temporary.unlink(missing_ok=True)
        if progress:
            progress(0, "Đang chia timeline thành các lô 40 segment…")
        try:
            with tempfile.TemporaryDirectory(
                prefix=f".{output.stem}_batches_",
                dir=output.parent,
            ) as batch_dir_value:
                batch_dir = Path(batch_dir_value)
                batch_tracks: list[dict[str, Any]] = []
                batches = self._split_safe_batches(timeline, batch_dir, sample_rate)
                for batch_index, batch in enumerate(batches, start=1):
                    batch_start = float(batch[0]["start"])
                    batch_end = max(float(item["end"]) for item in batch)
                    batch_duration = batch_end - batch_start
                    batch_output = batch_dir / f"batch_{batch_index:04d}.wav"
                    filter_graph = self._filter_graph(
                        batch,
                        batch_start,
                        batch_duration,
                        sample_rate,
                        "s",
                    )
                    command = self._audio_command(
                        batch,
                        filter_graph,
                        batch_output,
                        sample_rate,
                    )
                    self._ensure_command_length(command, f"lô {batch_index}/{len(batches)}")
                    self._run_batch(command, batch_output, batch_index, len(batches))
                    batch_tracks.append(
                        {
                            "audio": batch_output,
                            "start": batch_start,
                            "duration": batch_duration,
                        }
                    )
                    if progress:
                        percent = round(batch_index / len(batches) * 75)
                        progress(
                            percent,
                            f"Đã ghép lô {batch_index}/{len(batches)} "
                            f"({len(batch)} segment)…",
                        )

                final_graph = self._filter_graph(
                    batch_tracks,
                    0.0,
                    video_duration,
                    sample_rate,
                    "b",
                )
                command = self._audio_command(
                    batch_tracks,
                    final_graph,
                    temporary,
                    sample_rate,
                )
                final_output = command.pop()
                command.extend(
                    [
                        "-progress",
                        "pipe:1",
                        "-nostats",
                        final_output,
                    ]
                )
                self._ensure_command_length(command, "lần ghép cuối")
                self._run_final(command, temporary, video_duration, progress)
        except AudioTimelineError:
            temporary.unlink(missing_ok=True)
            raise
        temporary.replace(output)
        measured = self.ffmpeg.probe_duration(output) or video_duration
        if progress:
            progress(100, "Đã ghép audio timeline hoàn chỉnh.")
        return {
            "output_path": str(output),
            "duration_seconds": measured,
            "segment_count": len(timeline),
            "sample_rate": sample_rate,
            "format": "WAV",
            "video_duration_seconds": video_duration,
            "batch_size": self.BATCH_SIZE,
        }

    def _base_command(self) -> list[str]:
        return [self.ffmpeg.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error"]

    def _split_safe_batches(
        self,
        timeline: list[dict[str, Any]],
        batch_dir: Path,
        sample_rate: int,
    ) -> list[list[dict[str, Any]]]:
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        probe_output = batch_dir / "batch_probe.wav"
        for item in timeline:
            candidate = [*current, item]
            if len(candidate) > self.BATCH_SIZE:
                batches.append(current)
                current = [item]
                continue
            start = float(candidate[0]["start"])
            duration = max(float(value["end"]) for value in candidate) - start
            graph = self._filter_graph(candidate, start, duration, sample_rate, "s")
            command = self._audio_command(candidate, graph, probe_output, sample_rate)
            if current and self._command_length(command) > self.SAFE_COMMAND_LENGTH:
                batches.append(current)
                current = [item]
            else:
                current = candidate
        if current:
            start = float(current[0]["start"])
            duration = max(float(value["end"]) for value in current) - start
            graph = self._filter_graph(current, start, duration, sample_rate, "s")
            command = self._audio_command(current, graph, probe_output, sample_rate)
            self._ensure_command_length(command, "lô chứa một segment")
            batches.append(current)
        return batches

    @staticmethod
    def _filter_graph(
        items: list[dict[str, Any]],
        origin: float,
        output_duration: float,
        sample_rate: int,
        label_prefix: str,
    ) -> str:
        filters: list[str] = []
        labels: list[str] = []
        for input_index, item in enumerate(items):
            label = f"{label_prefix}{input_index}"
            delay_ms = round((float(item["start"]) - origin) * 1000)
            filters.append(
                f"[{input_index}:a]atrim=duration={float(item['duration']):.6f},"
                f"asetpts=PTS-STARTPTS,adelay={delay_ms}:all=1[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
            f"aresample={sample_rate},apad,atrim=duration={output_duration:.6f}[out]"
        )
        return ";".join(filters)

    def _audio_command(
        self,
        items: list[dict[str, Any]],
        filter_graph: str,
        output: Path,
        sample_rate: int,
    ) -> list[str]:
        command = self._base_command()
        for item in items:
            command.extend(["-i", str(item["audio"])])
        command.extend(
            [
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output),
            ]
        )
        return command

    @staticmethod
    def _command_length(command: list[str]) -> int:
        return len(subprocess.list2cmdline(command))

    def _ensure_command_length(self, command: list[str], stage: str) -> None:
        length = self._command_length(command)
        if length <= self.SAFE_COMMAND_LENGTH:
            return
        raise AudioTimelineError(
            "Lệnh FFmpeg vượt giới hạn Windows",
            f"Câu lệnh tại {stage} dài {length:,} ký tự.",
            "Rút ngắn đường dẫn thư mục project hoặc giảm kích thước lô Step 6.",
        )

    def _run_batch(
        self,
        command: list[str],
        output: Path,
        batch_index: int,
        batch_count: int,
    ) -> None:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except OSError as exc:
            raise self._process_start_error(exc, f"lô {batch_index}/{batch_count}") from exc
        if completed.returncode != 0 or not output.is_file():
            raise AudioTimelineError(
                "Không thể ghép lô audio Step 6",
                f"FFmpeg không tạo được lô {batch_index}/{batch_count}.",
                "Mở chi tiết kỹ thuật để kiểm tra file segment hoặc FFmpeg.",
                completed.stderr.strip() or f"FFmpeg exit code: {completed.returncode}",
            )

    def _run_final(
        self,
        command: list[str],
        output: Path,
        total_duration: float,
        progress: ProgressCallback | None,
    ) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise self._process_start_error(exc, "lần ghép cuối") from exc
        assert process.stdout is not None
        for raw_line in process.stdout:
            key, _, value = raw_line.strip().partition("=")
            if key not in {"out_time_us", "out_time_ms"} or total_duration <= 0:
                continue
            try:
                elapsed = int(value) / 1_000_000
            except ValueError:
                continue
            percent = min(99, max(75, 75 + round(elapsed / total_duration * 24)))
            if progress:
                progress(percent, f"Đang ghép các lô audio… {percent}%")
        stderr = process.stderr.read() if process.stderr else ""
        return_code = process.wait()
        if return_code != 0 or not output.is_file():
            raise AudioTimelineError(
                "Không thể ghép audio Step 6",
                "FFmpeg không tạo được audio timeline cuối.",
                "Mở chi tiết kỹ thuật để kiểm tra các lô audio hoặc FFmpeg.",
                stderr.strip() or f"FFmpeg exit code: {return_code}",
            )

    @staticmethod
    def _process_start_error(exc: OSError, stage: str) -> AudioTimelineError:
        if getattr(exc, "winerror", None) == 206:
            return AudioTimelineError(
                "Lệnh FFmpeg vượt giới hạn Windows",
                f"Danh sách file đầu vào vẫn quá dài tại {stage}.",
                "Giảm kích thước lô xử lý rồi chạy lại Step 6.",
                repr(exc),
            )
        return AudioTimelineError(
            "Không thể khởi động FFmpeg",
            f"Không thể chạy FFmpeg tại {stage}.",
            "Kiểm tra FFmpeg và quyền truy cập thư mục project.",
            repr(exc),
        )
