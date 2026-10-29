"""Use reference TXT lines as the canonical Step 2 segments and recover timing."""

from __future__ import annotations

import json
import math
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ...common.models import Segment, StepId, StepResult, TranscriptCandidate
from ...state import ProjectState
from .service import assess_transcript_timeline


def reference_path(project_root: Path, input_folder: Path, video_name: str) -> Path:
    filename = f"{Path(video_name).stem}.txt"
    for folder in (project_root, input_folder):
        path = folder / filename
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Không tìm thấy {filename} trong thư mục project hoặc thư mục input."
    )


def _read_lines(path: Path) -> list[str]:
    content = path.read_bytes()
    encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        lines = [line.strip() for line in content.decode(encoding).splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        raise ValueError(f"Không đọc được {path.name}; cần UTF-8 hoặc UTF-16.") from exc
    if not lines:
        raise ValueError(f"{path.name} không có dòng hội thoại nào.")
    return lines


def _normalized(value: str) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFKC", value).casefold()
        if character.isalnum()
    )


def _match_timelines(
    segments: list[Segment], lines: list[str],
) -> tuple[dict[int, int], list[dict[str, object]]]:
    """Anchor exact lines first, then match only inside the remaining ordered gaps."""
    source_texts = [_normalized(segment.source_text) for segment in segments]
    target_texts = [_normalized(line) for line in lines]
    valid_source = [
        bool(text) and math.isfinite(segment.start) and math.isfinite(segment.end)
        and segment.start >= 0 and segment.end > segment.start
        for segment, text in zip(segments, source_texts, strict=True)
    ]
    # Consider every full-line match. Keep the most anchors in order, then
    # prefer the chain with the greatest total ASR speaking duration.
    exact_index: dict[str, list[int]] = defaultdict(list)
    for line_index, text in enumerate(target_texts):
        if text:
            exact_index[text].append(line_index)
    size = len(lines)
    tree = [0] * (size + 1)
    scores: list[tuple[int, float, int]] = [(0, 0.0, 0)]
    links: list[tuple[int, int, int]] = [(-1, -1, -1)]

    def better_exact(left: int, right: int) -> int:
        return left if scores[left] >= scores[right] else right

    def query_exact(position: int) -> int:
        best = 0
        while position > 0:
            best = better_exact(best, tree[position])
            position -= position & -position
        return best

    def update_exact(position: int, state: int) -> None:
        while position <= size:
            tree[position] = better_exact(state, tree[position])
            position += position & -position

    for source_index, segment in enumerate(segments):
        if not valid_source[source_index]:
            continue
        pending: list[tuple[int, int]] = []
        for line_index in exact_index.get(source_texts[source_index], []):
            prior = query_exact(line_index)
            previous = scores[prior]
            position_gap = abs(
                source_index / max(1, len(segments) - 1)
                - line_index / max(1, size - 1)
            )
            scores.append((
                previous[0] + 1,
                previous[1] + segment.end - segment.start,
                previous[2] - round(position_gap * 1000),
            ))
            links.append((prior, source_index, line_index))
            pending.append((line_index + 1, len(scores) - 1))
        for position, state in pending:
            update_exact(position, state)
    anchors: list[tuple[int, int]] = []
    chosen = query_exact(size)
    while chosen:
        prior, source_index, line_index = links[chosen]
        anchors.append((source_index, line_index))
        chosen = prior
    anchors.reverse()
    mapped: dict[int, int] = {source_index: line_index for source_index, line_index in anchors}
    methods: dict[int, str] = {source_index: "exact" for source_index, _ in anchors}
    boundaries = [(-1, -1), *anchors, (len(segments), len(lines))]
    for (source_before, line_before), (source_after, line_after) in zip(boundaries, boundaries[1:]):
        if source_after - source_before <= 1 or line_after - line_before <= 1:
            continue
        source_range = range(source_before + 1, source_after)
        line_range = range(line_before + 1, line_after)
        exact_index: dict[str, list[int]] = defaultdict(list)
        gram_index: dict[str, set[int]] = defaultdict(set)
        for line_index in line_range:
            target = target_texts[line_index]
            exact_index[target].append(line_index)
            for offset in range(len(target) - 2):
                gram_index[target[offset:offset + 3]].add(line_index)

        # Sparse weighted ordered matching: each ASR row and TXT line is used once.
        size = line_after - line_before - 1
        tree = [0] * (size + 1)
        scores: list[tuple[int, int, float, int]] = [(0, 0, 0.0, 0)]
        links: list[tuple[int, int, int]] = [(-1, -1, -1)]

        def better(left: int, right: int) -> int:
            return left if scores[left] >= scores[right] else right

        def query(position: int) -> int:
            best = 0
            while position > 0:
                best = better(best, tree[position])
                position -= position & -position
            return best

        def update(position: int, state: int) -> None:
            while position <= size:
                tree[position] = better(state, tree[position])
                position += position & -position

        for source_index in source_range:
            if not valid_source[source_index]:
                continue
            source = source_texts[source_index]
            threshold = 0.9 if len(source) < 4 else 0.85
            if len(source) <= 6:
                candidates = exact_index.get(source, [])
            else:
                candidates = sorted({
                    line_index
                    for offset in range(len(source) - 2)
                    for line_index in gram_index.get(source[offset:offset + 3], ())
                })
            pending: list[tuple[int, int]] = []
            for line_index in candidates:
                target = target_texts[line_index]
                if not target:
                    continue
                common = SequenceMatcher(None, source, target, autojunk=False).find_longest_match(
                    0, len(source), 0, len(target)
                ).size
                similarity = common / len(source)
                if similarity + 1e-12 < threshold:
                    continue
                prior = query(line_index - line_before - 1)
                old = scores[prior]
                displacement = abs(
                    (source_index - source_before) / (source_after - source_before)
                    - (line_index - line_before) / (line_after - line_before)
                )
                scores.append((old[0] + 1, old[1] + common, old[2] + similarity,
                               old[3] - round(displacement * 1000)))
                links.append((prior, source_index, line_index))
                pending.append((line_index - line_before, len(scores) - 1))
            for position, state in pending:
                update(position, state)
        chosen = query(size)
        while chosen:
            prior, source_index, line_index = links[chosen]
            mapped[source_index] = line_index
            methods[source_index] = "partial"
            chosen = prior

    # Use the already numbered matches as boundaries for the remaining TXT
    # lines. A short ASR phrase must be considered even when it is not exact.
    ordered_matches = [(-1, -1), *sorted(mapped.items()), (len(segments), len(lines))]
    gaps = [
        (source_before, line_before, source_after, line_after)
        for (source_before, line_before), (source_after, line_after)
        in zip(ordered_matches, ordered_matches[1:])
    ]
    while gaps:
        source_before, line_before, source_after, line_after = gaps.pop()
        available_sources = [
            index for index in range(source_before + 1, source_after)
            if valid_source[index] and index not in mapped
        ]
        available_lines = list(range(line_before + 1, line_after))
        if not available_sources or not available_lines:
            continue
        reserve_order = len(available_sources) >= len(available_lines)
        best: tuple[tuple[float, ...], int, int] | None = None
        for line_position, line_index in enumerate(available_lines):
            target = target_texts[line_index]
            if not target:
                continue
            neighbors = [
                target_texts[neighbor] for neighbor in (line_index - 1, line_index + 1)
                if 0 <= neighbor < len(target_texts) and target_texts[neighbor] != target
            ]
            for source_position, source_index in enumerate(available_sources):
                if reserve_order and (
                    source_position < line_position
                    or len(available_sources) - source_position - 1
                    < len(available_lines) - line_position - 1
                ):
                    continue
                source = source_texts[source_index]
                if any(
                    SequenceMatcher(None, source, neighbor, autojunk=False)
                    .find_longest_match(0, len(source), 0, len(neighbor)).size
                    / len(source) > 0.70
                    for neighbor in neighbors
                ):
                    continue
                common = SequenceMatcher(
                    None, source, target, autojunk=False
                ).find_longest_match(0, len(source), 0, len(target)).size
                similarity = common / len(source)
                rate_band, rate_distance = _reading_rate_score(
                    segments[source_index], len(target)
                )
                score = (
                    float(similarity >= 0.30),
                    similarity if similarity >= 0.30 else 0.0,
                    float(-rate_band),
                    -rate_distance,
                    float(segments[source_index].id),
                )
                if best is None or score > best[0]:
                    best = (score, source_index, line_index)
        if best is None:
            continue
        _, source_index, line_index = best
        mapped[source_index] = line_index
        methods[source_index] = "neighbor" if best[0][0] else "timing"
        gaps.append((source_before, line_before, source_index, line_index))
        gaps.append((source_index, line_index, source_after, line_after))

    assignments: list[dict[str, object]] = []
    for source_index, line_index in sorted(mapped.items()):
        source, target = source_texts[source_index], target_texts[line_index]
        common = SequenceMatcher(None, source, target, autojunk=False).find_longest_match(
            0, len(source), 0, len(target)
        ).size
        assignments.append({
            "source_segment_ids": [segments[source_index].id],
            "txt_line": line_index + 1,
            "method": methods[source_index],
            "similarity": round(common / len(source), 3),
        })
    return mapped, assignments


def _duplicate_threshold(length: int) -> float:
    if length <= 5:
        return 0.6
    if length < 15:
        return 0.5
    return 0.4


def _same_asr_phrase(first: str, second: str) -> bool:
    if not first or not second:
        return False
    common = SequenceMatcher(None, first, second, autojunk=False).find_longest_match(
        0, len(first), 0, len(second)
    ).size
    return (common / len(first) >= _duplicate_threshold(len(first))
            and common / len(second) >= _duplicate_threshold(len(second)))


def _reading_rate_score(segment: Segment, character_count: int) -> tuple[int, float]:
    seconds_per_character = (segment.end - segment.start) / max(1, character_count)
    in_reference_range = 0.18 <= seconds_per_character <= 0.30
    return (0 if in_reference_range else 1, abs(seconds_per_character - 0.22))


def _remove_duplicate_pairs(
    segments: list[Segment], lines: list[str], mapped: dict[int, int],
) -> tuple[dict[int, int], set[int], dict[int, tuple[float, float]]]:
    """Resolve adjacent ASR repetitions in disjoint pairs after TXT mapping."""
    source_texts = [_normalized(segment.source_text) for segment in segments]
    removed: set[int] = set()
    combined_times: dict[int, tuple[float, float]] = {}
    index = 0
    while index + 1 < len(segments):
        first, second = index, index + 1
        if (not all(math.isfinite(value) for value in (
                segments[first].start, segments[first].end,
                segments[second].start, segments[second].end))
                or segments[first].end <= segments[first].start
                or segments[second].end <= segments[second].start
                or segments[second].start < segments[first].start):
            index += 1
            continue
        if not _same_asr_phrase(source_texts[first], source_texts[second]):
            index += 1
            continue
        first_line, second_line = mapped.get(first), mapped.get(second)
        if first_line is not None and second_line is not None:
            index += 2
            continue
        if first_line is None and second_line is None:
            first_length, second_length = len(source_texts[first]), len(source_texts[second])
            if first_length != second_length:
                keep = first if first_length > second_length else second
            else:
                keep = min((first, second), key=lambda position: (
                    _reading_rate_score(segments[position], first_length), position
                ))
            combined_times[keep] = (segments[first].start, segments[second].end)
        else:
            assigned = first if first_line is not None else second
            alternative = second if assigned == first else first
            line_index = mapped[assigned]
            target = _normalized(lines[line_index])
            match = SequenceMatcher(
                None, source_texts[alternative], target, autojunk=False
            ).find_longest_match(0, len(source_texts[alternative]), 0, len(target)).size
            alternative_compatible = (
                bool(source_texts[alternative]) and
                match / len(source_texts[alternative]) >=
                _duplicate_threshold(len(source_texts[alternative]))
            )
            keep = assigned
            if alternative_compatible and _reading_rate_score(
                segments[alternative], len(target)
            ) < _reading_rate_score(segments[assigned], len(target)):
                keep = alternative
                mapped[keep] = mapped.pop(assigned)
        removed.add(second if keep == first else first)
        index += 2
    return mapped, removed, combined_times


def _original_transcript(state: ProjectState, candidate_id: str):
    """Follow Step 2.5 outputs back to the transcript selected before TXT repair."""
    visited: set[str] = set()
    while candidate_id and candidate_id not in visited:
        visited.add(candidate_id)
        candidate = state.transcript_candidate(candidate_id)
        if candidate is None:
            break
        payload = json.loads(Path(candidate.path).read_text(encoding="utf-8"))
        if not isinstance(payload.get("txt_repair"), dict):
            result = state.candidate_result(StepId.STT, candidate_id)
            if result is not None:
                return candidate_id, payload, result
            break
        candidate_id = str(payload.get("source_transcript_candidate_id", ""))
    raise ValueError("Không tìm thấy output Step 2 gốc để đối chiếu TXT.")


def repair_transcript(
    state: ProjectState, txt_path: Path, progress: Callable[[int, str], None] | None = None,
) -> tuple[StepResult | None, str]:
    selected_id = state.selected_transcript_candidate_id or state.default_transcript_candidate_id
    if not selected_id:
        raise ValueError("Video chưa có output Step 2 hợp lệ để đối chiếu.")
    source_id, payload, source_result = _original_transcript(state, selected_id)
    rows = payload.get("segments")
    if not isinstance(rows, list) or len(rows) != len(source_result.segments):
        raise ValueError("Danh sách segment trong output Step 2 không hợp lệ.")
    lines = _read_lines(txt_path)
    if progress:
        progress(20, f"Đang căn chỉnh {len(rows)} segment với {len(lines)} dòng TXT…")
    mapped, assignments = _match_timelines(source_result.segments, lines)
    mapped, removed, combined_times = _remove_duplicate_pairs(
        source_result.segments, lines, mapped
    )
    segment_index_by_line = {line_index + 1: index for index, line_index in mapped.items()}
    for assignment in assignments:
        source_index = segment_index_by_line[assignment["txt_line"]]
        source_segment = source_result.segments[source_index]
        if assignment["source_segment_ids"] != [source_segment.id]:
            source = _normalized(source_segment.source_text)
            target = _normalized(lines[mapped[source_index]])
            common = SequenceMatcher(None, source, target, autojunk=False).find_longest_match(
                0, len(source), 0, len(target)
            ).size
            assignment["source_segment_ids"] = [source_segment.id]
            assignment["method"] = "duplicate_rate"
            assignment["similarity"] = round(common / len(source), 3)
    unchanged = len(rows) - len(mapped)
    if progress:
        progress(75, f"Đã ghép {len(mapped)}/{len(rows)} segment ASR; đang lưu output mới…")
    now = datetime.now(timezone.utc)
    candidate_id = f"stt-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    folder = Path(state.workspace_path("transcripts", candidate_id))
    output_path = folder / "transcript.json"
    new_segments = []
    new_rows = []
    for index, (row, segment) in enumerate(zip(rows, source_result.segments, strict=True)):
        old_text = str(row.get("text", row.get("source_text", "")))
        text = "" if index in removed else lines[mapped[index]] if index in mapped else old_text
        start, end = combined_times.get(index, (segment.start, segment.end))
        if index in removed:
            end = start
        new_segments.append(Segment(
            id=segment.id, start=start, end=end,
            speaker_id=segment.speaker_id, merge_parts=[dict(part) for part in segment.merge_parts],
            source_text=text,
        ))
        new_rows.append({**row, "text_old": old_text, "text": text,
                         "start": start, "end": end})
    issues = assess_transcript_timeline(new_segments)
    report = {
        "source_transcript_candidate_id": source_id,
        "reference_txt": str(txt_path),
        "txt_line_count": len(lines),
        "source_segment_count": len(rows),
        "mapped_segment_count": len(mapped),
        "unchanged_segment_count": unchanged,
        "removed_duplicate_count": len(removed),
    }
    new_payload = {
        **payload, "candidate_id": candidate_id, "created_at": now.isoformat(timespec="seconds"),
        "source_transcript_candidate_id": source_id,
        "txt_repair": {**report, "assignments": assignments},
        "timeline_issues": issues, "timeline_error_count": len(issues),
        "segments": new_rows,
    }
    folder.mkdir(parents=True, exist_ok=False)
    temporary = output_path.with_suffix(".json.part")
    try:
        temporary.write_text(json.dumps(new_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        folder.rmdir()
        raise
    summary = (
        f"Đã ghép {len(mapped)}/{len(rows)} segment ASR với {len(lines)} dòng TXT; "
        f"giữ nguyên text ASR ở {unchanged - len(removed)} segment; "
        f"đặt rỗng {len(removed)} dòng trùng; {len(issues)} segment cần kiểm tra timeline."
    )
    metadata = {
        **source_result.metadata, **report,
        "timeline_issues": issues, "timeline_error_count": len(issues),
        "transcript_candidate_id": candidate_id,
        "recommended_transcript_candidate_id": candidate_id,
    }
    candidate = TranscriptCandidate(
        id=candidate_id, label=f"TXT đối chiếu · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=new_payload["created_at"], path=str(output_path),
        segment_count=len(new_rows), summary=summary, metadata=metadata,
    )
    result = StepResult(
        step=StepId.STT, summary=summary,
        artifacts={"transcript": str(output_path)},
        segments=new_segments,
        metadata=metadata, transcript_candidates=[candidate],
    )
    return result, summary
