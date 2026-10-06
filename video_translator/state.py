from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import (
    AudioCandidate,
    BuildAudioCandidate,
    STEP_ORDER,
    Segment,
    StepId,
    StepResult,
    StepStatus,
    SyncCandidate,
    TranscriptCandidate,
    TranslationCandidate,
    TtsCandidate,
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
        self.translation_candidates: dict[str, TranslationCandidate] = {}
        self.selected_translation_candidate_id = ""
        self.default_translation_candidate_id = ""
        self.tts_candidates: dict[str, TtsCandidate] = {}
        self.selected_tts_candidate_id = ""
        self.default_tts_candidate_id = ""
        self.sync_candidates: dict[str, SyncCandidate] = {}
        self.selected_sync_candidate_id = ""
        self.default_sync_candidate_id = ""
        self.build_audio_candidates: dict[str, BuildAudioCandidate] = {}
        self.selected_build_audio_candidate_id = ""
        self.default_build_audio_candidate_id = ""
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
        selected_audio = self.audio_candidates.get(self.selected_audio_candidate_id)
        default_audio = self.audio_candidates.get(self.default_audio_candidate_id)
        if (
            selected_audio
            and self.selected_audio_stem in selected_audio.stems
            and Path(selected_audio.stem_path(self.selected_audio_stem)).is_file()
        ):
            self.default_audio_candidate_id = self.selected_audio_candidate_id
            self.default_audio_stem = self.selected_audio_stem
        elif (
            default_audio
            and self.default_audio_stem in default_audio.stems
            and Path(default_audio.stem_path(self.default_audio_stem)).is_file()
        ):
            self.selected_audio_candidate_id = self.default_audio_candidate_id
            self.selected_audio_stem = self.default_audio_stem
        self.transcript_candidates = {
            item["id"]: TranscriptCandidate(**item)
            for item in project.transcript_candidates
            if item.get("id")
        }
        self.selected_transcript_candidate_id = project.selected_transcript_candidate_id
        self.default_transcript_candidate_id = project.default_transcript_candidate_id
        self.translation_candidates = {
            item["id"]: TranslationCandidate(**item)
            for item in project.translation_candidates
            if item.get("id")
        }
        self.selected_translation_candidate_id = project.selected_translation_candidate_id
        self.default_translation_candidate_id = project.default_translation_candidate_id
        self.tts_candidates = {
            item["id"]: TtsCandidate(**item)
            for item in project.tts_candidates
            if item.get("id")
        }
        self.selected_tts_candidate_id = project.selected_tts_candidate_id
        self.default_tts_candidate_id = project.default_tts_candidate_id
        self.sync_candidates = {
            item["id"]: SyncCandidate(**item)
            for item in project.sync_candidates
            if item.get("id")
        }
        self.selected_sync_candidate_id = project.selected_sync_candidate_id
        self.default_sync_candidate_id = project.default_sync_candidate_id
        self.build_audio_candidates = {
            item["id"]: BuildAudioCandidate(**item)
            for item in project.build_audio_candidates
            if item.get("id")
        }
        self.selected_build_audio_candidate_id = project.selected_build_audio_candidate_id
        self.default_build_audio_candidate_id = project.default_build_audio_candidate_id
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
        self._register_legacy_translation()
        self._restore_selected_translation()
        self._register_legacy_tts()
        self._restore_selected_tts()
        self._restore_selected_sync()
        self._restore_selected_build_audio()
        for index, step in enumerate(STEP_ORDER):
            if self.statuses[step] == StepStatus.DONE:
                continue
            if self.statuses[step] == StepStatus.PENDING and (
                index == 0 or self.statuses[STEP_ORDER[index - 1]] == StepStatus.DONE
            ):
                self.statuses[step] = StepStatus.READY
            break
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
        self.project.translation_candidates = [
            asdict(candidate) for candidate in self.translation_candidates.values()
        ]
        self.project.selected_translation_candidate_id = self.selected_translation_candidate_id
        self.project.default_translation_candidate_id = self.default_translation_candidate_id
        self.project.tts_candidates = [asdict(candidate) for candidate in self.tts_candidates.values()]
        self.project.selected_tts_candidate_id = self.selected_tts_candidate_id
        self.project.default_tts_candidate_id = self.default_tts_candidate_id
        self.project.sync_candidates = [asdict(candidate) for candidate in self.sync_candidates.values()]
        self.project.selected_sync_candidate_id = self.selected_sync_candidate_id
        self.project.default_sync_candidate_id = self.default_sync_candidate_id
        self.project.build_audio_candidates = [
            asdict(candidate) for candidate in self.build_audio_candidates.values()
        ]
        self.project.selected_build_audio_candidate_id = self.selected_build_audio_candidate_id
        self.project.default_build_audio_candidate_id = self.default_build_audio_candidate_id
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
            "translation_candidates": [
                asdict(candidate) for candidate in result.translation_candidates
            ],
            "tts_candidates": [asdict(candidate) for candidate in result.tts_candidates],
            "sync_candidates": [asdict(candidate) for candidate in result.sync_candidates],
            "build_audio_candidates": [
                asdict(candidate) for candidate in result.build_audio_candidates
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
            translation_candidates=[
                TranslationCandidate(**item) for item in data.get("translation_candidates", [])
            ],
            tts_candidates=[TtsCandidate(**item) for item in data.get("tts_candidates", [])],
            sync_candidates=[SyncCandidate(**item) for item in data.get("sync_candidates", [])],
            build_audio_candidates=[
                BuildAudioCandidate(**item) for item in data.get("build_audio_candidates", [])
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

    def transcript_candidate(self, candidate_id: str) -> TranscriptCandidate | None:
        return self.transcript_candidates.get(candidate_id)

    def select_transcript_candidate(self, candidate_id: str) -> bool:
        result = self._result_from_transcript(candidate_id)
        if result is None:
            return False
        current = self.results.get(StepId.STT)
        current_path = current.artifacts.get("transcript", "") if current else ""
        selection_changed = (
            candidate_id != self.selected_transcript_candidate_id
            or not current_path
            or Path(current_path) != Path(result.artifacts["transcript"])
        )
        self.selected_transcript_candidate_id = candidate_id
        self.default_transcript_candidate_id = candidate_id
        if not selection_changed:
            self.project_changed.emit()
            self.save_project()
            return True
        self.results[StepId.STT] = result
        self.statuses[StepId.STT] = StepStatus.DONE
        self.invalidate_from(StepId.TRANSLATE)
        self.statuses[StepId.TRANSLATE] = StepStatus.READY
        self.step_changed.emit(StepId.STT.value)
        self.step_changed.emit(StepId.TRANSLATE.value)
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

    def translation_candidate(self, candidate_id: str) -> TranslationCandidate | None:
        return self.translation_candidates.get(candidate_id)

    def select_translation_candidate(self, candidate_id: str) -> bool:
        result = self._result_from_translation(candidate_id)
        if result is None:
            return False
        current = self.results.get(StepId.TRANSLATE)
        current_path = current.artifacts.get("translation", "") if current else ""
        selection_changed = (
            candidate_id != self.selected_translation_candidate_id
            or not current_path
            or Path(current_path) != Path(result.artifacts["translation"])
        )
        self.selected_translation_candidate_id = candidate_id
        self.default_translation_candidate_id = candidate_id
        if not selection_changed:
            self.project_changed.emit()
            self.save_project()
            return True
        self.results[StepId.TRANSLATE] = result
        self.statuses[StepId.TRANSLATE] = StepStatus.DONE
        self.invalidate_from(StepId.TTS)
        self.statuses[StepId.TTS] = StepStatus.READY
        self.step_changed.emit(StepId.TRANSLATE.value)
        self.step_changed.emit(StepId.TTS.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def remove_translation_candidate(self, candidate_id: str) -> None:
        removed_selected = candidate_id == self.selected_translation_candidate_id
        removed_default = candidate_id == self.default_translation_candidate_id
        self.translation_candidates.pop(candidate_id, None)
        fallback = self._latest_valid_translation_candidate()
        if removed_default:
            self.default_translation_candidate_id = fallback.id if fallback else ""
        if removed_selected:
            self.selected_translation_candidate_id = ""
            if fallback and self.select_translation_candidate(fallback.id):
                return
            self.invalidate_from(StepId.TRANSLATE)
            if self.statuses[StepId.STT] == StepStatus.DONE:
                self.statuses[StepId.TRANSLATE] = StepStatus.READY
        self.step_changed.emit(StepId.TRANSLATE.value)
        self.project_changed.emit()
        self.save_project()

    def _register_legacy_translation(self) -> None:
        if self.translation_candidates:
            return
        result = self.results.get(StepId.TRANSLATE)
        if not result:
            return
        path = Path(result.artifacts.get("translation", ""))
        if not path.is_file():
            self.invalidate_from(StepId.TRANSLATE)
            if self.statuses[StepId.STT] == StepStatus.DONE:
                self.statuses[StepId.TRANSLATE] = StepStatus.READY
            return
        created_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        candidate_id = "translate-legacy"
        candidate = TranslationCandidate(
            id=candidate_id,
            label=f"Bản dịch cũ · {path.stem}",
            created_at=created_at,
            path=str(path),
            segment_count=len(result.segments),
            summary=result.summary,
            metadata={**result.metadata, "translation_candidate_id": candidate_id},
        )
        self.translation_candidates[candidate_id] = candidate
        self.selected_translation_candidate_id = candidate_id
        self.default_translation_candidate_id = candidate_id
        result.metadata["translation_candidate_id"] = candidate_id

    def _restore_selected_translation(self) -> None:
        if StepId.STT not in self.results:
            return
        choices = (
            self.selected_translation_candidate_id,
            self.default_translation_candidate_id,
        )
        candidate = next(
            (
                self.translation_candidate(candidate_id)
                for candidate_id in choices
                if candidate_id
                and self.translation_candidate(candidate_id)
                and Path(self.translation_candidate(candidate_id).path).is_file()
            ),
            None,
        )
        candidate = candidate or self._latest_valid_translation_candidate()
        if not candidate:
            return
        self.selected_translation_candidate_id = candidate.id
        self.default_translation_candidate_id = candidate.id
        current = self.results.get(StepId.TRANSLATE)
        current_path = current.artifacts.get("translation", "") if current else ""
        if current and current_path and Path(current_path) == Path(candidate.path):
            current.metadata["translation_candidate_id"] = candidate.id
            return
        result = self._result_from_translation(candidate.id)
        if result:
            self.results[StepId.TRANSLATE] = result
            self.statuses[StepId.TRANSLATE] = StepStatus.DONE
            for later in STEP_ORDER[STEP_ORDER.index(StepId.TTS) :]:
                self.results.pop(later, None)
                self.statuses[later] = StepStatus.PENDING

    def _latest_valid_translation_candidate(self) -> TranslationCandidate | None:
        valid = [
            candidate
            for candidate in self.translation_candidates.values()
            if Path(candidate.path).is_file()
        ]
        return max(valid, key=lambda item: item.created_at, default=None)

    def _result_from_translation(self, candidate_id: str) -> StepResult | None:
        candidate = self.translation_candidate(candidate_id)
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
                    source_text=str(item.get("source_text", "")),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                    synced_audio_file=str(item.get("synced_audio_file", "")),
                )
                for index, item in enumerate(payload.get("segments", []), start=1)
            ]
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not segments or any(not segment.translated_text.strip() for segment in segments):
            return None
        metadata = {
            **candidate.metadata,
            **{key: value for key, value in payload.items() if key != "segments"},
            "translation_candidate_id": candidate.id,
        }
        summary = candidate.summary or f"Bản dịch {candidate.label} · {len(segments)} segment"
        return StepResult(
            step=StepId.TRANSLATE,
            summary=summary,
            artifacts={"translation": str(path)},
            segments=segments,
            metadata=metadata,
        )

    def tts_candidate(self, candidate_id: str) -> TtsCandidate | None:
        return self.tts_candidates.get(candidate_id)

    def select_tts_candidate(self, candidate_id: str) -> bool:
        result = self._result_from_tts(candidate_id)
        if result is None:
            return False
        self.selected_tts_candidate_id = candidate_id
        self.default_tts_candidate_id = candidate_id
        self.results[StepId.TTS] = result
        self.statuses[StepId.TTS] = StepStatus.DONE
        self.invalidate_from(StepId.SYNC)
        self.statuses[StepId.SYNC] = StepStatus.READY
        self.step_changed.emit(StepId.TTS.value)
        self.step_changed.emit(StepId.SYNC.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def remove_tts_candidate(self, candidate_id: str) -> None:
        removed_selected = candidate_id == self.selected_tts_candidate_id
        removed_default = candidate_id == self.default_tts_candidate_id
        self.tts_candidates.pop(candidate_id, None)
        fallback = self._latest_valid_tts_candidate()
        if removed_default:
            self.default_tts_candidate_id = fallback.id if fallback else ""
        if removed_selected:
            self.selected_tts_candidate_id = ""
            if fallback and self.select_tts_candidate(fallback.id):
                return
            self.invalidate_from(StepId.TTS)
            if self.statuses[StepId.TRANSLATE] == StepStatus.DONE:
                self.statuses[StepId.TTS] = StepStatus.READY
        self.step_changed.emit(StepId.TTS.value)
        self.project_changed.emit()
        self.save_project()

    def _register_legacy_tts(self) -> None:
        if self.tts_candidates:
            return
        result = self.results.get(StepId.TTS)
        if not result or not result.segments:
            return
        valid_files = [Path(segment.audio_file) for segment in result.segments]
        if not valid_files or any(not path.is_file() for path in valid_files):
            return
        folder = valid_files[0].parent
        manifest = Path(result.artifacts.get("tts_manifest", folder / "manifest.json"))
        if not manifest.is_file():
            return
        candidate_id = "tts-legacy"
        created_at = datetime.fromtimestamp(manifest.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        candidate = TtsCandidate(
            id=candidate_id,
            label=f"TTS cũ · {folder.name}",
            created_at=created_at,
            path=str(manifest),
            folder=str(folder),
            provider=str(result.metadata.get("provider", "Legacy")),
            voice=str(result.metadata.get("voice", "")),
            segment_count=len(result.segments),
            summary=result.summary,
            metadata={**result.metadata, "tts_candidate_id": candidate_id},
        )
        self.tts_candidates[candidate_id] = candidate
        self.selected_tts_candidate_id = candidate_id
        self.default_tts_candidate_id = candidate_id

    def _restore_selected_tts(self) -> None:
        if StepId.TRANSLATE not in self.results:
            return
        choices = (self.selected_tts_candidate_id, self.default_tts_candidate_id)
        candidate = next(
            (self.tts_candidate(item) for item in choices if item and self._result_from_tts(item)),
            None,
        )
        candidate = candidate or self._latest_valid_tts_candidate()
        if not candidate:
            return
        result = self._result_from_tts(candidate.id)
        if not result:
            return
        self.selected_tts_candidate_id = candidate.id
        self.default_tts_candidate_id = candidate.id
        current = self.results.get(StepId.TTS)
        if current and str(current.metadata.get("tts_candidate_id", "")) == candidate.id:
            return
        self.results[StepId.TTS] = result
        self.statuses[StepId.TTS] = StepStatus.DONE
        for later in STEP_ORDER[STEP_ORDER.index(StepId.SYNC):]:
            self.results.pop(later, None)
            self.statuses[later] = StepStatus.PENDING

    def _latest_valid_tts_candidate(self) -> TtsCandidate | None:
        valid = [item for item in self.tts_candidates.values() if self._result_from_tts(item.id)]
        return max(valid, key=lambda item: item.created_at, default=None)

    def _result_from_tts(self, candidate_id: str) -> StepResult | None:
        candidate = self.tts_candidate(candidate_id)
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
                    source_text=str(item.get("source_text", "")),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                )
                for index, item in enumerate(payload.get("segments", []), start=1)
            ]
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not segments or any(not item.audio_file or not Path(item.audio_file).is_file() for item in segments):
            return None
        metadata = {
            **candidate.metadata,
            **{key: value for key, value in payload.items() if key != "segments"},
            "tts_candidate_id": candidate.id,
        }
        return StepResult(
            step=StepId.TTS,
            summary=candidate.summary or f"{candidate.label} · {len(segments)} segment",
            artifacts={"tts_manifest": str(path), "audio_folder": candidate.folder},
            segments=segments,
            metadata=metadata,
        )

    def sync_candidate(self, candidate_id: str) -> SyncCandidate | None:
        return self.sync_candidates.get(candidate_id)

    def select_sync_candidate(self, candidate_id: str) -> bool:
        result = self._result_from_sync(candidate_id)
        if result is None or int(result.metadata.get("error_count", 0)) > 0:
            return False
        self.selected_sync_candidate_id = candidate_id
        self.default_sync_candidate_id = candidate_id
        self.results[StepId.SYNC] = result
        self.statuses[StepId.SYNC] = StepStatus.DONE
        self.invalidate_from(StepId.BUILD_AUDIO)
        self.statuses[StepId.BUILD_AUDIO] = StepStatus.READY
        self.step_changed.emit(StepId.SYNC.value)
        self.step_changed.emit(StepId.BUILD_AUDIO.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def refresh_sync_candidate(self, candidate_id: str, activate_if_complete: bool = True) -> bool:
        result = self._result_from_sync(candidate_id, allow_incomplete=True)
        if result is None:
            return False
        candidate = self.sync_candidate(candidate_id)
        error_count = int(result.metadata.get("error_count", 0))
        if candidate:
            candidate.error_count = error_count
            candidate.summary = result.summary
            candidate.metadata = dict(result.metadata)
        self.results[StepId.SYNC] = result
        self.statuses[StepId.SYNC] = StepStatus.ERROR if error_count else StepStatus.DONE
        self.invalidate_from(StepId.BUILD_AUDIO)
        if error_count:
            if self.selected_sync_candidate_id == candidate_id:
                self.selected_sync_candidate_id = ""
            if self.default_sync_candidate_id == candidate_id:
                self.default_sync_candidate_id = ""
        elif activate_if_complete:
            self.selected_sync_candidate_id = candidate_id
            self.default_sync_candidate_id = candidate_id
            self.statuses[StepId.BUILD_AUDIO] = StepStatus.READY
            self.step_changed.emit(StepId.BUILD_AUDIO.value)
        self.step_changed.emit(StepId.SYNC.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def refresh_repaired_chain(
        self,
        translation_candidate_id: str,
        tts_candidate_id: str,
        sync_candidate_id: str,
    ) -> bool:
        translation = self._result_from_translation(translation_candidate_id)
        tts = self._result_from_tts(tts_candidate_id)
        if translation is None or tts is None:
            return False
        translation_candidate = self.translation_candidate(translation_candidate_id)
        tts_candidate = self.tts_candidate(tts_candidate_id)
        if translation_candidate:
            translation_candidate.metadata = dict(translation.metadata)
        if tts_candidate:
            tts_candidate.metadata = dict(tts.metadata)
        if self.selected_translation_candidate_id == translation_candidate_id:
            self.results[StepId.TRANSLATE] = translation
            self.statuses[StepId.TRANSLATE] = StepStatus.DONE
            self.step_changed.emit(StepId.TRANSLATE.value)
        if self.selected_tts_candidate_id == tts_candidate_id:
            self.results[StepId.TTS] = tts
            self.statuses[StepId.TTS] = StepStatus.DONE
            self.step_changed.emit(StepId.TTS.value)
        return self.refresh_sync_candidate(sync_candidate_id, activate_if_complete=True)

    def remove_sync_candidate(self, candidate_id: str) -> None:
        removed_selected = candidate_id == self.selected_sync_candidate_id
        removed_default = candidate_id == self.default_sync_candidate_id
        current = self.results.get(StepId.SYNC)
        current_id = str(current.metadata.get("sync_candidate_id", "")) if current else ""
        self.sync_candidates.pop(candidate_id, None)
        fallback = self._latest_valid_sync_candidate()
        if removed_default:
            self.default_sync_candidate_id = fallback.id if fallback else ""
        if removed_selected:
            self.selected_sync_candidate_id = ""
        if current_id == candidate_id or removed_selected:
            if fallback and self.select_sync_candidate(fallback.id):
                return
            self.invalidate_from(StepId.SYNC)
            if self.statuses[StepId.TTS] == StepStatus.DONE:
                self.statuses[StepId.SYNC] = StepStatus.READY
        self.step_changed.emit(StepId.SYNC.value)
        self.project_changed.emit()
        self.save_project()

    def _restore_selected_sync(self) -> None:
        if StepId.TTS not in self.results:
            return
        current = self.results.get(StepId.SYNC)
        current_id = str(current.metadata.get("sync_candidate_id", "")) if current else ""
        if current_id and current_id in self.sync_candidates:
            restored = self._result_from_sync(current_id, allow_incomplete=True)
            if restored:
                self.results[StepId.SYNC] = restored
                self.statuses[StepId.SYNC] = (
                    StepStatus.ERROR
                    if int(restored.metadata.get("error_count", 0))
                    else StepStatus.DONE
                )
                return
        choices = (self.selected_sync_candidate_id, self.default_sync_candidate_id)
        candidate = next(
            (self.sync_candidate(item) for item in choices if item and self._result_from_sync(item)),
            None,
        )
        candidate = candidate or self._latest_valid_sync_candidate()
        if not candidate:
            return
        result = self._result_from_sync(candidate.id)
        if result:
            self.selected_sync_candidate_id = candidate.id
            self.default_sync_candidate_id = candidate.id
            self.results[StepId.SYNC] = result
            self.statuses[StepId.SYNC] = StepStatus.DONE

    def _latest_valid_sync_candidate(self) -> SyncCandidate | None:
        valid = [item for item in self.sync_candidates.values() if self._result_from_sync(item.id)]
        return max(valid, key=lambda item: item.created_at, default=None)

    def _result_from_sync(
        self,
        candidate_id: str,
        allow_incomplete: bool = False,
    ) -> StepResult | None:
        candidate = self.sync_candidate(candidate_id)
        if not candidate:
            return None
        path = Path(candidate.path)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_segments = payload.get("segments", [])
            segments = [
                Segment(
                    id=int(item.get("id", index)),
                    start=float(item.get("start", 0.0)),
                    end=float(item.get("end", 0.0)),
                    source_text=str(item.get("source_text", "")),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                    synced_audio_file=str(item.get("synced_audio_file", "")),
                )
                for index, item in enumerate(raw_segments, start=1)
            ]
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        error_count = int(payload.get("error_count", 0))
        if not segments or (error_count and not allow_incomplete):
            return None
        ready_files = [Path(item.synced_audio_file) for item in segments if item.synced_audio_file]
        expected_ready = len(segments) - error_count
        if len(ready_files) != expected_ready or any(not item.is_file() for item in ready_files):
            return None
        metadata = {
            **candidate.metadata,
            **{key: value for key, value in payload.items() if key not in {"segments", "source_tts_settings"}},
            "sync_candidate_id": candidate.id,
        }
        summary = (
            f"Đã đồng bộ {len(segments) - error_count}/{len(segments)} segment · "
            f"{error_count} segment cần sửa"
            if error_count
            else f"Đã đồng bộ thành công {len(segments)} segment"
        )
        return StepResult(
            step=StepId.SYNC,
            summary=summary,
            artifacts={"sync_manifest": str(path), "synced_folder": str(Path(candidate.folder) / "segments")},
            segments=segments,
            metadata=metadata,
        )

    def build_audio_candidate(self, candidate_id: str) -> BuildAudioCandidate | None:
        return self.build_audio_candidates.get(candidate_id)

    def select_build_audio_candidate(self, candidate_id: str) -> bool:
        result = self._result_from_build_audio(candidate_id)
        if result is None:
            return False
        self.selected_build_audio_candidate_id = candidate_id
        self.default_build_audio_candidate_id = candidate_id
        self.results[StepId.BUILD_AUDIO] = result
        self.statuses[StepId.BUILD_AUDIO] = StepStatus.DONE
        self.invalidate_from(StepId.RENDER)
        self.statuses[StepId.RENDER] = StepStatus.READY
        self.step_changed.emit(StepId.BUILD_AUDIO.value)
        self.step_changed.emit(StepId.RENDER.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def remove_build_audio_candidate(self, candidate_id: str) -> None:
        removed_selected = candidate_id == self.selected_build_audio_candidate_id
        removed_default = candidate_id == self.default_build_audio_candidate_id
        current = self.results.get(StepId.BUILD_AUDIO)
        current_id = str(current.metadata.get("build_audio_candidate_id", "")) if current else ""
        self.build_audio_candidates.pop(candidate_id, None)
        fallback = self._latest_valid_build_audio_candidate()
        if removed_default:
            self.default_build_audio_candidate_id = fallback.id if fallback else ""
        if removed_selected:
            self.selected_build_audio_candidate_id = ""
        if current_id == candidate_id or removed_selected:
            if fallback and self.select_build_audio_candidate(fallback.id):
                return
            self.invalidate_from(StepId.BUILD_AUDIO)
            if self.statuses[StepId.SYNC] == StepStatus.DONE:
                self.statuses[StepId.BUILD_AUDIO] = StepStatus.READY
        self.step_changed.emit(StepId.BUILD_AUDIO.value)
        self.project_changed.emit()
        self.save_project()

    def _restore_selected_build_audio(self) -> None:
        if StepId.SYNC not in self.results:
            return
        current = self.results.get(StepId.BUILD_AUDIO)
        current_id = str(current.metadata.get("build_audio_candidate_id", "")) if current else ""
        if current_id and current_id in self.build_audio_candidates:
            restored = self._result_from_build_audio(current_id)
            if restored:
                self.selected_build_audio_candidate_id = current_id
                self.default_build_audio_candidate_id = current_id
                self.results[StepId.BUILD_AUDIO] = restored
                self.statuses[StepId.BUILD_AUDIO] = StepStatus.DONE
                return
        choices = (
            self.selected_build_audio_candidate_id,
            self.default_build_audio_candidate_id,
        )
        candidate = next(
            (
                self.build_audio_candidate(item)
                for item in choices
                if item and self._result_from_build_audio(item)
            ),
            None,
        )
        candidate = candidate or self._latest_valid_build_audio_candidate()
        if not candidate:
            return
        result = self._result_from_build_audio(candidate.id)
        if result:
            self.selected_build_audio_candidate_id = candidate.id
            self.default_build_audio_candidate_id = candidate.id
            self.results[StepId.BUILD_AUDIO] = result
            self.statuses[StepId.BUILD_AUDIO] = StepStatus.DONE

    def _latest_valid_build_audio_candidate(self) -> BuildAudioCandidate | None:
        valid = [
            item
            for item in self.build_audio_candidates.values()
            if self._result_from_build_audio(item.id)
        ]
        return max(valid, key=lambda item: item.created_at, default=None)

    def _result_from_build_audio(self, candidate_id: str) -> StepResult | None:
        candidate = self.build_audio_candidate(candidate_id)
        if not candidate:
            return None
        manifest = Path(candidate.path)
        audio = Path(candidate.audio_file)
        if not manifest.is_file() or not audio.is_file():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            duration = float(payload.get("duration_seconds", candidate.duration_seconds))
            source_sync_id = str(
                payload.get("source_sync_candidate_id", candidate.source_sync_candidate_id)
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        source = self._result_from_sync(source_sync_id)
        if duration <= 0 or source is None:
            return None
        metadata = {
            **candidate.metadata,
            **payload,
            "build_audio_candidate_id": candidate.id,
        }
        return StepResult(
            step=StepId.BUILD_AUDIO,
            summary=candidate.summary or f"{candidate.label} · {duration:.2f}s",
            artifacts={"voice_track": str(audio), "build_audio_manifest": str(manifest)},
            segments=source.segments,
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
        if step == StepId.TRANSLATE and self.default_translation_candidate_id:
            if self.select_translation_candidate(self.default_translation_candidate_id):
                return
        if step == StepId.TTS and self.default_tts_candidate_id:
            if self.select_tts_candidate(self.default_tts_candidate_id):
                return
        if step == StepId.SYNC and self.default_sync_candidate_id:
            if self.select_sync_candidate(self.default_sync_candidate_id):
                return
        if step == StepId.BUILD_AUDIO and self.default_build_audio_candidate_id:
            if self.select_build_audio_candidate(self.default_build_audio_candidate_id):
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
        for candidate in result.translation_candidates:
            self.translation_candidates[candidate.id] = candidate
        translation_id = str(result.metadata.get("recommended_translation_candidate_id", ""))
        if translation_id and translation_id in self.translation_candidates:
            self.selected_translation_candidate_id = translation_id
            self.default_translation_candidate_id = translation_id
        for candidate in result.tts_candidates:
            self.tts_candidates[candidate.id] = candidate
        tts_id = str(result.metadata.get("recommended_tts_candidate_id", ""))
        if tts_id and tts_id in self.tts_candidates:
            self.selected_tts_candidate_id = tts_id
            self.default_tts_candidate_id = tts_id
        for candidate in result.sync_candidates:
            self.sync_candidates[candidate.id] = candidate
        sync_id = str(result.metadata.get("recommended_sync_candidate_id", ""))
        if sync_id and sync_id in self.sync_candidates:
            self.selected_sync_candidate_id = sync_id
            self.default_sync_candidate_id = sync_id
        for candidate in result.build_audio_candidates:
            self.build_audio_candidates[candidate.id] = candidate
        build_audio_id = str(result.metadata.get("recommended_build_audio_candidate_id", ""))
        if build_audio_id and build_audio_id in self.build_audio_candidates:
            self.selected_build_audio_candidate_id = build_audio_id
            self.default_build_audio_candidate_id = build_audio_id
        self.results[result.step] = result
        has_blocking_errors = (
            result.step == StepId.SYNC and int(result.metadata.get("error_count", 0)) > 0
        )
        self.statuses[result.step] = StepStatus.ERROR if has_blocking_errors else StepStatus.DONE
        for later in STEP_ORDER[index + 1 :]:
            if later in self.results:
                del self.results[later]
            self.statuses[later] = StepStatus.PENDING
            self.step_changed.emit(later.value)
        if index + 1 < len(STEP_ORDER) and not has_blocking_errors:
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
