from __future__ import annotations

import math
import json
import unicodedata
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from uuid import uuid4

from ...common.models import Segment, StepId, StepResult, TranscriptCandidate
from ...config.step_defaults import with_defaults
from ...state import ProjectState
from ...steps.step2_transcription.import_text import read_srt
from ...steps.step2_transcription.service import (
    SpeechToTextService, TranscriptSegment, assess_transcript_timeline,
)
from ...steps.step2_transcription.ocr_subtitles import (
    SubtitleOcrService, align_hybrid_text, collapse_srt_segments,
    map_selective_ocr_text,
)


LANGUAGE_CODES = {
    "Chinese": "zh",
    "English": "en",
    "Vietnamese": "vi",
    "Japanese": "ja",
    "Korean": "ko",
}


def _srt_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _comparison_text(value: str) -> str:
    return "".join(
        char for char in value.casefold()
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def _has_language_script_mismatch(value: str, language: str) -> bool:
    """Flag alphabetic text written outside the source language's usual scripts."""
    def script(char: str) -> str:
        codepoint = ord(char)
        if (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x20000 <= codepoint <= 0x323AF
        ):
            return "Han"
        name = unicodedata.name(char, "")
        for candidate in ("LATIN", "HANGUL", "HIRAGANA", "KATAKANA"):
            if candidate in name:
                return candidate.title()
        return "Other"

    expected = {
        "zh": {"Han"},
        "en": {"Latin"},
        "vi": {"Latin"},
        "ja": {"Han", "Hiragana", "Katakana"},
        "ko": {"Han", "Hangul"},
    }.get(language, set())
    return any(
        char.isalpha() and script(char) not in expected
        for char in value
    )


def _merge_contained_adjacent_segments(
    segments: list[TranscriptSegment],
) -> list[TranscriptSegment]:
    merged: list[TranscriptSegment] = []
    for segment in segments:
        current = segment
        while merged:
            previous = merged[-1]
            previous_text = _comparison_text(previous.text)
            current_text = _comparison_text(current.text)
            if not previous_text or not current_text:
                break
            if previous_text not in current_text and current_text not in previous_text:
                break
            shorter_length = min(len(previous_text), len(current_text))
            seconds_per_character = 0.20 if shorter_length <= 4 else 0.18
            if current.start - previous.end > shorter_length * seconds_per_character:
                break
            longer = current if len(current_text) > len(previous_text) else previous
            current = replace(
                longer,
                start=min(previous.start, current.start),
                end=max(previous.end, current.end),
            )
            merged.pop()
        merged.append(current)
    return merged


def _convert_srt(state: ProjectState, srt_path: Path) -> StepResult:
    rows = read_srt(srt_path)
    segments = [
        Segment(
            id=int(row["id"]), start=float(row["start"]), end=float(row["end"]),
            speaker_id=str(row["speaker_id"]), source_text=str(row["text"]),
            merge_parts=list(row["merge_parts"]),
        )
        for row in rows
    ]
    issues = assess_transcript_timeline(segments)
    now = datetime.now(timezone.utc)
    candidate_id = f"stt-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    output_path = Path(state.workspace_path("transcripts", candidate_id, "transcript.json"))
    payload = {
        "version": 3,
        "candidate_id": candidate_id,
        "created_at": now.isoformat(timespec="seconds"),
        "input_audio": state.audio_input_path(),
        "input_candidate_id": state.selected_audio_candidate_id,
        "input_stem": state.selected_audio_stem,
        "source_language": state.source_language,
        "language": state.source_language,
        "model": "SRT import",
        "import_format": "srt",
        "imported_from_file": str(srt_path),
        "duration_seconds": max(segment.end for segment in segments),
        "timeline_issues": issues,
        "timeline_error_count": len(issues),
        "segments": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=False)
    temporary_path = output_path.with_suffix(".json.part")
    try:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        output_path.parent.rmdir()
        raise
    summary = f"Đã chuyển {srt_path.name} thành {len(segments)} segment"
    metadata = {
        **{key: value for key, value in payload.items() if key != "segments"},
        "transcript_candidate_id": candidate_id,
        "recommended_transcript_candidate_id": candidate_id,
    }
    candidate = TranscriptCandidate(
        id=candidate_id,
        label=f"SRT · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=payload["created_at"], path=str(output_path),
        segment_count=len(segments), summary=summary, metadata=metadata,
    )
    return StepResult(
        step=StepId.STT, summary=summary,
        artifacts={"transcript": str(output_path)},
        segments=segments, metadata=metadata,
        transcript_candidates=[candidate],
    )


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    settings = with_defaults(StepId.STT, settings)
    if state.project is None:
        raise ValueError("Chưa mở project để xử lý SRT.")
    source_video = Path(state.input_video).resolve()
    srt_path = source_video.parent.parent / "srt" / f"{source_video.stem}.srt"
    if not settings.get("_srt_only"):
        if not srt_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy SRT của video: {srt_path}. Hãy chạy Step 1.5 trước.")
        return _convert_srt(state, srt_path)

    provider = str(settings["provider"])
    if provider not in {"Stable Whisper", "Faster Whisper"}:
        raise RuntimeError(f"{provider} hiện chưa được triển khai.")

    input_audio = Path(str(settings.get("input_audio", "")))
    model_name = str(settings["model"])
    requested_device = str(settings["device"])
    language_code = LANGUAGE_CODES.get(state.source_language)
    if language_code is None:
        raise ValueError(f"Ngôn ngữ nguồn của project chưa được hỗ trợ: {state.source_language}")
    segmentation_mode = str(settings["segmentation_mode"])
    if segmentation_mode not in {"Whisper", "OCR", "Hybrid"}:
        raise ValueError(f"Cơ chế tạo SRT không hợp lệ: {segmentation_mode}. Hãy chọn lại trong thiết lập Step 2.")
    batch_session = str(settings.get("_batch_model_session", ""))
    matched_ocr = 0
    ocr_cue_count = 0
    if segmentation_mode in {"Whisper", "OCR", "Hybrid"}:
        vad_recovery = bool(settings["vad_recovery"])
        service = SpeechToTextService(
            model_name=model_name,
            device=requested_device,
            language=language_code,
            vad_filter=vad_recovery,
            segment_recovery=vad_recovery,
            diarization=bool(settings["diarization"]),
            segmentation_mode="Whisper",
            batch_session=batch_session,
        )
        asr_progress = (
            (lambda value, message: progress(round(value * 0.6), message))
            if progress and segmentation_mode in {"OCR", "Hybrid"} else progress
        )
        transcription = service.transcribe(input_audio, asr_progress)
    if segmentation_mode in {"OCR", "Hybrid"}:
        target_indices = (
            {
                index for index, segment in enumerate(transcription.segments)
                if _has_language_script_mismatch(segment.text, language_code)
            }
            if segmentation_mode == "OCR" else set()
        )
        ordered_segments = sorted(
            transcription.segments, key=lambda item: (item.start, item.end),
        )
        whisper_gaps = [
            (previous.end, current.start)
            for previous, current in zip(ordered_segments, ordered_segments[1:])
            if current.start > previous.end
        ] if segmentation_mode == "OCR" else []
        ocr = SubtitleOcrService(language_code, requested_device, batch_session)
        ocr_progress = (
            (lambda value, message: progress(60 + round(value * 0.4), message))
            if progress and segmentation_mode in {"OCR", "Hybrid"} else progress
        )
        ocr_result = ocr.extract(
            source_video, ocr_progress,
            [transcription.segments[index] for index in sorted(target_indices)]
            if segmentation_mode == "OCR" else transcription.segments,
            selective=segmentation_mode == "OCR",
            gap_intervals=whisper_gaps,
        )
        ocr_cue_count = len(ocr_result.segments)
        if segmentation_mode == "OCR":
            mapped, matched_ocr = map_selective_ocr_text(
                transcription.segments, ocr_result.segments, target_indices, language_code,
                gap_intervals=whisper_gaps,
            )
            transcription = replace(transcription, segments=mapped)
        else:
            if ocr_result.stable_subtitle:
                mapped, matched_ocr = align_hybrid_text(
                    transcription.segments, ocr_result.segments, language_code,
                )
                transcription = replace(transcription, segments=mapped)
    if not transcription.segments:
        raise ValueError("Không nhận diện được phụ đề ở vùng phía dưới video.")
    segments = transcription.segments
    if segmentation_mode == "Whisper" and bool(settings["merge_contained_adjacent_segments"]):
        segments = _merge_contained_adjacent_segments(segments)
    segments = collapse_srt_segments(segments, language_code)
    cues: list[str] = []
    for index, item in enumerate(segments, start=1):
        cue_text = " ".join(item.text.split())
        empty_point = not cue_text and item.start == item.end
        if (not math.isfinite(item.start) or not math.isfinite(item.end)
                or item.start < 0
                or (not empty_point and (item.end <= item.start or not cue_text
                    or round(item.end * 1000) <= round(item.start * 1000)))):
            raise ValueError(f"Không thể ghi mục SRT {index}: thời gian hoặc nội dung không hợp lệ.")
        cues.append(
            f"{index}\n{_srt_timestamp(item.start)} --> {_srt_timestamp(item.end)}"
            f"\n{cue_text}\n"
        )

    srt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = srt_path.with_suffix(".srt.part")
    try:
        temporary_path.write_text("\n".join(cues), encoding="utf-8")
        temporary_path.replace(srt_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    detected = transcription.language or "không xác định"
    probability = (
        f" ({transcription.language_probability:.0%})"
        if transcription.language_probability is not None else ""
    )
    summary = (
        f"Đã tạo SRT với {len(cues)} mục · ngôn ngữ {detected}{probability} · "
        f"{model_name} · {transcription.actual_device}"
    )
    if segmentation_mode in {"OCR", "Hybrid"}:
        summary += f" · OCR {matched_ocr}/{ocr_cue_count} phụ đề đã ghép"
        if not ocr_result.stable_subtitle:
            summary += f" · {ocr_result.reason}"
    metadata = {
        "srt_only": True,
        "input_candidate_id": state.selected_audio_candidate_id,
        "input_stem": state.selected_audio_stem,
        "language": transcription.language,
        "language_probability": transcription.language_probability,
        "requested_device": requested_device,
        "actual_device": transcription.actual_device,
        "duration_seconds": transcription.duration_seconds,
        "srt_cue_count": len(cues),
        "segmentation_mode": segmentation_mode,
        "ocr_cue_count": ocr_cue_count,
        "ocr_matched_count": matched_ocr,
        "ocr_stable_subtitle": (
            ocr_result.stable_subtitle if segmentation_mode in {"OCR", "Hybrid"} else None
        ),
        "ocr_reason": ocr_result.reason if segmentation_mode in {"OCR", "Hybrid"} else "",
        "merge_contained_adjacent_segments": bool(
            segmentation_mode == "Whisper" and settings["merge_contained_adjacent_segments"]
        ),
        "hybrid_merge": None,
    }
    return StepResult(
        step=StepId.STT,
        summary=summary,
        artifacts={"srt": str(srt_path)},
        segments=[],
        metadata=metadata,
    )
