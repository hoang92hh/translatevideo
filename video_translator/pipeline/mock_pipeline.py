from __future__ import annotations

from typing import Any, Callable

from ..models import StepId, StepResult
from ..state import ProjectState
from .mock_steps import HANDLERS


class MockPipeline:
    """Điều phối implementation thật hoặc mô phỏng độc lập của từng step."""

    def execute(
        self,
        step: StepId,
        state: ProjectState,
        settings: dict[str, Any],
        progress: Callable[[int, str], None] | None = None,
    ) -> StepResult:
        handler: Callable[[ProjectState, dict[str, Any]], StepResult] = HANDLERS[step]
        if progress:
            progress(0, "Đang bắt đầu xử lý…")
        if step in {StepId.EXTRACT, StepId.STT, StepId.TRANSLATE, StepId.TTS}:
            result = handler(state, settings, progress)
        else:
            result = handler(state, settings)
        if progress:
            progress(100, "Hoàn thành.")
        return result
