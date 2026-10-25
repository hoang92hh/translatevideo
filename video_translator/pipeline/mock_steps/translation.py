from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ...config.prompt_templates import DEFAULT_PROMPT_TEMPLATE_ID, load_prompt_template
from ...config.translation import PROPER_NAME_MODE_AUTO, resolve_proper_name_policy
from ...common.models import StepId, StepResult, TranslationCandidate
from ...errors import UserFacingError
from ...steps.step3_translation.service import CONTEXT_SEGMENT_COUNT, TranslationWorkflow
from ...steps.step3_translation.quality import (
    assess_gemini_segments, assess_segments, gemini_translation_groups,
)
from ...steps.step3_translation.providers import create_translation_provider
from ...state import ProjectState
from .common import previous_segments


def _collapse_same_speaker_runs(segments: list[Any]) -> None:
    """Keep IDs but place a contiguous same-speaker utterance in its first segment."""
    if not segments:
        return
    tolerance = 0.001
    index = 0
    while index < len(segments) - 1:
        first = segments[index]
        if not first.translated_text.strip() or first.duration <= 0:
            index += 1
            continue
        speaker = first.speaker_id.strip() or "SPEAKER_UNKNOWN"
        run_end = index
        while run_end + 1 < len(segments):
            current = segments[run_end]
            following = segments[run_end + 1]
            following_speaker = following.speaker_id.strip() or "SPEAKER_UNKNOWN"
            if following_speaker != speaker or following.duration <= 0:
                break
            if abs(following.start - current.end) > tolerance:
                break
            if not following.translated_text.strip():
                break
            run_end += 1
        if run_end == index:
            index += 1
            continue
        first.translated_text = " ".join(
            segments[position].translated_text.strip()
            for position in range(index, run_end + 1)
        ).strip()
        first.end = segments[run_end].end
        for position in range(index + 1, run_end + 1):
            merged = segments[position]
            merged.translated_text = ""
            merged.start = merged.end
        index = run_end + 1



def _apply_gemini_empty_results(
    segments: list[Any], originals: list[Any], batch_size: int,
) -> None:
    """Keep the existing empty-result rule and apply its consecutive time window."""
    by_id = {segment.id: segment for segment in originals}
    for segment in segments:
        original = by_id[segment.id]
        segment.start, segment.end = original.start, original.end
        if not segment.translated_text.strip():
            segment.start = segment.end
    for group in gemini_translation_groups(segments, batch_size):
        if len(group) < 2:
            continue
        first = group[0]
        sources = [by_id[segment.id] for segment in group]
        first.end = max(segment.end for segment in sources)
        parts = []
        for source in sources:
            parts.extend(deepcopy(source.merge_parts) or [{
                "source_id": source.id,
                "start": source.start, "end": source.end,
                "duration": source.duration, "text": source.source_text,
            }])
        total = sum(max(0.0, float(part.get("duration", 0.0))) for part in parts)
        for part in parts:
            part["ratio"] = (
                max(0.0, float(part.get("duration", 0.0))) / total
                if total else 1.0 / len(parts)
            )
        first.merge_parts = parts


def _review_gemini_segments(
    translation_provider: Any,
    segments: list[Any],
    originals: list[Any],
    batch_size: int,
    dialogue_profile: dict[str, object],
    proper_name_policy: dict[str, object],
    review_issues: list[dict[str, object]],
    target_language: str,
) -> tuple[list[dict[str, object]], int]:
    """Repair nonempty utterances with absorbed source fragments as read-only context."""
    groups = gemini_translation_groups(segments, batch_size)
    group_by_id = {group[0].id: group for group in groups}
    positions = {segment.id: index for index, segment in enumerate(originals)}
    current_by_id = {segment.id: segment for segment in segments}
    target_ids = {int(issue["id"]) for issue in review_issues}
    all_issues = list(review_issues)
    review_count = 0
    for batch_start in range(0, len(segments), batch_size):
        batch_ids = {segment.id for segment in segments[batch_start:batch_start + batch_size]}
        targets = target_ids & batch_ids & set(group_by_id)
        if not targets:
            continue
        context_positions: set[int] = set()
        for target_id in targets:
            group = group_by_id[target_id]
            first = positions[group[0].id]
            last = positions[group[-1].id]
            context_positions.update(range(
                max(0, first - CONTEXT_SEGMENT_COUNT),
                min(len(originals), last + CONTEXT_SEGMENT_COUNT + 1),
            ))
        context = []
        for position in sorted(context_positions):
            source = deepcopy(originals[position])
            current = current_by_id[source.id]
            source.translated_text = current.translated_text
            source.speaker_id = current.speaker_id
            if source.id in targets:
                source.end = max(
                    originals[positions[member.id]].end
                    for member in group_by_id[source.id]
                )
            context.append(source)
        requested = [segment for segment in context if segment.id in targets]
        issues = [issue for issue in review_issues if int(issue["id"]) in targets]
        feedback = ""
        for attempt in range(2):
            try:
                reviewed = translation_provider.review_translation(
                    requested, dialogue_profile, proper_name_policy,
                    batch_index=batch_start // batch_size + 1,
                    batch_total=max(1, (len(segments) + batch_size - 1) // batch_size),
                    context_segments=context,
                    quality_issues=issues,
                    validation_feedback=feedback,
                )
                TranslationWorkflow._validate_target_language(
                    reviewed.corrections, requested, target_language,
                )
                break
            except UserFacingError as exc:
                if attempt == 1:
                    raise
                feedback = exc.error_message.message + "\n" + exc.error_message.technical_detail
        for segment in requested:
            current = current_by_id[segment.id]
            current.translated_text = reviewed.corrections[segment.id]
            if segment.id in reviewed.corrected_speakers:
                current.speaker_id = reviewed.corrected_speakers[segment.id]
            if not current.translated_text.strip():
                current.start = current.end
        all_issues.extend(reviewed.issues)
        review_count += 1
    if review_count:
        all_issues.extend(assess_gemini_segments(segments, originals, batch_size))
    return all_issues, review_count


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
    is_gemini = provider == GEMINI_PROVIDER_NAME
    original_segments = deepcopy(segments) if is_gemini else []
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
    provider_options = dict(settings)
    if provider == GEMINI_PROVIDER_NAME:
        diagnostic_id = f"gemini-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        provider_options["diagnostics_root"] = state.workspace_path(
            "translations", "diagnostics", diagnostic_id
        )
    translation_provider = create_translation_provider(
        provider,
        model_name,
        state.source_language,
        state.target_language,
        provider_options,
    )
    workflow = TranslationWorkflow(translation_provider, batch_size)
    response = workflow.run(
        segments,
        False,
        proper_name_policy,
        progress,
        state.target_language,
    )
    for segment in segments:
        segment.translated_text = response.translations[segment.id]
        if not segment.translated_text.strip():
            segment.start = segment.end

    review_issues = (
        response.review_issues + assess_gemini_segments(segments, original_segments, batch_size)
        if is_gemini else assess_segments(segments)
    )
    review_ids = {int(item["id"]) for item in review_issues}
    review_batch_count = 0
    if is_gemini:
        review_issues, review_batch_count = _review_gemini_segments(
            translation_provider, segments, original_segments, batch_size,
            response.dialogue_profile, response.proper_name_policy,
            review_issues, state.target_language,
        )
    elif review_ids:
        review_segments = [segment for segment in segments if segment.id in review_ids]
        review_response = translation_provider.review_translation(
            review_segments,
            response.dialogue_profile,
            response.proper_name_policy,
        )
        for segment in review_segments:
            if segment.id in review_response.corrections:
                segment.translated_text = review_response.corrections[segment.id]
            if segment.id in review_response.corrected_speakers:
                segment.speaker_id = review_response.corrected_speakers[segment.id]
        review_issues.extend(review_response.issues)
        review_batch_count = 1
    if is_gemini:
        TranslationWorkflow._validate_target_language(
            {segment.id: segment.translated_text for segment in segments},
            original_segments, state.target_language,
        )
        _apply_gemini_empty_results(segments, original_segments, batch_size)
    else:
        _collapse_same_speaker_runs(segments)
    skipped_translation_ids = [
        segment.id
        for segment in segments
        if not segment.translated_text.strip()
    ]

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
        "skipped_translation_ids": skipped_translation_ids,
        "context_consistency": False,
        "dialogue_profile_path": str(profile_path),
        "dialogue_profile": response.dialogue_profile,
        "proper_name_policy": response.proper_name_policy,
        "workflow": {
            "analysis_batches": response.analysis_batch_count,
            "translation_batches": response.translation_batch_count,
            "review_batches": response.review_batch_count + review_batch_count,
            "review_issues": review_issues if is_gemini else response.review_issues + review_issues,
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
        "skipped_translation_ids": skipped_translation_ids,
        "context_consistency": False,
        "dialogue_profile": response.dialogue_profile,
        "proper_name_policy": response.proper_name_policy,
        "workflow": {
            "analysis_batches": response.analysis_batch_count,
            "translation_batches": response.translation_batch_count,
            "review_batches": response.review_batch_count + review_batch_count,
            "review_issues": review_issues if is_gemini else response.review_issues + review_issues,
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
