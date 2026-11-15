from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from video_translator.common.models import STEP_ORDER, Segment, StepId, StepResult, StepStatus, TtsCandidate

class TtsStateMixin:
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
                    speaker_id=str(item.get("speaker_id", "")),
                    merge_parts=[
                        dict(part) for part in item.get("merge_parts", []) if isinstance(part, dict)
                    ],
                    source_text=str(item.get("source_text", "")),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                    tts_provider=str(item.get("tts_provider", "")),
                    tts_model=str(item.get("tts_model", "")),
                    tts_voice=str(item.get("tts_voice", "")),
                    tts_reference_voice=str(item.get("tts_reference_voice", "")),
                    tts_actual_device=str(item.get("tts_actual_device", "")),
                )
                for index, item in enumerate(payload.get("segments", []), start=1)
            ]
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not segments or any(
            item.duration > 0 and (not item.audio_file or not Path(item.audio_file).is_file())
            for item in segments
        ):
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

