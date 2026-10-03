from __future__ import annotations

from typing import Any

from ...models import Segment, StepId, StepResult
from ...state import ProjectState


def execute(state: ProjectState, settings: dict[str, Any]) -> StepResult:
    segments = [
        Segment(1, 1.20, 4.50, "大家好"),
        Segment(2, 4.50, 8.70, "今天我们讨论这个问题"),
        Segment(3, 9.10, 12.40, "谢谢大家的关注"),
    ]
    return StepResult(
        step=StepId.STT,
        summary="Đã mô phỏng nhận dạng giọng nói với timestamp.",
        artifacts={"transcript": state.workspace_path("transcripts", "transcript.json")},
        segments=segments,
        metadata=settings,
    )

