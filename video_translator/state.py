from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import STEP_ORDER, Segment, StepId, StepResult, StepStatus
from .project import VideoProject


class ProjectState(QObject):
    step_changed = Signal(str)
    project_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.input_video = ""
        self.output_folder = "output"
        self.source_language = "Chinese"
        self.target_language = "Vietnamese"
        self.project: VideoProject | None = None
        self.results: dict[StepId, StepResult] = {}
        self.statuses = {step: StepStatus.PENDING for step in STEP_ORDER}
        self.statuses[StepId.EXTRACT] = StepStatus.READY

    def bind_project(self, project: VideoProject) -> None:
        self.project = project
        self.input_video = str(project.path(project.input_video))
        self.output_folder = str(project.path("output"))
        self.source_language = project.source_language
        self.target_language = project.target_language
        self.results.clear()
        self.statuses = {step: StepStatus.PENDING for step in STEP_ORDER}
        self.statuses[StepId.EXTRACT] = StepStatus.READY
        for step in STEP_ORDER:
            stored = project.pipeline.get(step.value)
            if not stored:
                break
            self.results[step] = self._deserialize_result(step, stored)
            self.statuses[step] = StepStatus.DONE
        completed = len(self.results)
        if completed < len(STEP_ORDER):
            self.statuses[STEP_ORDER[completed]] = StepStatus.READY
        for step in STEP_ORDER:
            self.step_changed.emit(step.value)
        self.project_changed.emit()

    def workspace_path(self, *parts: str) -> str:
        if self.project:
            return str(self.project.path(*parts))
        return str(Path(*parts))

    def save_project(self) -> None:
        if not self.project:
            return
        self.project.source_language = self.source_language
        self.project.target_language = self.target_language
        self.project.pipeline = {
            step.value: self._serialize_result(result)
            for step, result in self.results.items()
        }
        self.project.save()

    @staticmethod
    def _serialize_result(result: StepResult) -> dict[str, Any]:
        return {
            "summary": result.summary,
            "artifacts": result.artifacts,
            "segments": [asdict(segment) for segment in result.segments],
            "metadata": result.metadata,
        }

    @staticmethod
    def _deserialize_result(step: StepId, data: dict[str, Any]) -> StepResult:
        return StepResult(
            step=step,
            summary=data.get("summary", ""),
            artifacts=data.get("artifacts", {}),
            segments=[Segment(**item) for item in data.get("segments", [])],
            metadata=data.get("metadata", {}),
        )

    def set_input_video(self, path: str) -> None:
        if path == self.input_video:
            return
        self.input_video = path
        self.invalidate_from(StepId.EXTRACT)
        self.statuses[StepId.EXTRACT] = StepStatus.READY
        self.step_changed.emit(StepId.EXTRACT.value)
        self.project_changed.emit()

    def set_languages(self, source: str, target: str) -> None:
        changed = source != self.source_language or target != self.target_language
        self.source_language = source
        self.target_language = target
        if changed:
            self.invalidate_from(StepId.STT)
            self.project_changed.emit()

    def mark_running(self, step: StepId) -> None:
        self.statuses[step] = StepStatus.RUNNING
        self.step_changed.emit(step.value)

    def mark_error(self, step: StepId) -> None:
        self.statuses[step] = StepStatus.ERROR
        self.step_changed.emit(step.value)

    def set_result(self, result: StepResult) -> None:
        index = STEP_ORDER.index(result.step)
        self.results[result.step] = result
        self.statuses[result.step] = StepStatus.DONE
        for later in STEP_ORDER[index + 1 :]:
            if later in self.results:
                del self.results[later]
            self.statuses[later] = StepStatus.PENDING
            self.step_changed.emit(later.value)
        if index + 1 < len(STEP_ORDER):
            self.statuses[STEP_ORDER[index + 1]] = StepStatus.READY
            self.step_changed.emit(STEP_ORDER[index + 1].value)
        self.step_changed.emit(result.step.value)
        self.project_changed.emit()
        self.save_project()

    def invalidate_from(self, step: StepId) -> None:
        start = STEP_ORDER.index(step)
        for current in STEP_ORDER[start:]:
            had_output = current in self.results
            self.results.pop(current, None)
            self.statuses[current] = StepStatus.STALE if had_output else StepStatus.PENDING
            self.step_changed.emit(current.value)

    def can_run(self, step: StepId) -> bool:
        index = STEP_ORDER.index(step)
        if index == 0:
            return bool(self.input_video)
        return self.statuses[STEP_ORDER[index - 1]] == StepStatus.DONE

    def previous_result(self, step: StepId) -> StepResult | None:
        index = STEP_ORDER.index(step)
        if index == 0:
            return None
        return self.results.get(STEP_ORDER[index - 1])

    def input_summary(self, step: StepId) -> str:
        if step == StepId.EXTRACT:
            return self.input_video or "Chưa chọn video đầu vào"
        previous = self.previous_result(step)
        if not previous:
            return "Đang chờ kết quả từ step trước"
        artifacts = " · ".join(previous.artifacts.values())
        segment_info = f"{len(previous.segments)} segments" if previous.segments else ""
        details = " · ".join(part for part in (artifacts, segment_info) if part)
        return details or previous.summary
