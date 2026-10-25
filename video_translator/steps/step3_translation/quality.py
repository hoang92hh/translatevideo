from __future__ import annotations

import re

from ...common.models import Segment

_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")


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
