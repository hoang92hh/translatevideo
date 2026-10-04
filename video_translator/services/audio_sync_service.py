from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..errors import UserFacingError
from .ffmpeg_service import FFmpegError, FFmpegService
from .google_translation_service import GoogleTranslationService
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
        if segment.get("status") == "ready":
            continue
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
            "Các segment đã chọn đều có trạng thái Đã xử lý.",
        )
    response = GoogleTranslationService(model_name, source_language, target_language).rewrite_for_timing(
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
    edited_texts: dict[int, str],
    target_language: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    sync_manifest, sync_payload = _read_manifest(sync_manifest_path, "Step 5")
    tts_manifest, tts_payload = _read_manifest(tts_manifest_path, "Step 4")
    translation_manifest, translation_payload = _read_manifest(translation_manifest_path, "Step 3")
    sync_segments = _segment_map(sync_payload)
    tts_segments = _segment_map(tts_payload)
    translation_segments = _segment_map(translation_payload)

    normalized = {int(segment_id): text.strip() for segment_id, text in edited_texts.items()}
    empty_ids = [segment_id for segment_id, text in normalized.items() if not text]
    if empty_ids:
        raise AudioSyncError(
            "Nội dung sửa đang trống",
            "Các segment chưa có nội dung: " + ", ".join(f"#{item:04d}" for item in empty_ids),
        )
    target_ids = [
        segment_id
        for segment_id in normalized
        if segment_id in sync_segments
        and sync_segments[segment_id].get("status") != "ready"
    ]
    if not target_ids:
        raise AudioSyncError(
            "Không có nội dung cần xử lý",
            "Tất cả segment trong danh sách đã được xử lý và chưa có thay đổi mới.",
        )
    missing = [
        segment_id
        for segment_id in target_ids
        if segment_id not in tts_segments or segment_id not in translation_segments
    ]
    if missing:
        raise AudioSyncError(
            "Chuỗi candidate không đồng nhất",
            "Không tìm thấy segment tương ứng ở Step 3 hoặc Step 4: "
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
    repair_root = sync_manifest.parent / "repairs" / f"batch-{uuid4().hex[:8]}"
    repair_root.mkdir(parents=True, exist_ok=False)
    if progress:
        progress(3, f"Đang tạo lại voice cho {len(target_ids)} segment…")
    synthesis = TextToSpeechService(provider, settings).synthesize(
        [{"id": segment_id, "text": normalized[segment_id]} for segment_id in target_ids],
        repair_root,
        target_language,
        (lambda value, message: progress(min(55, 3 + value // 2), message)) if progress else None,
    )
    service = AudioSyncService()
    outcomes: dict[int, SyncOutcome | None] = {}
    errors: dict[int, str] = {}
    for index, (segment_id, generated_path) in enumerate(zip(target_ids, synthesis.files, strict=True), start=1):
        sync_segment = sync_segments[segment_id]
        tts_segment = tts_segments[segment_id]
        text = normalized[segment_id]
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
            progress(55 + round(index / len(target_ids) * 35), f"Đang đồng bộ segment #{segment_id:04d}…")
        try:
            outcome = service.synchronize_file(
                target_audio,
                synced_audio,
                float(sync_segment.get("target_duration", 0.0)),
                float(sync_segment.get("allowed_duration", 0.0)),
                float(sync_payload.get("max_speed", 1.35)),
                bool(sync_payload.get("trim_silence", True)),
            )
        except Exception as exc:
            outcome = None
            errors[segment_id] = str(exc) or exc.__class__.__name__
            synced_audio.unlink(missing_ok=True)
        outcomes[segment_id] = outcome

        translation_segment = translation_segments[segment_id]
        translation_segment["translated_text"] = text
        translation_segment["updated_in_step_5"] = True
        tts_segment["translated_text"] = text
        tts_segment["audio_file"] = str(target_audio)
        tts_segment["updated_in_step_5"] = True
        sync_segment.setdefault("original_translated_text", str(sync_segment.get("translated_text", "")))
        previous_draft = str(sync_segment.get("draft_text") or sync_segment.get("translated_text", "")).strip()
        if text != previous_draft:
            sync_segment["edit_source"] = "manual"
        sync_segment["draft_text"] = text
        sync_segment["translated_text"] = text
        sync_segment["audio_file"] = str(target_audio)
        sync_segment["repair_attempts"] = int(sync_segment.get("repair_attempts", 0)) + 1
        sync_segment["corrected_in_step_5"] = True
        sync_segment["initial_sync_error"] = True
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
            sync_segment["used_gap"] = outcome.used_gap

    error_count = sum(1 for item in sync_segments.values() if item.get("status") != "ready")
    sync_payload["error_count"] = error_count
    sync_payload["status"] = "completed" if error_count == 0 else "needs_edit"
    sync_payload["repair_revision"] = int(sync_payload.get("repair_revision", 0)) + 1
    translation_payload["repair_revision"] = int(translation_payload.get("repair_revision", 0)) + 1
    tts_payload["repair_revision"] = int(tts_payload.get("repair_revision", 0)) + 1
    _write_manifest(translation_manifest, translation_payload)
    _write_manifest(tts_manifest, tts_payload)
    _write_manifest(sync_manifest, sync_payload)
    if progress:
        progress(100, "Đã cập nhật chuỗi Step 3 → Step 4 → Step 5.")
    resolved = sum(1 for segment_id in target_ids if sync_segments[segment_id].get("status") == "ready")
    return {
        "candidate_id": str(sync_payload.get("candidate_id", "")),
        "processed_count": len(target_ids),
        "resolved_count": resolved,
        "error_count": error_count,
        "message": f"Đã xử lý {len(target_ids)} segment · {resolved} segment đạt thời lượng.",
    }
