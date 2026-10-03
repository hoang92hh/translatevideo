from __future__ import annotations

from typing import Any

from ...models import StepId, StepResult
from ...state import ProjectState
from .common import previous_segments


def execute(state: ProjectState, settings: dict[str, Any]) -> StepResult:
    segments = previous_segments(state, StepId.TTS)
    for segment in segments:
        segment.audio_file = state.workspace_path("generated_audio", f"segment_{segment.id:04d}.wav")
    return StepResult(
        step=StepId.TTS,
        summary="Đã mô phỏng tạo giọng nói cho từng segment.",
        artifacts={"audio_folder": state.workspace_path("generated_audio")},
        segments=segments,
        metadata=settings,
    )

