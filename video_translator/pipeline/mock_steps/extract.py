from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...models import AudioCandidate, StepId, StepResult
from ...services import AudioSeparatorService, FFmpegService
from ...state import ProjectState


ORIGINAL_PROVIDER = "Original Audio — FFmpeg"
MDX_PROVIDER = "MDX-Net — Audio Separator"


def _sample_rate(value: str) -> int:
    number = float(value.lower().replace("khz", "").strip())
    return int(number * 1000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def execute(
    state: ProjectState,
    settings: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> StepResult:
    provider = str(settings.get("provider", ORIGINAL_PROVIDER))
    if provider not in {ORIGINAL_PROVIDER, MDX_PROVIDER}:
        raise RuntimeError(f"{provider} hiện chưa được triển khai.")

    stem = Path(state.input_video).stem or "input"
    source_path = state.workspace_path("extracted", f"{stem}_source_audio.wav")
    channels = 1 if settings.get("channels", "Stereo") == "Mono" else 2
    requested_rate = _sample_rate(str(settings.get("sample_rate", "44.1 kHz")))
    existing = state.candidate("original-mix")
    reuse_original = (
        provider == MDX_PROVIDER
        and existing is not None
        and Path(existing.stem_path("original")).is_file()
        and int(existing.metadata.get("sample_rate", 0)) == requested_rate
        and int(existing.metadata.get("channels", 0)) == channels
    )
    stale_part = Path(source_path).with_name(f"{Path(source_path).stem}.part{Path(source_path).suffix}")
    stale_part.unlink(missing_ok=True)
    if reuse_original:
        if progress:
            progress(20, "Đang tái sử dụng Original Mix hiện có…")
        audio_path = existing.stem_path("original")
        duration_seconds = existing.metadata.get("duration_seconds")
        file_size = Path(audio_path).stat().st_size
        configured_ffmpeg = str(settings.get("ffmpeg_path", "Auto")).strip()
        ffmpeg_path = (
            configured_ffmpeg
            if configured_ffmpeg and configured_ffmpeg.lower() != "auto"
            else str(existing.metadata.get("ffmpeg_path", "Auto"))
        )
    else:
        service = FFmpegService(str(settings.get("ffmpeg_path", "Auto")))
        ffmpeg_progress = progress
        if provider == MDX_PROVIDER and progress:
            ffmpeg_progress = lambda value, message: progress(round(value * 0.2), message)
        audio = service.extract_audio(
            state.input_video,
            source_path,
            requested_rate,
            channels,
            ffmpeg_progress,
        )
        audio_path = audio.output_path
        duration_seconds = audio.duration_seconds
        file_size = audio.file_size
        ffmpeg_path = audio.ffmpeg_path
    original = AudioCandidate(
        id="original-mix",
        label="Original Mix",
        provider="FFmpeg",
        model_family="Original",
        model_name="PCM WAV",
        created_at=_now(),
        stems={"original": audio_path},
        metadata={
            "duration_seconds": duration_seconds,
            "sample_rate": requested_rate,
            "channels": channels,
            "file_size": file_size,
            "ffmpeg_path": ffmpeg_path,
        },
    )

    candidates = [original]
    recommended = original
    recommended_stem = "original"
    if provider == MDX_PROVIDER:
        candidate_id = f"mdx-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        candidate_dir = state.workspace_path("audio_separation", candidate_id)
        separated = AudioSeparatorService(
            str(settings.get("model", "UVR-MDX-NET-Inst_HQ_4.onnx")),
            candidate_dir,
            ffmpeg_path,
            str(settings.get("device", "Auto")),
        ).separate(
            audio_path,
            (lambda value, message: progress(20 + round(value * 0.8), message)) if progress else None,
        )
        recommended = AudioCandidate(
            id=candidate_id,
            label=f"MDX · {settings.get('model', 'Default model')} · {separated.actual_device}",
            provider="Audio Separator",
            model_family="MDX",
            model_name=separated.model_name,
            created_at=_now(),
            stems={"voice": separated.voice_path, "background": separated.background_path},
            metadata={
                "requested_device": separated.requested_device,
                "actual_device": separated.actual_device,
                "execution_provider": separated.execution_provider,
                "device_name": separated.device_name,
                "device_diagnostics": separated.diagnostics,
            },
        )
        recommended_stem = "voice"
        candidates.append(recommended)

    size_mb = file_size / (1024 * 1024)
    duration_text = f"{float(duration_seconds):.1f}s" if duration_seconds is not None else "không xác định"
    device_text = f" · {separated.actual_device}" if provider == MDX_PROVIDER else ""
    return StepResult(
        step=StepId.EXTRACT,
        summary=(
            f"Đã tạo {len(candidates)} candidate · {duration_text} · {size_mb:.1f} MB · "
            f"{requested_rate} Hz · {'Mono' if channels == 1 else 'Stereo'}{device_text}"
        ),
        artifacts={"source_audio": audio_path, **recommended.stems},
        metadata={
            "provider": provider,
            "ffmpeg_path": ffmpeg_path,
            "reused_original": reuse_original,
            "recommended_candidate_id": recommended.id,
            "recommended_stem": recommended_stem,
            **(
                {
                    "requested_device": separated.requested_device,
                    "actual_device": separated.actual_device,
                    "execution_provider": separated.execution_provider,
                    "device_name": separated.device_name,
                    "device_diagnostics": separated.diagnostics,
                }
                if provider == MDX_PROVIDER
                else {}
            ),
        },
        audio_candidates=candidates,
    )
