from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ...config.translation import PROPER_NAME_MODE_AUTO, resolve_proper_name_policy
from ...models import StepId, StepResult, TranslationCandidate
from ...services import TranslationWorkflow, create_translation_provider
from ...state import ProjectState
from .common import previous_segments


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    provider = str(settings.get("provider", GEMINI_PROVIDER_NAME))
    model_name = str(settings.get("model", GEMINI_DEFAULT_MODEL))
    batch_size = max(1, int(settings.get("batch_size", 30)))
    consistency_enabled = bool(settings.get("context_consistency", True))
    proper_name_mode = str(settings.get("proper_name_mode", PROPER_NAME_MODE_AUTO))
    proper_name_policy = resolve_proper_name_policy(
        state.source_language,
        state.target_language,
        proper_name_mode,
    )
    segments = previous_segments(state, StepId.TRANSLATE)
    translation_provider = create_translation_provider(
        provider,
        model_name,
        state.source_language,
        state.target_language,
    )
    workflow = TranslationWorkflow(translation_provider, batch_size)
    response = workflow.run(
        segments,
        consistency_enabled,
        proper_name_policy,
        progress,
    )
    for segment in segments:
        segment.translated_text = response.translations[segment.id]

    now = datetime.now(timezone.utc)
    candidate_id = f"translate-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    output_path = Path(state.workspace_path("translations", candidate_id, "translated_segments.json"))
    profile_path = Path(state.workspace_path("translations", candidate_id, "dialogue_profile.json"))
    output_path.parent.mkdir(parents=True, exist_ok=False)
    payload = {
        "version": 3,
        "candidate_id": candidate_id,
        "created_at": now.isoformat(timespec="seconds"),
        "source_transcript_candidate_id": state.selected_transcript_candidate_id,
        "source_language": state.source_language,
        "target_language": state.target_language,
        "provider": provider,
        "model": model_name,
        "batch_size": batch_size,
        "translation_strategy": (
            "full_dialogue_roles_v3" if consistency_enabled else "timing_aware_v1"
        ),
        "context_consistency": consistency_enabled,
        "dialogue_profile_path": str(profile_path),
        "dialogue_profile": response.dialogue_profile,
        "proper_name_policy": response.proper_name_policy,
        "workflow": {
            "analysis_batches": response.analysis_batch_count,
            "translation_batches": response.translation_batch_count,
            "review_batches": response.review_batch_count,
            "review_issues": response.review_issues,
        },
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
    profile_payload = {
        "version": 1,
        "candidate_id": candidate_id,
        "created_at": now.isoformat(timespec="seconds"),
        "source_transcript_candidate_id": state.selected_transcript_candidate_id,
        "source_language": state.source_language,
        "target_language": state.target_language,
        "provider": provider,
        "model": model_name,
        "analysis_performed": consistency_enabled,
        "proper_name_policy": response.proper_name_policy,
        "dialogue_profile": response.dialogue_profile,
    }
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.part")
    temporary_profile_path = profile_path.with_suffix(f"{profile_path.suffix}.part")
    try:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_profile_path.write_text(
            json.dumps(profile_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_profile_path.replace(profile_path)
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        temporary_profile_path.unlink(missing_ok=True)
        profile_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
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
        "translation_strategy": (
            "full_dialogue_roles_v3" if consistency_enabled else "timing_aware_v1"
        ),
        "context_consistency": consistency_enabled,
        "dialogue_profile": response.dialogue_profile,
        "proper_name_policy": response.proper_name_policy,
        "workflow": {
            "analysis_batches": response.analysis_batch_count,
            "translation_batches": response.translation_batch_count,
            "review_batches": response.review_batch_count,
            "review_issues": response.review_issues,
        },
        "dialogue_profile_path": str(profile_path),
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
        artifacts={
            "translation": str(output_path),
            "dialogue_profile": str(profile_path),
        },
        segments=segments,
        metadata=metadata,
        translation_candidates=[candidate],
    )
