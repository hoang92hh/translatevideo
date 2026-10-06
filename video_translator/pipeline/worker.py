from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from ..errors import error_payload
from ..models import StepId
from ..state import ProjectState
from ..services.audio_sync_service import (
    borrow_neighbor_time,
    repair_sync_segments,
    rewrite_sync_drafts,
)
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


class AudioSyncAiRewriteWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(
        self,
        manifest_path: str,
        selected_texts: dict[int, str],
        model_name: str,
        source_language: str,
        target_language: str,
    ) -> None:
        super().__init__()
        self.manifest_path = manifest_path
        self.selected_texts = selected_texts
        self.model_name = model_name
        self.source_language = source_language
        self.target_language = target_language

    def run(self) -> None:
        try:
            result = rewrite_sync_drafts(
                self.manifest_path,
                self.selected_texts,
                self.model_name,
                self.source_language,
                self.target_language,
                self.progress_changed.emit,
            )
        except Exception as exc:
            self.failed.emit(error_payload(exc))
            return
        self.succeeded.emit(result)


class AudioSyncBatchRepairWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(
        self,
        sync_manifest_path: str,
        tts_manifest_path: str,
        translation_manifest_path: str,
        edited_texts: dict[int, str],
        target_language: str,
    ) -> None:
        super().__init__()
        self.sync_manifest_path = sync_manifest_path
        self.tts_manifest_path = tts_manifest_path
        self.translation_manifest_path = translation_manifest_path
        self.edited_texts = edited_texts
        self.target_language = target_language

    def run(self) -> None:
        try:
            result = repair_sync_segments(
                self.sync_manifest_path,
                self.tts_manifest_path,
                self.translation_manifest_path,
                self.edited_texts,
                self.target_language,
                self.progress_changed.emit,
            )
        except Exception as exc:
            self.failed.emit(error_payload(exc))
            return
        self.succeeded.emit(result)


class AudioSyncNeighborBorrowWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(self, manifest_path: str) -> None:
        super().__init__()
        self.manifest_path = manifest_path

    def run(self) -> None:
        try:
            result = borrow_neighbor_time(
                self.manifest_path,
                self.progress_changed.emit,
            )
        except Exception as exc:
            self.failed.emit(error_payload(exc))
            return
        self.succeeded.emit(result)
