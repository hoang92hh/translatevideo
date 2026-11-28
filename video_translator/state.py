from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .common.models import (
    AudioCandidate,
    BuildAudioCandidate,
    RenderCandidate,
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
from .project_paths import srt_path_for_video
from .steps.step1_separation.state import AudioStateMixin
from .steps.step2_transcription.state import TranscriptStateMixin
from .steps.step3_translation.state import TranslationStateMixin
from .steps.step4_tts.state import TtsStateMixin
from .steps.step5_alignment.state import SyncStateMixin
from .steps.step6_render.state import BuildAudioStateMixin
from .steps.step7_export.state import RenderStateMixin


class ProjectState(
    QObject,
    AudioStateMixin,
    TranscriptStateMixin,
    TranslationStateMixin,
    TtsStateMixin,
    SyncStateMixin,
    BuildAudioStateMixin,
    RenderStateMixin,
):
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
        self.render_candidates: dict[str, RenderCandidate] = {}
        self.selected_render_candidate_id = ""
        self.results: dict[StepId, StepResult] = {}
        self.statuses = {step: StepStatus.PENDING for step in STEP_ORDER}
        self.statuses[StepId.EXTRACT] = StepStatus.READY

    def bind_project(self, project: VideoProject, *, restore_candidates: bool = True) -> None:
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
        self.render_candidates = {
            item["id"]: RenderCandidate(**item)
            for item in project.render_candidates
            if item.get("id")
        }
        self.selected_render_candidate_id = project.selected_render_candidate_id
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
        step2_result = self.results.get(StepId.STT)
        srt_only = bool(step2_result and step2_result.metadata.get("srt_only"))
        if restore_candidates and not srt_only:
            self._restore_imported_srt_inputs()
            self._restore_selected_transcript()
        self._register_legacy_translation()
        if restore_candidates and not srt_only:
            self._restore_selected_translation()
        self._register_legacy_tts()
        if restore_candidates and not srt_only:
            self._restore_selected_tts()
            self._restore_selected_sync()
            self._restore_selected_build_audio()
            self._restore_selected_render()
        for index, step in enumerate(STEP_ORDER):
            if self.statuses[step] == StepStatus.DONE:
                continue
            if step == StepId.TRANSLATE and self.results.get(StepId.STT) and self.results[StepId.STT].metadata.get("srt_only"):
                break
            if self.statuses[step] == StepStatus.PENDING and (
                index == 0 or self.statuses[STEP_ORDER[index - 1]] == StepStatus.DONE
            ):
                self.statuses[step] = StepStatus.READY
            break
        for step in STEP_ORDER:
            self.step_changed.emit(step.value)
        self.project_changed.emit()

    def batch_resume_step(self, *, before_step: StepId | None = None) -> StepId | None:
        """Validate the active pipeline in order, without resurrecting old outputs."""
        artifacts = {
            StepId.EXTRACT: ("source_audio",),
            StepId.STT: ("transcript",),
            StepId.TRANSLATE: ("translation",),
            StepId.TTS: ("tts_manifest",),
            StepId.SYNC: ("sync_manifest",),
            StepId.BUILD_AUDIO: ("voice_track", "build_audio_manifest"),
            StepId.RENDER: ("output_video", "render_manifest"),
        }
        candidates = {
            StepId.STT: ("transcript_candidate_id", self.selected_transcript_candidate_id, self._result_from_transcript),
            StepId.TRANSLATE: ("translation_candidate_id", self.selected_translation_candidate_id, self._result_from_translation),
            StepId.TTS: ("tts_candidate_id", self.selected_tts_candidate_id, self._result_from_tts),
            StepId.SYNC: ("sync_candidate_id", self.selected_sync_candidate_id,
                          lambda value: self._result_from_sync(value, allow_incomplete=True)),
            StepId.BUILD_AUDIO: ("build_audio_candidate_id", self.selected_build_audio_candidate_id, self._result_from_build_audio),
            StepId.RENDER: ("render_candidate_id", self.selected_render_candidate_id, self._result_from_render),
        }
        sources = {
            StepId.STT: ("input_candidate_id", self.selected_audio_candidate_id),
            StepId.TRANSLATE: ("source_transcript_candidate_id", self.selected_transcript_candidate_id),
            StepId.TTS: ("source_translation_candidate_id", self.selected_translation_candidate_id),
            StepId.SYNC: ("source_tts_candidate_id", self.selected_tts_candidate_id),
            StepId.BUILD_AUDIO: ("source_sync_candidate_id", self.selected_sync_candidate_id),
            StepId.RENDER: ("source_build_audio_candidate_id", self.selected_build_audio_candidate_id),
        }
        for index, step in enumerate(STEP_ORDER):
            if step == before_step:
                # An explicit start only needs valid inputs from preceding steps.
                return step
            result = self.results.get(step)
            if step == StepId.STT and result and result.metadata.get("srt_only"):
                srt_path = result.artifacts.get("srt", "")
                if srt_path and Path(srt_path).is_file():
                    self.statuses[step] = StepStatus.DONE
                    return StepId.STT
                self.invalidate_from(step)
                return step
            # A file/candidate outside the active saved pipeline is not a checkpoint.
            valid = bool(self.project and self.project.pipeline.get(step.value) and result)
            if valid and step in candidates:
                key, selected_id, loader = candidates[step]
                candidate_id = str(result.metadata.get(key, ""))
                if candidate_id:
                    if selected_id and candidate_id != selected_id:
                        valid = False
                    else:
                        restored = loader(candidate_id)
                        valid = restored is not None
                        if restored is not None:
                            result = restored
                            self.results[step] = result
            if valid:
                valid = all(Path(str(result.artifacts.get(key, ""))).is_file() for key in artifacts[step])
            if valid and step == StepId.EXTRACT:
                valid = bool(self.audio_input_path() and Path(self.audio_input_path()).is_file())
            if valid and step in sources:
                key, source_id = sources[step]
                recorded_id = str(result.metadata.get(key, ""))
                if recorded_id and source_id and recorded_id != source_id:
                    valid = False
            if not valid:
                self.invalidate_from(step)
                return step
            blocking_errors = (
                step == StepId.STT and int(result.metadata.get("timeline_error_count", 0)) > 0
            ) or (
                step == StepId.SYNC and int(result.metadata.get("error_count", 0)) > 0
            )
            if blocking_errors:
                self.statuses[step] = StepStatus.ERROR
                if index + 1 < len(STEP_ORDER):
                    self.invalidate_from(STEP_ORDER[index + 1])
                return step
            self.statuses[step] = StepStatus.DONE
        return None

    def restore_selected_inputs(self, before_step: StepId, *, ocr_enabled: bool = False) -> StepId | None:
        """Hydrate the selected output immediately preceding an explicit start step."""
        target_index = STEP_ORDER.index(before_step)
        if target_index == 0:
            self.invalidate_from(before_step)
            self.statuses[before_step] = StepStatus.READY
            return before_step

        input_step = STEP_ORDER[target_index - 1]
        if input_step == StepId.EXTRACT:
            input_path = self.audio_input_path()
            if input_path and Path(input_path).is_file():
                self.statuses[input_step] = StepStatus.DONE
            elif not self.can_run(before_step, ocr_enabled=ocr_enabled):
                self.invalidate_from(before_step)
                return input_step
        else:
            selected_inputs = {
                StepId.STT: (
                    self.selected_transcript_candidate_id,
                    self._result_from_transcript,
                ),
                StepId.TRANSLATE: (
                    self.selected_translation_candidate_id,
                    self._result_from_translation,
                ),
                StepId.TTS: (
                    self.selected_tts_candidate_id,
                    self._result_from_tts,
                ),
                StepId.SYNC: (
                    self.selected_sync_candidate_id,
                    lambda value: self._result_from_sync(value, allow_incomplete=True),
                ),
                StepId.BUILD_AUDIO: (
                    self.selected_build_audio_candidate_id,
                    self._result_from_build_audio,
                ),
            }
            candidate_id, loader = selected_inputs[input_step]
            result = loader(candidate_id) if candidate_id else None
            blocking_errors = bool(result) and (
                input_step == StepId.STT
                and int(result.metadata.get("timeline_error_count", 0)) > 0
                or input_step == StepId.SYNC
                and int(result.metadata.get("error_count", 0)) > 0
            )
            if result is None or blocking_errors:
                self.invalidate_from(before_step)
                if blocking_errors:
                    self.statuses[input_step] = StepStatus.ERROR
                return input_step
            self.results[input_step] = result
            self.statuses[input_step] = StepStatus.DONE

        self.invalidate_from(before_step)
        self.statuses[before_step] = StepStatus.READY
        return before_step

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
        self.project.render_candidates = [
            asdict(candidate) for candidate in self.render_candidates.values()
        ]
        self.project.selected_render_candidate_id = self.selected_render_candidate_id
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
            "render_candidates": [asdict(candidate) for candidate in result.render_candidates],
        }

    @staticmethod
    def _deserialize_result(step: StepId, data: dict[str, Any]) -> StepResult:
        return StepResult(
            step=step,
            summary=data.get("summary", ""),
            artifacts=data.get("artifacts", {}),
            segments=[
                Segment(**{
                    key: value for key, value in item.items()
                    if key not in {"asr_metadata", "translation_metadata"}
                })
                for item in data.get("segments", [])
            ],
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
            render_candidates=[
                RenderCandidate(**item) for item in data.get("render_candidates", [])
            ],
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

    def mark_error(self, step: StepId, restore_previous: bool = True) -> None:
        if restore_previous and step == StepId.STT and self.default_transcript_candidate_id:
            if self.select_transcript_candidate(self.default_transcript_candidate_id):
                return
        if restore_previous and step == StepId.TRANSLATE and self.default_translation_candidate_id:
            if self.select_translation_candidate(self.default_translation_candidate_id):
                return
        if restore_previous and step == StepId.TTS and self.default_tts_candidate_id:
            if self.select_tts_candidate(self.default_tts_candidate_id):
                return
        if restore_previous and step == StepId.SYNC and self.default_sync_candidate_id:
            if self.select_sync_candidate(self.default_sync_candidate_id):
                return
        if restore_previous and step == StepId.BUILD_AUDIO and self.default_build_audio_candidate_id:
            if self.select_build_audio_candidate(self.default_build_audio_candidate_id):
                return
        if restore_previous and step == StepId.RENDER and self.selected_render_candidate_id:
            restored = self._result_from_render(self.selected_render_candidate_id)
            if restored:
                self.results[StepId.RENDER] = restored
                self.statuses[StepId.RENDER] = StepStatus.DONE
                self.step_changed.emit(StepId.RENDER.value)
                self.save_project()
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
            changed = build_audio_id != self.selected_build_audio_candidate_id
            self.selected_build_audio_candidate_id = build_audio_id
            self.default_build_audio_candidate_id = build_audio_id
            if changed:
                self.selected_render_candidate_id = ""
        for candidate in result.render_candidates:
            self.render_candidates[candidate.id] = candidate
        render_id = str(result.metadata.get("recommended_render_candidate_id", ""))
        if render_id and render_id in self.render_candidates:
            self.selected_render_candidate_id = render_id
        self.results[result.step] = result
        has_blocking_errors = (
            result.step == StepId.SYNC and int(result.metadata.get("error_count", 0)) > 0
        ) or (
            result.step == StepId.STT and int(result.metadata.get("timeline_error_count", 0)) > 0
        )
        self.statuses[result.step] = StepStatus.ERROR if has_blocking_errors else StepStatus.DONE
        for later in STEP_ORDER[index + 1 :]:
            if later in self.results:
                del self.results[later]
            self.statuses[later] = StepStatus.PENDING
            self.step_changed.emit(later.value)
        if index + 1 < len(STEP_ORDER) and not has_blocking_errors and not result.metadata.get("srt_only"):
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

    def can_run(self, step: StepId, *, srt_only: bool = False, ocr_enabled: bool = False) -> bool:
        index = STEP_ORDER.index(step)
        if index == 0:
            return bool(self.input_video)
        previous_done = self.statuses[STEP_ORDER[index - 1]] == StepStatus.DONE
        if step == StepId.STT:
            if not srt_only and self.input_video and (ocr_enabled or srt_path_for_video(self.input_video).is_file()):
                return True
            input_path = self.audio_input_path()
            return previous_done and bool(input_path) and Path(input_path).is_file()
        if step == StepId.TRANSLATE:
            transcript = self.results.get(StepId.STT)
            return previous_done and bool(transcript and not transcript.metadata.get("srt_only")
                                          and transcript.segments)
        return previous_done

    def previous_result(self, step: StepId) -> StepResult | None:
        index = STEP_ORDER.index(step)
        if index == 0:
            return None
        return self.results.get(STEP_ORDER[index - 1])

    def candidate_result(self, step: StepId, candidate_id: str) -> StepResult | None:
        active = self.results.get(step)
        candidate_keys = {
            StepId.STT: "transcript_candidate_id",
            StepId.TRANSLATE: "translation_candidate_id",
            StepId.TTS: "tts_candidate_id",
            StepId.SYNC: "sync_candidate_id",
            StepId.BUILD_AUDIO: "build_audio_candidate_id",
            StepId.RENDER: "render_candidate_id",
        }
        key = candidate_keys.get(step, "")
        if active and key and str(active.metadata.get(key, "")) == candidate_id:
            return active
        loaders = {
            StepId.STT: self._result_from_transcript,
            StepId.TRANSLATE: self._result_from_translation,
            StepId.TTS: self._result_from_tts,
            StepId.BUILD_AUDIO: self._result_from_build_audio,
        }
        if step == StepId.SYNC:
            return self._result_from_sync(candidate_id, allow_incomplete=True)
        if step == StepId.RENDER:
            return self._result_from_render(candidate_id, require_selected_source=False)
        loader = loaders.get(step)
        return loader(candidate_id) if loader else None

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
