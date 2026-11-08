"""Stable Whisper recognition for all Step 1.5 segmentation modes."""

from __future__ import annotations

import json
import math
import sys
import traceback
from typing import Any

from video_translator.common.models import StepId
from video_translator.config.step_defaults import default_value
from .worker import _merge_hybrid_segments


_BATCH_MODELS: dict[tuple[str, str, str], Any] = {}


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _progress(value: int, message: str) -> None:
    _emit({"type": "progress", "value": value, "message": message})


def _device(requested: str) -> tuple[str, str, str]:
    import torch

    if requested == "CPU":
        return "cpu", "CPU", "Người dùng chọn CPU."
    if requested == "GPU" and not torch.cuda.is_available():
        raise RuntimeError("PyTorch không phát hiện GPU CUDA khả dụng.")
    if requested == "GPU" or torch.cuda.is_available():
        return "cuda", "NVIDIA GPU (CUDA)", "PyTorch sử dụng GPU CUDA."
    return "cpu", "CPU", "Auto chọn CPU vì PyTorch không phát hiện GPU CUDA."


def _segment_dicts(result: Any, offset: float = 0.0) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    for segment in result.segments:
        text = str(segment.text).strip()
        if not text:
            continue
        words = [
            {
                "start": round(float(word.start) + offset, 3),
                "end": round(float(word.end) + offset, 3),
                "word": str(word.word),
                "probability": float(getattr(word, "probability", None) or 0.0),
            }
            for word in (segment.words or [])
            if word.start is not None and word.end is not None
        ]
        start = round(float(segment.start) + offset, 3)
        end = round(float(segment.end) + offset, 3)
        if math.isfinite(start) and math.isfinite(end) and end > start:
            segments.append({"start": start, "end": end, "text": text, "words": words})
    return segments


def _stable_transcribe(model: Any, audio: Any, language: str | None) -> Any:
    result = model.transcribe(
        audio, language=language, task="transcribe",
        condition_on_previous_text=False, word_timestamps=True,
        suppress_silence=True, suppress_word_ts=True,
        vad=False, regroup=True, verbose=None,
    )
    # Stable-ts already detects non-speech during transcribe. Refine the word
    # times, then rescan the complete input to constrain the final boundaries.
    if result.segments:
        model.refine(audio, result, verbose=None)
        result.adjust_by_silence(audio, vad=False, verbose=None)
    return result


def _vad_regions(audio: Any, sampling_rate: int) -> list[tuple[float, float]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    chunks = get_speech_timestamps(
        audio,
        VadOptions(
            threshold=0.25, min_speech_duration_ms=200,
            min_silence_duration_ms=100, speech_pad_ms=0,
        ),
        sampling_rate=sampling_rate,
    )
    return [(chunk["start"] / sampling_rate, chunk["end"] / sampling_rate)
            for chunk in chunks]


def _vad_text_segments(
    model: Any, audio: Any, sampling_rate: int,
    regions: list[tuple[float, float]], language: str | None,
) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    for index, (start, end) in enumerate(regions, 1):
        clip = audio[round(start * sampling_rate):round(end * sampling_rate)]
        if len(clip) < sampling_rate // 10:
            continue
        result = _stable_transcribe(model, clip, language)
        text = "".join(str(item.text) for item in result.segments).strip()
        if result.segments and text:
            cue_start = max(start, start + float(result.segments[0].start))
            cue_end = min(end, start + float(result.segments[-1].end))
            if cue_end <= cue_start:
                cue_start, cue_end = start, end
        else:
            cue_start, cue_end = start, end
        segments.append({
            "start": round(cue_start, 3), "end": round(cue_end, 3),
            "text": text, "words": [],
        })
        _progress(20 + round(index / len(regions) * 60),
                  f"Đã nhận dạng {index}/{len(regions)} vùng VAD bằng Stable Whisper…")
    return segments


def _uncovered_regions(
    regions: list[tuple[float, float]], segments: list[dict[str, object]],
) -> list[tuple[float, float]]:
    uncovered: list[tuple[float, float]] = []
    for start, end in regions:
        if end - start < 0.2:
            continue
        if not any(min(end, float(item["end"])) - max(start, float(item["start"]))
                   >= min(0.2, (end - start) * 0.5) for item in segments):
            uncovered.append((start, end))
    return uncovered


def _recover(
    model: Any, audio: Any, sampling_rate: int,
    regions: list[tuple[float, float]], language: str | None,
) -> list[dict[str, object]]:
    recovered: list[dict[str, object]] = []
    for start, end in regions:
        clip = audio[round(start * sampling_rate):round(end * sampling_rate)]
        for item in _segment_dicts(_stable_transcribe(model, clip, language), start):
            if str(item["text"]).strip():
                recovered.append(item)
    return recovered


def run(request: dict[str, Any]) -> None:
    requested = str(request.get("original_request", request.get("requested_device", default_value(StepId.STT, "device"))))
    attempted_device = ""
    try:
        import stable_whisper
        from faster_whisper.audio import decode_audio

        _progress(2, "Đang kiểm tra Stable Whisper và thiết bị…")
        device, actual_device, reason = _device(str(request.get("requested_device", default_value(StepId.STT, "device"))))
        attempted_device = device
        if requested == "Auto" and device == "cpu" and request.get("fallback_reason"):
            reason = f"Auto chuyển sang CPU: {request['fallback_reason']}"
        model_name = str(request.get("model_name", default_value(StepId.STT, "model")))
        model_key = (model_name, device, str(request["model_cache_dir"]))
        model = _BATCH_MODELS.get(model_key)
        if model is None:
            _progress(8, f"Đang nạp Stable Whisper {model_name} trên {actual_device}…")
            model = stable_whisper.load_model(
                model_name, device=device,
                download_root=str(request["model_cache_dir"]),
            )
            if request.get("batch_session"):
                _BATCH_MODELS[model_key] = model
        if request.get("type") == "preload":
            _emit({"type": "result", "actual_device": actual_device, "compute_type": "pytorch"})
            return

        source = str(request["input_audio"])
        language = str(request.get("language") or "") or None
        if language not in {None, "zh", "en", "vi", "ja", "ko"}:
            raise ValueError(f"Ngôn ngữ project không được hỗ trợ: {language}")
        sampling_rate = 16000
        audio = decode_audio(source, sampling_rate=sampling_rate)
        duration = len(audio) / sampling_rate
        mode = str(request.get("segmentation_mode", default_value(StepId.STT, "segmentation_mode")))
        if mode not in {"VAD", "Hybrid", "Whisper"}:
            raise ValueError(f"Cơ chế tạo SRT không hợp lệ: {mode}")
        use_recovery = bool(request.get("segment_recovery")) and bool(request.get("vad_filter", True))
        regions = _vad_regions(audio, sampling_rate) if mode in {"VAD", "Hybrid"} or use_recovery else []
        _progress(15, f"Đang nhận dạng {language or 'ngôn ngữ tự động'} bằng Stable Whisper…")

        if mode == "VAD":
            segments = _vad_text_segments(model, audio, sampling_rate, regions, language)
            metadata: dict[str, object] = {
                "enabled": True, "mode": "within_vad_region",
                "vad_region_count": len(regions),
                "empty_region_count": sum(not item["text"] for item in segments),
            }
            recovered: list[dict[str, object]] = []
        else:
            stable_result = _stable_transcribe(model, source, language)
            main = _segment_dicts(stable_result)
            recovered = []
            metadata = {"enabled": False}
            if mode == "Hybrid":
                _progress(75, "Đang đối chiếu kết quả Stable Whisper với vùng VAD…")
                vad_segments = _vad_text_segments(model, audio, sampling_rate, regions, language)
                missing = _uncovered_regions(regions, main)
                recovered = _recover(model, audio, sampling_rate, missing, language)
                segments, merge = _merge_hybrid_segments(vad_segments, main, recovered)
                metadata = {"enabled": True, "mode": "hybrid", **merge}
                recovered = []
            else:
                segments = main
                if use_recovery:
                    missing = _uncovered_regions(regions, main)
                    recovered = _recover(model, audio, sampling_rate, missing, language)
                    metadata = {"enabled": True, "mode": "uncovered_vad_regions",
                                "recovered_count": len(recovered)}

        _progress(98, "Đang hoàn thiện segment và SRT…")
        _emit({
            "type": "result", "segments": segments,
            "recovered_segments": recovered, "asr_recovery": metadata,
            "language": language or str(getattr(locals().get("stable_result"), "language", "")),
            "language_probability": None, "duration_seconds": duration,
            "requested_device": requested, "actual_device": actual_device,
            "compute_type": "pytorch", "device_selection_reason": reason,
        })
    except Exception as exc:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _emit({
            "type": "error", "title": "Stable Whisper không thể nhận dạng",
            "message": str(exc),
            "suggestion": "Kiểm tra stable-ts, model và cấu hình CPU/GPU rồi chạy lại.",
            "technical_detail": detail,
            "fallback_to_cpu": requested == "Auto" and attempted_device == "cuda",
        })


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            run(json.loads(line))
        except Exception as exc:
            _emit({"type": "error", "title": "Yêu cầu Step 1.5 không hợp lệ",
                   "message": str(exc), "technical_detail": traceback.format_exc()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
