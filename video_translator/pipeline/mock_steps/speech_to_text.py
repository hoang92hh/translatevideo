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
from ...steps.step2_transcription.ocr_text import OcrTextService
from ...steps.step2_transcription.service import (
    SpeechToTextService, TranscriptSegment, assess_transcript_timeline,
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
    if provider != "Whisper":
        raise RuntimeError(f"{provider} hiện chưa được triển khai.")

    input_audio = Path(str(settings.get("input_audio", "")))
    model_name = str(settings["model"])
    requested_device = str(settings["device"])
    language_code = LANGUAGE_CODES.get(state.source_language)
    if language_code is None:
        raise ValueError(f"Ngôn ngữ nguồn của project chưa được hỗ trợ: {state.source_language}")
    batch_session = str(settings.get("_batch_model_session", ""))
    service = SpeechToTextService(
        model_name=model_name,
        device=requested_device,
        language=language_code,
        diarization=bool(settings["diarization"]),
        compute_type=str(settings["compute_type"]),
        batch_session=batch_session,
        diagnostics_dir=str(srt_path.parent / "diagnostics" / source_video.stem),
    )
    asr_progress = (
        (lambda value, message: progress(round(value * 0.7), message))
        if progress and settings["ocr_enabled"] else progress
    )
    transcription = service.transcribe(input_audio, asr_progress)
    ocr_result = None
    if not transcription.segments:
        raise ValueError("Không nhận diện được lời nói trong audio.")
    segments = transcription.segments
    if bool(settings["merge_contained_adjacent_segments"]):
        segments = _merge_contained_adjacent_segments(segments)
    cues: list[str] = []
    speaker_cues: list[str] = []
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
        if bool(settings["diarization"]):
            speaker_text = f"[{item.speaker_id or 'SPEAKER_UNKNOWN'}] {cue_text}" if cue_text else ""
            speaker_cues.append(
                f"{index}\n{_srt_timestamp(item.start)} --> {_srt_timestamp(item.end)}"
                f"\n{speaker_text}\n"
            )

    srt_path.parent.mkdir(parents=True, exist_ok=True)
    device_log_path = srt_path.with_name(f"{srt_path.stem}_step1_5_device.json")
    device_log = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_audio": str(input_audio),
        "model": model_name,
        "requested_device": requested_device,
        "actual_device": transcription.actual_device,
        "compute_type": transcription.compute_type,
        "device_selection_reason": transcription.device_selection_reason,
        "gpu_fallback": transcription.device_fallback,
    }
    report_path = str((transcription.asr_recovery or {}).get("report_path", ""))
    device_log["report_path"] = report_path
    temporary_path = srt_path.with_suffix(".srt.part")
    try:
        temporary_path.write_text("\n".join(cues), encoding="utf-8")
        temporary_path.replace(srt_path)
        if speaker_cues:
            speaker_path = srt_path.with_name(f"{srt_path.stem}_speaker.srt")
            speaker_temporary_path = speaker_path.with_suffix(".srt.part")
            try:
                speaker_temporary_path.write_text("\n".join(speaker_cues), encoding="utf-8")
                speaker_temporary_path.replace(speaker_path)
            except Exception:
                speaker_temporary_path.unlink(missing_ok=True)
                raise
        device_log_temporary_path = device_log_path.with_suffix(".json.part")
        try:
            device_log_temporary_path.write_text(
                json.dumps(device_log, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            device_log_temporary_path.replace(device_log_path)
        except Exception:
            device_log_temporary_path.unlink(missing_ok=True)
            raise
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    if settings["ocr_enabled"]:
        ocr_progress = (
            (lambda value, message: progress(70 + round(value * 0.28), message))
            if progress else None
        )
        ocr_result = OcrTextService(language_code, settings, batch_session).update_srt(
            source_video, srt_path, speaker_path if speaker_cues else None, ocr_progress,
        )

    detected = transcription.language or "không xác định"
    probability = (
        f" ({transcription.language_probability:.0%})"
        if transcription.language_probability is not None else ""
    )
    summary = (
        f"Đã tạo SRT với {len(cues)} mục · ngôn ngữ {detected}{probability} · "
        f"{model_name} · {transcription.actual_device}"
    )
    if ocr_result is not None:
        summary += f" · OCR {len(ocr_result.lines)} dòng text"
    recovery = transcription.asr_recovery or {}
    if recovery.get("status") == "needs_review":
        summary += (
            f" · cần rà soát {len(recovery.get('review_cue_ids', []))} cue, "
            f"{len(recovery.get('uncovered_speech', []))} vùng tiếng nghi thiếu"
        )
    metadata = {
        "srt_only": True,
        "input_candidate_id": state.selected_audio_candidate_id,
        "input_stem": state.selected_audio_stem,
        "language": transcription.language,
        "language_probability": transcription.language_probability,
        "requested_device": requested_device,
        "actual_device": transcription.actual_device,
        "device_log_path": str(device_log_path),
        "duration_seconds": transcription.duration_seconds,
        "srt_cue_count": len(cues),
        "compute_type": transcription.compute_type,
        "vad_enabled": True,
        "diarization": bool(settings["diarization"]),
        "asr_recovery": transcription.asr_recovery,
        "ocr_enabled": bool(settings["ocr_enabled"]),
        "ocr_interval_seconds": settings["ocr_interval_seconds"],
        "ocr_max_scans_per_side": settings["ocr_max_scans_per_side"],
        "ocr_region_normalized": settings["ocr_region_normalized"],
        "ocr_row_count": len(ocr_result.lines) if ocr_result is not None else 0,
        "ocr_scan_count": ocr_result.scan_count if ocr_result is not None else 0,
        "ocr_actual_device": ocr_result.actual_device if ocr_result is not None else "",
        "merge_contained_adjacent_segments": bool(settings["merge_contained_adjacent_segments"]),
    }
    return StepResult(
        step=StepId.STT,
        summary=summary,
        artifacts={
            "srt": str(srt_path),
            "device_log": str(device_log_path),
            "asr_report": report_path,
            **({"speaker_srt": str(speaker_path)} if speaker_cues else {}),
        },
        segments=[],
        metadata=metadata,
    )
