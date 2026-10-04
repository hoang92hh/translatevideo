from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..errors import UserFacingError
from .ffmpeg_service import FFmpegError, FFmpegService
from .text_to_speech_service import TextToSpeechService


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
    error: str = ""


class AudioSyncService:
    SAMPLE_RATE = 48_000
    CHANNELS = 1

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
        trim_filter = []
        if trim_silence:
            trim_filter = [
                "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB:"
                "stop_periods=1:stop_duration=0.10:stop_threshold=-45dB"
            ]
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
            )
        finally:
            prepared.unlink(missing_ok=True)


def repair_sync_segment(
    manifest_path: str,
    segment_id: int,
    translated_text: str,
    target_language: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    manifest = Path(manifest_path)
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AudioSyncError(
            "Không thể mở candidate Step 5",
            str(manifest),
            "Kiểm tra file manifest của output cần sửa.",
            str(exc),
        ) from exc
    segments = payload.get("segments", [])
    segment = next(
        (item for item in segments if isinstance(item, dict) and int(item.get("id", -1)) == segment_id),
        None,
    )
    if segment is None:
        raise AudioSyncError("Không tìm thấy segment", f"Segment #{segment_id} không có trong candidate.")
    text = translated_text.strip()
    if not text:
        raise AudioSyncError("Nội dung sửa đang trống", "Hãy nhập câu dịch trước khi tạo lại giọng nói.")

    tts_settings = payload.get("source_tts_settings", {})
    provider = str(tts_settings.get("provider", ""))
    if not provider:
        raise AudioSyncError(
            "Thiếu cấu hình Step 4",
            "Candidate này không lưu provider TTS nguồn.",
            "Chạy lại Step 5 từ một output Step 4 mới.",
        )
    candidate_folder = manifest.parent
    repair_root = candidate_folder / "repairs" / f"segment_{segment_id:04d}"
    attempt_folder = repair_root / f"attempt-{uuid4().hex[:8]}"
    attempt_folder.mkdir(parents=True)
    if progress:
        progress(5, f"Đang tạo lại giọng cho segment #{segment_id:04d}…")
    synthesis = TextToSpeechService(provider, dict(tts_settings)).synthesize(
        [{"id": segment_id, "text": text}],
        attempt_folder,
        target_language,
        (lambda value, message: progress(min(55, 5 + value // 2), message)) if progress else None,
    )
    repaired_audio = Path(synthesis.files[0])
    synced_audio = candidate_folder / "segments" / f"segment_{segment_id:04d}.wav"
    service = AudioSyncService()
    if progress:
        progress(65, f"Đang đồng bộ lại segment #{segment_id:04d}…")
    outcome = service.synchronize_file(
        repaired_audio,
        synced_audio,
        float(segment.get("target_duration", 0.0)),
        float(segment.get("allowed_duration", 0.0)),
        float(payload.get("max_speed", 1.35)),
        bool(payload.get("trim_silence", True)),
    )
    segment.setdefault("original_translated_text", str(segment.get("translated_text", "")))
    segment["translated_text"] = text
    segment["audio_file"] = str(repaired_audio)
    segment["synced_audio_file"] = str(synced_audio) if outcome.success else ""
    segment["status"] = "ready" if outcome.success else "needs_edit"
    segment["error"] = outcome.error
    segment["input_duration"] = outcome.input_duration
    segment["prepared_duration"] = outcome.prepared_duration
    segment["output_duration"] = outcome.output_duration
    segment["speed_factor"] = outcome.speed_factor
    segment["used_gap"] = outcome.used_gap
    segment["corrected_in_step_5"] = True
    error_count = sum(1 for item in segments if item.get("status") != "ready")
    payload["error_count"] = error_count
    payload["status"] = "completed" if error_count == 0 else "needs_edit"
    temporary = manifest.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest)
    if progress:
        progress(100, "Đã cập nhật segment." if outcome.success else outcome.error)
    return {
        "candidate_id": str(payload.get("candidate_id", "")),
        "segment_id": segment_id,
        "success": outcome.success,
        "error_count": error_count,
        "message": outcome.error,
    }
