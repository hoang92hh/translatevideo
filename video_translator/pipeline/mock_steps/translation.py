from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ...config.prompt_templates import DEFAULT_PROMPT_TEMPLATE_ID, load_prompt_template
from ...config.translation import PROPER_NAME_MODE_AUTO, resolve_proper_name_policy
from ...common.models import StepId, StepResult, TranslationCandidate
from ...steps.step3_translation.service import TranslationWorkflow
from ...steps.step3_translation.providers import create_translation_provider
from ...state import ProjectState
from .common import previous_segments


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    provider = str(settings.get("provider", GEMINI_PROVIDER_NAME))
    model_name = str(settings.get("model", GEMINI_DEFAULT_MODEL))
    prompt_template_id = str(
        settings.get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID)
    ).strip() or DEFAULT_PROMPT_TEMPLATE_ID
    prompt_template = load_prompt_template(provider, prompt_template_id)
    configured_batch_size = max(1, int(settings.get("batch_size", 30)))
    proper_name_mode = str(settings.get("proper_name_mode", PROPER_NAME_MODE_AUTO))
    proper_name_policy = resolve_proper_name_policy(
        state.source_language,
        state.target_language,
        proper_name_mode,
    )
    segments = previous_segments(state, StepId.TRANSLATE)
    all_project_segments = bool(settings.get("all_project_segments", True))
    batch_size = (
        len(segments)
        if all_project_segments and segments
        else configured_batch_size
    )
    translation_strategy = (
        "project_file_single_batch_v1"
        if all_project_segments
        else "single_request_per_file_batch_v1"
    )
    translation_provider = create_translation_provider(
        provider,
        model_name,
        state.source_language,
        state.target_language,
        settings,
    )
    workflow = TranslationWorkflow(translation_provider, batch_size)
    response = workflow.run(
        segments,
        False,
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
        "prompt_template": prompt_template.template_id,
        "prompt_template_name": prompt_template.name,
        "project_url": str(settings.get("project_url", "")),
        "batch_size": batch_size,
        "configured_batch_size": configured_batch_size,
        "all_project_segments": all_project_segments,
        "translation_strategy": translation_strategy,
        "context_consistency": False,
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
        "prompt_template": prompt_template.template_id,
        "prompt_template_name": prompt_template.name,
        "project_url": str(settings.get("project_url", "")),
        "analysis_performed": False,
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
        "prompt_template": prompt_template.template_id,
        "prompt_template_name": prompt_template.name,
        "project_url": str(settings.get("project_url", "")),
        "batch_size": batch_size,
        "configured_batch_size": configured_batch_size,
        "all_project_segments": all_project_segments,
        "translation_strategy": translation_strategy,
        "context_consistency": False,
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
