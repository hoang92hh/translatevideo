from __future__ import annotations

import json
from pathlib import Path

from video_translator.common.models import RenderCandidate, StepId, StepResult, StepStatus

class RenderStateMixin:
    def render_candidate(self, candidate_id: str) -> RenderCandidate | None:
        return self.render_candidates.get(candidate_id)

    def remove_render_candidate(self, candidate_id: str) -> None:
        current = self.results.get(StepId.RENDER)
        current_id = str(current.metadata.get("render_candidate_id", "")) if current else ""
        self.render_candidates.pop(candidate_id, None)
        if candidate_id == self.selected_render_candidate_id:
            self.selected_render_candidate_id = ""
        if current_id == candidate_id:
            fallback = self._latest_valid_render_candidate()
            if fallback:
                restored = self._result_from_render(fallback.id)
                if restored:
                    self.selected_render_candidate_id = fallback.id
                    self.results[StepId.RENDER] = restored
                    self.statuses[StepId.RENDER] = StepStatus.DONE
            else:
                self.results.pop(StepId.RENDER, None)
                self.statuses[StepId.RENDER] = (
                    StepStatus.READY
                    if self.statuses[StepId.BUILD_AUDIO] == StepStatus.DONE
                    else StepStatus.PENDING
                )
        self.step_changed.emit(StepId.RENDER.value)
        self.project_changed.emit()
        self.save_project()

    def _restore_selected_render(self) -> None:
        if StepId.BUILD_AUDIO not in self.results:
            return
        current = self.results.get(StepId.RENDER)
        current_id = str(current.metadata.get("render_candidate_id", "")) if current else ""
        choices = (current_id, self.selected_render_candidate_id)
        candidate = next(
            (
                self.render_candidate(item)
                for item in choices
                if item and self._result_from_render(item)
            ),
            None,
        )
        candidate = candidate or self._latest_valid_render_candidate()
        if not candidate:
            return
        result = self._result_from_render(candidate.id)
        if result:
            self.selected_render_candidate_id = candidate.id
            self.results[StepId.RENDER] = result
            self.statuses[StepId.RENDER] = StepStatus.DONE

    def _latest_valid_render_candidate(self) -> RenderCandidate | None:
        valid = [
            item for item in self.render_candidates.values() if self._result_from_render(item.id)
        ]
        return max(valid, key=lambda item: item.created_at, default=None)

    def _result_from_render(
        self,
        candidate_id: str,
        require_selected_source: bool = True,
    ) -> StepResult | None:
        candidate = self.render_candidate(candidate_id)
        if not candidate:
            return None
        manifest = Path(candidate.path)
        video = Path(candidate.video_file)
        if not manifest.is_file() or not video.is_file():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            duration = float(payload.get("duration_seconds", candidate.duration_seconds))
            source_build_audio_id = str(payload.get("source_build_audio_candidate_id", ""))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if (
            duration <= 0
            or not source_build_audio_id
            or (
                require_selected_source
                and source_build_audio_id != self.selected_build_audio_candidate_id
            )
        ):
            return None
        source = self.results.get(StepId.BUILD_AUDIO)
        source_id = str(source.metadata.get("build_audio_candidate_id", "")) if source else ""
        if source_id != source_build_audio_id:
            source = self._result_from_build_audio(source_build_audio_id)
        subtitle = Path(candidate.subtitle_file) if candidate.subtitle_file else None
        artifacts = {
            "output_video": str(video),
            **({"subtitle": str(subtitle)} if subtitle and subtitle.is_file() else {}),
            "render_manifest": str(manifest),
        }
        return StepResult(
            step=StepId.RENDER,
            summary=candidate.summary or f"{candidate.label} · {duration:.2f}s",
            artifacts=artifacts,
            segments=source.segments if source else [],
            metadata={**candidate.metadata, **payload, "render_candidate_id": candidate.id},
        )

