from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from ..errors import error_payload
from ..models import StepId
from ..state import ProjectState
from .mock_pipeline import MockPipeline


class PipelineWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(
        self,
        pipeline: MockPipeline,
        step: StepId,
        state: ProjectState,
        settings: dict[str, Any],
    ) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.step = step
        self.state = state
        self.settings = settings

    def run(self) -> None:
        try:
            result = self.pipeline.execute(
                self.step,
                self.state,
                self.settings,
                self.progress_changed.emit,
            )
        except Exception as exc:
            self.failed.emit(error_payload(exc))
            return
        self.succeeded.emit(result)
