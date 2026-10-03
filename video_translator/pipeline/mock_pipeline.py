from __future__ import annotations

from typing import Any, Callable

from ..models import StepId, StepResult
from ..state import ProjectState
from .mock_steps import HANDLERS


class MockPipeline:
    """Điều phối các implementation mô phỏng độc lập của từng step."""

    def execute(self, step: StepId, state: ProjectState, settings: dict[str, Any]) -> StepResult:
        handler: Callable[[ProjectState, dict[str, Any]], StepResult] = HANDLERS[step]
        return handler(state, settings)

