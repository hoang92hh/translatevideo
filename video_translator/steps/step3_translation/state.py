from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from video_translator.errors import UserFacingError
from video_translator.common.models import STEP_ORDER, Segment, StepId, StepResult, StepStatus, TranslationCandidate
from video_translator.steps.step3_translation.csv_exchange import import_segments

class TranslationStateMixin:
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

    def import_translation_csv(self, source_candidate_id: str, csv_path: str) -> TranslationCandidate:
        source_candidate = self.translation_candidate(source_candidate_id)
        if not self.project or not source_candidate or not Path(source_candidate.path).is_file():
            raise ValueError("Không tìm thấy output Step 3 nguồn để nhập CSV.")
        source_payload = json.loads(Path(source_candidate.path).read_text(encoding="utf-8"))
        if not isinstance(source_payload, dict):
            raise ValueError("Output Step 3 nguồn không phải JSON object hợp lệ.")
        original_rows = source_payload.get("segments")
        if not isinstance(original_rows, list):
            raise ValueError("Output Step 3 nguồn không có danh sách segment hợp lệ.")
        transcript_id = str(source_payload.get("source_transcript_candidate_id", ""))
        if transcript_id and transcript_id != self.selected_transcript_candidate_id:
            raise ValueError("Hãy chọn transcript Step 2 của output nguồn trước khi nhập CSV.")
        rows = import_segments(Path(csv_path), original_rows)

        now = datetime.now(timezone.utc)
        candidate_id = f"translation-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        folder = Path(self.workspace_path("translations", candidate_id))
        output_path = folder / "translated_segments.json"
        profile_path = folder / "dialogue_profile.json"
        source_profile_path = Path(str(source_payload.get("dialogue_profile_path") or ""))
        has_profile = source_profile_path.is_file()
        payload = {
            **source_payload,
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            "source_translation_candidate_id": source_candidate_id,
            "imported_from_csv": Path(csv_path).name,
            "dialogue_profile_path": str(profile_path) if has_profile else "",
            "skipped_translation_ids": [row["id"] for row in rows if not row["translated_text"]],
            "segments": rows,
        }
        workflow = payload.get("workflow")
        if isinstance(workflow, dict):
            payload["workflow"] = {key: value for key, value in workflow.items() if key != "review_issues"}
        folder.mkdir(parents=True, exist_ok=False)
        try:
            if has_profile:
                profile_payload = json.loads(source_profile_path.read_text(encoding="utf-8"))
                profile_payload["candidate_id"] = candidate_id
                profile_payload["created_at"] = payload["created_at"]
                profile_path.write_text(json.dumps(profile_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            profile_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            folder.rmdir()
            raise

        summary = f"Đã nhập CSV · {len(rows)} segment · từ {source_candidate.label}"
        metadata = {
            **source_candidate.metadata,
            **{key: value for key, value in payload.items() if key != "segments"},
            "translation_candidate_id": candidate_id,
        }
        candidate = TranslationCandidate(
            id=candidate_id,
            label=f"CSV · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
            created_at=payload["created_at"],
            path=str(output_path),
            segment_count=len(rows),
            summary=summary,
            metadata=metadata,
        )
        self.translation_candidates[candidate_id] = candidate
        if not self.select_translation_candidate(candidate_id):
            self.translation_candidates.pop(candidate_id, None)
            profile_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            folder.rmdir()
            raise ValueError("Không thể nạp output JSON vừa tạo từ CSV.")
        return candidate

    def save_translation_candidate_edits(
        self,
        candidate_id: str,
        updates: dict[int, dict[str, object]],
    ) -> None:
        candidate = self.translation_candidate(candidate_id)
        if not candidate or candidate_id != self.selected_translation_candidate_id:
            raise UserFacingError(
                "Chưa chọn đúng bản dịch",
                "Chỉ có thể lưu candidate đang được dùng làm input Step 4.",
                "Nhấn “Dùng làm input Step 4”, sửa nội dung rồi lưu lại.",
            )
        path = Path(candidate.path)
        if not path.is_file():
            raise UserFacingError(
                "Không tìm thấy bản dịch cần lưu",
                "File translated_segments.json của output đang chọn không còn tồn tại.",
                "Chọn lại một output hợp lệ của Step 3.",
            )
        result = self.results.get(StepId.TRANSLATE)
        if (not result
                or result.metadata.get("translation_candidate_id") != candidate_id
                or Path(result.artifacts.get("translation", "")) != path):
            result = self._result_from_translation(candidate_id)
        if result is None:
            raise UserFacingError(
                "Không thể nạp bản dịch cần lưu",
                "Không thể đọc kết quả Step 3 từ translated_segments.json.",
                "Kiểm tra file bản dịch rồi mở lại output Step 3.",
            )

        expected_ids = {segment.id for segment in result.segments}
        if set(updates) != expected_ids:
            raise UserFacingError(
                "Danh sách segment đã thay đổi",
                "Bảng chỉnh sửa không còn khớp với candidate hiện tại.",
                "Tải lại Step 3 rồi thực hiện chỉnh sửa lại.",
            )
        manual_check = any("start" in update for update in updates.values())
        edited_segments = []
        allowed_speakers = {segment.speaker_id for segment in result.segments}
        for segment in result.segments:
            update = updates[segment.id]
            edited = replace(
                segment,
                source_text=segment.source_text if manual_check else str(update.get("source_text", "")),
                translated_text=str(update.get("translated_text", "")).strip(),
            )
            if manual_check:
                try:
                    edited.start = float(update["start"])
                    edited.end = float(update["end"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise UserFacingError("Timeline không hợp lệ", f"Kiểm tra Start/End của ID {segment.id}.",
                                          "Nhập thời gian bằng giây.") from exc
                edited.speaker_id = str(update.get("speaker_id", segment.speaker_id)).strip()
                if (not math.isfinite(edited.start) or not math.isfinite(edited.end)
                        or edited.start < 0 or edited.end < edited.start):
                    raise UserFacingError("Timeline không hợp lệ", f"ID {segment.id}: cần 0 ≤ Start ≤ End.",
                                          "Kiểm tra lại thời gian.")
                if edited.speaker_id not in allowed_speakers:
                    raise UserFacingError("Speaker không hợp lệ", f"ID {segment.id}: {edited.speaker_id}",
                                          "Chọn speaker ID có trong bản dịch.")
                if not edited.translated_text:
                    edited.start = edited.end
                elif edited.end <= edited.start:
                    raise UserFacingError("Câu dịch chưa có thời lượng", f"ID {segment.id} có chữ nhưng Start = End.",
                                          "Đặt Start/End để khôi phục câu này.")
            edited_segments.append(edited)
        if manual_check:
            active = sorted((item for item in edited_segments if item.translated_text),
                            key=lambda item: (item.start, item.end))
            for previous, following in zip(active, active[1:]):
                if previous.end > following.start + 0.000001:
                    raise UserFacingError("Timeline chồng nhau",
                                          f"ID {previous.id} và {following.id} đang chồng thời gian.",
                                          "Điều chỉnh Start/End; dùng Hiển thị toàn bộ segment nếu cần.")
        empty_ids = sorted(
            segment.id
            for segment in edited_segments
            if segment.duration > 0
            and not segment.translated_text
        )
        if empty_ids:
            raise UserFacingError(
                "Bản dịch còn trống",
                f"Các segment chưa có nội dung dịch: {empty_ids}",
                "Nhập nội dung Translation cho mọi segment có thời lượng rồi lưu lại.",
            )

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("manifest không phải object")
            raw_segments = payload.get("segments", [])
            if not isinstance(raw_segments, list):
                raise TypeError("segments không phải danh sách")
            manifest_segments = {
                int(item["id"]): item
                for item in raw_segments
                if isinstance(item, dict) and "id" in item
            }
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise UserFacingError(
                "File bản dịch không hợp lệ",
                "Không thể đọc cấu trúc translated_segments.json để lưu thay đổi.",
                "Mở file để kiểm tra hoặc chạy lại Step 3.",
                repr(exc),
            ) from exc
        if set(manifest_segments) != expected_ids or len(raw_segments) != len(expected_ids):
            raise UserFacingError(
                "File bản dịch không khớp",
                "Danh sách ID trong translated_segments.json khác với bảng đang hiển thị.",
                "Chọn lại candidate hoặc chạy lại Step 3.",
            )

        for segment in edited_segments:
            manifest_segments[segment.id]["source_text"] = segment.source_text
            manifest_segments[segment.id]["translated_text"] = segment.translated_text
            if manual_check:
                manifest_segments[segment.id].update(
                    speaker_id=segment.speaker_id, start=segment.start, end=segment.end,
                )
        from video_translator.config.gemini import GEMINI_PROVIDER_NAME
        from video_translator.config.deepseek import DEEPSEEK_PROVIDER_NAME
        from video_translator.config.opencode_go import OPENCODE_GO_PROVIDER_NAME
        from video_translator.config.web_translation import CHATGPT_WEB_PROVIDER_NAME
        if candidate.metadata.get("provider") in {GEMINI_PROVIDER_NAME, DEEPSEEK_PROVIDER_NAME, OPENCODE_GO_PROVIDER_NAME, CHATGPT_WEB_PROVIDER_NAME}:
            from video_translator.steps.step3_translation.quality import assess_manual_segments
            payload.setdefault("workflow", {})["review_issues"] = assess_manual_segments(
                edited_segments, self.target_language,
            )
            payload["skipped_translation_ids"] = [
                item.id for item in edited_segments if not item.translated_text
            ]

        edited_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            previous_edit_count = int(payload.get("manual_edit_count", 0) or 0)
        except (TypeError, ValueError):
            previous_edit_count = 0
        edit_count = previous_edit_count + 1
        payload["manually_edited_at"] = edited_at
        payload["manual_edit_count"] = edit_count
        temporary_path = path.with_suffix(f"{path.suffix}.part")
        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

        self.results[StepId.TRANSLATE] = result
        result.segments = edited_segments
        for key in ("workflow", "skipped_translation_ids"):
            if key in payload:
                candidate.metadata[key] = payload[key]
                result.metadata[key] = payload[key]
        candidate.metadata["manually_edited_at"] = edited_at
        candidate.metadata["manual_edit_count"] = edit_count
        result.metadata["manually_edited_at"] = edited_at
        result.metadata["manual_edit_count"] = edit_count
        self.statuses[StepId.TRANSLATE] = StepStatus.DONE
        self.invalidate_from(StepId.TTS)
        self.statuses[StepId.TTS] = StepStatus.READY
        self.step_changed.emit(StepId.TRANSLATE.value)
        self.step_changed.emit(StepId.TTS.value)
        self.project_changed.emit()
        self.save_project()

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
        if StepId.STT not in self.results or self.statuses[StepId.STT] != StepStatus.DONE:
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
                    speaker_id=str(item.get("speaker_id", "")),
                    merge_parts=[
                        dict(part) for part in item.get("merge_parts", []) if isinstance(part, dict)
                    ],
                    source_text=str(item.get("source_text", "")),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                    synced_audio_file=str(item.get("synced_audio_file", "")),
                )
                for index, item in enumerate(payload.get("segments", []), start=1)
            ]
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not segments or any(
            segment.end > segment.start and not segment.translated_text.strip()
            for segment in segments
        ):
            return None
        metadata = {
            **candidate.metadata,
            **{key: value for key, value in payload.items() if key != "segments"},
            "translation_candidate_id": candidate.id,
        }
        artifacts = {"translation": str(path)}
        dialogue_profile_path = str(metadata.get("dialogue_profile_path", "")).strip()
        if dialogue_profile_path and Path(dialogue_profile_path).is_file():
            artifacts["dialogue_profile"] = dialogue_profile_path
        summary = candidate.summary or f"Bản dịch {candidate.label} · {len(segments)} segment"
        return StepResult(
            step=StepId.TRANSLATE,
            summary=summary,
            artifacts=artifacts,
            segments=segments,
            metadata=metadata,
        )

