"""Editable Vietnamese translation cleanup, independent of Step 1.5 OCR."""

from __future__ import annotations

import unicodedata
from dataclasses import replace

from ...common.models import Segment


# Keep these thresholds local so translation cleanup can be tuned independently.
SHORT_IDENTICAL_MAX_CHARACTERS = 3
MIN_BOUNDARY_OVERLAP_CHARACTERS = 1


def _comparison_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _longest_boundary_overlap(left: str, right: str) -> int:
    for size in range(min(len(left), len(right)), MIN_BOUNDARY_OVERLAP_CHARACTERS - 1, -1):
        if left.endswith(right[:size]):
            return size
    return 0


def _unmatched_text(text: str, matched_characters: int) -> str:
    normalized = unicodedata.normalize("NFC", text)
    positions = [index for index, char in enumerate(normalized) if char.isalnum()]
    return normalized[positions[matched_characters - 1] + 1:].lstrip()


def collapse_adjacent_translations(segments: list[Segment]) -> int:
    """Apply the Step 1.5 adjacent-pair rules to translated text in place."""
    active: list[int] = []
    collapsed = 0
    for index, segment in enumerate(segments):
        if not segment.translated_text.strip():
            continue
        while active:
            previous_index = active[-1]
            previous, current = segments[previous_index], segments[index]
            left = _comparison_text(previous.translated_text)
            right = _comparison_text(current.translated_text)
            if not left or not right:
                break
            if left == right:
                if len(left) <= SHORT_IDENTICAL_MAX_CHARACTERS:
                    break
                keep_previous = True
                merged_text = previous.translated_text
            elif overlap := _longest_boundary_overlap(left, right):
                keep_previous = True
                remainder = _unmatched_text(current.translated_text, overlap)
                merged_text = (
                    f"{previous.translated_text.rstrip()} {remainder}"
                    if _comparison_text(remainder) else previous.translated_text
                )
            elif left.startswith(right) or left.endswith(right):
                keep_previous = True
                merged_text = previous.translated_text
            elif right.startswith(left) or right.endswith(left):
                keep_previous = False
                merged_text = current.translated_text
            else:
                break

            start = min(previous.start, current.start)
            end = max(previous.end, current.end)
            collapsed += 1
            if keep_previous:
                segments[previous_index] = replace(
                    previous, start=start, end=end, translated_text=merged_text,
                )
                segments[index] = replace(
                    current, start=end, end=end, translated_text="",
                )
                break

            segments[index] = replace(current, start=start, end=end)
            segments[previous_index] = replace(
                previous, start=start, end=start, translated_text="",
            )
            active.pop()

        if segments[index].translated_text.strip():
            active.append(index)
    return collapsed
