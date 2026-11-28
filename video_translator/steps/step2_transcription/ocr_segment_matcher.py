"""Scan existing subtitle cues using the OCR V25 algorithm from ocrLab."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path


TIMECODE = re.compile(r"^\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*$")


TIMELINE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)(?:\s+.*)?$")


MATCH_THRESHOLD = 0.70


NOT_FOUND = "A"


def match_threshold(length: int) -> float:
    """Use exactly 2/3 for three characters to avoid rounding out one typo."""
    if length <= 1:
        return 1.0
    if length == 2:
        return 0.5
    if length == 3:
        return 2 / 3
    return MATCH_THRESHOLD


def validate_interval(interval: float) -> float:
    if not 0 < interval <= 60:
        raise ValueError("Khoảng cách phải lớn hơn 0 và không quá 60 giây")
    return interval


def validate_extension(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError("Thời gian mở rộng phải là số hữu hạn không âm")
    return value


@dataclass(frozen=True)
class Segment:
    index: str
    start: float
    end: float
    text: str = ""
    timeline: str = ""


def seconds(value: str) -> float:
    m = TIMECODE.fullmatch(value)
    if not m:
        raise ValueError(f"Mốc thời gian SRT không hợp lệ: {value}")
    hh, mm, ss, ms = (int(n) for n in m.groups())
    if mm >= 60 or ss >= 60:
        raise ValueError(f"Mốc thời gian SRT không hợp lệ: {value}")
    ms_digits = len(m.group(4))
    return hh * 3600 + mm * 60 + ss + ms / (10 ** ms_digits)


def read_srt(path: Path) -> list[Segment]:
    raw = path.read_bytes()
    content = None
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else (
        "utf-8-sig", "gb18030", "cp1252",
    )
    for encoding in encodings:
        try:
            content = raw.decode(encoding)
            break
        except UnicodeError:
            pass
    if content is None:
        raise ValueError("Không đọc được mã hóa file SRT")
    content = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"\n\s*\n", content.strip())
    segments: list[Segment] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        timeline_pos = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timeline_pos is None:
            continue
        match = TIMELINE.fullmatch(lines[timeline_pos])
        if not match:
            raise ValueError(f"Dòng thời gian SRT không hợp lệ: {lines[timeline_pos]}")
        index = lines[timeline_pos-1] if timeline_pos > 0 else str(len(segments) + 1)
        if not index.isdecimal():
            index = str(len(segments) + 1)
        start, end = seconds(match.group(1)), seconds(match.group(2))
        if end < start:
            raise ValueError(f"Segment {index}: end nhỏ hơn start")
        text = "\n".join(lines[timeline_pos + 1:])
        segments.append(Segment(index, start, end, text, lines[timeline_pos]))
    if not segments:
        raise ValueError("Không tìm thấy segment SRT hợp lệ")
    return segments


def join_ocr_fragments(fragments: list[str]) -> str:
    """Join detected fragments without inserting separators into CJK text."""
    joined = ""
    for fragment in fragments:
        fragment = fragment.strip()
        if not fragment:
            continue
        if not joined:
            joined = fragment
            continue
        left, right = joined[-1], fragment[0]
        cjk_boundary = any(unicodedata.east_asian_width(char) in ("W", "F")
                           for char in (left, right))
        separator = "" if (cjk_boundary or right in ",.!?:;)]}" or left in "([{") else " "
        joined += separator + fragment
    return joined


def is_subtitle_character(c):
    name = unicodedata.name(c, '')
    return name.startswith(('CJK UNIFIED IDEOGRAPH', 'CJK COMPATIBILITY IDEOGRAPH')) or c == '〇' or c.isdecimal()


def normalized_text(text: str) -> tuple[str, list[int]]:
    """Comparison keys only: never modify stored OCR; retain raw offsets."""
    chars, positions = [], []
    for pos, char in enumerate(text):
        if is_subtitle_character(char):
            chars.append(char)
            positions.append(pos)
    return ''.join(chars), positions


@lru_cache(maxsize=1)
def chinese_converter():
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise RuntimeError("Thiếu OpenCC. Chạy: python -m pip install opencc-python-reimplemented") from exc
    return OpenCC("t2s")


@lru_cache(maxsize=16384)
def simplified(text: str) -> str:
    """Comparison only; preserve normalized character offsets for OCR slicing."""
    if not any("\u3400" <= c <= "\u9fff" for c in text):
        return text
    converter = chinese_converter()
    converted = converter.convert(text)
    if len(converted) == len(text):
        return converted
    return "".join(v if len(v := converter.convert(c)) == 1 else c for c in text)


def srt_reference(text: str) -> str:
    text = re.sub(r"<[^>]*>|\{\\[^}]*\}", "", text)
    return unicodedata.normalize("NFC", text)


@dataclass(frozen=True)
class TextMatch:
    start: int
    end: int
    score: float


def matching_span(recognized: str, reference: str, minimum_score=0.0) -> TextMatch | None:
    result = _matching_span(recognized, reference, minimum_score)
    if result is not None and result.score >= 1.0:
        return result
    a = normalized_text(recognized)[0]
    b = normalized_text(srt_reference(reference))[0]
    aa, bb = simplified(a), simplified(b)
    if (a, b) != (aa, bb):
        converted = _matching_span(aa, bb, minimum_score)
        if converted is not None and (result is None or converted.score > result.score):
            return converted
    return result


def _matching_span(recognized: str, reference: str, minimum_score=0.0) -> TextMatch | None:
    """Find a substring using the reference-length similarity threshold.
    The free prefix in the edit-distance table allows a reference to match
    anywhere in a multiline OCR result. Equal scores prefer reference length,
    preserving a substituted edge character (eghm -> eghk, rather than egh).
    """
    source, positions = normalized_text(recognized)
    needle, _ = normalized_text(srt_reference(reference))
    if not source or not needle:
        return None
    threshold = max(match_threshold(len(needle)), minimum_score)
    width = len(source)
    row = [(0, j) for j in range(width + 1)]
    for i, char in enumerate(needle, 1):
        new = [(i, 0)]
        for j, other in enumerate(source, 1):
            options = ((row[j - 1][0] + (char != other), row[j - 1][1]),
                       (row[j][0] + 1, row[j][1]),
                       (new[j - 1][0] + 1, new[j - 1][1]))
            new.append(min(options, key=lambda item: (item[0], abs(j - item[1] - i))))
        row = new
    candidates = []
    for end, (distance, start) in enumerate(row):
        if end <= start:
            continue
        length = end - start
        score = 1 - distance / max(len(needle), length)
        if score + 1e-9 >= threshold:
            candidates.append((score, -abs(length - len(needle)), -start, start, end))
    if not candidates:
        # Whisper can repeat a short phrase: "公的 公的" while the image
        # contains just "公的". Recognize it as one phrase, including when
        # deciding whether this frame belongs to the next segment.
        words = srt_reference(reference).split()
        if len(words) > 1 and len(set(words)) == 1:
            return _matching_span(recognized, words[0], minimum_score)
        return None
    score, _, _, start, end = max(candidates)
    return TextMatch(start, end, score)


@dataclass(frozen=True)
class ScanResult:
    text: str = NOT_FOUND
    timestamp: float | None = None
    deferred_owner: str | None = None
    deferred_blocks: tuple[OCRBlock, ...] = ()
    confidence: float = 0.0
    boundary_raw: str = ""
    last_valid_ocr: str = ""  # carry boundary across NO_TEXT, not previous record state


def norm(text):
    return normalized_text(text)[0]


def number_key(text):
    """Comparison only: common Chinese integers, never alter OCR output."""
    x = simplified(norm(text))
    digits = dict(zip('零一二三四五六七八九', range(10)))
    digits.update({'〇':0, '两':2})
    def convert(m):
        v=m.group(); total=0; digit=0
        if not any(c in '十百千' for c in v):
            return ''.join(str(digits[c]) for c in v)
        for c in v:
            if c in digits: digit=digits[c]
            else:
                total+=(digit or 1)*{'十':10,'百':100,'千':1000}[c]; digit=0
        return str(total+digit)
    return re.sub(r'[零〇一二两三四五六七八九十百千]+', convert, x)


def block_slice(raw, start, end):
    # Preserve source punctuation adjacent to the selected block (including ellipses).
    chars, positions = normalized_text(raw)
    if not chars or start >= end:
        return ""
    left = 0 if start == 0 else positions[start]
    right = len(raw) if end == len(chars) else positions[end]
    return raw[left:right].strip()


def comparison_score(raw, ref):
    """Whole-block comparison; conversions never modify the output."""
    if not norm(raw) or not norm(ref):
        return 0.0
    score = output_similarity(raw, ref)
    if score < 1.0:
        score = max(score, _output_similarity(number_key(raw), number_key(ref)))
    return score


def fits(raw, ref):
    return bool(norm(raw) and norm(ref) and
                comparison_score(raw, ref) + 1e-9 >= match_threshold(len(norm(ref))))


def partition(raw, refs):
    """Find ownership using matching cores; return complete original blocks.

    Prefer explicit OCR separators and exact reference anchors over arbitrary
    character cuts. A fuzzy core is evidence, not the output slice. Without a
    reliable boundary, retain the full-block threshold and reject ambiguous cuts.
    """
    chars, positions = normalized_text(raw)
    n = len(chars)
    if n < 2:
        return None
    separators = {i for i in range(1, n)
                  if raw[positions[i-1]+1:positions[i]].strip() or
                  raw[positions[i-1]+1:positions[i]]}
    # Commas and ellipses are the strongest explicit block boundaries.
    preferred = {i for i in separators if re.search(r"[,，、]|\.{3,}|…+|。{3,}",
                 raw[positions[i-1]+1:positions[i]])}
    # Exact anchors can establish a cut even in Chinese text without spaces.
    exact_starts, exact_ends = {}, {}
    x = simplified(chars)
    for j, ref in enumerate(refs):
        needle = simplified(norm(ref))
        starts, ends = set(), set()
        if needle:
            pos = x.find(needle)
            while pos >= 0:
                starts.add(pos); ends.add(pos+len(needle))
                pos = x.find(needle, pos+1)
        exact_starts[j], exact_ends[j] = starts, ends
    # Three explicit, individually matching blocks are outside the two-owner
    # policy. Do not silently combine two of them into a single allocation.
    if len(preferred) >= 2 and all(norm(r) for r in refs):
        for left in sorted(preferred):
            for right in sorted(preferred):
                if left < right and all(fits(block_slice(raw,a,b), refs[j])
                        for j,(a,b) in enumerate(((0,left),(left,right),(right,n)))):
                    return None
    baseline = max(comparison_score(raw, r) for r in refs)
    best = None
    ambiguous = False
    for indices in ((0,1), (1,2)):
        if any(not norm(refs[j]) for j in indices):
            continue
        # At most two adjacent segments. When a preferred separator exists,
        # never move characters across it just to improve the match score.
        cache = {}
        for cut in sorted(preferred) if preferred else range(1,n):
            cuts = (cut,)
            points = (0,)+cuts+(n,)
            blocks = tuple((j,points[k],points[k+1]) for k,j in enumerate(indices))
            reliable = tuple(c in separators or c in exact_ends[indices[k]] or
                             c in exact_starts[indices[k+1]] for k,c in enumerate(cuts))
            exact = 0; quality = 0.; valid = True
            for k,(j,start,end) in enumerate(blocks):
                key=(j,start,end)
                if key not in cache:
                    block=block_slice(raw,start,end)
                    full=comparison_score(block,refs[j])
                    core=matching_span(block,refs[j])
                    cache[key]=(full,core)
                full,core=cache[key]
                threshold=match_threshold(len(norm(refs[j])))
                bounded=(k==0 or reliable[k-1]) and (k==len(blocks)-1 or reliable[k])
                # A bounded block may contain extra OCR characters. Validate its
                # matching core, but never trim the original block to that core.
                if full+1e-9 < threshold and not (bounded and core is not None):
                    valid=False; break
                exact += int(full>=1-1e-9)
                quality += max(full,core.score if bounded and core else 0.)*len(norm(refs[j]))
            if not valid:
                continue
            quality /= sum(len(norm(refs[j])) for j in indices)
            if quality <= baseline+1e-9:
                continue
            # Explicit separators are stronger than an arbitrary character cut;
            # exact full blocks protect the adjacent text from drifting across.
            rank=(sum(c in preferred for c in cuts),exact,sum(reliable),
                  sum(c in separators for c in cuts),round(quality,9))
            if best is None or rank>best[0]:
                best=(rank,blocks); ambiguous=False
            elif rank==best[0] and blocks!=best[1]:
                ambiguous=True
    return best[1] if best and not ambiguous else None


def missing_block(text: str, reference: str):
    """Reverse partition: split SRT into two contiguous pieces, compare OCR
    against a complete edge using the same whole-block scoring and thresholds.
    Return (missing direction, missing reference), or None if ambiguous.
    """
    source = text
    target = srt_reference(reference)
    n, m = len(norm(target)), len(norm(source))
    if not m or n - m <= 2:
        return None
    baseline = comparison_score(source, target)
    candidates = []
    for cut in range(1, n):
        head, tail = block_slice(target, 0, cut), block_slice(target, cut, n)
        for direction, matched, missing in (("right", head, tail), ("left", tail, head)):
            if len(norm(missing)) < 3 or abs(len(norm(matched))-m) > 2:
                continue
            score = comparison_score(source, matched)
            if not fits(source, matched) or score <= baseline + 1e-9:
                continue
            # Same full-block evidence as partition; no fuzzy interior span
            # may manufacture a missing prefix/suffix.
            rank = (round(score, 9), -abs(len(norm(matched))-m))
            candidates.append((rank, direction, missing))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best = candidates[0]
    if any(c[0] == best[0] and c[1:] != best[1:] for c in candidates[1:]):
        return None
    return best[1], best[2]


@dataclass(frozen=True)
class OCRBlock:
    text: str
    timestamp: float
    confidence: float


def clean_ocr(text):
    """Remove non-subtitle glyphs from a committed block; preserve Han, digits and punctuation.
    A mixed fragment has only one recognition score; cleaning cannot raise it.
    """
    punctuation = set('，。！？、；：…—－·“”‘’《》〈〉「」『』（）【】,.!?;:-()[]\"\'')
    cleaned = ''.join(c for c in text if is_subtitle_character(c) or c in punctuation)
    return cleaned if any(is_subtitle_character(c) for c in cleaned) else ''


def same_block(a, b):
    x, y = simplified(norm(a)), simplified(norm(b))
    if x == y:
        return bool(x)
    # Short distinct subtitles must not be merged by a single shared character.
    return min(len(x), len(y)) >= 3 and output_similarity(x, y) >= .80


def sample_variant(a, b):
    """Within adjacent nonempty samples only: allow one missing edge character."""
    if same_block(a, b):
        return True
    x, y = simplified(norm(a)), simplified(norm(b))
    if len(x) > len(y):
        x, y = y, x
    return bool(x) and len(y) == len(x) + 1 and (y.startswith(x) or y.endswith(x))


def allocate_block(text, ref, anchor, prior_ref, following, timestamp, segment):
    """Return ownership separately from recognition confidence.

    Only a confirmed adjacent boundary excludes a nonempty block.
    Similarity to the current SRT is not an acceptance requirement.
    """
    if not norm(text):
        return 'EMPTY', '', ''
    x = simplified(norm(text))
    left = simplified(norm(anchor))
    own = comparison_score(text, ref)
    right = comparison_score(text, following)
    if left and x == left:
        return 'PREV', '', ''
    if following and x == simplified(norm(following)):
        return 'NEXT', '', ''
    if own >= 1-1e-9:
        return 'OWN', text, ''
    left_refs = tuple(dict.fromkeys(r for r in (anchor, prior_ref) if norm(r)))
    # Handle an exact A+B+C frame before the existing two-owner splitter.
    b, c = simplified(norm(ref)), simplified(norm(following))
    for lref in left_refs:
        a = simplified(norm(lref))
        if a and b and c and x == a+b+c:
            return '1:2', block_slice(text,len(a),len(a)+len(b)), block_slice(text,len(a)+len(b),len(x))
    for lref in left_refs or ('',):
        split = partition(text, (lref, ref, following))
        if split:
            own_span = next(((a,b) for j,a,b in split if j==1), None)
            tail = next(((a,b) for j,a,b in split if j==2), None)
            if own_span:
                return '1:2', block_slice(text,*own_span), block_slice(text,*tail) if tail else ''
    ls = max((comparison_score(text,r) for r in left_refs), default=0.)
    left_ok = any(simplified(norm(r)) == x or (min(len(x),len(norm(r)))>=3 and ls>=.80) for r in left_refs)
    right_ok = bool(following and (x == c or (min(len(x),len(c))>=3 and right>=.80)))
    # Weaker relative evidence is useful near the corresponding temporal edge,
    # not a universal threshold for short/shared phrases inside the segment.
    if timestamp <= segment.start+1e-8 and ls>=.50 and ls>own+.20:
        left_ok = True
    if timestamp >= segment.end-1e-8 and right>=.50 and right>own+.20:
        right_ok = True
    if left_ok and ls>own+1e-9 and ls>right+1e-9:
        return 'PREV','',''
    if right_ok and right>own+1e-9 and right>ls+1e-9:
        return 'NEXT','',''
    # No confirmed boundary: retain the OCR even when current similarity is zero.
    return 'OWN',text,''


STANDARD_CONFIDENCE = 0.95


def scan_segment(
    segment: Segment,
    next_segment: Segment | None,
    previous: ScanResult,
    duration: float,
    interval: float,
    recognize: Callable[[float], tuple[str, float]],
    previous_segment: Segment | None,
    *,
    extension: float,
    short_fallback: bool,
    short_interval: float,
) -> ScanResult:
    ref = clean_ocr(srt_reference(segment.text))
    is_short = len(norm(ref)) <= 2
    anchor = previous.last_valid_ocr
    prior_ref = clean_ocr(srt_reference(previous_segment.text)) if previous_segment else ""
    following = clean_ocr(srt_reference(next_segment.text)) if next_segment else ""
    lo = max(0.0, segment.start - extension)
    if previous.timestamp is not None:
        lo = max(lo, previous.timestamp)
    elif previous_segment:
        lo = max(lo, previous_segment.end)
    hi = min(
        segment.end + extension,
        math.nextafter(duration, -math.inf),
        math.nextafter(next_segment.start, -math.inf) if next_segment else duration,
    )
    # Leave a margin so rounding cannot move the sample onto an exclusive boundary.
    if next_segment and hi >= next_segment.start - 1e-8:
        hi = next_segment.start - 1e-6
    if hi >= duration - 1e-8:
        hi = duration - 1e-6

    observed: dict[float, OCRBlock] = {}

    def sample(timestamp):
        timestamp = round(timestamp, 9)
        if timestamp in observed:
            return observed[timestamp]
        if (previous.boundary_raw and previous.timestamp is not None
                and abs(timestamp - previous.timestamp) < 1e-8):
            raw, score = previous.boundary_raw, previous.confidence
        else:
            raw, score = recognize(timestamp)
        block = OCRBlock(raw, timestamp, score if raw else 0.0)
        observed[timestamp] = block
        return block

    def allocate(block):
        owner, text, tail = allocate_block(
            block.text, ref, anchor, prior_ref, following, block.timestamp, segment,
        )
        if text and not any(is_subtitle_character(char) for char in text):
            return "EMPTY", "", ""
        return owner, text, tail

    def result(selected):
        ordered = sorted(selected, key=lambda item: item[0].timestamp)
        block, text, _ = ordered[-1]
        deferred = tuple(replace(item, text=tail) for item, _, tail in ordered if tail and next_segment)
        return ScanResult(
            text=join_ocr_fragments([part for _, part, _ in ordered]),
            timestamp=block.timestamp,
            confidence=block.confidence,
            boundary_raw=text,
            last_valid_ocr=text,
            deferred_blocks=deferred,
            deferred_owner=next_segment.index if deferred and next_segment else None,
        )

    def finish_empty():
        text = "B+" + segment.text if is_short and short_fallback and norm(ref) else NOT_FOUND
        return ScanResult(
            text=text,
            timestamp=previous.timestamp,
            confidence=previous.confidence,
            boundary_raw=previous.boundary_raw,
            last_valid_ocr=anchor,
        )

    def same_reading(first, second):
        x, y = simplified(norm(first)), simplified(norm(second))
        if x == y:
            return bool(x)
        # Distinct numeric/ordinal subtitles must remain separate.
        if re.findall(r"[0-9]+", number_key(x)) != re.findall(r"[0-9]+", number_key(y)):
            return False
        return sample_variant(first, second) or (
            len(x) == len(y) and len(x) >= 3 and sum(a != b for a, b in zip(x, y)) == 1
        )

    def choose_block(candidates, initial=None):
        """Keep the best reading of one block and return the first distinct reading as lookahead."""
        best = initial
        if best and best[0].confidence > STANDARD_CONFIDENCE:
            return best, None
        for candidate in candidates:
            if candidate is None:
                if best is not None:
                    return best, None
                continue
            block, text, _ = candidate
            if best is None:
                best = candidate
            elif not same_reading(text, best[1]):
                return best, candidate
            elif block.confidence > best[0].confidence:
                best = candidate
            if best[0].confidence > STANDARD_CONFIDENCE:
                return best, None
        return best, None

    def commit(candidate):
        block, text, tail = candidate
        return block, clean_ocr(text), clean_ocr(tail)

    if lo > hi:
        return finish_empty()

    def primary_samples():
        nonlocal lo, hi
        if is_short:
            count = int(math.floor((hi - lo) / short_interval + 1e-9))
            times = [min(hi, lo + i * short_interval) for i in range(count + 1)]
            if hi - times[-1] > 1e-8:
                times.append(hi)
            for timestamp in times:
                block = sample(timestamp)
                owner, text, tail = allocate(block)
                if owner in ("PREV", "NEXT"):
                    yield None
                    if owner == "NEXT":
                        return
                elif text:
                    yield block, text, tail
            return

        midpoint = min(hi, max(lo, (segment.start + segment.end) / 2))
        left_open = right_open = True
        step = 0
        while left_open or right_open:
            if step == 0:
                points = [("mid", midpoint)]
            else:
                points = []
                if left_open:
                    points.append(("left", max(lo, midpoint - step * interval)))
                if right_open:
                    points.append(("right", min(hi, midpoint + step * interval)))
            for direction, timestamp in points:
                if (direction == "left" and not left_open) or (direction == "right" and not right_open):
                    continue
                if timestamp < lo - 1e-8 or timestamp > hi + 1e-8:
                    continue
                block = sample(timestamp)
                owner, text, tail = allocate(block)
                if owner == "PREV":
                    left_open = False
                    lo = max(lo, timestamp)
                    yield None
                elif owner == "NEXT":
                    right_open = False
                    hi = min(hi, timestamp)
                    yield None
                elif text:
                    yield block, text, tail
                if direction in ("left", "mid") and timestamp <= lo + 1e-8:
                    left_open = False
                if direction in ("right", "mid") and timestamp >= hi - 1e-8:
                    right_open = False
            step += 1

    standard, pending = choose_block(primary_samples())
    if standard is None and previous.deferred_owner == segment.index:
        for block in previous.deferred_blocks:
            if not lo - 1e-8 <= block.timestamp <= hi + 1e-8:
                continue
            owner, text, tail = allocate(block)
            if owner in ("OWN", "1:2") and text:
                standard = (block, text, tail)
                break
    if standard is None:
        return finish_empty()

    standard = commit(standard)
    block, text, _ = standard
    if is_short:
        return result([standard])
    missing_info = missing_block(text, ref)
    if missing_info is None:
        return result([standard])

    direction, _ = missing_info
    right = direction == "right"
    edge = hi if right else lo

    def in_missing_side(timestamp):
        if right:
            return block.timestamp + 1e-8 < timestamp <= hi + 1e-8
        return lo - 1e-8 <= timestamp < block.timestamp - 1e-8

    def secondary_part(other, part, other_tail):
        if not part:
            return None
        x, y = simplified(norm(part)), simplified(norm(text))
        if same_reading(part, text) or x in y:
            return None
        if y in x:
            if not right and x.endswith(y):
                part = block_slice(part, 0, len(x) - len(y))
            elif right and x.startswith(y):
                part = block_slice(part, len(y), len(x))
            else:
                return None
        return (other, part, other_tail) if part and not same_reading(part, text) else None

    initial = secondary_part(*pending) if pending and in_missing_side(pending[0].timestamp) else None

    def supplemental_samples():
        entered = False
        entry_owner = "NEXT" if right else "PREV"
        step = 0
        while True:
            timestamp = edge - step * short_interval if right else edge + step * short_interval
            if not in_missing_side(timestamp):
                return
            step += 1
            if initial and abs(timestamp - initial[0].timestamp) < 1e-8:
                entered = True
                continue
            other = sample(timestamp)
            owner, part, tail = allocate(other)
            if owner in ("PREV", "NEXT"):
                if not entered and owner == entry_owner:
                    continue
                yield None
                return
            if part:
                entered = True
                candidate = secondary_part(other, part, tail)
                if candidate is not None:
                    yield candidate

    second, _ = choose_block(supplemental_samples(), initial)
    if second is None:
        return result([standard])

    combined = result([standard, commit(second)])
    # Drop the supplemental block if it adds excess text and worsens the match.
    if (len(norm(combined.text)) - len(norm(ref)) > 2
            and comparison_score(combined.text, ref) + 1e-9 < comparison_score(text, ref)):
        return result([standard])
    return combined


def output_similarity(first: str, second: str) -> float:
    a, b = normalized_text(first)[0], normalized_text(second)[0]
    score = _output_similarity(a, b)
    if score >= 1.0:
        return score
    aa, bb = simplified(a), simplified(b)
    return max(score, _output_similarity(aa, bb)) if (a, b) != (aa, bb) else score


def _output_similarity(first: str, second: str) -> float:
    """Whole-text edit similarity for classification; never used to stop scanning."""
    a, _ = normalized_text(first)
    b, _ = normalized_text(second)
    if not a or not b:
        return 1.0 if a == b else 0.0
    row = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        new = [i]
        for j, other in enumerate(b, 1):
            new.append(min(row[j] + 1, new[j - 1] + 1,
                           row[j - 1] + (char != other)))
        row = new
    return 1 - row[-1] / max(len(a), len(b))
