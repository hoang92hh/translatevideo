from __future__ import annotations

from copy import deepcopy

from ...models import Segment, StepId
from ...state import ProjectState


def previous_segments(state: ProjectState, step: StepId) -> list[Segment]:
    previous = state.previous_result(step)
    if previous is None:
        raise RuntimeError("Step trước chưa có kết quả.")
    return deepcopy(previous.segments)

