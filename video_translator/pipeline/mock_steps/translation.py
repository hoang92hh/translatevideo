from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ...models import StepId, StepResult, TranslationCandidate
from ...services import GoogleTranslationService
from ...state import ProjectState
from .common import previous_segments


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    provider = str(settings.get("provider", GEMINI_PROVIDER_NAME))
    if provider != GEMINI_PROVIDER_NAME:
        raise RuntimeError(f"{provider} hiện chưa được triển khai.")
    model_name = str(settings.get("model", GEMINI_DEFAULT_MODEL))
    batch_size = max(1, int(settings.get("batch_size", 30)))
    segments = previous_segments(state, StepId.TRANSLATE)
    service = GoogleTranslationService(model_name, state.source_language, state.target_language)
    response = service.translate(segments, batch_size, progress)
    for segment in segments:
        segment.translated_text = response.translations[segment.id]

    now = datetime.now(timezone.utc)
    candidate_id = f"translate-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    output_path = Path(state.workspace_path("translations", candidate_id, "translated_segments.json"))
    output_path.parent.mkdir(parents=True, exist_ok=False)
    payload = {
        "version": 1,
        "candidate_id": candidate_id,
        "created_at": now.isoformat(timespec="seconds"),
        "source_transcript_candidate_id": state.selected_transcript_candidate_id,
        "source_language": state.source_language,
        "target_language": state.target_language,
        "provider": provider,
        "model": model_name,
        "batch_size": batch_size,
        "translation_strategy": "timing_aware_v1",
        "segments": [
            {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "speaker_id": segment.speaker_id,
                "merge_parts": segment.merge_parts,
                "source_text": segment.source_text,
                "translated_text": segment.translated_text,
            }
            for segment in segments
        ],
    }
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.part")
    try:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        try:
            output_path.parent.rmdir()
        except OSError:
            pass
        raise

    summary = (
        f"Đã dịch {len(segments)} segment · {state.source_language} → {state.target_language} · "
        f"{model_name}"
    )
    metadata = {
        "provider": provider,
        "model": model_name,
        "batch_size": batch_size,
        "translation_strategy": "timing_aware_v1",
        "source_transcript_candidate_id": state.selected_transcript_candidate_id,
        "credential_source": response.credential_source,
        "translation_candidate_id": candidate_id,
        "recommended_translation_candidate_id": candidate_id,
    }
    candidate = TranslationCandidate(
        id=candidate_id,
        label=f"{model_name} · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=now.isoformat(timespec="seconds"),
        path=str(output_path),
        segment_count=len(segments),
        summary=summary,
        metadata=metadata,
    )
    return StepResult(
        step=StepId.TRANSLATE,
        summary=summary,
        artifacts={"translation": str(output_path)},
        segments=segments,
        metadata=metadata,
        translation_candidates=[candidate],
    )
