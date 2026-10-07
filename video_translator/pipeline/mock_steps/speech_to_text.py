from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...models import Segment, StepId, StepResult, TranscriptCandidate
from ...services import SpeechToTextService
from ...services.speech_to_text_service import (
    SPEAKER_CHANGE_MIN_DURATION_SECONDS,
    SPEAKER_CHANGE_MIN_WORDS,
)
from ...state import ProjectState


LANGUAGE_CODES = {
    "Chinese": "zh",
    "English": "en",
    "Vietnamese": "vi",
    "Japanese": "ja",
    "Korean": "ko",
}


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    provider = str(settings.get("provider", "Faster Whisper"))
    if provider != "Faster Whisper":
        raise RuntimeError(f"{provider} hiện chưa được triển khai.")

    input_audio = Path(str(settings.get("input_audio", "")))
    model_name = str(settings.get("model", "medium"))
    requested_device = str(settings.get("device", "Auto"))
    service = SpeechToTextService(
        model_name=model_name,
        device=requested_device,
        language=LANGUAGE_CODES.get(state.source_language),
        vad_filter=bool(settings.get("vad", True)),
        diarization=bool(settings.get("diarization", True)),
    )
    transcription = service.transcribe(input_audio, progress)
    segments = [
        Segment(
            id=index,
            start=item.start,
            end=item.end,
            speaker_id=item.speaker_id,
            merge_parts=[dict(part) for part in item.merge_parts],
            source_text=item.text,
        )
        for index, item in enumerate(transcription.segments, start=1)
    ]

    now = datetime.now(timezone.utc)
    candidate_id = f"stt-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    transcript_path = Path(state.workspace_path("transcripts", candidate_id, "transcript.json"))
    transcript_path.parent.mkdir(parents=True, exist_ok=False)
    payload = {
        "version": 3,
        "candidate_id": candidate_id,
        "created_at": now.isoformat(timespec="seconds"),
        "input_audio": str(input_audio.resolve()),
        "source_language": state.source_language,
        "language": transcription.language,
        "language_probability": transcription.language_probability,
        "model": model_name,
        "requested_device": requested_device,
        "actual_device": transcription.actual_device,
        "compute_type": transcription.compute_type,
        "device_selection_reason": transcription.device_selection_reason,
        "vad_filter": bool(settings.get("vad", True)),
        "diarization": bool(settings.get("diarization", True)),
        "diarization_model": transcription.diarization_model,
        "diarization_device": transcription.diarization_device,
        "diarization_turns": [dict(turn) for turn in transcription.diarization_turns],
        "diarization_smoothing": {
            "min_duration_seconds": SPEAKER_CHANGE_MIN_DURATION_SECONDS,
            "min_words": SPEAKER_CHANGE_MIN_WORDS,
        },
        "duration_seconds": transcription.duration_seconds,
        "segments": [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "speaker_id": segment.speaker_id,
                "merge_parts": segment.merge_parts,
                "text": segment.source_text,
            }
            for segment in segments
        ],
    }
    temporary_path = transcript_path.with_suffix(f"{transcript_path.suffix}.part")
    try:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(transcript_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        try:
            transcript_path.parent.rmdir()
        except OSError:
            pass
        raise

    detected = transcription.language or "không xác định"
    probability = (
        f" ({transcription.language_probability:.0%})"
        if transcription.language_probability is not None
        else ""
    )
    summary = (
        f"Đã nhận dạng {len(segments)} segment · ngôn ngữ {detected}{probability} · "
        f"{len({segment.speaker_id for segment in segments if segment.speaker_id})} speaker · "
        f"{model_name} · {transcription.actual_device}"
    )
    metadata = {
        **settings,
        "language": transcription.language,
        "language_probability": transcription.language_probability,
        "requested_device": requested_device,
        "actual_device": transcription.actual_device,
        "compute_type": transcription.compute_type,
        "device_selection_reason": transcription.device_selection_reason,
        "duration_seconds": transcription.duration_seconds,
        "diarization": bool(settings.get("diarization", True)),
        "diarization_model": transcription.diarization_model,
        "diarization_device": transcription.diarization_device,
        "diarization_smoothing": {
            "min_duration_seconds": SPEAKER_CHANGE_MIN_DURATION_SECONDS,
            "min_words": SPEAKER_CHANGE_MIN_WORDS,
        },
        "transcript_candidate_id": candidate_id,
        "recommended_transcript_candidate_id": candidate_id,
    }
    candidate = TranscriptCandidate(
        id=candidate_id,
        label=f"{model_name} · {transcription.actual_device} · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=now.isoformat(timespec="seconds"),
        path=str(transcript_path),
        segment_count=len(segments),
        summary=summary,
        metadata=metadata,
    )
    return StepResult(
        step=StepId.STT,
        summary=summary,
        artifacts={"transcript": str(transcript_path)},
        segments=segments,
        metadata=metadata,
        transcript_candidates=[candidate],
    )
