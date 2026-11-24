"""Match OCR readings to existing SRT cues using the ocrLab algorithm."""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

TIMECODE = re.compile(r"^\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*$")

TIMELINE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)(?:\s+.*)?$")

MATCH_THRESHOLD = 0.70


NOT_FOUND = "Không thấy chữ"


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

def validate_scan_limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Số lần quét mỗi phía phải là số nguyên dương")
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


def normalized_text(text: str) -> tuple[str, list[int]]:
    """Keep source positions so a matched OCR block can be returned verbatim."""
    chars, positions = [], []
    for pos, char in enumerate(text):
        for folded in char.casefold():
            if folded.isalnum():
                chars.append(folded)
                positions.append(pos)
    return "".join(chars), positions

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
    previous_update: str = ""
    previous_update_frame: str = ""

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


def scan_segment(segment: Segment, next_segment: Segment | None,
                 previous: ScanResult, duration: float, interval: float,
                 recognize, cancelled: threading.Event,
                 previous_segment: Segment | None = None, on_sample=None,
                 all_segments=None, current_index=None, max_scans_per_side: int = 5) -> ScanResult:
    validate_interval(interval)
    validate_scan_limit(max_scans_per_side)
    midpoint = (segment.start+segment.end)/2
    left_limit = max(0,(previous_segment.start+previous_segment.end)/2) if previous_segment else 0
    right_limit = min(duration,(next_segment.start+next_segment.end)/2) if next_segment else duration
    refs = [srt_reference(v.text) if v else "" for v in (previous_segment,segment,next_segment)]
    ref = refs[1]; length = len(norm(ref))
    short_srt = length == 2
    if short_srt:
        interval /= 2
    extra_next = (all_segments[current_index+2].text if all_segments is not None and
                  current_index is not None and current_index+2<len(all_segments) else "")
    # A future block is cached ONLY by a validated partition, not by leftover text.
    cached = previous.boundary_owner == segment.index and previous.raw and fits(previous.raw, ref)
    if cached and not short_srt:
        return ScanResult(text=previous.raw,raw=previous.raw,timestamp=previous.timestamp,
                          reference=segment.text,parts=(previous.raw,),classification="1:N",
                          boundary_owner=segment.index,used_frame=previous.used_frame,
                          used_end=len(norm(previous.used_frame)),last_scan_reason="block đã phân bổ hợp lệ")
    history=[]  # (raw, accepted, left boundary, right boundary), includes rejected readings
    observations=[]  # (timestamp, selected OCR, raw OCR)
    left_open=right_open=True; done=False
    text=""; case="NO_TEXT"; boundary=""; owner=1; boundary_time=None
    used_frame=previous.used_frame; used_end=previous.used_end
    last_time=None; last_raw=""; last_reason=""
    previous_update=""; previous_update_frame=""
    if cached and short_srt:
        t = previous.timestamp if previous.timestamp is not None else midpoint
        observations = [(t, previous.raw, previous.used_frame or previous.raw)]
        text = boundary = previous.raw
        boundary_time = t
        case = "1:N"
    for distance in range(max_scans_per_side+1):
        if done or not (left_open or right_open): break
        for offset in ((0.,) if distance==0 else (-distance*interval,distance*interval)):
            if cancelled.is_set(): return ScanResult()
            if (offset<0 and not left_open) or (offset>0 and not right_open): continue
            t=midpoint+offset
            if t<left_limit or (previous_segment and t<=left_limit):
                left_open=False; continue
            if t>=right_limit:
                right_open=False; continue
            result=recognize(t); raw=result[0] if isinstance(result,tuple) else result
            raw=raw or ""
            selected=""; ls=rs=False; tail=""; tail_owner=1; reason="không có chữ"
            if norm(raw):
                # Compare full readings before any cut. Rejected variants remain rejected.
                split=partition(raw,refs)
                same_frame=bool(used_frame and frame_variant(raw,used_frame))
                consumed=mapped_end(used_frame,raw,used_end) if same_frame else 0
                # A genuine separate current subtitle outranks an old frame variant.
                current_exact=(comparison_score(raw,ref)>=1 and
                               comparison_score(raw,refs[0])<1 and norm(raw)!=norm(used_frame))
                revised=fuller_previous(raw, previous, previous_segment, refs, split)
                # A rejected earlier reading can still supply a fuller version of
                # the previous owner's text; do this before inheriting rejection.
                if revised and not current_exact:
                    previous_update,previous_update_frame=revised,raw
                    used_frame,used_end=raw,len(norm(raw))
                    previous=replace(previous,text=revised,raw=raw,
                                     parts=previous.parts[:-1]+(raw,),
                                     used_frame=raw,used_end=used_end)
                    ls=True; reason="OCR đầy đủ hơn của segment trước; cập nhật segment trước"
                else:
                    rejected=next((h for h in history if not h[1] and equivalent(raw,h[0])),None)
                    if rejected and not current_exact and not split:
                        ls,rs=rejected[2:]; reason="OCR cũ/tương đồng đã bị loại; bỏ qua"
                    elif consumed and not current_exact:
                        # Only a validated partition may allocate previously unused text.
                        valid_split=bool(split and any(j==1 and a>=consumed for j,a,b in split))
                        if valid_split:
                            selected,ls,rs,tail,tail_owner,reason=assess(raw,refs,previous.raw,False,offset)
                        else:
                            ls=True; reason="OCR đã dùng; không có block còn lại khớp SRT"
                    elif not split and future_only(raw,refs,extra_next):
                        rs=True; reason="biên nội dung SRT phía sau"
                    else:
                        selected,ls,rs,tail,tail_owner,reason=assess(raw,refs,previous.raw,False,offset)
                if ls: left_open=False
                if rs and offset>=0: right_open=False
                if short_srt and selected and not fits(selected, ref):
                    selected = ""
                    reason += "; OCR không đạt ngưỡng SRT 2 ký tự, bỏ qua"
                if selected:
                    if short_srt:
                        # Keep the last valid reading in scan order, never concatenate.
                        observations = [(t, selected, raw)]
                        text = selected
                        case = "1:N" if reason.startswith("1:N") else "1:1"
                        boundary = tail; owner = tail_owner; boundary_time = t
                        reason += "; OCR hợp lệ, tiếp tục quét SRT 2 ký tự"
                    elif not observations:
                        observations=[(t,selected,raw)]; text=selected
                        case="1:N" if reason.startswith("1:N") else "1:1"
                        boundary=tail; owner=tail_owner; boundary_time=t
                        done=(case=="1:N" or len(norm(selected))>=length or span_in_reference(selected,ref) is None)
                    else:
                        candidate=list(observations)
                        duplicate=next((i for i,o in enumerate(candidate) if equivalent(o[1],selected)),None)
                        if duplicate is not None:
                            old=candidate[duplicate]
                            if len(norm(selected))>len(norm(old[1])) and comparison_score(selected,ref)>comparison_score(old[1],ref):
                                candidate[duplicate]=(t,selected,raw)
                            else:
                                candidate=None; reason+="; OCR tương đồng, bỏ qua"
                        else:
                            new_span=span_in_reference(selected,ref)
                            if new_span is None:
                                candidate=None; reason+="; không khớp phần SRT hiện tại"
                            elif any((old_span:=span_in_reference(o[1],ref)) and
                                     max(0,min(old_span.end,new_span.end)-max(old_span.start,new_span.start))/min(old_span.end-old_span.start,new_span.end-new_span.start)>=.7 for o in candidate):
                                candidate=None; reason+="; OCR cùng vị trí SRT, bỏ qua"
                            else:
                                candidate.append((t,selected,raw))
                        if candidate is not None:
                            candidate.sort(key=lambda o:o[0])
                            merged=join_ocr_fragments([o[1] for o in candidate])
                            block,bl,br,bt,bo,why=assess(merged,refs,previous.raw,False,offset)
                            if bl: left_open=False
                            if br and offset>=0: right_open=False
                            # A trial merge may not take text belonging to either neighbor.
                            if block and comparison_score(block,ref)>comparison_score(text,ref)+1e-9 and not (bl or br):
                                observations=candidate; text=block
                                case="N:1" if len(candidate)>1 else "1:1"
                                boundary=candidate[-1][1]; owner=1; boundary_time=candidate[-1][0]
                                done=len(norm(text))>=length or comparison_score(text,ref)>=1.
                                reason+="; ghép hợp lệ"
                            else:
                                reason+="; không chấp nhận ghép"
                    if done: reason+="; chuyển segment"
                    else: reason+="; quét thêm"
                history.append((raw,bool(selected and any(o[2]==raw for o in observations)),ls,rs))
            if on_sample: on_sample(t,raw,reason)
            last_time,last_raw,last_reason=t,raw,reason
            if done: break
    if not text:
        return ScanResult(reference=segment.text,last_scan_time=last_time,last_scan_raw=last_raw,last_scan_reason=last_reason,
                          used_frame=used_frame,used_end=used_end,
                          previous_update=previous_update,previous_update_frame=previous_update_frame)
    last=sorted(observations,key=lambda o:o[0])[-1]
    frame=last[2]; fragment=last[1]
    start=simplified(norm(frame)).find(simplified(norm(fragment)))
    end_used=start+len(norm(fragment)) if start>=0 else len(norm(frame))
    owner_segment=(previous_segment,segment,next_segment)[owner]
    return ScanResult(text,boundary,boundary_time,segment.text,tuple(o[1] for o in observations),
                      last_time,last_raw,last_reason,case,owner_segment.index if owner_segment else None,frame,end_used,
                      previous_update=previous_update,previous_update_frame=previous_update_frame)


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
