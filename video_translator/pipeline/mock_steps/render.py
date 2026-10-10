from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...models import RenderCandidate, StepId, StepResult
from ...services.video_render_service import VideoRenderService
from ...state import ProjectState
from .common import previous_segments


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _split_text_by_ratio(text: str, ratios: list[float]) -> list[str]:
    if len(ratios) <= 1:
        return [text.strip()]
    spaced = bool(re.search(r"\s", text.strip()))
    units = re.findall(r"\S+", text) if spaced else list(text.strip())
    if not units:
        return [""] * len(ratios)
    boundaries = [0]
    cumulative = 0.0
    count = len(units)
    for index, ratio in enumerate(ratios[:-1]):
        cumulative += max(0.0, ratio)
        target = round(count * cumulative)
        remaining_parts = len(ratios) - index - 1
        if count >= len(ratios):
            target = max(boundaries[-1] + 1, min(target, count - remaining_parts))
        else:
            target = max(boundaries[-1], min(target, count))
        boundaries.append(target)
    boundaries.append(count)
    separator = " " if spaced else ""
    return [
        separator.join(units[boundaries[index] : boundaries[index + 1]]).strip()
        for index in range(len(ratios))
    ]


def _subtitle_cues(segment, timing: dict[str, object] | None) -> list[tuple[float, float, str]]:
    parts = [part for part in segment.merge_parts if isinstance(part, dict)]
    text = segment.translated_text.strip()
    if not parts:
        return [(segment.start, max(segment.end, segment.start + 0.05), text)]
    ratios = [max(0.0, float(part.get("ratio", 0.0))) for part in parts]
    ratio_total = sum(ratios)
    if ratio_total <= 0:
        ratios = [1.0 / len(parts)] * len(parts)
    else:
        ratios = [value / ratio_total for value in ratios]
    chunks = _split_text_by_ratio(text, ratios)
    start = float((timing or {}).get("adjusted_start", segment.start))
    duration = float((timing or {}).get("play_duration", segment.duration))
    duration = max(0.05, duration)
    cues: list[tuple[float, float, str]] = []
    cursor = start
    cumulative = 0.0
    for index, (ratio, chunk) in enumerate(zip(ratios, chunks, strict=True)):
        cumulative += ratio
        end = start + duration if index == len(ratios) - 1 else start + duration * cumulative
        if chunk:
            cues.append((cursor, max(end, cursor + 0.05), chunk))
        cursor = end
    return cues


def _write_subtitle(
    path: Path,
    segments,
    timings: dict[int, dict[str, object]] | None = None,
) -> None:
    blocks: list[str] = []
    subtitle_index = 1
    for segment in segments:
        for start, end, text in _subtitle_cues(segment, (timings or {}).get(segment.id)):
            if not text:
                continue
            blocks.append(
                f"{subtitle_index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}"
            )
            subtitle_index += 1
    if not blocks:
        raise RuntimeError("Không có nội dung bản dịch để tạo subtitle.")
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    segments = previous_segments(state, StepId.RENDER)
    build_candidate = state.build_audio_candidate(state.selected_build_audio_candidate_id)
    if build_candidate is None or not Path(build_candidate.audio_file).is_file():
        raise RuntimeError("Step 7 cần một voice track Step 6 hoàn chỉnh còn tồn tại.")

    voice_volume = max(0.0, min(1.0, float(settings.get("voice_volume", 1.0))))
    include_original_voice = bool(settings.get("include_original_voice", True))
    include_background = bool(settings.get("include_background", True))
    audio_candidate = state.candidate(state.selected_audio_candidate_id)
    original_voice_value = audio_candidate.stem_path("voice") if audio_candidate else ""
    original_voice = Path(original_voice_value) if original_voice_value else None
    if not original_voice or not original_voice.is_file() or not include_original_voice:
        original_voice = None
    original_voice_volume = max(
        0.0,
        min(1.0, float(settings.get("original_voice_volume", 0.2))),
    )
    background_value = audio_candidate.stem_path("background") if audio_candidate else ""
    background = Path(background_value) if background_value else None
    if not background or not background.is_file() or not include_background:
        background = None
    background_volume = max(0.0, min(1.0, float(settings.get("background_volume", 0.8))))
    create_subtitle = bool(settings.get("subtitle", False))
    burn_subtitle = create_subtitle and bool(settings.get("burn_subtitle", False))

    now = datetime.now(timezone.utc)
    candidate_id = f"render-{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    candidate_folder = Path(state.workspace_path("output", candidate_id))
    video_output = candidate_folder / "translated_video.mp4"
    subtitle_output = candidate_folder / "translated.srt"
    manifest = candidate_folder / "manifest.json"
    candidate_folder.mkdir(parents=True, exist_ok=False)
    try:
        if create_subtitle:
            timings: dict[int, dict[str, object]] = {}
            sync_id = build_candidate.source_sync_candidate_id
            sync_candidate = state.sync_candidate(sync_id)
            if sync_candidate and Path(sync_candidate.path).is_file():
                sync_payload = json.loads(Path(sync_candidate.path).read_text(encoding="utf-8"))
                timings = {
                    int(item.get("id", 0)): item
                    for item in sync_payload.get("segments", [])
                    if isinstance(item, dict)
                }
            _write_subtitle(subtitle_output, segments, timings)
        result = VideoRenderService().render(
            source_video_path=state.input_video,
            voice_track_path=build_candidate.audio_file,
            output_path=video_output,
            voice_volume=voice_volume,
            original_voice_path=original_voice,
            original_voice_volume=original_voice_volume,
            background_path=background,
            background_volume=background_volume,
            subtitle_path=subtitle_output if create_subtitle else None,
            burn_subtitle=burn_subtitle,
            progress=progress,
        )
        payload = {
            "version": 1,
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            "source_video": state.input_video,
            "source_build_audio_candidate_id": build_candidate.id,
            "voice_track": build_candidate.audio_file,
            "voice_volume": voice_volume,
            "original_voice_file": str(original_voice) if original_voice else "",
            "original_voice_volume": original_voice_volume if original_voice else 0.0,
            "background_file": str(background) if background else "",
            "subtitle_file": str(subtitle_output) if create_subtitle else "",
            **result,
        }
        temporary = manifest.with_suffix(".json.part")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest)
    except Exception:
        shutil.rmtree(candidate_folder, ignore_errors=True)
        raise

    components = ["hình ảnh", "voice mới"]
    if original_voice:
        components.append("voice gốc")
    if background:
        components.append("background")
    if create_subtitle:
        components.append("subtitle burn" if burn_subtitle else "subtitle SRT")
    summary = f"Đã render {' + '.join(components)} · H.264/AAC · {float(result['duration_seconds']):.2f}s"
    render_candidate = RenderCandidate(
        id=candidate_id,
        label=f"Video hoàn chỉnh · {now.astimezone().strftime('%d/%m/%Y %H:%M:%S')}",
        created_at=now.isoformat(timespec="seconds"),
        path=str(manifest),
        folder=str(candidate_folder),
        video_file=str(video_output),
        subtitle_file=str(subtitle_output) if create_subtitle else "",
        original_voice_used=original_voice is not None,
        background_used=background is not None,
        burned_subtitle=burn_subtitle,
        duration_seconds=float(result["duration_seconds"]),
        summary=summary,
        metadata=payload,
    )
    return StepResult(
        step=StepId.RENDER,
        summary=summary,
        artifacts={
            "output_video": str(video_output),
            **({"subtitle": str(subtitle_output)} if create_subtitle else {}),
            "render_manifest": str(manifest),
        },
        segments=segments,
        metadata={
            **settings,
            **result,
            "render_candidate_id": candidate_id,
            "recommended_render_candidate_id": candidate_id,
            "source_build_audio_candidate_id": build_candidate.id,
        },
        render_candidates=[render_candidate],
    )
