from __future__ import annotations

import json
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


def _write_subtitle(path: Path, segments) -> None:
    blocks: list[str] = []
    subtitle_index = 1
    for segment in segments:
        text = segment.translated_text.strip()
        if not text:
            continue
        end = max(segment.end, segment.start + 0.05)
        blocks.append(
            f"{subtitle_index}\n{_srt_timestamp(segment.start)} --> {_srt_timestamp(end)}\n{text}"
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

    include_background = bool(settings.get("include_background", True))
    audio_candidate = state.candidate(state.selected_audio_candidate_id)
    background_value = audio_candidate.stem_path("background") if audio_candidate else ""
    background = Path(background_value) if background_value else None
    if not background or not background.is_file() or not include_background:
        background = None
    background_volume = max(0.0, min(1.0, float(settings.get("background_volume", 1.0))))
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
            _write_subtitle(subtitle_output, segments)
        result = VideoRenderService().render(
            state.input_video,
            build_candidate.audio_file,
            video_output,
            background,
            background_volume,
            subtitle_output if create_subtitle else None,
            burn_subtitle,
            progress,
        )
        payload = {
            "version": 1,
            "candidate_id": candidate_id,
            "created_at": now.isoformat(timespec="seconds"),
            "source_video": state.input_video,
            "source_build_audio_candidate_id": build_candidate.id,
            "voice_track": build_candidate.audio_file,
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
