from __future__ import annotations

import json
from pathlib import Path

from video_translator.common.models import Segment, StepId, StepResult, StepStatus, SyncCandidate

class SyncStateMixin:
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
        transcript_candidate_id: str,
        translation_candidate_id: str,
        tts_candidate_id: str,
        sync_candidate_id: str,
    ) -> bool:
        transcript = (
            self._result_from_transcript(transcript_candidate_id)
            if transcript_candidate_id
            else None
        )
        translation = self._result_from_translation(translation_candidate_id)
        tts = self._result_from_tts(tts_candidate_id)
        if translation is None or tts is None:
            return False
        transcript_candidate = self.transcript_candidate(transcript_candidate_id)
        translation_candidate = self.translation_candidate(translation_candidate_id)
        tts_candidate = self.tts_candidate(tts_candidate_id)
        if transcript_candidate and transcript:
            transcript_candidate.metadata = dict(transcript.metadata)
        if translation_candidate:
            translation_candidate.metadata = dict(translation.metadata)
        if tts_candidate:
            tts_candidate.metadata = dict(tts.metadata)
        if (
            transcript
            and self.selected_transcript_candidate_id == transcript_candidate_id
        ):
            self.results[StepId.STT] = transcript
            self.statuses[StepId.STT] = StepStatus.DONE
            self.step_changed.emit(StepId.STT.value)
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
                    speaker_id=str(item.get("speaker_id", "")),
                    merge_parts=[
                        dict(part) for part in item.get("merge_parts", []) if isinstance(part, dict)
                    ],
                    source_text=str(item.get("source_text", "")),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                    synced_audio_file=str(item.get("synced_audio_file", "")),
                    tts_provider=str(item.get("tts_provider", "")),
                    tts_model=str(item.get("tts_model", "")),
                    tts_voice=str(item.get("tts_voice", "")),
                    tts_reference_voice=str(item.get("tts_reference_voice", "")),
                    tts_actual_device=str(item.get("tts_actual_device", "")),
                )
                for index, item in enumerate(raw_segments, start=1)
            ]
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        error_count = int(payload.get("error_count", 0))
        if not segments or (error_count and not allow_incomplete):
            return None
        playable_segments = [item for item in segments if item.end > item.start]
        ready_files = [Path(item.synced_audio_file) for item in playable_segments if item.synced_audio_file]
        expected_ready = len(playable_segments) - error_count
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

