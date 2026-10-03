from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import AudioCandidate, STEP_ORDER, Segment, StepId, StepResult, StepStatus
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
        self.audio_candidates: dict[str, AudioCandidate] = {}
        self.selected_audio_candidate_id = ""
        self.selected_audio_stem = ""
        self.default_audio_candidate_id = ""
        self.default_audio_stem = ""
        self.results: dict[StepId, StepResult] = {}
        self.statuses = {step: StepStatus.PENDING for step in STEP_ORDER}
        self.statuses[StepId.EXTRACT] = StepStatus.READY

    def bind_project(self, project: VideoProject) -> None:
        self.project = project
        self.input_video = str(project.path(project.input_video))
        self.output_folder = str(project.path("output"))
        self.source_language = project.source_language
        self.target_language = project.target_language
        self.audio_candidates = {
            item["id"]: AudioCandidate(**item)
            for item in project.audio_candidates
            if item.get("id")
        }
        self.selected_audio_candidate_id = project.selected_audio_candidate_id
        self.selected_audio_stem = project.selected_audio_stem
        self.default_audio_candidate_id = project.default_audio_candidate_id
        self.default_audio_stem = project.default_audio_stem
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
        self.project.audio_candidates = [asdict(candidate) for candidate in self.audio_candidates.values()]
        self.project.selected_audio_candidate_id = self.selected_audio_candidate_id
        self.project.selected_audio_stem = self.selected_audio_stem
        self.project.default_audio_candidate_id = self.default_audio_candidate_id
        self.project.default_audio_stem = self.default_audio_stem
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
            "audio_candidates": [asdict(candidate) for candidate in result.audio_candidates],
        }

    @staticmethod
    def _deserialize_result(step: StepId, data: dict[str, Any]) -> StepResult:
        return StepResult(
            step=step,
            summary=data.get("summary", ""),
            artifacts=data.get("artifacts", {}),
            segments=[Segment(**item) for item in data.get("segments", [])],
            metadata=data.get("metadata", {}),
            audio_candidates=[AudioCandidate(**item) for item in data.get("audio_candidates", [])],
        )

    def add_audio_candidate(self, candidate: AudioCandidate) -> None:
        self.audio_candidates[candidate.id] = candidate

    def candidate(self, candidate_id: str) -> AudioCandidate | None:
        return self.audio_candidates.get(candidate_id)

    def audio_input_path(self, candidate_id: str = "", stem: str = "") -> str:
        candidate = self.candidate(candidate_id or self.selected_audio_candidate_id)
        if not candidate:
            return ""
        return candidate.stem_path(stem or self.selected_audio_stem)

    def select_audio_input(self, candidate_id: str, stem: str) -> None:
        if candidate_id == self.selected_audio_candidate_id and stem == self.selected_audio_stem:
            return
        candidate = self.candidate(candidate_id)
        if not candidate or stem not in candidate.stems:
            return
        self.selected_audio_candidate_id = candidate_id
        self.selected_audio_stem = stem
        self.invalidate_from(StepId.STT)
        if self.statuses[StepId.EXTRACT] == StepStatus.DONE:
            self.statuses[StepId.STT] = StepStatus.READY
            self.step_changed.emit(StepId.STT.value)
        self.project_changed.emit()
        self.save_project()

    def set_default_audio_input(self, candidate_id: str, stem: str) -> None:
        candidate = self.candidate(candidate_id)
        if not candidate or stem not in candidate.stems:
            return
        self.default_audio_candidate_id = candidate_id
        self.default_audio_stem = stem
        self.project_changed.emit()
        self.step_changed.emit(StepId.EXTRACT.value)
        self.save_project()

    def remove_audio_candidate(self, candidate_id: str) -> None:
        if candidate_id == "original-mix":
            return
        removed_selected = candidate_id == self.selected_audio_candidate_id
        self.audio_candidates.pop(candidate_id, None)
        if removed_selected:
            self.selected_audio_candidate_id = ""
            self.selected_audio_stem = ""
            self.invalidate_from(StepId.STT)
        if candidate_id == self.default_audio_candidate_id:
            self.default_audio_candidate_id = ""
            self.default_audio_stem = ""
        self.step_changed.emit(StepId.EXTRACT.value)
        self.project_changed.emit()
        self.save_project()

    def valid_default_audio_input(self) -> tuple[str, str] | None:
        choices = (
            (self.default_audio_candidate_id, self.default_audio_stem),
            (self.selected_audio_candidate_id, self.selected_audio_stem),
        )
        for candidate_id, stem in choices:
            path = self.audio_input_path(candidate_id, stem)
            if path and Path(path).is_file():
                return candidate_id, stem
        return None

    def activate_audio_candidate(self, candidate_id: str, stem: str) -> None:
        candidate = self.candidate(candidate_id)
        if not candidate or not Path(candidate.stem_path(stem)).is_file():
            return
        self.selected_audio_candidate_id = candidate_id
        self.selected_audio_stem = stem
        self.invalidate_from(StepId.STT)
        self.statuses[StepId.EXTRACT] = StepStatus.DONE
        self.statuses[StepId.STT] = StepStatus.READY
        self.step_changed.emit(StepId.EXTRACT.value)
        self.step_changed.emit(StepId.STT.value)
        self.project_changed.emit()
        self.save_project()

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
        self.invalidate_from(step)
        self.statuses[step] = StepStatus.RUNNING
        self.step_changed.emit(step.value)
        self.save_project()

    def mark_error(self, step: StepId) -> None:
        self.statuses[step] = StepStatus.ERROR
        self.step_changed.emit(step.value)

    def set_result(self, result: StepResult) -> None:
        index = STEP_ORDER.index(result.step)
        for candidate in result.audio_candidates:
            self.add_audio_candidate(candidate)
        recommended_id = str(result.metadata.get("recommended_candidate_id", ""))
        recommended_stem = str(result.metadata.get("recommended_stem", ""))
        if recommended_id and recommended_stem:
            self.selected_audio_candidate_id = recommended_id
            self.selected_audio_stem = recommended_stem
            if not self.default_audio_candidate_id:
                self.default_audio_candidate_id = recommended_id
                self.default_audio_stem = recommended_stem
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
        previous_done = self.statuses[STEP_ORDER[index - 1]] == StepStatus.DONE
        if step == StepId.STT:
            input_path = self.audio_input_path()
            return previous_done and bool(input_path) and Path(input_path).is_file()
        return previous_done

    def previous_result(self, step: StepId) -> StepResult | None:
        index = STEP_ORDER.index(step)
        if index == 0:
            return None
        return self.results.get(STEP_ORDER[index - 1])

    def input_summary(self, step: StepId) -> str:
        if step == StepId.EXTRACT:
            return self.input_video or "Chưa chọn video đầu vào"
        if step == StepId.STT:
            path = self.audio_input_path()
            return path or "Chưa chọn output âm thanh từ Step 1"
        previous = self.previous_result(step)
        if not previous:
            return "Đang chờ kết quả từ step trước"
        artifacts = " · ".join(previous.artifacts.values())
        segment_info = f"{len(previous.segments)} segments" if previous.segments else ""
        details = " · ".join(part for part in (artifacts, segment_info) if part)
        return details or previous.summary
