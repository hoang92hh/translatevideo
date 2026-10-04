from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from ..errors import error_payload
from ..models import StepId
from ..state import ProjectState
from ..services.audio_sync_service import repair_sync_segment
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


class AudioSyncRepairWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(
        self,
        manifest_path: str,
        segment_id: int,
        translated_text: str,
        target_language: str,
    ) -> None:
        super().__init__()
        self.manifest_path = manifest_path
        self.segment_id = segment_id
        self.translated_text = translated_text
        self.target_language = target_language

    def run(self) -> None:
        try:
            result = repair_sync_segment(
                self.manifest_path,
                self.segment_id,
                self.translated_text,
                self.target_language,
                self.progress_changed.emit,
            )
        except Exception as exc:
            self.failed.emit(error_payload(exc))
            return
        self.succeeded.emit(result)
