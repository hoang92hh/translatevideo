from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Callable

from ...models import StepId, StepResult
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
    output_format = str(settings.get("format", "WAV")).upper()
    sample_rate_text = str(settings.get("sample_rate", "48 kHz"))
    sample_rate = 24_000 if sample_rate_text.startswith("24") else 48_000
    suffix = ".m4a" if output_format == "AAC" else ".wav"
    output = Path(state.workspace_path("output", f"dubbed_audio{suffix}"))
    result = AudioTimelineService().build(
        candidate.path,
        output,
        sample_rate,
        output_format,
        progress,
    )
    return StepResult(
        step=StepId.BUILD_AUDIO,
        summary=f"Đã ghép {result['segment_count']} segment · {output_format} · {sample_rate // 1000} kHz",
        artifacts={"dubbed_audio": str(output)},
        segments=segments,
        metadata={**settings, **result, "source_sync_candidate_id": candidate.id},
    )
