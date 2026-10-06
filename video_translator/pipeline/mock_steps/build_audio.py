from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import Callable
from uuid import uuid4

from ...models import BuildAudioCandidate, StepId, StepResult
from ...services.audio_timeline_service import AudioTimelineService
from ...state import ProjectState
from .common import previous_segments


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    segments = previous_segments(state, StepId.BUILD_AUDIO)
    candidate = state.sync_candidate(state.selected_sync_candidate_id)
    if candidate is None or not Path(candidate.path).is_file():
        raise RuntimeError("Step 6 cần một candidate Step 5 hoàn chỉnh còn tồn tại.")
    now = datetime.now(timezone.utc)
    candidate_id = f"audio-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    candidate_folder = Path(state.workspace_path("built_audio", candidate_id))
    output = candidate_folder / "voice_track.wav"
    manifest = candidate_folder / "manifest.json"
    candidate_folder.mkdir(parents=True, exist_ok=False)
    try:
        result = AudioTimelineService().build(
            candidate.path,
            state.input_video,
            output,
            48_000,
            progress,
        )
        payload = {
            "version": 1,
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            "source_sync_candidate_id": candidate.id,
            "source_sync_manifest": candidate.path,
            "audio_file": str(output),
            **result,
        }
        temporary = manifest.with_suffix(".json.part")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest)
    except Exception:
        shutil.rmtree(candidate_folder, ignore_errors=True)
        raise

    summary = (
        f"Đã ghép {result['segment_count']} segment voice · WAV mono 48 kHz · "
        f"{float(result['duration_seconds']):.2f}s"
    )
    build_candidate = BuildAudioCandidate(
        id=candidate_id,
        label=f"Voice track · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=now.isoformat(timespec="seconds"),
        path=str(manifest),
        folder=str(candidate_folder),
        audio_file=str(output),
        source_sync_candidate_id=candidate.id,
        duration_seconds=float(result["duration_seconds"]),
        segment_count=int(result["segment_count"]),
        summary=summary,
        metadata=payload,
    )
    return StepResult(
        step=StepId.BUILD_AUDIO,
        summary=summary,
        artifacts={"voice_track": str(output), "build_audio_manifest": str(manifest)},
        segments=segments,
        metadata={
            **settings,
            **result,
            "source_sync_candidate_id": candidate.id,
            "build_audio_candidate_id": candidate_id,
            "recommended_build_audio_candidate_id": candidate_id,
        },
        build_audio_candidates=[build_candidate],
    )
