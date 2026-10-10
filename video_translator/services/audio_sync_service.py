from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..config.gemini import GEMINI_PROVIDER_NAME
from ..errors import UserFacingError
from .ffmpeg_service import FFmpegError, FFmpegService
from .text_to_speech_service import TextToSpeechService
from .translation_provider import create_translation_provider


ProgressCallback = Callable[[int, str], None]


class AudioSyncError(UserFacingError):
    pass


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    success: bool
    input_duration: float
    prepared_duration: float
    output_duration: float
    target_duration: float
    allowed_duration: float
    speed_factor: float
    used_gap: float
    detected_leading_silence: float = 0.0
    detected_trailing_silence: float = 0.0
    trimmed_leading_silence: float = 0.0
    trimmed_trailing_silence: float = 0.0
    silence_trim_decision: str = "disabled"
    error: str = ""


class AudioSyncService:
    SAMPLE_RATE = 48_000
    CHANNELS = 1
    SILENCE_THRESHOLD_DB = -55
    MIN_EDGE_SILENCE = 0.25
    EDGE_SILENCE_GUARD = 0.12

    def __init__(self) -> None:
        try:
            self.ffmpeg = FFmpegService()
        except FFmpegError as exc:
            raise AudioSyncError(
                "Không tìm thấy FFmpeg",
                str(exc),
                "Cài FFmpeg và bảo đảm ffmpeg/ffprobe có trong PATH rồi chạy lại Step 5.",
            ) from exc

    @staticmethod
    def _atempo_filter(speed: float) -> str:
        factors: list[float] = []
        remaining = max(1.0, speed)
        while remaining > 2.0:
            factors.append(2.0)
            remaining /= 2.0
        if remaining > 1.0001:
            factors.append(remaining)
        return ",".join(f"atempo={factor:.8f}" for factor in factors)

    @staticmethod
    def _edge_trim_filters(
        input_duration: float,
        leading_trim: float,
        trailing_trim: float,
    ) -> list[str]:
        if leading_trim <= 0.000001 and trailing_trim <= 0.000001:
            return []
        end = max(leading_trim + 0.01, input_duration - trailing_trim)
        return [
            f"atrim=start={leading_trim:.6f}:end={end:.6f}",
            "asetpts=PTS-STARTPTS",
        ]

    def _detect_edge_silence(self, source: Path, duration: float) -> tuple[float, float]:
        command = [
            self.ffmpeg.ffmpeg_path,
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-vn",
            "-af",
            (
                f"silencedetect=noise={self.SILENCE_THRESHOLD_DB}dB:"
                f"d={self.MIN_EDGE_SILENCE:.3f}"
            ),
            "-f",
            "null",
            os.devnull,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if completed.returncode != 0:
            return 0.0, 0.0

        intervals: list[tuple[float, float]] = []
        current_start: float | None = None
        for match in re.finditer(
            r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)",
            completed.stderr,
        ):
            event, raw_value = match.groups()
            value = max(0.0, float(raw_value))
            if event == "start":
                current_start = value
            elif current_start is not None:
                intervals.append((current_start, min(duration, value)))
                current_start = None
        if current_start is not None:
            intervals.append((current_start, duration))

        leading = 0.0
        trailing = 0.0
        if intervals and intervals[0][0] <= 0.02:
            leading = max(0.0, intervals[0][1])
        if intervals and intervals[-1][1] >= duration - 0.05:
            trailing = max(0.0, duration - intervals[-1][0])
        return leading, trailing

    def _safe_edge_trim(
        self,
        source: Path,
        input_duration: float,
        allowed_duration: float,
        enabled: bool,
    ) -> tuple[float, float, float, float, str]:
        if not enabled:
            return 0.0, 0.0, 0.0, 0.0, "disabled"
        excess = input_duration - max(allowed_duration, 0.01)
        if excess <= 0.0001:
            return 0.0, 0.0, 0.0, 0.0, "fits_allowed_duration"

        leading, trailing = self._detect_edge_silence(source, input_duration)
        leading_available = max(0.0, leading - self.EDGE_SILENCE_GUARD)
        trailing_available = max(0.0, trailing - self.EDGE_SILENCE_GUARD)
        if leading_available + trailing_available <= 0.0001:
            return leading, trailing, 0.0, 0.0, "no_safe_edge_silence"

        # Ưu tiên bỏ silence cuối để không làm dịch thời điểm bắt đầu phát lời.
        trailing_trim = min(excess, trailing_available)
        leading_trim = min(max(0.0, excess - trailing_trim), leading_available)
        return leading, trailing, leading_trim, trailing_trim, "edge_silence_trimmed"

    def _run_ffmpeg(self, source: Path, output: Path, filters: list[str]) -> None:
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
            str(source),
        ]
        if filters:
            command.extend(["-af", ",".join(filters)])
        command.extend(
            [
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(self.SAMPLE_RATE),
                "-ac",
                str(self.CHANNELS),
                str(temporary),
            ]
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if completed.returncode != 0 or not temporary.is_file():
            temporary.unlink(missing_ok=True)
            detail = completed.stderr.strip() or f"FFmpeg kết thúc với mã {completed.returncode}."
            raise AudioSyncError(
                "Không thể đồng bộ audio",
                f"FFmpeg không xử lý được file {source.name}.",
                "Kiểm tra file TTS nguồn hoặc mở chi tiết kỹ thuật.",
                detail,
            )
        temporary.replace(output)

    def synchronize_file(
        self,
        source_path: str | Path,
        output_path: str | Path,
        target_duration: float,
        allowed_duration: float,
        max_speed: float,
        trim_silence: bool,
    ) -> SyncOutcome:
        source = Path(source_path)
        output = Path(output_path)
        if not source.is_file():
            raise AudioSyncError(
                "Thiếu audio TTS",
                f"Không tìm thấy file nguồn: {source}",
                "Chọn lại candidate Step 4 còn đầy đủ file audio.",
            )
        input_duration = self.ffmpeg.probe_duration(source)
        if not input_duration or input_duration <= 0:
            raise AudioSyncError(
                "Không đọc được thời lượng audio",
                str(source),
                "Kiểm tra ffprobe và file audio TTS nguồn.",
            )

        prepared = output.with_name(f".{output.stem}.prepared.wav")
        (
            detected_leading,
            detected_trailing,
            trimmed_leading,
            trimmed_trailing,
            trim_decision,
        ) = self._safe_edge_trim(
            source,
            input_duration,
            allowed_duration,
            trim_silence,
        )
        trim_filter = self._edge_trim_filters(
            input_duration,
            trimmed_leading,
            trimmed_trailing,
        )
        try:
            self._run_ffmpeg(source, prepared, trim_filter)
            prepared_duration = self.ffmpeg.probe_duration(prepared) or input_duration
            required_speed = prepared_duration / max(allowed_duration, 0.01)
            if required_speed > max_speed + 0.0001:
                output.unlink(missing_ok=True)
                return SyncOutcome(
                    success=False,
                    input_duration=input_duration,
                    prepared_duration=prepared_duration,
                    output_duration=0.0,
                    target_duration=target_duration,
                    allowed_duration=allowed_duration,
                    speed_factor=required_speed,
                    used_gap=max(0.0, allowed_duration - target_duration),
                    detected_leading_silence=detected_leading,
                    detected_trailing_silence=detected_trailing,
                    trimmed_leading_silence=trimmed_leading,
                    trimmed_trailing_silence=trimmed_trailing,
                    silence_trim_decision=trim_decision,
                    error=(
                        f"Cần tốc độ {required_speed:.2f}x nhưng giới hạn là {max_speed:.2f}x"
                    ),
                )

            speed_factor = max(1.0, required_speed)
            natural_after_speed = prepared_duration / speed_factor
            final_duration = (
                target_duration if natural_after_speed <= target_duration else natural_after_speed
            )
            filters: list[str] = []
            atempo = self._atempo_filter(speed_factor)
            if atempo:
                filters.append(atempo)
            filters.extend(["apad", f"atrim=duration={final_duration:.6f}"])
            self._run_ffmpeg(prepared, output, filters)
            measured = self.ffmpeg.probe_duration(output) or final_duration
            return SyncOutcome(
                success=True,
                input_duration=input_duration,
                prepared_duration=prepared_duration,
                output_duration=measured,
                target_duration=target_duration,
                allowed_duration=allowed_duration,
                speed_factor=speed_factor,
                used_gap=max(0.0, measured - target_duration),
                detected_leading_silence=detected_leading,
                detected_trailing_silence=detected_trailing,
                trimmed_leading_silence=trimmed_leading,
                trimmed_trailing_silence=trimmed_trailing,
                silence_trim_decision=trim_decision,
            )
        finally:
            prepared.unlink(missing_ok=True)

    def prepare_timeline_file(
        self,
        source_path: str | Path,
        output_path: str | Path,
        speed_factor: float,
        leading_trim: float = 0.0,
        trailing_trim: float = 0.0,
    ) -> float:
        source = Path(source_path)
        input_duration = self.ffmpeg.probe_duration(source) or 0.0
        filters = self._edge_trim_filters(input_duration, leading_trim, trailing_trim)
        atempo = self._atempo_filter(max(1.0, speed_factor))
        if atempo:
            filters.append(atempo)
        self._run_ffmpeg(source, Path(output_path), filters)
        measured = self.ffmpeg.probe_duration(output_path)
        if not measured or measured <= 0:
            raise AudioSyncError(
                "Không đọc được audio sau khi cân timeline",
                str(output_path),
                "Kiểm tra file TTS nguồn và FFprobe.",
            )
        return measured


NEIGHBOR_MAX_SHIFT = 0.5
NEIGHBOR_GAP_LIMIT = 1.0


def _timeline_schedule(
    items: list[dict[str, Any]],
    durations: list[float],
) -> list[float] | None:
    if not items or len(items) != len(durations):
        return None
    group_start = float(items[0].get("start", 0.0))
    group_end = float(items[-1].get("end", group_start))
    lower: list[float] = []
    upper: list[float] = []
    for item, duration in zip(items, durations, strict=True):
        start = float(item.get("start", 0.0))
        lower.append(max(group_start, start - NEIGHBOR_MAX_SHIFT))
        upper.append(min(start + NEIGHBOR_MAX_SHIFT, group_end - duration))

    earliest: list[float] = []
    for index, duration in enumerate(durations):
        value = lower[index]
        if index:
            value = max(value, earliest[index - 1] + durations[index - 1])
        earliest.append(value)
    latest = [0.0] * len(items)
    for index in range(len(items) - 1, -1, -1):
        value = upper[index]
        if index < len(items) - 1:
            value = min(value, latest[index + 1] - durations[index])
        latest[index] = value
    if any(earliest[index] > latest[index] + 0.0001 for index in range(len(items))):
        return None

    starts: list[float] = []
    for index, item in enumerate(items):
        preferred = float(item.get("start", 0.0))
        if index:
            preferred = max(preferred, starts[index - 1] + durations[index - 1])
        starts.append(min(latest[index], max(earliest[index], preferred)))
    return starts


def _borrow_groups(segments: list[dict[str, Any]]) -> list[tuple[int, int]]:
    error_indexes = [index for index, item in enumerate(segments) if item.get("status") != "ready"]
    raw_groups: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(error_indexes):
        first_error = error_indexes[cursor]
        last_error = first_error
        while cursor + 1 < len(error_indexes) and error_indexes[cursor + 1] == last_error + 1:
            cursor += 1
            last_error = error_indexes[cursor]
        first = first_error
        last = last_error
        if first_error > 0:
            gap = float(segments[first_error].get("start", 0.0)) - float(segments[first_error - 1].get("end", 0.0))
            if gap <= NEIGHBOR_GAP_LIMIT:
                first -= 1
        if last_error + 1 < len(segments):
            gap = float(segments[last_error + 1].get("start", 0.0)) - float(segments[last_error].get("end", 0.0))
            if gap <= NEIGHBOR_GAP_LIMIT:
                last += 1
        raw_groups.append((first, last))
        cursor += 1

    merged: list[tuple[int, int]] = []
    for first, last in raw_groups:
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def apply_neighbor_borrow(
    segments: list[dict[str, Any]],
    max_speed: float,
    trim_silence: bool,
) -> int:
    """Cân timeline cho các nhóm có lỗi, chỉ dùng tối đa một hàng xóm mỗi phía."""
    for item in segments:
        prepared = max(0.01, float(item.get("prepared_duration", item.get("input_duration", 0.01))))
        speed = max(1.0, float(item.get("speed_factor", 1.0)))
        item.setdefault("play_duration", prepared / speed)
        item.setdefault("adjusted_start", float(item.get("start", 0.0)))
        item.setdefault("adjusted_end", float(item.get("adjusted_start", 0.0)) + float(item["play_duration"]))
        item.setdefault("sync_strategy", "local")
        item.setdefault("borrowed_before", 0.0)
        item.setdefault("borrowed_after", 0.0)

    service: AudioSyncService | None = None
    for first, last in _borrow_groups(segments):
        group = segments[first : last + 1]
        error_positions = [index for index, item in enumerate(group) if item.get("status") != "ready"]
        if not error_positions:
            continue

        def durations_for(speed: float) -> list[float]:
            values: list[float] = []
            for index, item in enumerate(group):
                prepared = max(0.01, float(item.get("prepared_duration", item.get("input_duration", 0.01))))
                if index in error_positions:
                    values.append(prepared / speed)
                else:
                    values.append(max(0.01, float(item.get("play_duration", prepared))))
            return values

        low = 1.0
        high = max(1.0, max_speed)
        schedule = _timeline_schedule(group, durations_for(low))
        chosen_speed = low
        if schedule is None:
            schedule = _timeline_schedule(group, durations_for(high))
            if schedule is None:
                continue
            for _ in range(18):
                middle = (low + high) / 2
                candidate = _timeline_schedule(group, durations_for(middle))
                if candidate is None:
                    low = middle
                else:
                    high = middle
                    schedule = candidate
            chosen_speed = high
        durations = durations_for(chosen_speed)
        schedule = _timeline_schedule(group, durations)
        if schedule is None:
            continue

        service = service or AudioSyncService()
        render_failed = False
        for position in error_positions:
            item = group[position]
            source_value = str(item.get("audio_file", "")).strip()
            output_value = str(item.get("timeline_output_file") or item.get("sync_output_file") or "").strip()
            source = Path(source_value)
            output = Path(output_value) if output_value else Path("__missing_timeline_output__")
            if not source_value or not source.is_file() or not output_value:
                render_failed = True
                break
            try:
                measured = service.prepare_timeline_file(
                    source,
                    output,
                    chosen_speed,
                    float(item.get("trimmed_leading_silence", 0.0)),
                    float(item.get("trimmed_trailing_silence", 0.0)),
                )
            except Exception:
                render_failed = True
                break
            durations[position] = measured
        if render_failed:
            continue
        schedule = _timeline_schedule(group, durations)
        if schedule is None:
            continue

        for position, item in enumerate(group):
            adjusted_start = schedule[position]
            adjusted_end = adjusted_start + durations[position]
            original_start = float(item.get("start", 0.0))
            original_end = float(item.get("end", original_start))
            item["adjusted_start"] = round(adjusted_start, 6)
            item["adjusted_end"] = round(adjusted_end, 6)
            item["play_duration"] = round(durations[position], 6)
            item["borrowed_before"] = round(max(0.0, original_start - adjusted_start), 6)
            item["borrowed_after"] = round(max(0.0, adjusted_end - original_end), 6)
            if position in error_positions:
                output = str(item.get("timeline_output_file") or item.get("sync_output_file") or "")
                item["synced_audio_file"] = output
                item["output_duration"] = durations[position]
                item["speed_factor"] = chosen_speed
                item["status"] = "ready"
                item["repair_status"] = "resolved"
                item["sync_strategy"] = "neighbor_borrow"
                item["error"] = ""
            elif abs(adjusted_start - original_start) > 0.0001:
                item["sync_strategy"] = "neighbor_donor"

    return sum(1 for item in segments if item.get("status") != "ready")


def borrow_neighbor_time(
    manifest_path: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    manifest, payload = _read_manifest(manifest_path, "Step 5")
    raw_segments = payload.get("segments", [])
    segments = [item for item in raw_segments if isinstance(item, dict)] if isinstance(raw_segments, list) else []
    pending_voice = [
        item for item in segments if item.get("repair_status") == "awaiting_voice"
    ]
    if pending_voice:
        raise AudioSyncError(
            "Segment đang chờ tạo voice",
            "Vẫn còn nội dung mới chưa được tạo voice: "
            + ", ".join(f"#{int(item.get('id', 0)):04d}" for item in pending_voice),
            "Chọn các row này và bấm “Tạo lại voice và đồng bộ” trước khi vay thời gian.",
        )
    unresolved_indexes = [index for index, item in enumerate(segments) if item.get("status") != "ready"]
    if not unresolved_indexes:
        raise AudioSyncError("Không còn segment lỗi", "Tất cả segment trong candidate đã được xử lý.")
    error_indexes = unresolved_indexes

    max_speed = max(1.0, float(payload.get("max_speed", 1.35)))
    service = AudioSyncService()
    attempt_folder = manifest.parent / "repairs" / f"borrow-{uuid4().hex[:8]}"
    attempt_folder.mkdir(parents=True, exist_ok=False)
    claimed: set[int] = set()
    staged: dict[int, tuple[Path, float]] = {}
    resolved_count = 0

    def eligible_neighbor(index: int, error_index: int) -> bool:
        if index < 0 or index >= len(segments) or index in claimed:
            return False
        neighbor = segments[index]
        if neighbor.get("status") != "ready":
            return False
        if index < error_index:
            gap = float(segments[error_index].get("start", 0.0)) - float(neighbor.get("end", 0.0))
        else:
            gap = float(neighbor.get("start", 0.0)) - float(segments[error_index].get("end", 0.0))
        return gap <= NEIGHBOR_GAP_LIMIT

    def prepare(index: int) -> tuple[Path, float]:
        if index in staged:
            return staged[index]
        item = segments[index]
        source = Path(str(item.get("audio_file", "")))
        if not source.is_file():
            raise AudioSyncError(
                "Thiếu audio Step 4",
                f"Không tìm thấy audio của segment #{int(item.get('id', 0)):04d}: {source}",
            )
        output = attempt_folder / f"segment_{int(item.get('id', index + 1)):04d}.wav"
        measured = service.prepare_timeline_file(
            source,
            output,
            max_speed,
            float(item.get("trimmed_leading_silence", 0.0)),
            float(item.get("trimmed_trailing_silence", 0.0)),
        )
        staged[index] = (output, measured)
        return output, measured

    for order, error_index in enumerate(error_indexes, start=1):
        error_item = segments[error_index]
        if error_item.get("status") == "ready" or error_index in claimed:
            continue
        if progress:
            progress(
                round((order - 1) / max(1, len(error_indexes)) * 90),
                f"Đang thử vay thời gian cho segment #{int(error_item.get('id', 0)):04d}…",
            )
        has_next = eligible_neighbor(error_index + 1, error_index)
        has_previous = eligible_neighbor(error_index - 1, error_index)
        options: list[list[int]] = []
        if has_next:
            options.append([error_index, error_index + 1])
        if has_previous:
            if has_next:
                options.append([error_index - 1, error_index, error_index + 1])
            else:
                options.append([error_index - 1, error_index])

        selected_group: list[int] | None = None
        selected_starts: list[float] | None = None
        selected_durations: list[float] | None = None
        last_error = "Không có segment A/C hợp lệ để cho vay thời gian."
        for indexes in options:
            group = [segments[index] for index in indexes]
            estimated = [
                max(0.01, float(item.get("prepared_duration", item.get("input_duration", 0.01)))) / max_speed
                for item in group
            ]
            if _timeline_schedule(group, estimated) is None:
                last_error = "Thời gian của hàng xóm vẫn không đủ ở tốc độ tối đa."
                continue
            try:
                prepared = [prepare(index) for index in indexes]
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                continue
            durations = [duration for _, duration in prepared]
            starts = _timeline_schedule(group, durations)
            if starts is None:
                last_error = "Audio đo lại không thể xếp tuần tự trong khung A/B/C."
                continue
            selected_group = indexes
            selected_starts = starts
            selected_durations = durations
            break

        if selected_group is None or selected_starts is None or selected_durations is None:
            error_item["seq"] = int(error_item.get("seq", 1)) + 1
            error_item["repair_status"] = "too_long"
            error_item["error"] = last_error
            continue

        for position, index in enumerate(selected_group):
            item = segments[index]
            staged_file, measured = staged[index]
            segment_id = int(item.get("id", index + 1))
            target_value = str(item.get("sync_output_file") or item.get("timeline_output_file") or "").strip()
            target = Path(target_value) if target_value else manifest.parent / "segments" / f"segment_{segment_id:04d}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.stem}.borrow{target.suffix}")
            shutil.copy2(staged_file, temporary)
            temporary.replace(target)
            adjusted_start = selected_starts[position]
            adjusted_end = adjusted_start + measured
            original_start = float(item.get("start", 0.0))
            original_end = float(item.get("end", original_start))
            item["synced_audio_file"] = str(target)
            item["sync_output_file"] = str(target)
            item["timeline_output_file"] = str(target)
            item["adjusted_start"] = round(adjusted_start, 6)
            item["adjusted_end"] = round(adjusted_end, 6)
            item["play_duration"] = round(measured, 6)
            item["output_duration"] = round(measured, 6)
            item["speed_factor"] = max_speed
            item["borrowed_before"] = round(max(0.0, original_start - adjusted_start), 6)
            item["borrowed_after"] = round(max(0.0, adjusted_end - original_end), 6)
            item["sync_strategy"] = "neighbor_borrow" if index == error_index else "neighbor_donor"
            if index == error_index:
                item["status"] = "ready"
                item["repair_status"] = "resolved"
                item["error"] = ""
        claimed.update(selected_group)
        resolved_count += 1

    error_count = sum(1 for item in segments if item.get("status") != "ready")
    payload["error_count"] = error_count
    payload["status"] = "completed" if error_count == 0 else "needs_edit"
    payload["repair_revision"] = int(payload.get("repair_revision", 0)) + 1
    _write_manifest(manifest, payload)
    if progress:
        progress(100, f"Đã xử lý vay thời gian: {resolved_count}/{len(error_indexes)} segment đạt.")
    return {
        "candidate_id": str(payload.get("candidate_id", "")),
        "processed_count": len(error_indexes),
        "resolved_count": resolved_count,
        "error_count": error_count,
        "message": f"Đã vay thời gian cho {resolved_count}/{len(error_indexes)} segment lỗi.",
    }


def _read_manifest(path: str | Path, step_name: str) -> tuple[Path, dict[str, Any]]:
    manifest = Path(path)
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AudioSyncError(
            f"Không thể mở candidate {step_name}",
            str(manifest),
            "Kiểm tra file manifest của output đang được cập nhật.",
            str(exc),
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise AudioSyncError(
            f"Candidate {step_name} không hợp lệ",
            f"Manifest {manifest.name} không có danh sách segment hợp lệ.",
        )
    return manifest, payload


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _segment_map(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(item.get("id", -1)): item
        for item in payload.get("segments", [])
        if isinstance(item, dict)
    }


def rewrite_sync_drafts(
    manifest_path: str,
    selected_texts: dict[int, str],
    model_name: str,
    source_language: str,
    target_language: str,
    progress: ProgressCallback | None = None,
    provider_name: str = GEMINI_PROVIDER_NAME,
) -> dict[str, Any]:
    manifest, payload = _read_manifest(manifest_path, "Step 5")
    if not selected_texts:
        raise AudioSyncError(
            "Chưa chọn segment",
            "Bạn chưa đánh dấu checkbox cho segment nào.",
            "Chọn ít nhất một segment rồi nhấn nút AI lần nữa.",
        )
    segments = _segment_map(payload)
    chosen: list[dict[str, object]] = []
    max_speed = max(1.0, float(payload.get("max_speed", 1.35)))
    target_speed = min(max_speed, 1.25)
    for raw_segment_id, selected_text in selected_texts.items():
        segment_id = int(raw_segment_id)
        segment = segments.get(segment_id)
        if segment is None:
            raise AudioSyncError("Không tìm thấy segment", f"Segment #{segment_id:04d} không có trong Step 5.")
        current_text = selected_text.strip()
        if not current_text:
            raise AudioSyncError("Nội dung đang trống", f"Segment #{segment_id:04d} chưa có nội dung để AI chỉnh sửa.")
        measured = max(0.01, float(segment.get("prepared_duration", 0.0)))
        allowed = max(0.01, float(segment.get("allowed_duration", 0.0)))
        desired = allowed * target_speed
        chosen.append(
            {
                "id": segment_id,
                "source_text": str(segment.get("source_text", "")),
                "current_translation": current_text,
                "measured_tts_seconds": round(measured, 3),
                "allowed_seconds": round(allowed, 3),
                "current_required_speed": round(measured / allowed, 3),
                "target_speed": round(target_speed, 2),
                "requested_reduction_ratio": round(min(1.0, desired / measured), 3),
            }
        )
    if not chosen:
        raise AudioSyncError(
            "Không có segment cần AI chỉnh sửa",
            "Không có dữ liệu hợp lệ trong các segment đã chọn.",
        )
    provider = create_translation_provider(
        provider_name,
        model_name,
        source_language,
        target_language,
    )
    response = provider.rewrite_for_timing(
        chosen,
        progress=progress,
    )
    for segment_id, text in response.translations.items():
        segment = segments[segment_id]
        segment["draft_text"] = text
        segment["edit_source"] = "ai"
        segment["repair_status"] = "awaiting_voice"
        segment["ai_rewrite_count"] = int(segment.get("ai_rewrite_count", 0)) + 1
    _write_manifest(manifest, payload)
    return {
        "candidate_id": str(payload.get("candidate_id", "")),
        "translations": response.translations,
        "credential_source": response.credential_source,
        "message": f"AI đã chỉnh sửa {len(response.translations)} segment.",
    }


def repair_sync_segments(
    sync_manifest_path: str,
    tts_manifest_path: str,
    translation_manifest_path: str,
    transcript_manifest_path: str,
    segment_updates: dict[int, dict[str, Any]],
    target_language: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    sync_manifest, sync_payload = _read_manifest(sync_manifest_path, "Step 5")
    tts_manifest, tts_payload = _read_manifest(tts_manifest_path, "Step 4")
    translation_manifest, translation_payload = _read_manifest(translation_manifest_path, "Step 3")
    transcript_manifest: Path | None = None
    transcript_payload: dict[str, Any] | None = None
    transcript_segments: dict[int, dict[str, Any]] = {}
    if transcript_manifest_path:
        transcript_manifest, transcript_payload = _read_manifest(transcript_manifest_path, "Step 2")
        transcript_segments = _segment_map(transcript_payload)
    sync_segments = _segment_map(sync_payload)
    tts_segments = _segment_map(tts_payload)
    translation_segments = _segment_map(translation_payload)

    normalized: dict[int, dict[str, Any]] = {}
    for raw_segment_id, raw_update in segment_updates.items():
        if not isinstance(raw_update, dict):
            continue
        try:
            normalized[int(raw_segment_id)] = {
                "translated_text": str(raw_update.get("translated_text", "")).strip(),
                "speaker_id": str(raw_update.get("speaker_id", "")).strip(),
                "start": max(0.0, float(raw_update.get("start", 0.0))),
                "end": max(0.0, float(raw_update.get("end", 0.0))),
            }
        except (TypeError, ValueError) as exc:
            raise AudioSyncError("Thời gian segment chưa hợp lệ", str(exc)) from exc
    invalid_time_ids = [
        segment_id
        for segment_id, update in normalized.items()
        if float(update["end"]) < float(update["start"])
    ]
    if invalid_time_ids:
        raise AudioSyncError(
            "Thời gian segment chưa hợp lệ",
            "End phải lớn hơn hoặc bằng Start cho: "
            + ", ".join(f"#{item:04d}" for item in invalid_time_ids),
        )
    empty_ids = [
        segment_id
        for segment_id, update in normalized.items()
        if (
            float(update["start"]) < float(update["end"])
            and (not update["translated_text"] or not update["speaker_id"])
        )
    ]
    if empty_ids:
        raise AudioSyncError(
            "Nội dung sửa đang trống",
            "Segment có thời lượng lớn hơn 0 phải có speaker và nội dung: "
            + ", ".join(f"#{item:04d}" for item in empty_ids),
        )
    target_ids = [segment_id for segment_id in normalized if segment_id in sync_segments]
    if not target_ids:
        raise AudioSyncError(
            "Chưa chọn segment",
            "Bạn chưa đánh dấu checkbox cho segment nào cần tạo lại voice.",
        )
    missing = [
        segment_id
        for segment_id in target_ids
        if (
            segment_id not in tts_segments
            or segment_id not in translation_segments
            or (transcript_segments and segment_id not in transcript_segments)
        )
    ]
    if missing:
        raise AudioSyncError(
            "Chuỗi candidate không đồng nhất",
            "Không tìm thấy segment tương ứng trong chuỗi Step 2–4: "
            + ", ".join(f"#{item:04d}" for item in missing),
        )

    settings = dict(sync_payload.get("source_tts_settings", {}))
    provider = str(settings.get("provider", ""))
    if not provider:
        raise AudioSyncError(
            "Thiếu cấu hình Step 4",
            "Candidate Step 5 không lưu provider TTS nguồn.",
            "Chạy lại Step 5 từ một output Step 4 hợp lệ.",
        )
    for segment_id in target_ids:
        update = normalized[segment_id]
        start = float(update["start"])
        end = float(update["end"])
        text = str(update["translated_text"])
        speaker_id = str(update["speaker_id"])
        duration = end - start
        sync_segment = sync_segments[segment_id]
        previous_speaker = str(sync_segment.get("speaker_id", ""))
        related = [translation_segments[segment_id], tts_segments[segment_id], sync_segments[segment_id]]
        if transcript_segments:
            related.append(transcript_segments[segment_id])
        for item in related:
            item["start"] = start
            item["end"] = end
            item["speaker_id"] = speaker_id
            item["updated_in_step_5"] = True
        translation_segments[segment_id]["translated_text"] = text
        tts_segments[segment_id]["translated_text"] = text
        sync_segment.setdefault("original_translated_text", str(sync_segment.get("translated_text", "")))
        previous_draft = str(sync_segment.get("draft_text") or sync_segment.get("translated_text", "")).strip()
        if text != previous_draft:
            sync_segment["edit_source"] = "manual"
        sync_segment["draft_text"] = text
        sync_segment["translated_text"] = text
        sync_segment["target_duration"] = duration
        sync_segment["allowed_duration"] = duration
        sync_segment["corrected_in_step_5"] = True
        sync_segment["speaker_corrected_in_step_5"] = speaker_id != previous_speaker

    active_ids = [
        segment_id
        for segment_id in target_ids
        if float(normalized[segment_id]["end"]) > float(normalized[segment_id]["start"])
    ]
    zero_duration_ids = [segment_id for segment_id in target_ids if segment_id not in active_ids]
    for segment_id in zero_duration_ids:
        sync_segment = sync_segments[segment_id]
        start = float(normalized[segment_id]["start"])
        sync_segment["synced_audio_file"] = ""
        sync_segment["status"] = "ready"
        sync_segment["repair_status"] = "resolved"
        sync_segment["error"] = ""
        sync_segment["input_duration"] = 0.0
        sync_segment["prepared_duration"] = 0.0
        sync_segment["output_duration"] = 0.0
        sync_segment["speed_factor"] = 1.0
        sync_segment["play_duration"] = 0.0
        sync_segment["adjusted_start"] = start
        sync_segment["adjusted_end"] = start
        sync_segment["sync_strategy"] = "zero_duration"
        sync_segment["borrowed_before"] = 0.0
        sync_segment["borrowed_after"] = 0.0
        sync_segment["used_gap"] = 0.0

    repair_root = sync_manifest.parent / "repairs" / f"batch-{uuid4().hex[:8]}"
    synthesis = None
    if active_ids:
        repair_root.mkdir(parents=True, exist_ok=False)
        if progress:
            progress(3, f"Đang tạo lại voice cho {len(active_ids)} segment…")
        synthesis = TextToSpeechService(provider, settings).synthesize(
            [
                {
                    "id": segment_id,
                    "text": normalized[segment_id]["translated_text"],
                    "speaker_id": normalized[segment_id]["speaker_id"],
                }
                for segment_id in active_ids
            ],
            repair_root,
            target_language,
            (lambda value, message: progress(min(55, 3 + value // 2), message)) if progress else None,
        )
    service = AudioSyncService()
    outcomes: dict[int, SyncOutcome | None] = {}
    errors: dict[int, str] = {}
    generated_files = synthesis.files if synthesis else []
    generated_segments = synthesis.segments if synthesis else []
    for index, (segment_id, generated_path, segment_result) in enumerate(
        zip(active_ids, generated_files, generated_segments, strict=True), start=1
    ):
        sync_segment = sync_segments[segment_id]
        tts_segment = tts_segments[segment_id]
        text = str(normalized[segment_id]["translated_text"])
        speaker_id = str(normalized[segment_id]["speaker_id"])
        target_audio_value = str(tts_segment.get("audio_file", "")).strip()
        if target_audio_value:
            target_audio = Path(target_audio_value)
        else:
            target_audio = Path(tts_manifest).parent / "segments" / f"segment_{segment_id:04d}.wav"
        target_audio.parent.mkdir(parents=True, exist_ok=True)
        audio_part = target_audio.with_name(f".{target_audio.stem}.repair{target_audio.suffix}")
        shutil.copy2(generated_path, audio_part)
        audio_part.replace(target_audio)
        synced_audio = sync_manifest.parent / "segments" / f"segment_{segment_id:04d}.wav"
        if progress:
            progress(55 + round(index / len(active_ids) * 35), f"Đang đồng bộ segment #{segment_id:04d}…")
        try:
            outcome = service.synchronize_file(
                target_audio,
                synced_audio,
                float(sync_segment.get("target_duration", 0.0)),
                float(sync_segment.get("allowed_duration", 0.0)),
                float(sync_payload.get("max_speed", 1.35)),
                bool(sync_payload.get("trim_silence", False)),
            )
        except Exception as exc:
            outcome = None
            errors[segment_id] = str(exc) or exc.__class__.__name__
            synced_audio.unlink(missing_ok=True)
        outcomes[segment_id] = outcome

        tts_segment["audio_file"] = str(target_audio)
        tts_segment["tts_provider"] = segment_result.provider
        tts_segment["tts_model"] = segment_result.model
        tts_segment["tts_voice"] = segment_result.voice
        tts_segment["tts_reference_voice"] = segment_result.reference_voice
        tts_segment["tts_actual_device"] = segment_result.actual_device
        tts_segment["updated_in_step_5"] = True
        sync_segment["audio_file"] = str(target_audio)
        sync_segment["tts_provider"] = segment_result.provider
        sync_segment["tts_model"] = segment_result.model
        sync_segment["tts_voice"] = segment_result.voice
        sync_segment["tts_reference_voice"] = segment_result.reference_voice
        sync_segment["tts_actual_device"] = segment_result.actual_device
        sync_segment["sync_output_file"] = str(synced_audio)
        sync_segment["timeline_output_file"] = str(synced_audio)
        sync_segment["repair_attempts"] = int(sync_segment.get("repair_attempts", 0)) + 1
        sync_segment.setdefault("initial_sync_error", sync_segment.get("status") != "ready")
        legacy_seq = 1 if sync_segment.get("status") != "ready" or sync_segment.get("corrected_in_step_5") else 0
        current_seq = int(sync_segment.get("seq", legacy_seq))
        sync_segment["seq"] = current_seq
        if outcome is None:
            sync_segment["seq"] = current_seq + 1
            sync_segment["synced_audio_file"] = ""
            sync_segment["status"] = "needs_edit"
            sync_segment["repair_status"] = "error"
            sync_segment["error"] = errors[segment_id]
        else:
            if not outcome.success:
                sync_segment["seq"] = current_seq + 1
            sync_segment["synced_audio_file"] = str(synced_audio) if outcome.success else ""
            sync_segment["status"] = "ready" if outcome.success else "needs_edit"
            sync_segment["repair_status"] = "resolved" if outcome.success else "too_long"
            sync_segment["error"] = outcome.error
            sync_segment["input_duration"] = outcome.input_duration
            sync_segment["prepared_duration"] = outcome.prepared_duration
            sync_segment["output_duration"] = outcome.output_duration
            sync_segment["speed_factor"] = outcome.speed_factor
            sync_segment["play_duration"] = (
                outcome.prepared_duration / max(1.0, outcome.speed_factor)
            )
            sync_segment["adjusted_start"] = float(sync_segment.get("start", 0.0))
            sync_segment["adjusted_end"] = (
                float(sync_segment.get("start", 0.0)) + sync_segment["play_duration"]
            )
            sync_segment["sync_strategy"] = "local"
            sync_segment["borrowed_before"] = 0.0
            sync_segment["borrowed_after"] = 0.0
            sync_segment["used_gap"] = outcome.used_gap
            sync_segment["detected_leading_silence"] = round(
                outcome.detected_leading_silence, 6
            )
            sync_segment["detected_trailing_silence"] = round(
                outcome.detected_trailing_silence, 6
            )
            sync_segment["trimmed_leading_silence"] = round(
                outcome.trimmed_leading_silence, 6
            )
            sync_segment["trimmed_trailing_silence"] = round(
                outcome.trimmed_trailing_silence, 6
            )
            sync_segment["silence_trim_decision"] = outcome.silence_trim_decision

    error_count = sum(1 for item in sync_segments.values() if item.get("status") != "ready")
    sync_payload["error_count"] = error_count
    sync_payload["status"] = "completed" if error_count == 0 else "needs_edit"
    sync_payload["repair_revision"] = int(sync_payload.get("repair_revision", 0)) + 1
    translation_payload["repair_revision"] = int(translation_payload.get("repair_revision", 0)) + 1
    tts_payload["repair_revision"] = int(tts_payload.get("repair_revision", 0)) + 1
    if transcript_manifest and transcript_payload is not None:
        transcript_payload["repair_revision"] = int(transcript_payload.get("repair_revision", 0)) + 1
        _write_manifest(transcript_manifest, transcript_payload)
    _write_manifest(translation_manifest, translation_payload)
    _write_manifest(tts_manifest, tts_payload)
    _write_manifest(sync_manifest, sync_payload)
    if progress:
        progress(100, "Đã cập nhật thời gian/speaker/nội dung trong chuỗi Step 2 → Step 5.")
    resolved = sum(1 for segment_id in target_ids if sync_segments[segment_id].get("status") == "ready")
    return {
        "candidate_id": str(sync_payload.get("candidate_id", "")),
        "processed_count": len(target_ids),
        "resolved_count": resolved,
        "error_count": error_count,
        "message": f"Đã xử lý {len(target_ids)} segment · {resolved} segment đạt thời lượng.",
    }
