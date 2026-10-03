from __future__ import annotations

from pathlib import Path
from typing import Any

from ...models import StepId, StepResult
from ...state import ProjectState


def execute(state: ProjectState, settings: dict[str, Any]) -> StepResult:
    stem = Path(state.input_video).stem or "input"
    return StepResult(
        step=StepId.EXTRACT,
        summary="Đã mô phỏng tách audio bằng FFmpeg.",
        artifacts={"source_audio": state.workspace_path("extracted", f"{stem}_source_audio.wav")},
        metadata={"provider": "FFmpeg", **settings},
    )

