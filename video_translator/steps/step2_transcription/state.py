from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from video_translator.errors import UserFacingError
from video_translator.common.models import STEP_ORDER, Segment, StepId, StepResult, StepStatus, TranscriptCandidate
from video_translator.steps.step2_transcription.service import assess_transcript_timeline
from video_translator.steps.step2_transcription.import_text import read_srt
from video_translator.steps.step2_transcription.import_text import read_txt
from video_translator.steps.step2_transcription.csv_exchange import import_segments as import_transcript_csv_segments

class TranscriptStateMixin:
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
            self.results[StepId.STT] = result
            has_errors = bool(result.metadata.get("timeline_error_count", 0))
            self.statuses[StepId.STT] = StepStatus.ERROR if has_errors else StepStatus.DONE
            if has_errors:
                self.invalidate_from(StepId.TRANSLATE)
            self.step_changed.emit(StepId.STT.value)
            self.project_changed.emit()
            self.save_project()
            return True
        self.results[StepId.STT] = result
        has_errors = bool(result.metadata.get("timeline_error_count", 0))
        self.statuses[StepId.STT] = StepStatus.ERROR if has_errors else StepStatus.DONE
        self.invalidate_from(StepId.TRANSLATE)
        if not has_errors:
            self.statuses[StepId.TRANSLATE] = StepStatus.READY
        self.step_changed.emit(StepId.STT.value)
        self.step_changed.emit(StepId.TRANSLATE.value)
        self.project_changed.emit()
        self.save_project()
        return True

    def import_transcript_text(self, file_path: str, source_candidate_id: str = "") -> TranscriptCandidate:
        if not self.project or not self.can_run(StepId.STT):
            raise ValueError("Cần có audio Step 1 hợp lệ trước khi nhập transcript vào Step 2.")
        source = Path(file_path)
        extension = source.suffix.lower()
        if extension not in {".txt", ".srt"}:
            raise ValueError("Chỉ hỗ trợ file .txt hoặc .srt.")
        if not source.is_file():
            raise ValueError(f"Không tìm thấy file: {source}")

        if extension == ".txt":
            original = self.transcript_candidate(source_candidate_id)
            if not original or not Path(original.path).is_file():
                raise ValueError("Nhập TXT cần một output transcript Step 2 đang được chọn.")
            payload = json.loads(Path(original.path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
                raise ValueError("Output transcript nguồn không có danh sách segment hợp lệ.")
            original_input = str(original.metadata.get("input_candidate_id", ""))
            if original_input and original_input != self.selected_audio_candidate_id:
                raise ValueError("Hãy chọn lại audio Step 1 tương ứng với transcript nguồn trước khi nhập TXT.")
            original_stem = str(original.metadata.get("input_stem", ""))
            if original_stem and original_stem != self.selected_audio_stem:
                raise ValueError("Hãy chọn lại đúng Voice hoặc Original Mix của transcript nguồn trước khi nhập TXT.")
            rows = read_txt(source, payload["segments"])
            base_metadata = dict(original.metadata)
            source_transcript_id = original.id
        else:
            rows = read_srt(source)
            payload = {
                "version": 3,
                "input_audio": self.audio_input_path(),
                "source_language": self.source_language,
                "language": self.source_language,
                "language_probability": None,
                "model": "SRT import",
                "requested_device": "",
                "actual_device": "",
                "compute_type": "",
                "device_selection_reason": "",
                "vad_filter": False,
                "segment_recovery": False,
                "diarization": False,
                "diarization_turns": [],
                "duration_seconds": max(row["end"] for row in rows),
                "asr_recovery": {"enabled": False},
            }
            base_metadata = {}
            source_transcript_id = ""

        now = datetime.now(timezone.utc)
        candidate_id = f"stt-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        folder = Path(self.workspace_path("transcripts", candidate_id))
        output_path = folder / "transcript.json"
        issues = assess_transcript_timeline([
            Segment(
                id=int(row["id"]), start=float(row["start"]), end=float(row["end"]),
                source_text=str(row["text"]),
            )
            for row in rows
        ])
        payload.update({
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            "imported_from_file": source.name,
            "import_format": extension[1:],
            "source_transcript_candidate_id": source_transcript_id,
            "timeline_issues": issues,
            "timeline_error_count": len(issues),
            "segments": rows,
        })
        folder.mkdir(parents=True, exist_ok=False)
        temporary = output_path.with_suffix(".json.part")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(output_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            folder.rmdir()
            raise

        summary = f"Đã nhập {extension.upper()} · {len(rows)} segment"
        metadata = {
            **base_metadata,
            **{key: value for key, value in payload.items() if key != "segments"},
            "input_candidate_id": self.selected_audio_candidate_id,
            "input_stem": self.selected_audio_stem,
            "transcript_candidate_id": candidate_id,
            "recommended_transcript_candidate_id": candidate_id,
        }
        candidate = TranscriptCandidate(
            id=candidate_id,
            label=f"{extension[1:].upper()} · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
            created_at=payload["created_at"], path=str(output_path),
            segment_count=len(rows), summary=summary, metadata=metadata,
        )
        self.transcript_candidates[candidate_id] = candidate
        if not self.select_transcript_candidate(candidate_id):
            self.transcript_candidates.pop(candidate_id, None)
            output_path.unlink(missing_ok=True)
            folder.rmdir()
            raise ValueError("Không thể nạp output transcript vừa tạo.")
        return candidate

    def import_transcript_csv(self, source_candidate_id: str, csv_path: str) -> TranscriptCandidate:
        source_candidate = self.transcript_candidate(source_candidate_id)
        if not self.project or not source_candidate or not Path(source_candidate.path).is_file():
            raise ValueError("Không tìm thấy output Step 2 nguồn để nhập CSV.")
        source_payload = json.loads(Path(source_candidate.path).read_text(encoding="utf-8"))
        if not isinstance(source_payload, dict):
            raise ValueError("Output Step 2 nguồn không phải JSON object hợp lệ.")
        original_rows = source_payload.get("segments")
        if not isinstance(original_rows, list):
            raise ValueError("Output Step 2 nguồn không có danh sách segment hợp lệ.")
        rows = import_transcript_csv_segments(Path(csv_path), original_rows)
        issues = assess_transcript_timeline([
            Segment(
                id=int(row["id"]), start=float(row["start"]), end=float(row["end"]),
                source_text=str(row["text"]),
            )
            for row in rows
        ])

        now = datetime.now(timezone.utc)
        candidate_id = f"stt-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        folder = Path(self.workspace_path("transcripts", candidate_id))
        output_path = folder / "transcript.json"
        payload = {
            **source_payload,
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            "source_transcript_candidate_id": source_candidate_id,
            "imported_from_csv": Path(csv_path).name,
            "timeline_issues": issues,
            "timeline_error_count": len(issues),
            "segments": rows,
        }
        folder.mkdir(parents=True, exist_ok=False)
        temporary = output_path.with_suffix(".json.part")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(output_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            folder.rmdir()
            raise

        summary = f"Đã nhập CSV · {len(rows)} segment · {len(issues)} lỗi timeline"
        metadata = {
            **source_candidate.metadata,
            **{key: value for key, value in payload.items() if key != "segments"},
            "transcript_candidate_id": candidate_id,
            "recommended_transcript_candidate_id": candidate_id,
        }
        candidate = TranscriptCandidate(
            id=candidate_id,
            label=f"CSV · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
            created_at=payload["created_at"], path=str(output_path),
            segment_count=len(rows), summary=summary, metadata=metadata,
        )
        self.transcript_candidates[candidate_id] = candidate
        if not self.select_transcript_candidate(candidate_id):
            self.transcript_candidates.pop(candidate_id, None)
            output_path.unlink(missing_ok=True)
            folder.rmdir()
            raise ValueError("Không thể nạp output transcript vừa tạo từ CSV.")
        return candidate

    def save_transcript_candidate_edits(self, candidate_id: str, updates: dict) -> int:
        candidate = self.transcript_candidate(candidate_id)
        if not candidate or candidate_id != self.selected_transcript_candidate_id:
            raise UserFacingError("Chưa chọn transcript", "Chỉ lưu transcript đang dùng làm input Step 3.",
                                  "Chọn Dùng làm input Step 3 rồi mở lại popup.")
        result = self._result_from_transcript(candidate_id)
        if result is None or set(updates) != {item.id for item in result.segments}:
            raise UserFacingError("Transcript không khớp", "Danh sách ID đã thay đổi hoặc không đọc được file.",
                                  "Mở lại popup từ output cần sửa.")
        edited = []
        for item in result.segments:
            try:
                start, end = float(updates[item.id]["start"]), float(updates[item.id]["end"])
                if not math.isfinite(start) or not math.isfinite(end):
                    raise ValueError("Thời gian phải là số hữu hạn")
            except (KeyError, TypeError, ValueError) as exc:
                raise UserFacingError("Thời gian không hợp lệ", f"Kiểm tra Start/End của ID {item.id}.",
                                      "Nhập số giây.", str(exc)) from exc
            parts = item.merge_parts
            if (start, end) != (item.start, item.end):
                parts = [{**part, "start": start, "end": end,
                          "duration": max(0.0, end - start)} for part in parts]
            edited.append(replace(item, start=start, end=end, merge_parts=parts))
        path = Path(candidate.path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload["segments"]
            if len(rows) != len(edited) or {int(row["id"]) for row in rows} != set(updates):
                raise ValueError("Danh sách ID trong file đã thay đổi")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise UserFacingError("Không đọc được transcript", "File transcript không còn khớp.",
                                  "Mở lại output để kiểm tra.", str(exc)) from exc
        by_id = {item.id: item for item in edited}
        for row in rows:
            item = by_id[int(row["id"])]
            row.update(start=item.start, end=item.end, merge_parts=item.merge_parts)
            row.pop("updated_in_step_5", None)
        issues = assess_transcript_timeline(edited)
        payload["timeline_issues"] = issues
        payload["timeline_error_count"] = len(issues)
        payload["manually_edited_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        recovery = payload.get("asr_recovery", {})
        if isinstance(recovery, dict):
            original_by_id = {item.id: item for item in result.segments}
            for record in recovery.get("final_segment_sources", []):
                item = by_id.get(record.get("id"))
                if item and (item.start, item.end) != (original_by_id[item.id].start, original_by_id[item.id].end):
                    record.update(after_start=item.start, after_end=item.end,
                                  action="manually_adjusted", reason="manual_timeline_edit")
            sources = {record.get("id"): record.get("source")
                       for record in recovery.get("final_segment_sources", [])}
            recovery["max_main_overlap_seconds"] = max((
                max(0.0, min(main.end, recovered.end) - max(main.start, recovered.start))
                for main in edited if sources.get(main.id) == "main"
                for recovered in edited if sources.get(recovered.id) == "recovery"
            ), default=0.0)
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        candidate.metadata.update({key: value for key, value in payload.items() if key != "segments"})
        result.segments = edited
        result.metadata = {**result.metadata, **candidate.metadata}
        candidate.summary = result.summary = (
            f"Transcript {len(edited)} segment · "
            + (f"đã lưu, còn {len(issues)} segment lỗi timeline" if issues else "timeline hợp lệ")
        )
        self.results[StepId.STT] = result
        self.statuses[StepId.STT] = StepStatus.ERROR if issues else StepStatus.DONE
        self.invalidate_from(StepId.TRANSLATE)
        if not issues:
            self.statuses[StepId.TRANSLATE] = StepStatus.READY
        self.step_changed.emit(StepId.STT.value)
        self.step_changed.emit(StepId.TRANSLATE.value)
        self.project_changed.emit()
        self.save_project()
        return len(issues)

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

    def _restore_imported_srt_inputs(self) -> None:
        """Recover Step 1 when a selected SRT transcript still has its audio input."""
        if StepId.EXTRACT in self.results:
            return
        transcript = self.transcript_candidate(self.selected_transcript_candidate_id)
        audio = self.candidate(self.selected_audio_candidate_id)
        if not transcript or not audio or not Path(transcript.path).is_file():
            return
        metadata = transcript.metadata
        if (
            metadata.get("import_format") != "srt"
            or metadata.get("input_candidate_id") != audio.id
            or metadata.get("input_stem") != self.selected_audio_stem
        ):
            return
        selected_path = audio.stem_path(self.selected_audio_stem)
        original = next(
            (
                candidate for candidate in self.audio_candidates.values()
                if candidate.stem_path("original")
                and Path(candidate.stem_path("original")).is_file()
            ),
            None,
        )
        if not selected_path or not Path(selected_path).is_file() or not original:
            return
        self.results[StepId.EXTRACT] = StepResult(
            step=StepId.EXTRACT,
            summary="Đã khôi phục audio Step 1 từ output đã chọn",
            artifacts={"source_audio": original.stem_path("original"), **audio.stems},
            metadata={
                "recommended_candidate_id": audio.id,
                "recommended_stem": self.selected_audio_stem,
                "recovered_from_candidates": True,
            },
            audio_candidates=[original] if original.id == audio.id else [original, audio],
        )
        self.statuses[StepId.EXTRACT] = StepStatus.DONE

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
            issues = assess_transcript_timeline(current.segments)
            current.metadata.update(timeline_issues=issues, timeline_error_count=len(issues))
            self.statuses[StepId.STT] = StepStatus.ERROR if issues else StepStatus.DONE
            if issues:
                self.invalidate_from(StepId.TRANSLATE)
            return
        result = self._result_from_transcript(candidate.id)
        if result:
            self.results[StepId.STT] = result
            self.statuses[StepId.STT] = (
                StepStatus.ERROR if result.metadata.get("timeline_error_count", 0) else StepStatus.DONE
            )
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
            segments = []
            legacy_recovered = 0
            for index, item in enumerate(payload.get("segments", []), start=1):
                parts = [
                    dict(part) for part in item.get("merge_parts", []) if isinstance(part, dict)
                ]
                start = float(item.get("start", 0.0))
                end = float(item.get("end", 0.0))
                speaker_id = str(item.get("speaker_id", ""))
                if item.get("updated_in_step_5") and parts:
                    part_starts = [float(part["start"]) for part in parts if part.get("start") is not None]
                    part_ends = [float(part["end"]) for part in parts if part.get("end") is not None]
                    if part_starts and part_ends:
                        start, end = min(part_starts), max(part_ends)
                        legacy_recovered += 1
                    original_speaker = next(
                        (str(part.get("speaker_id", "")) for part in parts if part.get("speaker_id")),
                        "",
                    )
                    speaker_id = original_speaker or speaker_id
                segments.append(Segment(
                    id=int(item.get("id", index)),
                    start=start,
                    end=end,
                    speaker_id=speaker_id,
                    merge_parts=parts,
                    source_text=str(item.get("text", item.get("source_text", ""))),
                    translated_text=str(item.get("translated_text", "")),
                    audio_file=str(item.get("audio_file", "")),
                    synced_audio_file=str(item.get("synced_audio_file", "")),
                ))
        except (AttributeError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        metadata = {**candidate.metadata, **{key: value for key, value in payload.items() if key != "segments"}}
        metadata["transcript_candidate_id"] = candidate.id
        if legacy_recovered:
            metadata["legacy_step5_timeline_recovered"] = legacy_recovered
        issues = assess_transcript_timeline(segments)
        metadata.update(timeline_issues=issues, timeline_error_count=len(issues))
        summary = candidate.summary or f"Transcript {candidate.label} · {len(segments)} segment"
        return StepResult(
            step=StepId.STT,
            summary=summary,
            artifacts={"transcript": str(path)},
            segments=segments,
            metadata=metadata,
        )

