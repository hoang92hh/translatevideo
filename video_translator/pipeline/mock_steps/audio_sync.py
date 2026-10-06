from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...errors import UserFacingError
from ...models import StepId, StepResult, SyncCandidate
from ...services import AudioSyncService
from ...state import ProjectState
from .common import previous_segments


def _tts_settings(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(metadata.get("provider", "")),
        "voice": str(metadata.get("voice", "Default")),
        "speed": float(metadata.get("speed", 1.0)),
        "device": str(metadata.get("requested_device", "Auto")),
        "reference_voice": str(metadata.get("reference_voice", "")),
        "voice_consent": bool(metadata.get("voice_rights_confirmed", False)),
    }


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    segments = previous_segments(state, StepId.SYNC)
    if not segments or any(not item.audio_file or not Path(item.audio_file).is_file() for item in segments):
        raise UserFacingError(
            "Output Step 4 không hợp lệ",
            "Step 5 cần một file audio còn tồn tại cho mọi segment.",
            "Chọn lại output Step 4 hợp lệ rồi chạy Step 5.",
        )
    max_speed = max(1.0, float(settings.get("max_speed", 1.35)))
    use_gap = bool(settings.get("use_gap", True))
    trim_silence = bool(settings.get("trim_silence", True))
    now = datetime.now(timezone.utc)
    candidate_id = f"sync-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    candidate_folder = Path(state.workspace_path("synchronized_audio", candidate_id))
    segment_folder = candidate_folder / "segments"
    manifest_path = candidate_folder / "manifest.json"
    segment_folder.mkdir(parents=True, exist_ok=False)
    service = AudioSyncService()
    payload_segments: list[dict[str, Any]] = []
    error_count = 0
    try:
        for index, segment in enumerate(segments):
            target_duration = max(0.01, segment.duration)
            next_start = segments[index + 1].start if index + 1 < len(segments) else segment.end
            gap_duration = max(0.0, next_start - segment.end) if use_gap else 0.0
            allowed_duration = target_duration + gap_duration
            output = segment_folder / f"segment_{segment.id:04d}.wav"
            if progress:
                value = round(index / max(1, len(segments)) * 95)
                progress(value, f"Đang đồng bộ segment {index + 1}/{len(segments)}…")
            outcome = service.synchronize_file(
                segment.audio_file,
                output,
                target_duration,
                allowed_duration,
                max_speed,
                trim_silence,
            )
            if outcome.success:
                segment.synced_audio_file = str(output)
            else:
                segment.synced_audio_file = ""
                error_count += 1
            payload_segments.append(
                {
                    "id": segment.id,
                    "start": segment.start,
                    "end": segment.end,
                    "source_text": segment.source_text,
                    "original_translated_text": segment.translated_text,
                    "translated_text": segment.translated_text,
                    "audio_file": segment.audio_file,
                    "synced_audio_file": segment.synced_audio_file,
                    "sync_output_file": str(output),
                    "timeline_output_file": str(output),
                    "target_duration": target_duration,
                    "allowed_duration": allowed_duration,
                    "input_duration": outcome.input_duration,
                    "prepared_duration": outcome.prepared_duration,
                    "output_duration": outcome.output_duration,
                    "speed_factor": outcome.speed_factor,
                    "play_duration": (
                        outcome.prepared_duration / max(1.0, outcome.speed_factor)
                    ),
                    "adjusted_start": segment.start,
                    "adjusted_end": segment.start + (
                        outcome.prepared_duration / max(1.0, outcome.speed_factor)
                    ),
                    "sync_strategy": "local",
                    "borrowed_before": 0.0,
                    "borrowed_after": 0.0,
                    "used_gap": outcome.used_gap,
                    "status": "ready" if outcome.success else "needs_edit",
                    "initial_sync_error": not outcome.success,
                    "seq": 0 if outcome.success else 1,
                    "repair_status": "pending" if not outcome.success else "not_needed",
                    "draft_text": segment.translated_text,
                    "edit_source": "original",
                    "repair_attempts": 0,
                    "ai_rewrite_count": 0,
                    "error": outcome.error,
                    "corrected_in_step_5": False,
                }
            )
        source_metadata = dict(state.results[StepId.TTS].metadata)
        metadata = {
            "source_tts_candidate_id": state.selected_tts_candidate_id,
            "sync_candidate_id": candidate_id,
            "max_speed": max_speed,
            "use_gap": use_gap,
            "trim_silence": trim_silence,
            "error_count": error_count,
            "status": "completed" if error_count == 0 else "needs_edit",
        }
        if error_count == 0:
            metadata["recommended_sync_candidate_id"] = candidate_id
        payload = {
            "version": 1,
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            **metadata,
            "source_tts_settings": _tts_settings(source_metadata),
            "segments": payload_segments,
        }
        temporary = manifest_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest_path)
    except Exception:
        shutil.rmtree(candidate_folder, ignore_errors=True)
        raise

    if error_count:
        summary = (
            f"Đã đồng bộ {len(segments) - error_count}/{len(segments)} segment · "
            f"{error_count} segment cần sửa"
        )
    else:
        summary = f"Đã đồng bộ thành công {len(segments)} segment"
    candidate = SyncCandidate(
        id=candidate_id,
        label=f"Audio Sync · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=now.isoformat(timespec="seconds"),
        path=str(manifest_path),
        folder=str(candidate_folder),
        segment_count=len(segments),
        error_count=error_count,
        summary=summary,
        metadata=metadata,
    )
    return StepResult(
        step=StepId.SYNC,
        summary=summary,
        artifacts={"sync_manifest": str(manifest_path), "synced_folder": str(segment_folder)},
        segments=segments,
        metadata=metadata,
        sync_candidates=[candidate],
    )
