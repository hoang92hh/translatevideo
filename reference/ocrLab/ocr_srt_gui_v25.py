"""OCR V25: midpoint confidence search and two-block mapping.

Windows: Python 3.13 + opencv-python + rapidocr + onnxruntime-gpu + CUDA 12 / cuDNN 9

Run: python ocr_srt_gui_v25.py

Requires subtitle_region_selector.py in same directory.

"""

from __future__ import annotations

import json

import queue

import re

import subprocess

import sys

import threading

import traceback
import time

import unicodedata

from dataclasses import dataclass, replace

from difflib import SequenceMatcher
from functools import lru_cache

from pathlib import Path

import tkinter as tk

from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(__file__).resolve().parent

SELECTOR_SCRIPT = APP_DIR / "subtitle_region_selector.py"

VIDEO_TYPES = [("Video", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"), ("All files", "*.*")]

SRT_TYPES = [("SubRip SRT", "*.srt"), ("All files", "*.*")]

TIMECODE = re.compile(r"^\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*$")

TIMELINE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)(?:\s+.*)?$")

MATCH_THRESHOLD = 0.70

MATCH_RULES = "1 chữ: 100%; 2 chữ: 50%; 3 chữ: 66,7%; từ 4 chữ: 70%"

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
    import math
    if not math.isfinite(value) or value < 0:
        raise ValueError("Thời gian mở rộng phải là số hữu hạn không âm")
    return value


@dataclass(frozen=True)

class Segment:
    index: str
    start: float
    end: float
    text: str = ""

def seconds(value: str) -> float:
    m = TIMECODE.fullmatch(value)
    if not m:
        raise ValueError(f"Mốc thời gian SRT không hợp lệ: {value}")
    hh, mm, ss, ms = (int(n) for n in m.groups())
    if mm >= 60 or ss >= 60:
        raise ValueError(f"Mốc thời gian SRT không hợp lệ: {value}")
    ms_digits = len(m.group(4))
    return hh * 3600 + mm * 60 + ss + ms / (10 ** ms_digits)

def format_srt_time(value: float) -> str:
    total = max(0, round(value * 1000))
    hours, remainder = divmod(total, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def srt_record(segment: Segment, text: str) -> str:
    return (f"{segment.index}\n"
            f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
            f"{text}\n\n")


def read_srt(path: Path) -> list[Segment]:
    raw = path.read_bytes()
    content = None
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "cp1252"):
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
        segments.append(Segment(index, start, end, text))
    if not segments:
        raise ValueError("Không tìm thấy segment SRT hợp lệ")
    return segments

def parse_region(path: Path) -> tuple[float, float, float, float]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    values = payload.get("region_normalized")
    if values is None:
        values = payload.get("region_percent")
        if values is None:
            raise ValueError("JSON không có region_normalized hoặc region_percent")
        values = {key: float(values[key]) / 100.0 for key in ("x", "y", "width", "height")}
    x, y, w, h = (float(values[key]) for key in ("x", "y", "width", "height"))
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
        raise ValueError("Vùng tọa độ không nằm hợp lệ trong khung video")
    return x, y, w, h

def crop_region(frame, region):
    height, width = frame.shape[:2]
    x, y, w, h = region
    x1, y1 = round(x * width), round(y * height)
    x2, y2 = round((x + w) * width), round((y + h) * height)
    x1, y1 = max(0, min(width - 1, x1)), max(0, min(height - 1, y1))
    x2, y2 = max(x1 + 1, min(width, x2)), max(y1 + 1, min(height, y2))
    return frame[y1:y2, x1:x2]

def preprocess(img, mode, cv):
    if mode == 'Original': return img.copy()
    if mode == 'Upscale 2x': return cv.resize(img, None, fx=2, fy=2, interpolation=cv.INTER_CUBIC)
    gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
    if mode == 'Threshold Otsu': gray = cv.threshold(gray, 0, 255, cv.THRESH_BINARY | cv.THRESH_OTSU)[1]
    return cv.cvtColor(gray, cv.COLOR_GRAY2BGR)



IMAGE_MODES = ("Original", "Grayscale", "Threshold Otsu", "Upscale 2x")
OCR_METHODS = ("Det+Rec", "Rec only")
SEEK_MODES = ("Timestamp", "Frame index")


def validate_ocr_options(image_mode, method, seek, text_score):
    if image_mode not in IMAGE_MODES or method not in OCR_METHODS or seek not in SEEK_MODES:
        raise ValueError("Chế độ OCR không hợp lệ")
    if not 0 <= text_score <= 1:
        raise ValueError("Text score phải từ 0 đến 1")


def run_ocr_image(engine, crop, cv, image_mode, method, text_score):
    image = preprocess(crop, image_mode, cv)
    full = method == "Det+Rec"
    return engine(image.copy(), use_det=full, use_cls=full, use_rec=True, text_score=text_score)


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


def extract_text(ocr_result, text_score=0.5) -> tuple[str, float]:
    """Keep raw recognition fragments; preserve the existing score filter.
    Pure noise fragments do not contribute to the subtitle score. A mixed
    fragment retains its original recognition score, with no recalculation.
    """
    import math
    texts = getattr(ocr_result, 'txts', None)
    scores = getattr(ocr_result, 'scores', None)
    texts = [] if texts is None else list(texts)
    scores = [] if scores is None else list(scores)
    retained = []
    for i, raw in enumerate(texts):
        score = float(scores[i]) if i < len(scores) else 0.0
        if str(raw).strip() and math.isfinite(score) and text_score <= score <= 1.0:
            retained.append((str(raw), score))
    content_scores = [c for t,c in retained if any(is_subtitle_character(ch) for ch in t)]
    return (join_ocr_fragments([t for t,_ in retained]),
            sum(content_scores)/len(content_scores) if content_scores else 0.0)


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


def contains_with_fallback(needle, haystack):
    return needle in haystack or simplified(needle) in simplified(haystack)


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

def source_slice(text: str, start: int, end: int) -> str:
    source, positions = normalized_text(text)
    if not source or end <= start:
        return ""
    left = 0 if start == 0 else positions[start]
    right = len(text) if end == len(source) else positions[end - 1] + 1
    return text[left:right].strip()

def matching_block(recognized: str, reference: str) -> str | None:
    span = matching_span(recognized, reference)
    if span is None:
        return None
    if span.start == 0 and span.end == len(normalized_text(recognized)[0]):
        return recognized.strip()
    return source_slice(recognized, span.start, span.end)

def reference_match(recognized: str, reference: str, minimum_score=0.0) -> TextMatch | None:
    """Match an OCR block or one image of a longer SRT sentence."""
    match = matching_span(recognized, reference, minimum_score)
    source, _ = normalized_text(recognized)
    target, _ = normalized_text(srt_reference(reference))
    if source and len(target) > len(source) * 1.25:
        reverse = matching_span(srt_reference(reference), recognized, minimum_score)
        if reverse is not None:
            partial = TextMatch(0, len(source), reverse.score)
            if match is None or (partial.score, partial.end) > (match.score, match.end - match.start):
                match = partial
    return match

@dataclass(frozen=True)

class ScanResult:
    text: str = NOT_FOUND
    raw: str = ""
    timestamp: float | None = None
    reference: str = ""
    parts: tuple[str, ...] = ()
    last_scan_time: float | None = None
    last_scan_raw: str = ""
    last_scan_reason: str = ""
    classification: str = "NO_TEXT"
    boundary_owner: str | None = None
    used_frame: str = ""
    used_end: int = 0
    deferred_frame: str = ""
    deferred_owner: str | None = None
    deferred_time: float | None = None
    deferred_blocks: tuple[OCRBlock, ...] = ()
    previous_update: str = ""
    previous_update_frame: str = ""
    confidence: float = 0.0
    boundary_raw: str = ""
    last_valid_ocr: str = ""  # carry boundary across NO_TEXT, not previous record state

@dataclass(frozen=True)

class FrameSelection:
    text: str
    previous_boundary: bool = False
    next_boundary: bool = False
    reason: str = ""

def norm(text):
    return normalized_text(text)[0]


def equivalent(a, b):
    """OCR variants within one segment, never a neighbor-boundary test."""
    x, y = norm(a), norm(b)
    if not x or not y:
        return False
    x, y = simplified(x), simplified(y)
    if x == y or x in y or y in x:
        return True
    # A single shared character in two short Chinese phrases is insufficient.
    if min(len(x), len(y)) <= 2:
        return False
    return (output_similarity(x, y) >= .70 or
            (min(len(x), len(y)) / max(len(x), len(y)) >= .65 and
             SequenceMatcher(None, x, y, autojunk=False).ratio() >= .75))


def frame_variant(a, b):
    """Compare full frame readings, not arbitrary substring containment.
    Short common prefixes must not consume a genuinely longer subtitle."""
    x, y = simplified(norm(a)), simplified(norm(b))
    if not x or not y:
        return False
    if x == y:
        return True
    if min(len(x), len(y)) <= 2:
        return False
    return min(len(x), len(y)) / max(len(x), len(y)) >= .65 and (
        _output_similarity(x, y) >= .70 or
        SequenceMatcher(None, x, y, autojunk=False).ratio() >= .75)


def mapped_end(old, new, end):
    """Project a consumed prefix onto a variant OCR, preserving raw offsets."""
    a, b = simplified(norm(old)), simplified(norm(new))
    if end >= len(a):
        return len(b)
    if end <= 0:
        return 0
    for tag, i, j, k, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if i <= end <= j and j > i:
            return k + round((end-i)*(l-k)/(j-i))
    return min(end, len(b))


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


def future_only(raw, refs, extra_next=""):
    """Conservative lookahead: a strong complete future suffix plus a weak
    current match. Do not infer a boundary from a shared short prefix."""
    x=number_key(raw); b=number_key(refs[1]); c=number_key(refs[2]); d=number_key(extra_next)
    if not x or not b:
        return False
    current=_output_similarity(x,b)
    for target in (c,d):
        if len(target)>=4 and x.endswith(target) and len(target)/len(x)>=.55:
            if current < .70 and _output_similarity(x,target)>current:
                return True
    if c and d and current < .50 and _output_similarity(x,c+d)>=.70:
        return True
    return False


def span_in_reference(text, ref):
    return matching_span(ref, text) if norm(text) and norm(ref) else None


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


def assess(raw, refs, anchor="", anchor_is_current=False, offset=0):
    """Classify before assigning; no unvalidated remainder can become a block."""
    n = len(norm(raw)); length = len(norm(refs[1]))
    if not n:
        return "",False,False,"",1,"không có chữ"
    scores = [comparison_score(raw,r) for r in refs]
    split = partition(raw,refs)
    if split:
        j,l,r = split[-1]
        middle = next((block_slice(raw,a,b) for k,a,b in split if k==1), "")
        return middle,split[0][0]==0,j==2,block_slice(raw,l,r),j,"1:N; tách block đã kiểm chứng"
    prior = comparison_score(raw,anchor) if anchor and not anchor_is_current else 0.
    left = max(scores[0], prior if prior>=.90 else 0.)
    right = scores[2]
    partial = span_in_reference(raw,refs[1])
    # A complete current match wins ties. A short piece of B can require more OCR.
    if left > scores[1]+1e-9 and left>=right and (fits(raw,refs[0]) or prior>=.90):
        if not (n<length and partial and partial.score>=.999 and offset>=0 and scores[0]<.999):
            return "",True,False,"",1,"biên nội dung trước"
    if right > scores[1]+1e-9 and right>left and fits(raw,refs[2]):
        if not (n<length and partial and partial.score>=.999 and offset<0):
            return "",False,True,"",1,"biên SRT kế tiếp"
    # Without a validated multi-SRT partition, a matched substring is evidence
    # of ownership, not a license to discard OCR characters from the same frame.
    return raw,False,False,raw,1,"OCR hiện tại" if partial or scores[1] else "OCR khác SRT"


def fuller_previous(raw, previous, previous_segment, refs, split):
    """A longer reading of an already-owned frame, never an unassigned suffix.

    Only revise the immediately preceding segment, and only its final complete
    OCR observation. A validated partition or a stronger current/future match
    takes precedence over expansion of the previous reading.
    """
    if (split or not previous_segment or not previous.parts or
            previous.boundary_owner != previous_segment.index or
            not previous.raw or not previous.used_frame or
            previous.used_end != len(norm(previous.used_frame))):
        return ""
    old = simplified(norm(previous.raw)); new = simplified(norm(raw))
    if (old != simplified(norm(previous.used_frame)) or
            not old or len(new) <= len(old) or old not in new or
            not frame_variant(raw, previous.raw)):
        return ""
    if any(fits(raw, r) and comparison_score(raw, r) > comparison_score(raw, refs[0]) + 1e-9
           for r in refs[1:]):
        return ""
    last_part = previous.parts[-1]
    if simplified(norm(last_part)) != old:
        return ""
    if len(previous.parts) == 1:
        return raw
    # N:1: replace only the final accepted observation; preserve earlier OCR.
    if not previous.text.endswith(last_part):
        return ""
    return previous.text[:-len(last_part)] + raw


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
    raw: str
    timestamp: float
    confidence: float
    first: float
    last: float


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


def collect_blocks(samples):
    blocks = []
    group = []
    def flush():
        if group:
            best = max(group, key=lambda item: item[3])
            blocks.append(OCRBlock(best[2], best[1], best[0], best[3], group[0][0], group[-1][0]))
            group.clear()
    for t, raw, score in samples:
        text = clean_ocr(raw)
        if not text:
            flush()
            continue
        if group and not sample_variant(text, max(group, key=lambda item: item[3])[2]):
            flush()
        group.append((t, raw, text, score))
    flush()
    return blocks


STANDARD_CONFIDENCE = 0.95
SHORT_CONFIDENCE = 0.95


def allocate_block(text, ref, anchor, prior_ref, following, timestamp, segment, previous_timestamp):
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


def scan_segment(segment: Segment, next_segment: Segment | None,
                 previous: ScanResult, duration: float, interval: float,
                 recognize, cancelled: threading.Event,
                 previous_segment: Segment | None = None, on_sample=None,
                 all_segments=None, current_index=None, extension: float = 1.0,
                 short_fallback: bool = False, short_interval: float = 0.125) -> ScanResult:
    import math
    validate_interval(interval); validate_interval(short_interval); validate_extension(extension)
    ref = clean_ocr(srt_reference(segment.text))
    is_short = len(norm(ref)) <= 2
    anchor = previous.last_valid_ocr
    prior_ref = clean_ocr(srt_reference(previous_segment.text)) if previous_segment else ''
    following = clean_ocr(srt_reference(next_segment.text)) if next_segment else ''
    lo = max(0., segment.start-extension)
    if previous.timestamp is not None:
        lo = max(lo, previous.timestamp)
    elif previous_segment:
        lo = max(lo, previous_segment.end)
    # Preserve the old time limits, including exclusion of the next SRT start.
    hi = min(segment.end+extension, math.nextafter(duration, -math.inf),
             math.nextafter(next_segment.start, -math.inf) if next_segment else duration)
    # A microsecond margin avoids rounding an exclusive upper bound back onto it.
    if next_segment and hi >= next_segment.start-1e-8:
        hi = next_segment.start-1e-6
    if hi >= duration-1e-8:
        hi = duration-1e-6
    observed = {}
    last = (None, '', 0.)
    deferred = []
    def log(t, text, reason):
        if on_sample: on_sample(t, text, reason)
    def sample(t):
        nonlocal last
        t = round(t, 9)
        if t in observed:
            raw, score = observed[t]
            log(t, raw, f'CACHE | confidence={score:.4f}')
        else:
            if previous.boundary_raw and previous.timestamp is not None and abs(t-previous.timestamp)<1e-8:
                value = (previous.boundary_raw, previous.confidence)
            else:
                value = recognize(t)
            raw, score = value if isinstance(value, tuple) else (value, 0.)
            raw, score = raw or '', float(score)
            if not raw: score = 0.0
            observed[t] = (raw, score)
            log(t, raw, f'QUÉT | confidence={score:.4f}')
        last = (t, raw, score)
        return OCRBlock(raw, raw, t, score, t, t)
    def allocate(block):
        return allocate_block(block.text, ref, anchor, prior_ref, following,
                              block.timestamp, segment, previous.timestamp)
    def result(selected, classification):
        if not selected:
            return ScanResult(text=NOT_FOUND, reference=segment.text, last_valid_ocr=anchor,
                timestamp=previous.timestamp, confidence=previous.confidence,
                boundary_raw=previous.boundary_raw, boundary_owner=previous.boundary_owner, used_frame=previous.used_frame,
                used_end=previous.used_end, last_scan_time=last[0], last_scan_raw=last[1],
                last_scan_reason='EMPTY', classification='NO_TEXT')
        selected = sorted(selected, key=lambda item:item[0].timestamp)
        parts = tuple(item[1] for item in selected)
        b,text,_ = selected[-1]
        for item,part,tail in selected:
            log(item.timestamp,part,f'BLOCK CHỌN | confidence={item.confidence:.4f}')
            if tail and next_segment:
                deferred.append(replace(item,text=tail,raw=tail))
                log(item.timestamp,tail,'CHUYỂN BLOCK | phần SRT kế tiếp')
        return ScanResult(text=join_ocr_fragments(list(parts)), raw=text, timestamp=b.timestamp,
            confidence=b.confidence, boundary_raw=text, parts=parts, classification=classification,
            boundary_owner=segment.index,last_valid_ocr=text,used_frame=clean_ocr(b.text),used_end=len(norm(text)),
            reference=segment.text,last_scan_time=last[0],last_scan_raw=last[1],
            last_scan_reason=classification,deferred_blocks=tuple(deferred),
            deferred_owner=next_segment.index if deferred and next_segment else None)
    def finish_empty():
        empty = result([], 'NO_TEXT')
        if is_short and short_fallback and norm(ref):
            return replace(empty,text='B+'+segment.text,classification='SRT_FALLBACK')
        return empty

    def same_reading(a, b):
        # Three-character readings may differ by one OCR substitution (委屈了 / 季屈了).
        # Preserve distinct numeric/ordinal subtitles rather than merging 第二世 / 第三世.
        x, y = simplified(norm(a)), simplified(norm(b))
        if x == y: return bool(x)
        if re.findall(r'[0-9]+', number_key(x)) != re.findall(r'[0-9]+', number_key(y)):
            return False
        if sample_variant(a,b): return True
        return len(x)==len(y) and len(x)>=3 and sum(i!=j for i,j in zip(x,y))==1

    def choose_block(candidates, initial=None):
        """One accumulator for both primary and supplemental blocks.
        Never compare confidence across different text blocks. Preserve the
        first different sample as lookahead; no further OCR after a transition.
        """
        best = initial
        if best and best[0].confidence > STANDARD_CONFIDENCE:
            return best, None, 'CONFIDENCE'
        for event, candidate in candidates:
            if cancelled.is_set(): return None, None, 'CANCELLED'
            if event == 'BOUNDARY':
                if best is not None: return best, None, 'BOUNDARY'
                continue
            if candidate is None: continue
            block, text, _ = candidate
            if best is None:
                best = candidate
                log(block.timestamp,text,'KHỞI TẠO BLOCK')
            elif not same_reading(text,best[1]):
                log(block.timestamp,text,'CHUYỂN BLOCK | chốt block đang xét; giữ mẫu mới')
                return best, candidate, 'TRANSITION'
            elif block.confidence > best[0].confidence:
                best = candidate
                log(block.timestamp,text,'CẬP NHẬT BLOCK | confidence cao hơn')
            if best[0].confidence > STANDARD_CONFIDENCE:
                return best,None,'CONFIDENCE'
        return best,None,'EXHAUSTED'

    def commit(candidate):
        b, text, tail = candidate
        return b, clean_ocr(text), clean_ocr(tail)

    def announce(candidate, reason, label='BLOCK CHUẨN'):
        b, text, _ = candidate
        log(b.timestamp,text,f'{label} | confidence={b.confidence:.4f}; chốt={reason}')

    if lo>hi or cancelled.is_set(): return finish_empty()

    def primary_samples():
        nonlocal lo,hi
        if is_short:
            count=int(math.floor((hi-lo)/short_interval+1e-9))
            times=[min(hi,lo+i*short_interval) for i in range(count+1)]
            if hi-times[-1]>1e-8: times.append(hi)
            for t in times:
                if cancelled.is_set(): return
                b=sample(t); kind,text,tail=allocate(b)
                if kind in ('PREV','NEXT'):
                    log(t,b.text,f'BIÊN | {kind}')
                    yield 'BOUNDARY',None
                    if kind=='NEXT': return
                elif text: yield 'TEXT',(b,text,tail)
            return
        mid=min(hi,max(lo,(segment.start+segment.end)/2))
        left_open=right_open=True
        k=0
        while left_open or right_open:
            if cancelled.is_set(): return
            points=[('mid',mid)] if k==0 else ([('left',max(lo,mid-k*interval))] if left_open else [])+([('right',min(hi,mid+k*interval))] if right_open else [])
            for direction,t in points:
                if (direction=='left' and not left_open) or (direction=='right' and not right_open): continue
                if t<lo-1e-8 or t>hi+1e-8: continue
                if cancelled.is_set(): return
                b=sample(t); kind,text,tail=allocate(b)
                if kind=='PREV':
                    left_open=False; lo=max(lo,t)
                    log(t,b.text,'EMPTY | ĐÓNG BIÊN TRÁI')
                    yield 'BOUNDARY',None
                elif kind=='NEXT':
                    right_open=False; hi=min(hi,t)
                    log(t,b.text,'EMPTY | ĐÓNG BIÊN PHẢI')
                    yield 'BOUNDARY',None
                elif text:
                    yield 'TEXT',(b,text,tail)
                if direction in ('left','mid') and t<=lo+1e-8: left_open=False
                if direction in ('right','mid') and t>=hi-1e-8: right_open=False
            k+=1

    standard, pending, reason=choose_block(primary_samples())
    if cancelled.is_set(): return ScanResult()
    if standard is None and previous.deferred_owner==segment.index:
        for b in previous.deferred_blocks:
            if not lo-1e-8<=b.timestamp<=hi+1e-8: continue
            kind,text,tail=allocate(b)
            if kind in ('OWN','1:2') and text:
                standard=(b,text,tail); reason='TRANSFER'; break
    if standard is None: return finish_empty()
    standard = commit(standard)
    announce(standard,reason)
    b,text,tail=standard
    kind,_,_=allocate(b)
    if is_short: return result([standard],'1:1')
    if kind=='1:2': log(b.timestamp,text,'SO KHỚP | 1:2 | tách theo SRT liền kề')
    delta=len(norm(ref))-len(norm(text)); score=comparison_score(text,ref)
    log(b.timestamp,text,f'SO KHỚP | SRT={len(norm(ref))}; OCR={len(norm(text))}; thiếu={delta}; similarity={score:.2%}; ngưỡng={match_threshold(len(norm(ref))):.2%}')
    missing_info=missing_block(text,ref)
    if missing_info is None:
        return result([standard],'1:2' if kind=='1:2' else '1:1')
    direction,missing=missing_info
    right=direction=='right'
    log(b.timestamp,text,f'2:1 | thiếu phía {"phải" if right else "trái"}: {missing}; bước={short_interval:g}s')
    edge=hi if right else lo
    def in_missing_side(t):
        return b.timestamp+1e-8<t<=hi+1e-8 if right else lo-1e-8<=t<b.timestamp-1e-8
    def secondary_part(other,part,other_tail):
        if not part: return None
        x,y=simplified(norm(part)),simplified(norm(text))
        if same_reading(part,text) or x in y: return None
        if y in x:
            if not right and x.endswith(y): part=block_slice(part,0,len(x)-len(y))
            elif right and x.startswith(y): part=block_slice(part,len(y),len(x))
            else: return None
        return (other,part,other_tail) if part and not same_reading(part,text) else None
    initial=None
    if pending and in_missing_side(pending[0].timestamp):
        initial=secondary_part(*pending)
        if initial: log(initial[0].timestamp,initial[1],'BỔ SUNG | tận dụng mẫu chuyển block')
    def supplemental_samples():
        entered=False
        entry_owner='NEXT' if right else 'PREV'
        j=0
        while True:
            if cancelled.is_set(): return
            t=edge-j*short_interval if right else edge+j*short_interval
            if not in_missing_side(t): return
            j+=1
            if initial and abs(t-initial[0].timestamp)<1e-8:
                entered=True
                continue
            other=sample(t); owner,part,other_tail=allocate(other)
            if owner in ('PREV','NEXT'):
                if not entered and owner==entry_owner:
                    log(t,other.text,f'BỔ SUNG | bỏ qua biên xuất phát {owner}')
                    continue
                log(t,other.text,f'DỪNG BỔ SUNG | phạm biên {owner}')
                yield 'BOUNDARY',None
                return
            if not part: continue
            entered=True
            candidate=secondary_part(other,part,other_tail)
            if candidate is None:
                log(t,part,'BỔ SUNG | trùng hoặc chồng lấn block chuẩn')
                continue
            yield 'TEXT',candidate
    second, third, second_reason=choose_block(supplemental_samples(),initial)
    if cancelled.is_set(): return ScanResult()
    if second is None:
        log(b.timestamp,text,'KẾT THÚC BỔ SUNG | không có block thứ hai; giữ block chuẩn')
        return result([standard],'2:1_PARTIAL')
    second = commit(second)
    announce(second,second_reason,'BLOCK CHUẨN 2')
    # Recompare in timeline order; never let block 2 replace block 1.
    merged=join_ocr_fragments([item[1] for item in sorted([standard,second],key=lambda x:x[0].timestamp)])
    merged_score=comparison_score(merged,ref)
    # A clearly worse fit with excessive length is evidence of over-merge;
    # do not require the supplemental OCR to equal inaccurate ASR text.
    if len(norm(merged))-len(norm(ref))>2 and merged_score+1e-9<score:
        log(second[0].timestamp,second[1],'LOẠI BLOCK 2 | ghép thừa, so khớp giảm')
        return result([standard],'2:1_PARTIAL')
    log(second[0].timestamp,second[1],f'SO KHỚP SAU GHÉP | similarity={merged_score:.2%}; giới hạn 2 block')
    return result([standard,second],'2:1')


def initialize_cuda_ocr(report, det_model="small", rec_model="medium",
                        default_model=False, method="Det+Rec", text_score=0.5):
    """Start RapidOCR on CUDA and verify real detection/recognition sessions.
    Reject silent CPU fallback. Requires ONNX Runtime GPU and NVIDIA CUDA DLLs.
    """
    import cv2
    import numpy as np
    import onnxruntime as ort
    from rapidocr import RapidOCR, EngineType, ModelType, OCRVersion
    report("log", "Đang nạp CUDA/cuDNN từ môi trường Python...")
    # directory='' discovers NVIDIA wheels in this exact virtual environment.
    ort.preload_dlls(directory="")
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("ONNX Runtime không có CUDAExecutionProvider. Hãy kiểm tra onnxruntime-gpu.")
    report("log", "Đang khởi tạo RapidOCR bằng GPU CUDA...")
    report("log", f"PP-OCRv6: Detection={det_model}, Recognition={rec_model}")
    params = {
        "EngineConfig.onnxruntime.use_cuda": True,
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.model_type": ModelType(det_model),
        "Det.lang_type": "ch",
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.ocr_version": OCRVersion.PPOCRV6,
        "Rec.model_type": ModelType(rec_model),
        "Rec.lang_type": "ch",
    }
    if default_model:
        params = {"EngineConfig.onnxruntime.use_cuda": True}
    engine = RapidOCR(params=params)
    report("log", f"Model thực tế: {engine.cfg.Det.ocr_version}/{engine.cfg.Det.model_type}, "
                  f"{engine.cfg.Rec.ocr_version}/{engine.cfg.Rec.model_type}")
    # RapidOCR lazily creates ONNX sessions. Trigger BOTH detector and recognizer.
    # White test images alone do not create a recognition session.
    sample = np.full((160, 800, 3), 255, dtype=np.uint8)
    cv2.putText(sample, "HELLO WORLD", (50, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    full = method == "Det+Rec"
    probe = engine(sample, use_det=full, use_cls=full, use_rec=True, text_score=0.5)
    probe_text = getattr(probe, "txts", None)
    if probe_text is None or len(probe_text) == 0:
        raise RuntimeError("Không kích hoạt được mô hình Recognition trong bước thử GPU.")
    checked = []
    components = [("Recognition", engine.text_rec)]
    if full:
        components.insert(0, ("Detection", engine.text_det))
    for label, component in components:
        # In RapidOCR 3.10.0: component.session = OrtInferSession;
        # component.session.session = onnxruntime.InferenceSession.
        real_session = getattr(getattr(component, "session", None), "session", None)
        if real_session is None or not hasattr(real_session, "get_providers"):
            raise RuntimeError(f"Không thể kiểm tra ONNX Session của {label}.")
        providers = real_session.get_providers()
        report("log", f"{label}: {providers}")
        if not providers or providers[0] != "CUDAExecutionProvider":
            raise RuntimeError(f"{label} không sử dụng CUDA; đã chặn chạy ngầm trên CPU: {providers}")
        checked.append(label)
    report("log", f"GPU CUDA OK: {method}. Bắt đầu xử lý SRT.")
    return engine

def output_similarity(first: str, second: str, threshold=None) -> float:
    a, b = normalized_text(first)[0], normalized_text(second)[0]
    threshold = match_threshold(len(b)) if threshold is None else threshold
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

def ocr_worker(video_path: Path, srt_path: Path, region, output: Path,
               report, cancelled: threading.Event, interval: float, extension: float = 1.0,
               short_fallback: bool = False, det_model: str = "small",
               rec_model: str = "medium", image_mode: str = "Original",
               method: str = "Det+Rec", seek: str = "Timestamp", text_score: float = 0.5,
               default_model: bool = True, short_interval: float = 0.125):
    import cv2
    segments = read_srt(srt_path)
    report("total", len(segments))
    if cancelled.is_set():
        report("done", str(output))
        return
    chinese_converter()
    validate_ocr_options(image_mode, method, seek, text_score)
    engine = initialize_cuda_ocr(report, det_model, rec_model, default_model, method, text_score)
    report("log", f"OCR: {image_mode} | {method} | {seek} | Text score={text_score:g}")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Không thể mở video: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frames / fps if frames > 0 and fps > 0 else None
    try:
        if duration is None or duration <= 0:
            raise RuntimeError("Không xác định được thời lượng video để giới hạn quét.")
        previous = ScanResult()
        sample_path = output.with_name(output.stem + f"_ocr_samples_{time.time_ns()}.jsonl")
        report("log", f"Dữ liệu OCR thô: {sample_path}")
        with output.open("w", encoding="utf-8-sig", newline="\n") as stream, \
                sample_path.open("x", encoding="utf-8", newline="\n") as samples:
            samples.write(json.dumps({"type": "settings", "video": str(video_path), "srt": str(srt_path),
                "region": list(region), "fps": fps, "image_mode": image_mode, "method": method,
                "seek": seek, "text_score": text_score, "default_model": default_model,
                "model_config": str(engine.cfg), "interval": interval, "short_interval": short_interval, "extension": extension}, ensure_ascii=False) + "\n")
            samples.flush()
            from collections import OrderedDict
            attempted_frames = OrderedDict()
            previous_record_offset = None
            for seq, segment in enumerate(segments, 1):
                def recognize(target):
                    frame_id = max(0, min(frames-1, int(target * fps + 1e-9)))
                    # Timestamp and frame-index seeking do not necessarily return the same frame.
                    key = frame_id if seek == "Frame index" else round(target, 9)
                    if key in attempted_frames:
                        attempted_frames.move_to_end(key)
                        return attempted_frames[key]
                    if seek == "Frame index":
                        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_id)
                    else:
                        capture.set(cv2.CAP_PROP_POS_MSEC, target * 1000.0)
                    valid, frame = capture.read()
                    if not valid:
                        report("log", f"Không đọc được frame @ {target:.3f}s")
                        return ""
                    crop = crop_region(frame, region).copy()
                    if crop.size == 0:
                        return ""
                    reported_frame = capture.get(cv2.CAP_PROP_POS_FRAMES) - 1
                    reported_msec = capture.get(cv2.CAP_PROP_POS_MSEC)
                    begin = time.perf_counter()
                    result = run_ocr_image(engine, crop, cv2, image_mode, method, text_score)
                    elapsed = (time.perf_counter() - begin) * 1000
                    text, confidence = extract_text(result, text_score)
                    def values(name):
                        v = getattr(result, name, None)
                        return [] if v is None else list(v)
                    fragments = [str(v) for v in values("txts")]
                    scores = [float(v) for v in values("scores")]
                    boxes = [v.tolist() if hasattr(v, "tolist") else v for v in values("boxes")]
                    samples.write(json.dumps({"type": "sample", "segment": segment.index,
                        "requested_s": target, "reported_frame": reported_frame,
                        "reported_msec": reported_msec, "text": text, "fragments": fragments,
                        "scores": scores, "clean_confidence": confidence, "boxes": boxes, "elapsed_ms": round(elapsed, 2)},
                        ensure_ascii=False) + "\n")
                    samples.flush()
                    report("log", f"OCR raw @ {target:.3f}s | {' | '.join(fragments) or 'EMPTY'} | {scores} | {elapsed:.0f}ms")
                    attempted_frames[key] = (text, confidence)
                    if len(attempted_frames) > 512:
                        attempted_frames.popitem(last=False)
                    return attempted_frames[key]
                def explain_sample(target, recognized, reason):
                    report("log", f"Segment {segment.index} @ {target:.3f}s: "
                           + (recognized or "(không có chữ)") + f" [{reason}]")
                following = segments[seq] if seq < len(segments) else None
                preceding = segments[seq - 2] if seq > 1 else None
                result = scan_segment(segment, following, previous, duration,
                                      interval, recognize, cancelled, preceding, explain_sample,
                                      all_segments=segments, current_index=seq-1,
                                      extension=extension, short_fallback=short_fallback, short_interval=short_interval)
                if result.last_scan_time is not None:
                    report("log", f"Segment {segment.index} kết thúc quét tại "
                           f"@ {result.last_scan_time:.3f}s: "
                           f"{result.last_scan_raw or '(không có chữ)'} "
                           f"[{result.last_scan_reason}]")
                if result.timestamp is not None and result.raw:
                    report("log", f"Segment {segment.index} OCR đã chấp nhận làm biên trái cho segment sau "
                           f"@ {result.timestamp:.3f}s: {result.raw or '(không có chữ)'} | confidence={result.confidence:.4f}")
                text = result.text
                classification = result.classification
                report("log", f"Segment {segment.index}: {classification} | {text}")
                # Keep immediate record state; last_valid_ocr carries the boundary across NO_TEXT.
                previous = result
                if cancelled.is_set():
                    report("log", "Đã dừng theo yêu cầu. Những segment hoàn thành đã được lưu.")
                    break
                if result.previous_update and preceding and previous_record_offset is not None:
                    # Only the immediately preceding record is revised. Preserve all
                    # completed earlier records and flush the corrected file as before.
                    stream.seek(previous_record_offset)
                    stream.write(srt_record(preceding, result.previous_update))
                    stream.truncate()
                    report("log", f"Cập nhật segment {preceding.index}: {result.previous_update}")
                previous_record_offset = stream.tell()
                stream.write(srt_record(segment, text))
                stream.flush()
                report("progress", (seq, segment.index, text[:90]))
    finally:
        capture.release()
    report("done", str(output))

class OCRApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("OCR Tool V25 — SRT + vùng phụ đề (GPU CUDA)")
        root.geometry("900x900")
        root.minsize(700, 690)
        self.video = tk.StringVar()
        self.srt = tk.StringVar()
        self.json_path = tk.StringVar()
        self.output = tk.StringVar()
        self.region_text = tk.StringVar(value="Chưa có tọa độ")
        self.status = tk.StringVar(value="Sẵn sàng")
        self.percent = tk.StringVar(value="0 / 0")
        self.scan_interval = tk.StringVar(value="0.25")
        self.short_interval = tk.StringVar(value="0.125")
        self.extension = tk.StringVar(value="1")
        self.det_model = tk.StringVar(value="small")
        self.rec_model = tk.StringVar(value="small")
        self.default_model = tk.BooleanVar(value=True)
        self.image_mode = tk.StringVar(value="Original")
        self.ocr_method = tk.StringVar(value="Det+Rec")
        self.seek_mode = tk.StringVar(value="Timestamp")
        self.text_score = tk.StringVar(value="0.5")
        self.short_fallback = tk.BooleanVar(value=False)
        self.scan_summary = tk.StringVar()
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.selector_process = None
        self.selector_json = None
        self.build()
        self.scan_interval.trace_add("write", self.update_scan_summary)
        self.short_interval.trace_add("write", self.update_scan_summary)
        self.extension.trace_add("write", self.update_scan_summary)
        self.update_scan_summary()
        self.root.after(150, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    def build(self):
        pad = ttk.Frame(self.root, padding=15)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="QUÉT OCR THEO SEGMENT SRT — GPU CUDA", font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 12))
        self.row(pad, "1. Video nguồn", self.video, self.pick_video, "Chọn video")
        region_box = ttk.LabelFrame(pad, text="2. Tọa độ vùng phụ đề", padding=10)
        region_box.pack(fill="x", pady=(3, 8))
        toolbar = ttk.Frame(region_box)
        toolbar.pack(fill="x")
        self.select_button = ttk.Button(toolbar, text="Mở công cụ vẽ vùng", command=self.open_selector)
        self.select_button.pack(side="left")
        ttk.Button(toolbar, text="Tải JSON đã lưu", command=self.pick_region).pack(side="left", padx=8)
        ttk.Label(region_box, textvariable=self.region_text, font=("Consolas", 10)).pack(anchor="w", pady=(9, 1))
        ttk.Label(region_box, textvariable=self.json_path, foreground="#555555", wraplength=750).pack(anchor="w")
        self.row(pad, "3. File SRT", self.srt, self.pick_srt, "Chọn SRT")
        self.row(pad, "4. File SRT kết quả", self.output, self.pick_output, "Nơi lưu SRT")
        ttk.Label(pad, text="Đầu ra: OCR hợp lệ; không tìm thấy ghi A hoặc B+SRT theo tùy chọn").pack(anchor="w", pady=(0, 6))
        scan_box = ttk.LabelFrame(pad, text="5. Quét Midpoint / biên", padding=10)
        scan_box.pack(fill="x", pady=(3, 10))
        scan_line = ttk.Frame(scan_box)
        scan_line.pack(fill="x")
        ttk.Label(scan_line, text="Bước quét SRT > 2 ký tự (giây):").pack(side="left")
        ttk.Entry(scan_line, textvariable=self.scan_interval, width=9).pack(side="left", padx=(6, 0))
        ttk.Label(scan_line, text="Mở rộng mỗi phía (giây):").pack(side="left", padx=(16, 0))
        ttk.Entry(scan_line, textvariable=self.extension, width=7).pack(side="left", padx=(6, 0))
        short_line = ttk.Frame(scan_box)
        short_line.pack(fill="x", pady=(6, 0))
        ttk.Label(short_line, text="Bước quét SRT ≤ 2 ký tự (giây):").pack(side="left")
        ttk.Entry(short_line, textvariable=self.short_interval, width=9).pack(side="left", padx=(6, 0))
        ttk.Label(scan_box, textvariable=self.scan_summary, foreground="#555555",
                  wraplength=770).pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(scan_box, text="SRT ≤ 2 ký tự: nếu không có OCR, xuất B+SRT text",
                        variable=self.short_fallback).pack(anchor="w", pady=(6, 0))
        model_box = ttk.LabelFrame(pad, text="6. Model OCR — PP-OCRv6", padding=10)
        model_box.pack(fill="x", pady=(0, 10))
        ttk.Label(model_box, text="Detection:").pack(side="left")
        self.det_select = ttk.Combobox(model_box, textvariable=self.det_model,
                                       values=("tiny", "small", "medium"), state="readonly", width=10)
        self.det_select.pack(side="left", padx=(6, 18))
        ttk.Label(model_box, text="Recognition:").pack(side="left")
        self.rec_select = ttk.Combobox(model_box, textvariable=self.rec_model,
                                       values=("tiny", "small", "medium"), state="readonly", width=10)
        self.rec_select.pack(side="left", padx=(6, 0))
        # Single configuration per run, unlike the diagnostic matrix in OCR Test.
        model_default = ttk.Checkbutton(pad, text="Default V10 (model mặc định RapidOCR)",
                                        variable=self.default_model, command=self.update_model_controls)
        model_default.pack(anchor="w")
        self.default_check = model_default
        image_box = ttk.LabelFrame(pad, text="7. Xử lý ảnh / OCR (từ OCR Test)", padding=8)
        image_box.pack(fill="x", pady=(4, 8))
        line = ttk.Frame(image_box); line.pack(fill="x")
        self.ocr_controls = []
        for label, var, opts, width in (("Ảnh:", self.image_mode, IMAGE_MODES, 17),
                                       ("OCR:", self.ocr_method, OCR_METHODS, 10),
                                       ("Đọc frame:", self.seek_mode, SEEK_MODES, 12)):
            ttk.Label(line, text=label).pack(side="left", padx=(0, 4))
            control = ttk.Combobox(line, textvariable=var, values=opts, state="readonly", width=width)
            control.pack(side="left", padx=(0, 9)); self.ocr_controls.append(control)
        ttk.Label(line, text="Score:").pack(side="left")
        self.score_entry = ttk.Entry(line, textvariable=self.text_score, width=5)
        self.score_entry.pack(side="left", padx=4)
        ttk.Label(image_box, text="Original: ảnh gốc | Grayscale: ảnh xám | Otsu: đen trắng | Upscale: phóng to 2x",
                  foreground="#555555", wraplength=820).pack(anchor="w", pady=(6, 0))
        ttk.Label(image_box, text="Det+Rec: tìm và đọc chữ | Rec only: đọc cả vùng crop | Score: ngưỡng tin cậy 0–1",
                  foreground="#555555", wraplength=820).pack(anchor="w")
        self.update_model_controls()
        commands = ttk.Frame(pad)
        commands.pack(fill="x")
        self.run_button = ttk.Button(commands, text="▶ BẮT ĐẦU OCR", command=self.start)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(commands, text="■ Dừng", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(commands, textvariable=self.percent).pack(side="right")
        self.progress = ttk.Progressbar(pad, mode="determinate")
        self.progress.pack(fill="x", pady=(10, 6))
        ttk.Label(pad, textvariable=self.status, wraplength=760).pack(anchor="w")
        ttk.Label(pad, text="Nhật ký", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(12, 3))
        logframe = ttk.Frame(pad)
        logframe.pack(fill="both", expand=True)
        self.logbox = tk.Text(logframe, height=11, state="disabled", wrap="word", font=("Consolas", 9))
        scroll = ttk.Scrollbar(logframe, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=scroll.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
    def update_model_controls(self):
        state = "disabled" if self.running or self.default_model.get() else "readonly"
        self.det_select.configure(state=state)
        self.rec_select.configure(state=state)

    def set_ocr_controls(self, running):
        self.default_check.configure(state="disabled" if running else "normal")
        for control in self.ocr_controls:
            control.configure(state="disabled" if running else "readonly")
        self.score_entry.configure(state="disabled" if running else "normal")
        self.update_model_controls()

    def update_scan_summary(self, *_):
        try:
            step = validate_interval(float(self.scan_interval.get().strip().replace(",", ".")))
            short_step = validate_interval(float(self.short_interval.get().strip().replace(",", ".")))
            extension = validate_extension(float(self.extension.get().strip().replace(",", ".")))
            self.scan_summary.set(
                f"Quét Midpoint trái/phải; mở rộng {extension:g}s mỗi phía, bước {step:g}s. "
                f"SRT ≤ 2 ký tự: bước {short_step:g}s. "
                "SRT > 2: chốt confidence > 95%; tối đa 2 block. SRT ≤ 2: trái → phải, chốt > 95%, 1:1. Case 2:1 dùng bước quét ngắn; dừng khi đủ 2 block sạch hoặc phạm biên. "
                "Không có OCR: A. Biên trái: OCR hợp lệ gần nhất (giữ qua A); biên phải: SRT kế tiếp. " + MATCH_RULES)
        except (ValueError, OverflowError):
            self.scan_summary.set("Nhập cả hai bước quét > 0 đến 60 giây và thời gian mở rộng >= 0.")
    def row(self, parent, title, var, callback, button_label):
        ttk.Label(parent, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4, 3))
        line = ttk.Frame(parent)
        line.pack(fill="x", pady=(0, 8))
        ttk.Entry(line, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(line, text=button_label, command=callback).pack(side="left", padx=(8, 0))
    def pick_video(self):
        picked = filedialog.askopenfilename(title="Chọn video", filetypes=VIDEO_TYPES)
        if picked:
            self.video.set(picked)
            expected = Path(picked).with_name(Path(picked).stem + "_subtitle_region.json")
            if expected.is_file():
                self.load_region(expected)
            else:
                self.json_path.set("")
                self.region_text.set("Chưa có tọa độ cho video này")
    def pick_srt(self):
        picked = filedialog.askopenfilename(title="Chọn file SRT", filetypes=SRT_TYPES)
        if picked:
            self.srt.set(picked)
            self.output.set(str(Path(picked).with_name(Path(picked).stem + "_ocr.srt")))
    def pick_output(self):
        picked = filedialog.asksaveasfilename(title="Lưu kết quả OCR", defaultextension=".srt",
                                              filetypes=SRT_TYPES,
                                              initialfile=Path(self.output.get()).name or "ocr_srt.srt")
        if picked:
            self.output.set(picked)
    def pick_region(self):
        picked = filedialog.askopenfilename(title="Chọn JSON vùng phụ đề", filetypes=[("JSON", "*.json")])
        if picked:
            self.load_region(Path(picked))
    def load_region(self, path: Path):
        try:
            x, y, w, h = parse_region(path)
        except Exception as exc:
            messagebox.showerror("Lỗi tọa độ", str(exc))
            return
        self.json_path.set(str(path))
        self.region_text.set(f"X: {x * 100:.4f}%   Y: {y * 100:.4f}%   Width: {w * 100:.4f}%   Height: {h * 100:.4f}%")
        self.append_log("Đã nạp tọa độ: " + str(path))
    def open_selector(self):
        path = Path(self.video.get().strip().strip('"'))
        if not path.is_file():
            messagebox.showwarning("Thiếu video", "Hãy chọn video nguồn trước.")
            return
        if not SELECTOR_SCRIPT.is_file():
            messagebox.showerror("Thiếu công cụ", f"Đặt subtitle_region_selector.py cùng thư mục với ứng dụng:\n{SELECTOR_SCRIPT}")
            return
        if self.selector_process and self.selector_process.poll() is None:
            messagebox.showinfo("Đang mở", "Cửa sổ chọn vùng đang mở. Hãy lưu rồi đóng cửa sổ đó.")
            return
        json_file = path.with_name(path.stem + "_subtitle_region.json")
        self.selector_json = json_file
        try:
            self.selector_process = subprocess.Popen(
                [sys.executable, str(SELECTOR_SCRIPT), str(path), "--output", str(json_file)], cwd=str(APP_DIR)
            )
        except Exception as exc:
            messagebox.showerror("Không mở được công cụ", str(exc))
            return
        self.select_button.configure(state="disabled")
        self.status.set("Chọn vùng trên video, nhấn S/Enter để lưu rồi Q/Esc để đóng cửa sổ chọn vùng.")
        self.root.after(400, self.check_selector)
    def check_selector(self):
        if self.selector_process and self.selector_process.poll() is None:
            self.root.after(400, self.check_selector)
            return
        self.select_button.configure(state="normal")
        if self.selector_json and self.selector_json.is_file():
            self.load_region(self.selector_json)
            self.status.set("Đã nạp tọa độ từ công cụ chọn vùng.")
        else:
            self.status.set("Cửa sổ chọn vùng đã đóng nhưng chưa tìm thấy JSON. Nhấn S/Enter để lưu.")
        self.selector_process = None
    def append_log(self, line):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
    def report(self, what, data):
        self.queue.put((what, data))
    def start(self):
        if self.running:
            return
        try:
            video = Path(self.video.get().strip().strip('"'))
            srt = Path(self.srt.get().strip().strip('"'))
            region_file = Path(self.json_path.get().strip())
            output = Path(self.output.get().strip().strip('"'))
            if not video.is_file():
                raise ValueError("Chưa chọn video hợp lệ")
            if not srt.is_file():
                raise ValueError("Chưa chọn file SRT hợp lệ")
            if not region_file.is_file():
                raise ValueError("Chưa chọn tọa độ vùng phụ đề")
            region = parse_region(region_file)
            try:
                interval = validate_interval(float(self.scan_interval.get().strip().replace(",", ".")))
                short_interval = validate_interval(float(self.short_interval.get().strip().replace(",", ".")))
                extension = validate_extension(float(self.extension.get().strip().replace(",", ".")))
            except (ValueError, OverflowError) as exc:
                raise ValueError(f"Cấu hình quét không hợp lệ: {exc}") from exc
            image_mode, method, seek = self.image_mode.get(), self.ocr_method.get(), self.seek_mode.get()
            text_score = float(self.text_score.get().strip().replace(",", "."))
            validate_ocr_options(image_mode, method, seek, text_score)
            default_model = self.default_model.get()
            count = len(read_srt(srt))
            if not str(self.output.get()).strip():
                raise ValueError("Chưa chọn đường dẫn SRT")
            if output.suffix.lower() != ".srt":
                raise ValueError("File đầu ra phải có phần mở rộng .srt")
            if output.resolve() in (video.resolve(), srt.resolve(), region_file.resolve()):
                raise ValueError("File đầu ra không được trùng file đầu vào")
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() and not messagebox.askyesno("Ghi đè", f"File đã tồn tại. Ghi đè?\n{output}"):
                return
        except Exception as exc:
            messagebox.showerror("Thông tin chưa hợp lệ", str(exc))
            return
        short_fallback = self.short_fallback.get()
        det_model, rec_model = self.det_model.get(), self.rec_model.get()
        self.running = True
        self.set_ocr_controls(True)
        self.stop_event.clear()
        self.progress.configure(maximum=count, value=0)
        self.percent.set(f"0 / {count}")
        self.status.set("Đang khởi tạo RapidOCR CUDA...")
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.append_log(f"Bắt đầu {count} segment, vùng chọn: {region_file.name}")
        self.append_log(f"Quét Midpoint trái/phải; bước {interval:g}s; SRT ≤ 2 ký tự: {short_interval:g}s; mở rộng {extension:g}s mỗi phía; {MATCH_RULES}")
        self.append_log("SRT > 2: Midpoint, trái/phải, chốt confidence > 95%; 1:1 / 1:2 / 2:1. SRT ≤ 2: trái → phải, chốt > 95%, 1:1. Case 2:1 dùng bước quét ngắn; dừng khi đủ 2 block sạch hoặc phạm biên.")
        self.append_log("Không có OCR hợp lệ: A. Biên trái: OCR cuối hợp lệ (giữ qua A); biên phải: SRT kế tiếp.")
        self.append_log(f"Dự phòng SRT ≤ 2 ký tự: {'B+SRT' if short_fallback else 'A'}")
        def job():
            try:
                ocr_worker(video, srt, region, output, self.report, self.stop_event, interval, extension,
                           short_fallback, det_model, rec_model, image_mode, method, seek, text_score, default_model,
                           short_interval=short_interval)
            except Exception as exc:
                self.report("error", f"{exc}\n{traceback.format_exc()}")
        threading.Thread(target=job, daemon=True).start()
    def stop(self):
        if self.running:
            self.stop_event.set()
            self.status.set("Đang yêu cầu dừng sau thao tác OCR hiện tại...")
            self.stop_button.configure(state="disabled")
    def poll(self):
        try:
            while True:
                what, data = self.queue.get_nowait()
                if what == "log":
                    self.append_log(data)
                elif what == "total":
                    self.progress.configure(maximum=data)
                elif what == "progress":
                    number, index, snippet = data
                    self.progress.configure(value=number)
                    self.percent.set(f"{number} / {int(float(self.progress['maximum']))}")
                    self.status.set(f"Đang quét segment {index}...")
                    self.append_log(f"[{index}] {snippet}")
                elif what == "done":
                    self.append_log("Đã lưu SRT: " + data)
                    self.status.set("Đã dừng; lưu kết quả một phần." if self.stop_event.is_set() else "Hoàn tất OCR.")
                    self.running = False
                    self.set_ocr_controls(False)
                    self.run_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    messagebox.showinfo("Kết quả OCR", f"Đã lưu tại:\n{data}")
                elif what == "error":
                    self.append_log(data)
                    self.status.set("Có lỗi khi OCR, xem nhật ký.")
                    self.running = False
                    self.set_ocr_controls(False)
                    self.run_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    messagebox.showerror("OCR thất bại", data.splitlines()[0])
        except queue.Empty:
            pass
        self.root.after(150, self.poll)
    def on_close(self):
        if self.running:
            messagebox.showwarning("Đang xử lý", "Hãy bấm Dừng trước khi thoát để lưu các segment đã xử lý.")
            return
        if self.selector_process and self.selector_process.poll() is None:
            messagebox.showwarning("Đang chọn vùng", "Hãy đóng cửa sổ chọn vùng trước khi thoát.")
            return
        self.root.destroy()

def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    OCRApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
