"""Extract subtitle cues from video and align their text with ASR cues."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from .batch_session import run_batch_worker
from .service import TranscriptSegment


LANGUAGES = {"zh": "ch_sim", "en": "en", "vi": "vi", "ja": "ja", "ko": "ko"}
HAN_TEXT_MATCH_RATIO = 0.8
_WORKER = "video_translator.steps.step2_transcription.ocr_worker"


@dataclass(frozen=True)
class OcrResult:
    segments: list[TranscriptSegment]
    duration_seconds: float
    device: str
    stable_subtitle: bool = True
    reason: str = ""


class SubtitleOcrService:
    def __init__(self, language: str, device: str, batch_session: str = "") -> None:
        if language not in LANGUAGES:
            raise ValueError(f"Ngôn ngữ OCR không được hỗ trợ: {language}")
        self.language = language
        self.device = device
        self.batch_session = batch_session

    def extract(
        self, video: str | Path,
        progress: Callable[[int, str], None] | None = None,
        whisper: list[TranscriptSegment] | None = None,
    ) -> OcrResult:
        source = Path(video)
        if not source.is_file():
            raise FileNotFoundError(f"Không tìm thấy video để nhận diện phụ đề: {source}")
        request: dict[str, object] = {
            "input_video": str(source.resolve()),
            "language": LANGUAGES[self.language],
            "requested_device": self.device,
        }
        if whisper is not None:
            request["hybrid"] = True
            request["whisper_intervals"] = [
                {"start": item.start, "end": item.end} for item in whisper
            ]
        if self.batch_session:
            result, error, stderr, code = run_batch_worker(
                self.batch_session, "ocr", request, progress,
            )
        else:
            result, error, stderr, code = self._run_once(request, progress)
        if error or code != 0 or result is None:
            message = str((error or {}).get("message", "OCR không trả về kết quả."))
            detail = str((error or {}).get("technical_detail", stderr))
            raise RuntimeError(f"{message}\n{detail}".strip())
        segments = [
            TranscriptSegment(
                start=float(item["start"]), end=float(item["end"]),
                text=str(item["text"]),
                confidence=(float(item["confidence"]) if item.get("confidence") is not None else None),
            )
            for item in result["segments"]
        ]
        segments = _prepare_ocr_cues(segments, self.language)
        return OcrResult(
            segments=segments,
            duration_seconds=float(result["duration_seconds"]),
            device=str(result["actual_device"]),
            stable_subtitle=bool(result.get("stable_subtitle", True)),
            reason=str(result.get("reason", "")),
        )

    @staticmethod
    def _run_once(
        request: dict[str, object], progress: Callable[[int, str], None] | None,
    ) -> tuple[dict[str, object] | None, dict[str, object] | None, str, int]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
            process = subprocess.Popen(
                [sys.executable, "-m", _WORKER],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_file,
                text=True, encoding="utf-8", errors="replace", env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()
            result = error = None
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "progress" and progress:
                    progress(int(event["value"]), str(event["message"]))
                elif event.get("type") == "result":
                    result = event
                elif event.get("type") == "error":
                    error = event
            code = process.wait()
            stderr_file.seek(0)
            return result, error, stderr_file.read().strip(), code


def map_ocr_text(
    whisper: list[TranscriptSegment], ocr: list[TranscriptSegment], language: str = "",
) -> tuple[list[TranscriptSegment], int]:
    """Prefer OCR for aligned cues and cautiously reconcile unaligned OCR cues."""
    if not whisper:
        return [], 0

    ocr = _prepare_ocr_cues(ocr, language)

    assigned: list[list[TranscriptSegment]] = [[] for _ in whisper]
    unmatched: list[TranscriptSegment] = []
    for cue in ocr:
        overlaps = [
            max(0.0, min(item.end, cue.end) - max(item.start, cue.start))
            for item in whisper
        ]
        best_index = max(range(len(whisper)), key=overlaps.__getitem__)
        if overlaps[best_index] > 0:
            assigned[best_index].append(cue)
        else:
            unmatched.append(cue)

    separator = "" if language in {"zh", "ja", "ko"} else " "
    mapped = list(whisper)
    matched_count = 0
    for index, (item, cues) in enumerate(zip(whisper, assigned, strict=True)):
        if not cues:
            continue
        cues.sort(key=lambda cue: (cue.start, cue.end))
        if len(cues) == 1:
            selected_text = cues[0].text
            matched_count += 1
        else:
            blocks = _contiguous_ocr_blocks(cues, separator)
            selected_text = max(
                blocks,
                key=lambda block: _text_similarity(block, item.text),
            )
            matched_count += len(cues)
        mapped[index] = replace(item, text=_clean_internal_ocr_blocks(selected_text))

    # A second pass considers only OCR cues that did not overlap any Whisper cue.
    # Replace Whisper text only when the OCR wording is sufficiently similar.
    replaced_in_second_pass: set[int] = set()
    for cue in unmatched:
        nearby = []
        for index, item in enumerate(whisper):
            if assigned[index] or index in replaced_in_second_pass:
                continue
            distance = max(item.start - cue.end, cue.start - item.end, 0.0)
            if distance <= 1.0:
                nearby.append((_text_similarity(cue.text, item.text), -distance, index))
        if not nearby:
            continue
        similarity, _, index = max(nearby)
        if similarity > 0.70:
            mapped[index] = replace(mapped[index], text=cue.text)
            replaced_in_second_pass.add(index)
            matched_count += 1

    _collapse_adjacent_mapped_duplicates(mapped, separator)

    return mapped, matched_count


def _ordered_common_length(left: str, right: str) -> int:
    """Length of the longest ordered (possibly noncontiguous) common sequence."""
    previous = [0] * (len(right) + 1)
    for char in left:
        current = [0] * (len(right) + 1)
        for index, other in enumerate(right, start=1):
            current[index] = previous[index - 1] + 1 if char == other else max(
                previous[index], current[index - 1],
            )
        previous = current
    return previous[-1]


def _ocr_blocks(text: str) -> list[str]:
    return [
        part.strip() for part in _OCR_BLOCK_DELIMITERS.split(text)
        if _comparison_text(part)
    ]


def _contains_han(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in text)


def align_hybrid_text(
    whisper: list[TranscriptSegment], ocr: list[TranscriptSegment], language: str,
) -> tuple[list[TranscriptSegment], int]:
    """Replace Whisper text with OCR text; never move Whisper time.

    Every cue keeps the start/end produced by Whisper; only ``text`` changes.
    A Hán Whisper cue is replaced only by a block that matches it at least
    80% (measured on the longer string), so the OCR text is a reference rather
    than a replacement for merely similar wording. A non-Hán cue (Latin,
    digits, symbols) is replaced by the block of the OCR line that shares its
    timeline, because the two scripts cannot be compared character by
    character. The existing rule still drops Whisper-only cues that are numeric
    or symbol-only, or whose ASR confidence is very low.
    """
    cleaned = _prepare_ocr_cues(ocr, language)
    mapped = list(whisper)
    used: set[int] = set()
    matched = 0
    for cue in cleaned:
        overlaps = [
            index for index, item in enumerate(whisper)
            if min(item.end, cue.end) > max(item.start, cue.start)
        ]
        if not overlaps:
            continue
        if (cue.confidence or 0.0) < 0.62:
            continue
        blocks = _ocr_blocks(cue.text)
        if not blocks:
            continue
        target = max(
            overlaps,
            key=lambda index: min(whisper[index].end, cue.end)
            - max(whisper[index].start, cue.start),
        )
        item = whisper[target]
        if _contains_han(item.text):
            # Hán: block phải giống text Whisper >= 80%, tính trên chuỗi dài hơn.
            voice_key = _comparison_text(item.text)
            best: tuple[float, str] | None = None
            for block in blocks:
                block_key = _comparison_text(block)
                if not block_key:
                    continue
                ratio = _ordered_common_length(block_key, voice_key) / max(
                    1, len(block_key), len(voice_key),
                )
                if best is None or ratio > best[0]:
                    best = (ratio, block)
            if best is None or best[0] < HAN_TEXT_MATCH_RATIO:
                continue
            selected = best[1]
        else:
            # Không phải chữ Hán (Latin, số, ký hiệu): thay theo timeline mapping.
            selected = max(blocks, key=len)
        # Whisper owns the timeline: only the text of the target cue changes.
        # The interval is never expanded and no overlapping cue is absorbed,
        # so start/end stay exactly as produced by Whisper.
        mapped[target] = replace(mapped[target], text=selected)
        used.update(overlaps)
        matched += 1
    for index, item in enumerate(mapped):
        if index in used or item.text == COLLAPSED_SEGMENT_TEXT:
            continue
        key = _comparison_text(item.text)
        if not any(char.isalpha() for char in item.text) or (
            item.confidence is not None and item.confidence < 0.2
        ):
            mapped[index] = replace(item, start=item.end, text=COLLAPSED_SEGMENT_TEXT)
    mapped.sort(key=lambda item: (item.start, item.end))
    return mapped, matched


COLLAPSED_SEGMENT_TEXT = " "
_SENTENCE_PUNCTUATION = frozenset(",.!?;:'\"，。！？；：、…—–-“”‘’")
_TRAILING_OCR_NUMBER = re.compile(
    r"(?<=[。！？!?])\s*[0-9]{1,2}\s*[:：!！?,，.。;；]*$"
)


def _prepare_ocr_cues(
    cues: list[TranscriptSegment], language: str,
) -> list[TranscriptSegment]:
    cleaned = _clean_ocr_cues(cues)
    for index, cue in enumerate(cleaned):
        if cue.confidence is None or cue.confidence >= 0.75:
            continue
        clearer = [
            other for other in cleaned
            if other is not cue and other.confidence is not None
            and other.confidence >= 0.75
            and min(abs(other.start - cue.end), abs(cue.start - other.end)) <= 6.0
            and _text_similarity(cue.text, other.text) >= 0.7
        ]
        if clearer:
            best = max(clearer, key=lambda other: other.confidence or 0.0)
            cleaned[index] = replace(cue, text=best.text, confidence=best.confidence)
    merged = _deduplicate_adjacent_ocr_cues(cleaned, language)
    return [replace(cue, text=_clean_internal_ocr_blocks(cue.text)) for cue in merged]


def _clean_ocr_cues(cues: list[TranscriptSegment]) -> list[TranscriptSegment]:
    cleaned: list[TranscriptSegment] = []
    for cue in cues:
        text = _clean_ocr_text(cue.text)
        if any(char.isalpha() for char in text):
            cleaned.append(replace(cue, text=text))
    return cleaned


def _clean_ocr_text(text: str) -> str:
    kept = "".join(
        char for char in text
        if char.isalnum()
        or unicodedata.category(char).startswith("M")
        or char.isspace()
        or char in _SENTENCE_PUNCTUATION
    )
    kept = re.sub(r"\s+", " ", kept).strip()
    kept = re.sub(r"\s+([,，。！？；：、.!?;:])", r"\1", kept)
    return _remove_trailing_ocr_number(kept)


def _remove_trailing_ocr_number(text: str) -> str:
    """Drop a short numeric overlay after a completed Chinese subtitle."""
    if not re.search(r"[\u3400-\u9fff]", text):
        return text
    return _TRAILING_OCR_NUMBER.sub("", text).rstrip()


_OCR_BLOCK_DELIMITERS = re.compile(r"([,，。.!?！？;；:：、]+)")


def _differs_by_at_most_one_character(first: str, second: str) -> bool:
    if abs(len(first) - len(second)) > 1:
        return False
    left = right = differences = 0
    while left < len(first) and right < len(second):
        if first[left] == second[right]:
            left += 1
            right += 1
            continue
        differences += 1
        if differences > 1:
            return False
        if len(first) > len(second):
            left += 1
        elif len(second) > len(first):
            right += 1
        else:
            left += 1
            right += 1
    return differences + len(first) - left + len(second) - right <= 1


def _clean_internal_ocr_blocks(text: str) -> str:
    """Keep one long repeated block, preferring length then the earlier reading."""
    parts = _OCR_BLOCK_DELIMITERS.split(text)
    blocks = [
        (parts[index], parts[index + 1] if index + 1 < len(parts) else "")
        for index in range(0, len(parts), 2)
        if _comparison_text(parts[index])
    ]
    while len(blocks) > 1:
        windows: list[tuple[int, int, str]] = []
        for start in range(len(blocks)):
            content = ""
            for end in range(start + 1, len(blocks) + 1):
                content += _comparison_text(blocks[end - 1][0])
                if len(content) >= 4:
                    windows.append((start, end, content))

        best: tuple[tuple[int, int, int, int, int], int, int] | None = None
        for first_start, first_end, first_text in windows:
            for second_start, second_end, second_text in windows:
                if second_start < first_end:
                    continue
                if not _differs_by_at_most_one_character(first_text, second_text):
                    continue
                score = (
                    max(len(first_text), len(second_text)),
                    min(len(first_text), len(second_text)),
                    int(first_text == second_text),
                    -first_start, -second_start,
                )
                remove_start, remove_end = (
                    (first_start, first_end)
                    if len(first_text) < len(second_text)
                    else (second_start, second_end)
                )
                if best is None or score > best[0]:
                    best = score, remove_start, remove_end
        if best is None:
            break
        del blocks[best[1]:best[2]]

    cleaned = "".join(content + punctuation for content, punctuation in blocks).strip()
    cleaned = re.sub(r"[,，]{2,}", lambda match: match.group()[-1], cleaned)
    cleaned = re.sub(r"[.。]{2,}", lambda match: match.group()[-1], cleaned)
    return _remove_trailing_ocr_number(re.sub(r"\s+", " ", cleaned))


def _deduplicate_adjacent_ocr_cues(
    cues: list[TranscriptSegment], language: str,
) -> list[TranscriptSegment]:
    """Merge adjacent OCR text at exact boundaries before Whisper alignment."""
    separator = "" if language in {"zh", "ja", "ko"} else " "
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end))
    result: list[TranscriptSegment] = []
    for cue in ordered:
        current = cue
        while result:
            previous = result[-1]
            gap = current.start - previous.end
            if gap > 0.6:
                break
            if current.end - previous.start > 6.5:
                break
            left = _comparison_text(previous.text)
            right = _comparison_text(current.text)
            if not left or not right:
                break
            start = min(previous.start, current.start)
            end = max(previous.end, current.end)

            if left == right:
                survivor = max(
                    (previous, current),
                    key=lambda item: (item.end - item.start, len(item.text)),
                )
                result.pop()
                current = replace(survivor, start=start, end=end)
                continue
            if left.startswith(right) or left.endswith(right):
                result.pop()
                current = replace(previous, start=start, end=end)
                continue
            if right.startswith(left) or right.endswith(left):
                result.pop()
                current = replace(current, start=start, end=end)
                continue

            overlap = _longest_suffix_prefix_overlap(left, right)
            if overlap:
                remainder = _remove_comparison_prefix(current.text, overlap)
                remainder_key = _comparison_text(remainder)
                text = previous.text
                if remainder_key and not (
                    left.startswith(remainder_key) or left.endswith(remainder_key)
                ):
                    text = separator.join((previous.text, remainder))
                result.pop()
                current = replace(previous, start=start, end=end, text=text)
                continue

            overlap = _longest_suffix_prefix_overlap(right, left)
            if overlap:
                remainder = _remove_comparison_suffix(current.text, overlap)
                remainder_key = _comparison_text(remainder)
                text = previous.text
                if remainder_key and not (
                    left.startswith(remainder_key) or left.endswith(remainder_key)
                ):
                    text = separator.join((remainder, previous.text))
                result.pop()
                current = replace(previous, start=start, end=end, text=text)
                continue
            break

        result.append(current)
    return result


def _longest_suffix_prefix_overlap(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    return max(
        (size for size in range(1, limit + 1)
         if left.endswith(right[:size])),
        default=0,
    )


def _remove_comparison_prefix(text: str, count: int) -> str:
    positions = [index for index, char in enumerate(text) if char.isalnum()]
    return text[positions[count - 1] + 1:].lstrip()


def _remove_comparison_suffix(text: str, count: int) -> str:
    positions = [index for index, char in enumerate(text) if char.isalnum()]
    return text[:positions[-count]].rstrip()


def _collapse_adjacent_mapped_duplicates(
    mapped: list[TranscriptSegment], separator: str,
) -> None:
    """Join matching boundaries while preserving short, repeated utterances."""
    active: list[int] = []
    for index in range(len(mapped)):
        while active:
            previous_index = active[-1]
            previous, current = mapped[previous_index], mapped[index]
            left, right = _comparison_text(previous.text), _comparison_text(current.text)
            if left == right:
                if len(left) <= 3:
                    break
                keep_previous = True
                merged_text = previous.text
            elif overlap := _longest_suffix_prefix_overlap(left, right):
                keep_previous = True
                remainder = _remove_comparison_prefix(current.text, overlap)
                merged_text = (
                    separator.join((previous.text, remainder))
                    if _comparison_text(remainder) else previous.text
                )
            elif left.startswith(right) or left.endswith(right):
                keep_previous = True
                merged_text = previous.text
            elif right.startswith(left) or right.endswith(left):
                keep_previous = False
                merged_text = current.text
            else:
                break

            start = min(previous.start, current.start)
            end = max(previous.end, current.end)
            if keep_previous:
                mapped[previous_index] = replace(
                    previous, start=start, end=end, text=merged_text,
                )
                mapped[index] = replace(
                    current, start=end, end=end, text=COLLAPSED_SEGMENT_TEXT,
                )
                break

            mapped[index] = replace(current, start=start, end=end)
            mapped[previous_index] = replace(
                previous, start=start, end=start, text=COLLAPSED_SEGMENT_TEXT,
            )
            active.pop()

        if mapped[index].text != COLLAPSED_SEGMENT_TEXT:
            active.append(index)


def _contiguous_ocr_blocks(cues: list[TranscriptSegment], separator: str) -> list[str]:
    """Build every contiguous cue block without changing its OCR text."""
    blocks: list[str] = []
    for start in range(len(cues)):
        parts: list[str] = []
        for cue in cues[start:]:
            parts.append(cue.text)
            blocks.append(separator.join(parts))
    return blocks


def _text_similarity(first: str, second: str) -> float:
    left, right = _comparison_text(first), _comparison_text(second)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _comparison_text(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())
