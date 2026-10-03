from __future__ import annotations

from typing import Any

from ...models import StepId, StepResult
from ...state import ProjectState
from .common import previous_segments


def execute(state: ProjectState, settings: dict[str, Any]) -> StepResult:
    segments = previous_segments(state, StepId.SYNC)
    for segment in segments:
        segment.synced_audio_file = state.workspace_path("synchronized_audio", f"segment_{segment.id:04d}.wav")
    return StepResult(
        step=StepId.SYNC,
        summary="Đã mô phỏng đồng bộ thời lượng các audio segment.",
        artifacts={"synced_folder": state.workspace_path("synchronized_audio")},
        segments=segments,
        metadata=settings,
    )

