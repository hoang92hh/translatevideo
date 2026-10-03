from __future__ import annotations

from typing import Any

from ...models import StepId, StepResult
from ...state import ProjectState
from .common import previous_segments


def execute(state: ProjectState, settings: dict[str, Any]) -> StepResult:
    segments = previous_segments(state, StepId.BUILD_AUDIO)
    return StepResult(
        step=StepId.BUILD_AUDIO,
        summary="Đã mô phỏng ghép audio theo timestamp.",
        artifacts={"dubbed_audio": state.workspace_path("temp", "dubbed_audio.wav")},
        segments=segments,
        metadata=settings,
    )

