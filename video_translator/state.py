from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import (
    AudioCandidate,
    STEP_ORDER,
    Segment,
    StepId,
    StepResult,
    StepStatus,
    TranscriptCandidate,
)
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
        self.transcript_candidates: dict[str, TranscriptCandidate] = {}
        self.selected_transcript_candidate_id = ""
        self.default_transcript_candidate_id = ""
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
        self.transcript_candidates = {
            item["id"]: TranscriptCandidate(**item)
            for item in project.transcript_candidates
            if item.get("id")
        }
        self.selected_transcript_candidate_id = project.selected_transcript_candidate_id
        self.default_transcript_candidate_id = project.default_transcript_candidate_id
        self.results.clear()
        self.statuses = {step: StepStatus.PENDING for step in STEP_ORDER}
        self.statuses[StepId.EXTRACT] = StepStatus.READY
        for step in STEP_ORDER:
            stored = project.pipeline.get(step.value)
            if not stored:
                break
            self.results[step] = self._deserialize_result(step, stored)
            self.statuses[step] = StepStatus.DONE
        self._register_legacy_transcript()
        self._restore_selected_transcript()
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
        self.project.transcript_candidates = [
            asdict(candidate) for candidate in self.transcript_candidates.values()
        ]
        self.project.selected_transcript_candidate_id = self.selected_transcript_candidate_id
        self.project.default_transcript_candidate_id = self.default_transcript_candidate_id
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
            "transcript_candidates": [
                asdict(candidate) for candidate in result.transcript_candidates
            ],
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
            transcript_candidates=[
                TranscriptCandidate(**item) for item in data.get("transcript_candidates", [])
            ],
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

    def transcript_candidate(self, candidate_id: str) -> TranscriptCandidate | None:
        return self.transcript_candidates.get(candidate_id)

    def select_transcript_candidate(self, candidate_id: str) -> bool:
        result = self._result_from_transcript(candidate_id)
        if result is None:
            return False
        self.selected_transcript_candidate_id = candidate_id
        self.results[StepId.STT] = result
        self.statuses[StepId.STT] = StepStatus.DONE
        self.invalidate_from(StepId.TRANSLATE)
        self.statuses[StepId.TRANSLATE] = StepStatus.READY
        self.step_changed.emit(StepId.STT.value)
        self.step_changed.emit(StepId.TRANSLATE.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def set_default_transcript_candidate(self, candidate_id: str) -> bool:
        candidate = self.transcript_candidate(candidate_id)
        if not candidate or not Path(candidate.path).is_file():
            return False
        self.default_transcript_candidate_id = candidate_id
        self.step_changed.emit(StepId.STT.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def remove_transcript_candidate(self, candidate_id: str) -> None:
        removed_selected = candidate_id == self.selected_transcript_candidate_id
        removed_default = candidate_id == self.default_transcript_candidate_id
        self.transcript_candidates.pop(candidate_id, None)
        fallback = self._latest_valid_transcript_candidate()
        if removed_default:
            self.default_transcript_candidate_id = fallback.id if fallback else ""
        if removed_selected:
            self.selected_transcript_candidate_id = ""
            if fallback and self.select_transcript_candidate(fallback.id):
                return
            self.invalidate_from(StepId.STT)
            if self.statuses[StepId.EXTRACT] == StepStatus.DONE:
                self.statuses[StepId.STT] = StepStatus.READY
        self.step_changed.emit(StepId.STT.value)
        self.project_changed.emit()
        self.save_project()

    def _register_legacy_transcript(self) -> None:
        if self.transcript_candidates:
            return
        result = self.results.get(StepId.STT)
        if not result:
            return
        path = Path(result.artifacts.get("transcript", ""))
        if not path.is_file():
            return
        created_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        candidate_id = "stt-legacy"
        candidate = TranscriptCandidate(
            id=candidate_id,
            label=f"Transcript cũ · {path.stem}",
            created_at=created_at,
            path=str(path),
            segment_count=len(result.segments),
            summary=result.summary,
            metadata={**result.metadata, "transcript_candidate_id": candidate_id},
        )
        self.transcript_candidates[candidate_id] = candidate
        self.selected_transcript_candidate_id = candidate_id
        self.default_transcript_candidate_id = candidate_id
        result.metadata["transcript_candidate_id"] = candidate_id

    def _restore_selected_transcript(self) -> None:
        if StepId.EXTRACT not in self.results:
            return
        choices = (
            self.selected_transcript_candidate_id,
            self.default_transcript_candidate_id,
        )
        candidate = next(
            (
                self.transcript_candidate(candidate_id)
                for candidate_id in choices
                if candidate_id
                and self.transcript_candidate(candidate_id)
                and Path(self.transcript_candidate(candidate_id).path).is_file()
            ),
            None,
        )
        candidate = candidate or self._latest_valid_transcript_candidate()
        if not candidate:
            return
        self.selected_transcript_candidate_id = candidate.id
        default = self.transcript_candidate(self.default_transcript_candidate_id)
        if not default or not Path(default.path).is_file():
            self.default_transcript_candidate_id = candidate.id
        current = self.results.get(StepId.STT)
        current_path = current.artifacts.get("transcript", "") if current else ""
        if current and current_path and Path(current_path) == Path(candidate.path):
            current.metadata["transcript_candidate_id"] = candidate.id
            return
        result = self._result_from_transcript(candidate.id)
        if result:
            self.results[StepId.STT] = result
            self.statuses[StepId.STT] = StepStatus.DONE
            for later in STEP_ORDER[STEP_ORDER.index(StepId.TRANSLATE) :]:
                self.results.pop(later, None)
                self.statuses[later] = StepStatus.PENDING

    def _latest_valid_transcript_candidate(self) -> TranscriptCandidate | None:
        valid = [
            candidate
            for candidate in self.transcript_candidates.values()
            if Path(candidate.path).is_file()
        ]
        return max(valid, key=lambda item: item.created_at, default=None)

    def _result_from_transcript(self, candidate_id: str) -> StepResult | None:
        candidate = self.transcript_candidate(candidate_id)
        if not candidate:
            return None
        path = Path(candidate.path)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            segments = [
                Segment(
                    id=int(item.get("id", index)),
                    start=float(item.get("start", 0.0)),
                    end=float(item.get("end", 0.0)),
                    source_text=str(item.get("text", item.get("source_text", ""))),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                    synced_audio_file=str(item.get("synced_audio_file", "")),
                )
                for index, item in enumerate(payload.get("segments", []), start=1)
            ]
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        metadata = {**candidate.metadata, **{key: value for key, value in payload.items() if key != "segments"}}
        metadata["transcript_candidate_id"] = candidate.id
        summary = candidate.summary or f"Transcript {candidate.label} · {len(segments)} segment"
        return StepResult(
            step=StepId.STT,
            summary=summary,
            artifacts={"transcript": str(path)},
            segments=segments,
            metadata=metadata,
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
        self.invalidate_from(step)
        self.statuses[step] = StepStatus.RUNNING
        self.step_changed.emit(step.value)
        self.save_project()

    def mark_error(self, step: StepId) -> None:
        if step == StepId.STT and self.default_transcript_candidate_id:
            if self.select_transcript_candidate(self.default_transcript_candidate_id):
                return
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
            self.default_audio_candidate_id = recommended_id
            self.default_audio_stem = recommended_stem
        for candidate in result.transcript_candidates:
            self.transcript_candidates[candidate.id] = candidate
        transcript_id = str(result.metadata.get("recommended_transcript_candidate_id", ""))
        if transcript_id and transcript_id in self.transcript_candidates:
            self.selected_transcript_candidate_id = transcript_id
            self.default_transcript_candidate_id = transcript_id
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
