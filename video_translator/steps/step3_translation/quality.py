from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher

from ...common.models import Segment

_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")

MANUAL_ISSUE_LABELS = {
    "source_language_leftovers": "Còn chữ Trung Quốc",
    "similar_translation_with_neighbor": "Giống câu liền kề ≥70%",
    "contained_translation_with_neighbor": "Câu liền kề chứa nhau",
}


def _translation_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold())


def _contains_words(shorter: list[str], longer: list[str]) -> bool:
    return bool(shorter) and any(
        longer[index:index + len(shorter)] == shorter
        for index in range(len(longer) - len(shorter) + 1)
    )


def assess_manual_segments(
    segments: list[Segment], target_language: str = "Vietnamese",
) -> list[dict[str, object]]:
    """Produce advisory flags only; never change text, speakers, or timing."""
    issues: dict[int, list[str]] = {}
    related: dict[int, set[int]] = {}
    similarity: dict[int, float] = {}
    for segment in segments:
        text = segment.translated_text.strip()
        if text and target_language == "Vietnamese" and re.search(r"[\u3400-\u9fff\uf900-\ufaff]", text):
            issues.setdefault(segment.id, []).append("source_language_leftovers")
    # Empty merge/skip markers do not separate adjacent spoken translations.
    spoken = [segment for segment in segments if segment.translated_text.strip()]
    for first, second in zip(spoken, spoken[1:]):
        left, right = _translation_words(first.translated_text), _translation_words(second.translated_text)
        if not left or not right:
            continue
        # Ratio = 2 * matched words / total words, preserving their order.
        ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
        reasons = []
        if ratio >= 0.7:
            reasons.append("similar_translation_with_neighbor")
        if left != right and (_contains_words(left, right) or _contains_words(right, left)):
            reasons.append("contained_translation_with_neighbor")
        if reasons:
            for current, neighbor in ((first, second), (second, first)):
                issues.setdefault(current.id, []).extend(reasons)
                related.setdefault(current.id, set()).add(neighbor.id)
                similarity[current.id] = max(similarity.get(current.id, 0.0), ratio)
    return [{"id": segment.id, "issues": list(dict.fromkeys(issues[segment.id])),
             **({"related_ids": sorted(related[segment.id]),
                 "similarity_percent": round(similarity[segment.id] * 100, 1)}
                if segment.id in related else {})}
            for segment in segments if segment.id in issues]

# Review is text-only. Length can suggest an edit, never force loss of meaning.
GEMINI_REVIEW_ADVISORY_ISSUES = {"translation_too_long"}
GEMINI_REVIEW_CONTEXTUAL_ISSUES = {
    "missing_number", "duplicate_with_previous", "duplicate_with_next",
    "repeated_sentence_inside_translation", "overlapping_translation_with_neighbor",
}


def assess_segments(segments: list[Segment]) -> list[dict[str, object]]:
    """Run deterministic post-translation checks before any AI review."""
    issues: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for index, segment in enumerate(segments):
        segment_issues: list[str] = []
        text = segment.translated_text.strip()
        source = segment.source_text.strip()

        if segment.id in seen_ids:
            segment_issues.append("duplicate_id")
        seen_ids.add(segment.id)
        if segment.duration == 0 and not text:
            # Empty zero-duration segments are intentional merge/skip markers.
            continue
        if not text:
            segment_issues.append("empty_translation")
        elif source and text == source:
            # A non-Vietnamese source copied verbatim into the translation is
            # a deterministic translation failure and must enter AI review.
            segment_issues.append("translation_unchanged_from_source")
        elif len(text) < 2 and len(source) >= 4:
            segment_issues.append("translation_too_short")
        if segment.duration == 0 and text:
            segment_issues.append("zero_duration_with_translation")
            if index > 0:
                segments[index - 1].end = segment.end
        elif segment.duration > 0 and text:
            words_per_second = len(text.split()) / segment.duration
            if words_per_second > 4.5:
                segment_issues.append("reading_speed_too_high")
            if len(text.replace(" ", "")) / segment.duration > 18:
                segment_issues.append("translation_too_long")
        if _NUMBER_PATTERN.search(source) and not _NUMBER_PATTERN.search(text):
            segment_issues.append("missing_number")
        if index > 0 and text and text == segments[index - 1].translated_text.strip():
            segment_issues.append("duplicate_with_previous")
        if index + 1 < len(segments) and text and text == segments[index + 1].translated_text.strip():
            segment_issues.append("duplicate_with_next")
        if segment_issues:
            issues.append({"id": segment.id, "issues": segment_issues})
    return issues


def gemini_translation_groups(
    segments: list[Segment], batch_size: int, originals: list[Segment],
) -> list[list[Segment]]:
    """Group empty results only across exactly touching original timestamps."""
    by_id = {segment.id: segment for segment in originals}
    groups: list[list[Segment]] = []
    size = max(1, batch_size)
    for start in range(0, len(segments), size):
        current: list[Segment] | None = None
        for segment in segments[start:start + size]:
            if segment.translated_text.strip():
                current = [segment]
                groups.append(current)
            elif current is not None:
                if by_id[current[-1].id].end == by_id[segment.id].start:
                    current.append(segment)
                else:
                    current = None
    return groups


def assess_gemini_segments(
    segments: list[Segment], originals: list[Segment], batch_size: int,
) -> list[dict[str, object]]:
    """Assess complete utterances on copies; never mutate source/timeline."""
    by_id = {segment.id: segment for segment in originals}
    representatives: list[Segment] = []
    for group in gemini_translation_groups(segments, batch_size, originals):
        first = group[0]
        sources = [by_id[segment.id] for segment in group]
        representatives.append(replace(
            first,
            start=sources[0].start,
            end=max(segment.end for segment in sources),
            source_text=" ".join(segment.source_text for segment in sources),
        ))
    issues: list[dict[str, object]] = []
    for issue in assess_segments(representatives):
        reasons = [
            reason for reason in issue["issues"]
            if reason not in {"reading_speed_too_high", "zero_duration_with_translation"}
        ]
        if reasons:
            issues.append({"id": issue["id"], "issues": reasons})
    for segment in representatives:
        sentences = [
            sentence.strip().casefold()
            for sentence in re.split(r"(?<=[.!?…])\s+", segment.translated_text)
            if sentence.strip()
        ]
        if len(sentences) != len(set(sentences)):
            issues.append({"id": segment.id, "issues": ["repeated_sentence_inside_translation"]})
        source = re.sub(r"\s|[.!?。！？]", "", segment.source_text)
        if source == "死了" and re.search(r"\bchết\s+đi\b", segment.translated_text, re.IGNORECASE):
            issues.append({"id": segment.id, "issues": ["unsupported_imperative"]})
    for previous, following in zip(representatives, representatives[1:]):
        left = re.findall(r"\w+", previous.translated_text.casefold())
        right = re.findall(r"\w+", following.translated_text.casefold())
        if any(left[-size:] == right[:size] for size in range(4, min(len(left), len(right)) + 1)):
            for segment in (previous, following):
                issues.append({"id": segment.id, "issues": ["overlapping_translation_with_neighbor"]})
    return issues
