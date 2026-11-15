from __future__ import annotations

from pathlib import Path

from video_translator.common.models import AudioCandidate, StepId, StepStatus

class AudioStateMixin:
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
        candidate = self.candidate(candidate_id)
        if not candidate or stem not in candidate.stems:
            return
        selection_changed = (
            candidate_id != self.selected_audio_candidate_id
            or stem != self.selected_audio_stem
        )
        self.selected_audio_candidate_id = candidate_id
        self.selected_audio_stem = stem
        self.default_audio_candidate_id = candidate_id
        self.default_audio_stem = stem
        if not selection_changed:
            self.project_changed.emit()
            self.save_project()
            return
        self.invalidate_from(StepId.STT)
        if self.statuses[StepId.EXTRACT] == StepStatus.DONE:
            self.statuses[StepId.STT] = StepStatus.READY
            self.step_changed.emit(StepId.STT.value)
        self.project_changed.emit()
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
            (self.selected_audio_candidate_id, self.selected_audio_stem),
            (self.default_audio_candidate_id, self.default_audio_stem),
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
        self.default_audio_candidate_id = candidate_id
        self.default_audio_stem = stem
        self.invalidate_from(StepId.STT)
        self.statuses[StepId.EXTRACT] = StepStatus.DONE
        self.statuses[StepId.STT] = StepStatus.READY
        self.step_changed.emit(StepId.EXTRACT.value)
        self.step_changed.emit(StepId.STT.value)
        self.project_changed.emit()
        self.save_project()

