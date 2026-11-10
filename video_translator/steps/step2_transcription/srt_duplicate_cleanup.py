"""Remove boundary duplicates from Step 1.5 segments before writing SRT."""

from __future__ import annotations

from dataclasses import replace

from .service import TranscriptSegment


def _comparison_text(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def _boundary_overlap(left: str, right: str) -> int:
    return max(
        (size for size in range(1, min(len(left), len(right)) + 1)
         if left.endswith(right[:size])),
        default=0,
    )


def collapse_srt_segments(
    segments: list[TranscriptSegment], language: str,
) -> list[TranscriptSegment]:
    """Clear a segment covered by the previous suffix or next prefix."""
    del language  # The same comparison applies to every supported language.
    mapped = sorted(segments, key=lambda item: (item.start, item.end))
    for index, segment in enumerate(mapped):
        text = _comparison_text(segment.text)
        if not text:
            continue

        previous_index = next(
            (candidate for candidate in range(index - 1, -1, -1)
             if _comparison_text(mapped[candidate].text)),
            None,
        )
        if previous_index is not None:
            previous = mapped[previous_index]
            previous_text = _comparison_text(previous.text)
            if (previous_text != text
                    and _boundary_overlap(previous_text, text) / len(text) >= 0.9):
                original_start, original_end = segment.start, segment.end
                mapped[previous_index] = replace(previous, end=original_end)
                mapped[index] = replace(segment, end=original_start, text="")
                continue

        next_index = next(
            (candidate for candidate in range(index + 1, len(mapped))
             if _comparison_text(mapped[candidate].text)),
            None,
        )
        if next_index is None:
            continue
        following = mapped[next_index]
        following_text = _comparison_text(following.text)
        if (following_text != text
                and _boundary_overlap(text, following_text) / len(text) >= 0.9):
            original_start, original_end = segment.start, segment.end
            mapped[next_index] = replace(
                following, start=(original_start + original_end) / 2,
            )
            mapped[index] = replace(segment, start=original_end, end=original_end, text="")
    return mapped
