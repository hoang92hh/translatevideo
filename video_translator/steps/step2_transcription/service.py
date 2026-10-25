from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from ...config.diarization import configured_model_path, missing_model_files
from ...errors import UserFacingError

ProgressCallback = Callable[[int, str], None]

SPEAKER_CHANGE_MIN_DURATION_SECONDS = 0.8
SPEAKER_CHANGE_MIN_WORDS = 2
RECOVERY_MAX_MAIN_OVERLAP_SECONDS = 0.1
RECOVERY_FALLBACK_GAP_SECONDS = 0.2


class SpeechToTextError(UserFacingError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker_id: str = ""
    merge_parts: tuple[dict[str, object], ...] = ()


TIMELINE_ISSUE_LABELS = {
    "invalid_interval": "Start/End không hợp lệ",
    "non_increasing_start": "Start trùng hoặc sai thứ tự",
    "overlap": "Chồng thời gian",
}


def assess_transcript_timeline(segments) -> list[dict[str, object]]:
    """Collect editable timing errors without changing any transcript content."""
    flags: dict[int, set[str]] = {}
    valid = []
    for index, item in enumerate(segments, start=1):
        segment_id = getattr(item, "id", index)
        if (not math.isfinite(item.start) or not math.isfinite(item.end)
                or item.start < 0 or item.end <= item.start):
            flags.setdefault(segment_id, set()).add("invalid_interval")
        else:
            valid.append((segment_id, item))
        if index > 1 and item.start <= segments[index - 2].start:
            for affected in (segment_id, getattr(segments[index - 2], "id", index - 1)):
                flags.setdefault(affected, set()).add("non_increasing_start")
    active = []
    for segment_id, item in sorted(valid, key=lambda pair: pair[1].start):
        active = [(previous_id, previous) for previous_id, previous in active if previous.end > item.start]
        for previous_id, _ in active:
            flags.setdefault(previous_id, set()).add("overlap")
            flags.setdefault(segment_id, set()).add("overlap")
        active.append((segment_id, item))
    return [{"id": getattr(item, "id", index), "issues": sorted(flags[getattr(item, "id", index)])}
            for index, item in enumerate(segments, start=1) if getattr(item, "id", index) in flags]


@dataclass(frozen=True, slots=True)
class Transcription:
    segments: list[TranscriptSegment]
    language: str
    language_probability: float | None
    duration_seconds: float | None
    requested_device: str
    actual_device: str
    compute_type: str
    device_selection_reason: str
    diarization_model: str = ""
    diarization_device: str = ""
    diarization_turns: tuple[dict[str, object], ...] = ()
    asr_recovery: dict[str, object] | None = None


class SpeechToTextService:
    def __init__(
        self,
        model_name: str,
        device: str,
        language: str | None,
        vad_filter: bool,
        diarization: bool = False,
        merge_adjacent_segments: bool = False,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.language = language
        self.vad_filter = vad_filter
        self.diarization = diarization
        self.merge_adjacent_segments = merge_adjacent_segments

    @staticmethod
    def model_cache_dir() -> Path:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".cache"
        path = root / "TransLanguage" / "faster-whisper-models"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def transcribe(
        self,
        input_audio: str | Path,
        progress: ProgressCallback | None = None,
    ) -> Transcription:
        source = Path(input_audio)
        if not source.is_file():
            raise SpeechToTextError(
                "Không tìm thấy audio đầu vào",
                f"File không tồn tại: {source}",
                "Chọn lại Voice hoặc Original Mix còn tồn tại từ Step 1.",
            )
        request = {
            "input_audio": str(source.resolve()),
            "model_name": self.model_name,
            "model_cache_dir": str(self.model_cache_dir()),
            "requested_device": self.device,
            "language": self.language,
            "vad_filter": self.vad_filter,
        }
        stt_progress = (
            (lambda value, message: progress(round(value * 0.58), message))
            if progress and self.diarization
            else progress
        )
        result = self._run_worker(request, stt_progress)
        raw_segments = result.get("segments")
        if not isinstance(raw_segments, list):
            raise SpeechToTextError(
                "Kết quả Faster Whisper không hợp lệ",
                "Worker không trả về danh sách segment hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            )
        try:
            normalized = [
                {
                    "start": float(item["start"]),
                    "end": float(item["end"]),
                    "text": str(item["text"]),
                    "words": list(item.get("words", [])),
                }
                for item in raw_segments
            ]
            raw_recovered = result.get("recovered_segments", [])
            if not isinstance(raw_recovered, list):
                raw_recovered = []
            normalized_recovered = []
            recovery_fallback_indices: set[int] = set()
            recovery_input_warnings: list[dict[str, object]] = []
            recovery_info = result.get("asr_recovery", {})
            timing_audit = recovery_info.get("main_timing_audit", []) if isinstance(recovery_info, dict) else []
            for item in raw_recovered:
                start = float(item["start"])
                end_value = item.get("end")
                end = float(end_value) if end_value is not None else None
                if end is None or not math.isfinite(end) or end <= start:
                    recovery_fallback_indices.add(len(normalized_recovered))
                    # Only a missing/invalid Recovery end needs a substitute.
                    # Prefer the original enclosing Main interval when available.
                    parents = [
                        (float(record["before_start"]), float(record["before_end"]))
                        for record in timing_audit
                        if isinstance(record, dict)
                        and record.get("before_start") is not None
                        and record.get("before_end") is not None
                        and float(record["before_start"]) <= start < float(record["before_end"])
                    ]
                    if not parents:
                        parents = [
                            (float(main["start"]), float(main["end"]))
                            for main in normalized
                            if float(main["start"]) <= start < float(main["end"])
                        ]
                    if not parents:
                        # Keep the utterance as an explicitly invalid interval
                        # for manual repair, never invent a usable duration.
                        if end is None or not math.isfinite(end):
                            end = start
                        recovery_input_warnings.append({
                            "stage": "segment", "start": start, "end": end,
                            "text": str(item["text"]),
                            "reason": "missing_recovery_end_requires_manual_timing",
                        })
                    else:
                        end = max(parents, key=lambda interval: interval[0])[1]
                normalized_recovered.append({
                    "start": start,
                    "end": end,
                    "text": str(item["text"]),
                    "words": list(item.get("words", [])),
                    "alignment_start": float(item["alignment_start"] if item.get("alignment_start") is not None else start),
                    "alignment_end": float(item["alignment_end"] if item.get("alignment_end") is not None else end),
                    "before_start": float(item["before_start"] if item.get("before_start") is not None else start),
                    "before_end": float(item["before_end"] if item.get("before_end") is not None else end),
                })
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechToTextError(
                "Kết quả Faster Whisper không hợp lệ",
                "Một hoặc nhiều segment thiếu timestamp hoặc nội dung hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            ) from exc
        if not normalized:
            raise SpeechToTextError(
                "Không phát hiện lời nói",
                "Faster Whisper đã xử lý xong nhưng không tạo được segment nào.",
                "Thử chọn Original Mix, tắt Voice activity detection hoặc kiểm tra lại audio từ Step 1.",
            )
        diarization_model = ""
        diarization_device = ""
        diarization_turns: tuple[dict[str, object], ...] = ()
        if self.diarization:
            model_path = configured_model_path().resolve()
            missing = missing_model_files(model_path)
            if missing:
                raise SpeechToTextError(
                    "Model speaker diarization chưa sẵn sàng",
                    f"Không thể dùng model local tại: {model_path}",
                    "Mở Cài đặt → API & Providers → Speaker diarization — Local để chọn đúng thư mục model.",
                    "Thiếu hoặc không hợp lệ:\n" + "\n".join(missing),
                )
            if progress:
                progress(60, "Đang chuẩn bị speaker diarization…")
            diarization_device = "GPU" if "GPU" in str(result.get("actual_device", "")) else "CPU"
            diarization_progress = (
                (lambda value, message: progress(60 + round(value * 0.38), message))
                if progress
                else None
            )
            try:
                diarization_result = self._run_diarization_worker(
                    source,
                    model_path,
                    diarization_device,
                    diarization_progress,
                )
            except SpeechToTextError:
                if self.device != "Auto" or diarization_device != "GPU":
                    raise
                if progress:
                    progress(60, "Diarization GPU lỗi; Auto đang thử lại bằng CPU…")
                diarization_result = self._run_diarization_worker(
                    source,
                    model_path,
                    "CPU",
                    diarization_progress,
                )
            turns = diarization_result.get("turns")
            if not isinstance(turns, list) or not turns:
                raise SpeechToTextError(
                    "Không xác định được người nói",
                    "Speaker diarization không trả về khoảng lời nói hợp lệ.",
                    "Kiểm tra file Voice và thư mục model local rồi chạy lại Step 2.",
                )
            segments = self._segments_with_speakers(
                normalized,
                turns,
                merge_adjacent_segments=self.merge_adjacent_segments,
            )
            diarization_model = str(diarization_result.get("model", ""))
            diarization_device = str(diarization_result.get("actual_device", ""))
            diarization_turns = tuple(dict(turn) for turn in turns)
        else:
            segments = [
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item["text"]),
                )
                for item in normalized
            ]

        recovery_metadata = (
            dict(result.get("asr_recovery", {}))
            if isinstance(result.get("asr_recovery"), dict)
            else {"enabled": False}
        )
        overlap_warnings: list[dict[str, object]] = []
        retained_recovered: list[dict[str, object]] = []
        for item in normalized_recovered:
            # Keep overlaps for review using the full ASR word extent;
            # never cut or rebuild the utterance to make it fit.
            start = float(item["alignment_start"] if self.diarization else item["start"])
            end = float(item["alignment_end"] if self.diarization else item["end"])
            overlap = self._max_overlap(start, end, segments)
            if overlap > RECOVERY_MAX_MAIN_OVERLAP_SECONDS + 1e-9:
                overlap_warnings.append({
                    "stage": "word_alignment" if self.diarization else "segment",
                    "start": item["start"],
                    "end": item["end"],
                    "text": item["text"],
                    "main_overlap_seconds": round(overlap, 3),
                    "reason": "overlap_kept_for_content_and_acoustic_review",
                })
            retained_recovered.append(item)

        if self.diarization:
            recovered_segments = self._segments_with_speakers(
                retained_recovered,
                turns,
                merge_adjacent_segments=self.merge_adjacent_segments,
                source_id_offset=max(
                    (
                        int(part.get("source_id", 0))
                        for segment in segments
                        for part in segment.merge_parts
                    ),
                    default=0,
                ),
            )
        else:
            recovered_segments = [
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item["text"]),
                )
                for item in retained_recovered
            ]

        # Preserve an acoustic speaker if one exists. Only unknown Recovery
        # speakers get a symbolic label; Main labels are never rewritten here.
        recovered_segments = [
            replace(item, speaker_id="SPEAKER_RECOVERY")
            if item.speaker_id in {"", "SPEAKER_UNKNOWN"} else item
            for item in recovered_segments
        ]

        final_recovered: list[TranscriptSegment] = []
        for item in recovered_segments:
            overlap = self._max_overlap(item.start, item.end, segments)
            if overlap > RECOVERY_MAX_MAIN_OVERLAP_SECONDS + 1e-9:
                overlap_warnings.append({
                    "stage": "final_segment",
                    "start": item.start,
                    "end": item.end,
                    "text": item.text,
                    "main_overlap_seconds": round(overlap, 3),
                    "reason": "overlap_kept_after_speaker_assignment",
                })
            final_recovered.append(item)
        recovery_metadata["max_main_overlap_seconds"] = RECOVERY_MAX_MAIN_OVERLAP_SECONDS
        recovery_metadata["rejected_postprocess_overlap_count"] = 0
        recovery_metadata["rejected_postprocess_overlaps"] = []
        recovery_metadata["postprocess_overlap_warnings"] = overlap_warnings
        recovery_metadata["final_accepted_segment_count"] = len(final_recovered)

        main_audit = recovery_metadata.get("main_timing_audit", [])
        def source_index(item: TranscriptSegment, fallback: int) -> int:
            indices = [
                int(part["raw_index"]) for part in item.merge_parts
                if isinstance(part, dict) and "raw_index" in part
            ]
            return min(indices) if indices else fallback

        source_details: dict[int, dict[str, object]] = {}
        for index, item in enumerate(segments):
            origin = source_index(item, index)
            audit = main_audit[origin] if isinstance(main_audit, list) and origin < len(main_audit) else {}
            source_details[id(item)] = {
                "source": "main", "source_index": origin,
                "before_start": audit.get("before_start", item.start),
                "before_end": audit.get("before_end", item.end),
                "reason": audit.get("reason", "main_asr"),
                "action": audit.get("action", "kept"),
            }
        fallback_recovery_ids: set[int] = set()
        for index, item in enumerate(final_recovered):
            origin = source_index(item, index)
            raw = normalized_recovered[origin] if origin < len(normalized_recovered) else {}
            reliable_timing = self._has_reliable_word_timing(raw)
            if (
                origin in recovery_fallback_indices
                or not reliable_timing
                or self._max_overlap(item.start, item.end, segments) > 0
            ):
                fallback_recovery_ids.add(id(item))
            source_details[id(item)] = {
                "source": "recovery", "source_index": origin,
                "before_start": raw.get("before_start", item.start),
                "before_end": raw.get("before_end", item.end),
                "reason": "targeted_asr_recovery",
                "action": "kept",
            }
        recovery_main_pairs: dict[int, int] = {}
        for recovery in final_recovered:
            if id(recovery) not in fallback_recovery_ids:
                continue
            overlapping = [
                main for main in segments
                if min(main.end, recovery.end) > max(main.start, recovery.start)
            ]
            if overlapping:
                # The Recovery start anchors the split. A later Main is only
                # an end limit when the Recovery already begins inside a Main.
                containing_start = [main for main in overlapping if main.start < recovery.start]
                main = (
                    max(containing_start, key=lambda item: item.start)
                    if containing_start else min(overlapping, key=lambda item: item.start)
                )
                recovery_main_pairs[id(recovery)] = id(main)
        tagged = sorted(
            [(item, "main") for item in segments] + [(item, "recovery") for item in final_recovered],
            key=lambda pair: (pair[0].start, pair[0].end),
        )
        ordered = [item for item, _ in tagged]
        timeline_warnings = list(recovery_input_warnings)
        resolved = self._non_overlapping_timeline(
            ordered, recovery_main_pairs, timeline_warnings,
        )
        for original, adjusted in zip(ordered, resolved, strict=True):
            detail = dict(source_details[id(original)])
            if (adjusted.start, adjusted.end) != (original.start, original.end):
                detail["action"] = "adjusted"
                detail["reason"] = "recovery_main_pair_timing_fallback"
            source_details[id(adjusted)] = detail
        tagged = [
            (adjusted, origin)
            for adjusted, (_, origin) in zip(resolved, tagged, strict=True)
        ]
        segments = resolved
        recovery_metadata["postprocess_overlap_warnings"] = timeline_warnings
        recovery_metadata["max_main_overlap_seconds"] = max(
            (self._max_overlap(item.start, item.end, [main for main, source in tagged if source == "main"])
             for item, source in tagged if source == "recovery"), default=0.0,
        )
        recovery_metadata["final_segment_sources"] = [
            {
                "id": index, **source_details[id(item)],
                "after_start": item.start, "after_end": item.end,
            }
            for index, (item, source) in enumerate(tagged, start=1)
        ]

        probability = result.get("language_probability")
        duration = result.get("duration_seconds")
        return Transcription(
            segments=segments,
            language=str(result.get("language", "")),
            language_probability=float(probability) if probability is not None else None,
            duration_seconds=float(duration) if duration is not None else None,
            requested_device=str(result.get("requested_device", self.device)),
            actual_device=str(result.get("actual_device", "")),
            compute_type=str(result.get("compute_type", "default")),
            device_selection_reason=str(result.get("device_selection_reason", "")),
            diarization_model=diarization_model,
            diarization_device=diarization_device,
            diarization_turns=diarization_turns,
            asr_recovery=recovery_metadata,
        )

    @staticmethod
    def _has_reliable_word_timing(item: dict[str, object]) -> bool:
        """Use complete, ordered, confident ASR word timing as evidence."""
        words = item.get("words")
        if not isinstance(words, list) or not words:
            return False
        try:
            times = [(float(word["start"]), float(word["end"])) for word in words]
            confidence = [float(word.get("probability") or 0.0) for word in words]
            normalize = lambda value: "".join(character for character in str(value).casefold() if character.isalnum())
            return (
                all(math.isfinite(start) and math.isfinite(end) and start >= 0 and end > start
                    for start, end in times)
                and all(times[index][0] >= times[index - 1][1] - 0.25 for index in range(1, len(times)))
                and float(item["start"]) - 0.25 <= min(start for start, _ in times)
                and max(end for _, end in times) <= float(item["end"]) + 0.25
                and normalize(item.get("text", "")) != ""
                and normalize(item.get("text", "")) == normalize("".join(str(word.get("word", "")) for word in words))
                and all(math.isfinite(value) for value in confidence)
                and sum(confidence) / len(confidence) >= 0.4
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    @staticmethod
    def _non_overlapping_timeline(
        segments: list[TranscriptSegment],
        recovery_main_pairs: dict[int, int],
        warnings: list[dict[str, object]],
    ) -> list[TranscriptSegment]:
        """Adjust only a selected overlapping Recovery/Main pair at a time."""
        for index, item in enumerate(segments):
            if (
                not math.isfinite(item.start) or not math.isfinite(item.end)
                or item.start < 0 or item.end <= item.start
            ):
                warnings.append({"stage": "final_segment", "start": item.start, "end": item.end,
                                 "text": item.text, "reason": "invalid_interval_requires_manual_timing"})
                return list(segments)
            if index and item.start <= segments[index - 1].start:
                warnings.append({"stage": "final_segment", "start": item.start, "end": item.end,
                                 "text": item.text, "reason": "non_increasing_start_requires_manual_timing"})
                return list(segments)
        result = list(segments)
        positions = {id(item): index for index, item in enumerate(segments)}
        for recovery_id, main_id in sorted(recovery_main_pairs.items(), key=lambda pair: positions[pair[0]]):
            recovery_index, main_index = positions[recovery_id], positions[main_id]
            recovery, main = segments[recovery_index], result[main_index]
            warning = {
                "stage": "final_segment", "start": recovery.start, "end": recovery.end,
                "text": recovery.text,
            }
            main_end = min(main.end, recovery.start) if main_index < recovery_index else main.end
            # Only this Main and Recovery may change. Other rows provide fixed
            # boundaries; neither their start nor their end is moved for padding.
            lower = max(
                (main_end if index == main_index else item.end
                 for index, item in enumerate(result[:recovery_index])),
                default=recovery.start,
            )
            slot_start = max(recovery.start, lower)
            slot_end = min(
                recovery.end,
                segments[recovery_index + 1].start if recovery_index + 1 < len(segments) else recovery.end,
            )
            if main_end <= main.start or slot_end <= slot_start:
                warnings.append({**warning, "reason": "recovery_pair_requires_manual_timing"})
                continue
            # Both margins are taken from Recovery, never from Main or neighbors.
            recovery_start = slot_start + RECOVERY_FALLBACK_GAP_SECONDS
            recovery_end = slot_end - RECOVERY_FALLBACK_GAP_SECONDS
            if recovery_end <= recovery_start:
                # Keep the available positive interval rather than delete text.
                recovery_start, recovery_end = slot_start, slot_end
                warnings.append({**warning, "reason": "insufficient_duration_for_recovery_fallback_gap"})
            updates = (
                (main_index, main, main.start, main_end),
                (recovery_index, recovery, recovery_start, recovery_end),
            )
            for index, item, start, end in updates:
                if (start, end) == (item.start, item.end):
                    continue
                parts = tuple({
                    **part, "start": start, "end": end,
                    "duration": round(end - start, 3),
                } for part in item.merge_parts)
                result[index] = replace(item, start=start, end=end, merge_parts=parts)
        # Never repair an unresolved collision by trimming an unrelated neighbor.
        for previous, following in zip(result, result[1:]):
            if previous.end > following.start:
                warnings.append({"stage": "final_segment", "start": following.start,
                                 "end": previous.end, "text": following.text,
                                 "reason": "overlap_requires_manual_timing"})
        return result

    @staticmethod
    def _max_overlap(start: float, end: float, segments: list[TranscriptSegment]) -> float:
        return max(
            (max(0.0, min(end, item.end) - max(start, item.start)) for item in segments),
            default=0.0,
        )

    @staticmethod
    def _speaker_for_interval(start: float, end: float, turns: list[dict[str, object]]) -> str:
        best_speaker = ""
        best_overlap = 0.0
        midpoint = (start + end) / 2.0
        for turn in turns:
            turn_start = float(turn.get("start", 0.0))
            turn_end = float(turn.get("end", turn_start))
            overlap = max(0.0, min(end, turn_end) - max(start, turn_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = str(turn.get("speaker_id", ""))
            elif not best_speaker and turn_start <= midpoint <= turn_end:
                best_speaker = str(turn.get("speaker_id", ""))
        return best_speaker or "SPEAKER_UNKNOWN"

    @staticmethod
    def _join_text(left: str, right: str) -> str:
        if not left:
            return right.strip()
        if not right:
            return left.strip()
        left_char = left.rstrip()[-1]
        right_char = right.lstrip()[0]
        cjk = lambda char: "\u3400" <= char <= "\u9fff"
        separator = "" if cjk(left_char) and cjk(right_char) else " "
        return f"{left.rstrip()}{separator}{right.lstrip()}"

    @staticmethod
    def _speaker_runs(units: list[dict[str, object]]) -> list[tuple[int, int]]:
        if not units:
            return []
        runs: list[tuple[int, int]] = []
        start = 0
        for index in range(1, len(units)):
            if units[index]["speaker_id"] != units[start]["speaker_id"]:
                runs.append((start, index))
                start = index
        runs.append((start, len(units)))
        return runs

    @staticmethod
    def _run_is_stable(
        units: list[dict[str, object]],
        start: int,
        end: int,
    ) -> bool:
        spoken_duration = sum(
            max(0.0, float(unit["end"]) - float(unit["start"]))
            for unit in units[start:end]
        )
        word_count = sum(1 for unit in units[start:end] if str(unit["text"]).strip())
        return (
            spoken_duration >= SPEAKER_CHANGE_MIN_DURATION_SECONDS
            or word_count >= SPEAKER_CHANGE_MIN_WORDS
        )

    @classmethod
    def _smooth_word_speakers(cls, units: list[dict[str, object]]) -> None:
        """Remove isolated speaker changes before they can create tiny segments."""
        if not units:
            return

        # Diarization can leave very small uncovered gaps. Attach those words to
        # the nearest known speaker instead of creating SPEAKER_UNKNOWN islands.
        for index, unit in enumerate(units):
            if unit["speaker_id"] != "SPEAKER_UNKNOWN":
                continue
            previous = next(
                (
                    candidate
                    for candidate in reversed(units[:index])
                    if candidate["speaker_id"] != "SPEAKER_UNKNOWN"
                ),
                None,
            )
            following = next(
                (
                    candidate
                    for candidate in units[index + 1 :]
                    if candidate["speaker_id"] != "SPEAKER_UNKNOWN"
                ),
                None,
            )
            if previous is None and following is None:
                continue
            if previous is None:
                unit["speaker_id"] = following["speaker_id"]
            elif following is None:
                unit["speaker_id"] = previous["speaker_id"]
            elif previous["speaker_id"] == following["speaker_id"]:
                unit["speaker_id"] = previous["speaker_id"]
            else:
                previous_gap = max(0.0, float(unit["start"]) - float(previous["end"]))
                following_gap = max(0.0, float(following["start"]) - float(unit["end"]))
                unit["speaker_id"] = (
                    previous["speaker_id"]
                    if previous_gap <= following_gap
                    else following["speaker_id"]
                )

        # A speaker change is accepted when it covers at least 0.8 seconds of
        # recognized speech OR at least two words. Short islands are attached
        # only to a stable neighboring run, preventing unstable runs from
        # repeatedly exchanging labels.
        for _ in range(len(units)):
            runs = cls._speaker_runs(units)
            stable = [cls._run_is_stable(units, start, end) for start, end in runs]
            changes: list[tuple[int, int, str]] = []
            for run_index, (start, end) in enumerate(runs):
                if stable[run_index]:
                    continue
                previous_index = run_index - 1 if run_index > 0 and stable[run_index - 1] else None
                following_index = (
                    run_index + 1
                    if run_index + 1 < len(runs) and stable[run_index + 1]
                    else None
                )
                if previous_index is None and following_index is None:
                    continue
                if previous_index is None:
                    target = str(units[runs[following_index][0]]["speaker_id"])
                elif following_index is None:
                    target = str(units[runs[previous_index][0]]["speaker_id"])
                else:
                    previous_run = runs[previous_index]
                    following_run = runs[following_index]
                    previous_speaker = str(units[previous_run[0]]["speaker_id"])
                    following_speaker = str(units[following_run[0]]["speaker_id"])
                    if previous_speaker == following_speaker:
                        target = previous_speaker
                    else:
                        previous_gap = max(
                            0.0,
                            float(units[start]["start"])
                            - float(units[previous_run[1] - 1]["end"]),
                        )
                        following_gap = max(
                            0.0,
                            float(units[following_run[0]]["start"])
                            - float(units[end - 1]["end"]),
                        )
                        target = previous_speaker if previous_gap <= following_gap else following_speaker
                changes.append((start, end, target))
            if not changes:
                break
            for start, end, target in changes:
                for unit in units[start:end]:
                    unit["speaker_id"] = target

    @classmethod
    def _segments_with_speakers(
        cls,
        raw_segments: list[dict[str, object]],
        turns: list[dict[str, object]],
        merge_adjacent_segments: bool = False,
        source_id_offset: int = 0,
    ) -> list[TranscriptSegment]:
        """Keep each ASR utterance intact and assign an acoustic speaker only.

        Word alignment must not replace, split, or duplicate the original text.
        The legacy merge option is accepted without merging ASR utterances.
        """
        result: list[TranscriptSegment] = []
        for raw_index, raw in enumerate(raw_segments):
            start = float(raw["start"])
            end = float(raw["end"])
            text = str(raw["text"])
            result.append(
                TranscriptSegment(
                    start=start,
                    end=end,
                    text=text,
                    speaker_id=cls._speaker_for_interval(start, end, turns),
                    merge_parts=({
                        "source_id": source_id_offset + raw_index + 1,
                        "raw_index": raw_index,
                        "start": start,
                        "end": end,
                        "duration": round(max(0.0, end - start), 3),
                        "text": text,
                        "ratio": 1.0,
                    },),
                )
            )
        return result

    def _run_diarization_worker(
        self,
        source: Path,
        model_path: Path,
        device: str,
        progress: ProgressCallback | None,
    ) -> dict[str, object]:
        request = {
            "input_audio": str(source.resolve()),
            "model_path": str(model_path),
            "device": device,
        }
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        command = [sys.executable, "-m", "video_translator.steps.step2_transcription.providers.faster_whisper.diarization_worker"]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=creation_flags,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()
            for raw_line in process.stdout:
                try:
                    event = json.loads(raw_line.strip())
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "progress" and progress:
                    progress(int(event.get("value", 0)), str(event.get("message", "")))
                elif event.get("type") == "result":
                    completed = event
                elif event.get("type") == "error":
                    worker_error = event
            return_code = process.wait()
            stderr_file.seek(0)
            stderr = stderr_file.read().strip()
        if worker_error or return_code != 0 or completed is None:
            detail = str((worker_error or {}).get("technical_detail", ""))
            if stderr:
                detail = f"{detail}\n\nWorker stderr:\n{stderr}".strip()
            message = str((worker_error or {}).get("message", "Worker kết thúc bất thường."))
            raise SpeechToTextError(
                "Speaker diarization thất bại",
                message,
                "Kiểm tra thư mục model local, FFmpeg và cấu hình GPU rồi thử lại.",
                detail,
            )
        return completed

    def _run_worker(
        self,
        request: dict[str, object],
        progress: ProgressCallback | None,
    ) -> dict[str, object]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        command = [sys.executable, "-m", "video_translator.steps.step2_transcription.providers.faster_whisper.worker"]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None

        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=creation_flags,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()

            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "progress" and progress:
                    progress(int(event.get("value", 0)), str(event.get("message", "")))
                elif event_type == "result":
                    result = event
                elif event_type == "error":
                    worker_error = event

            return_code = process.wait()
            stderr_file.seek(0)
            stderr = stderr_file.read().strip()

        if worker_error:
            if self.device == "Auto" and bool(worker_error.get("fallback_to_cpu", False)):
                if progress:
                    progress(5, "CUDA không thể khởi tạo; Auto đang thử lại bằng CPU…")
                fallback_reason = str(worker_error.get("message", "CUDA không thể khởi tạo."))
                fallback_request = {
                    **request,
                    "requested_device": "CPU",
                    "original_request": "Auto",
                    "fallback_reason": fallback_reason,
                }
                fallback_result = self._run_worker(fallback_request, progress)
                fallback_result["requested_device"] = "Auto"
                return fallback_result
            detail = str(worker_error.get("technical_detail", ""))
            if stderr:
                detail = f"{detail}\n\nWorker stderr:\n{stderr}".strip()
            raise SpeechToTextError(
                str(worker_error.get("title", "Faster Whisper không thể nhận dạng")),
                str(worker_error.get("message", "Worker Faster Whisper gặp lỗi.")),
                str(worker_error.get("suggestion", "")),
                detail,
            )
        if return_code != 0 or result is None:
            raise SpeechToTextError(
                "Worker Faster Whisper kết thúc bất thường",
                "Tiến trình nhận dạng không trả về kết quả hợp lệ.",
                "Kiểm tra dependency, model và cấu hình CPU/GPU rồi thử lại.",
                stderr or f"Worker exit code: {return_code}",
            )
        return result



