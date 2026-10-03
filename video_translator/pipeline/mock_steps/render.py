from __future__ import annotations

from typing import Any

from ...models import StepId, StepResult
from ...state import ProjectState
from .common import previous_segments


def execute(state: ProjectState, settings: dict[str, Any]) -> StepResult:
    segments = previous_segments(state, StepId.RENDER)
    return StepResult(
        step=StepId.RENDER,
        summary="Đã mô phỏng render video hoàn chỉnh.",
        artifacts={
            "output_video": state.workspace_path("output", "output_translated.mp4"),
            "subtitle": state.workspace_path("subtitles", "translated.srt"),
        },
        segments=segments,
        metadata=settings,
    )

