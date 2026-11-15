from __future__ import annotations

import json
from pathlib import Path

from video_translator.common.models import BuildAudioCandidate, StepId, StepResult, StepStatus

class BuildAudioStateMixin:
    def build_audio_candidate(self, candidate_id: str) -> BuildAudioCandidate | None:
        return self.build_audio_candidates.get(candidate_id)

    def select_build_audio_candidate(self, candidate_id: str) -> bool:
        result = self._result_from_build_audio(candidate_id)
        if result is None:
            return False
        changed = candidate_id != self.selected_build_audio_candidate_id
        self.selected_build_audio_candidate_id = candidate_id
        self.default_build_audio_candidate_id = candidate_id
        if changed:
            self.selected_render_candidate_id = ""
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

