from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ...models import Segment, StepId, StepResult
from ...services import SpeechToTextService
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
    )
    transcription = service.transcribe(input_audio, progress)
    segments = [
        Segment(
            id=index,
            start=item.start,
            end=item.end,
            source_text=item.text,
        )
        for index, item in enumerate(transcription.segments, start=1)
    ]

    transcript_path = Path(state.workspace_path("transcripts", "transcript.json"))
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
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
        "duration_seconds": transcription.duration_seconds,
        "segments": [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.source_text,
            }
            for segment in segments
        ],
    }
    temporary_path = transcript_path.with_suffix(f"{transcript_path.suffix}.part")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(transcript_path)

    detected = transcription.language or "không xác định"
    probability = (
        f" ({transcription.language_probability:.0%})"
        if transcription.language_probability is not None
        else ""
    )
    return StepResult(
        step=StepId.STT,
        summary=(
            f"Đã nhận dạng {len(segments)} segment · ngôn ngữ {detected}{probability} · "
            f"{model_name} · {transcription.actual_device}"
        ),
        artifacts={"transcript": str(transcript_path)},
        segments=segments,
        metadata={
            **settings,
            "language": transcription.language,
            "language_probability": transcription.language_probability,
            "requested_device": requested_device,
            "actual_device": transcription.actual_device,
            "compute_type": transcription.compute_type,
            "device_selection_reason": transcription.device_selection_reason,
            "duration_seconds": transcription.duration_seconds,
        },
    )
