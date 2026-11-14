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


_BATCH_MODELS: dict[tuple[str, str, str, str, str], Any] = {}


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


def _faster_compute_type(device: str) -> str:
    if device == "cpu":
        return "int8"
    import ctranslate2

    supported = ctranslate2.get_supported_compute_types("cuda", 0)
    for compute_type in ("float16", "int8_float16", "int8_float32", "float32", "int8"):
        if compute_type in supported:
            return compute_type
    raise RuntimeError(f"CTranslate2 không hỗ trợ kiểu tính toán CUDA phù hợp: {sorted(supported)}")


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


def _colab_vad_segments(
    model: Any, audio: Any, language: str | None,
) -> tuple[list[dict[str, object]], int, float]:
    """Run the Colab VAD -> compact audio -> stable-ts path on original time."""
    import numpy as np
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    sampling_rate = 16000
    regions = get_speech_timestamps(
        torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)),
        load_silero_vad(), sampling_rate=sampling_rate,
        threshold=0.25, min_speech_duration_ms=100,
        min_silence_duration_ms=1000, speech_pad_ms=150,
        return_seconds=False,
    )
    pieces: list[Any] = []
    mapping: list[tuple[int, int, int]] = []
    cursor = 0
    for region in regions:
        source_start, source_end = int(region["start"]), int(region["end"])
        if source_end <= source_start:
            continue
        pieces.append(audio[source_start:source_end])
        mapping.append((cursor, cursor + source_end - source_start, source_start))
        cursor += source_end - source_start
    if not pieces:
        return [], 0, 0.0
    compact = np.ascontiguousarray(np.concatenate(pieces), dtype=np.float32)
    _progress(20, f"VAD giữ {len(mapping)} vùng tiếng; nhận dạng {len(compact) / sampling_rate:.1f}s audio…")
    result = model.transcribe(
        compact, language=language, task="transcribe",
        initial_prompt="以下是普通话的句子。" if language == "zh" else None,
        word_timestamps=True, regroup=False, vad=True,
        vad_threshold=0.25, demucs=False, beam_size=5,
        condition_on_previous_text=True, verbose=False,
    )
    result.split_by_gap(0.28)
    result.split_by_punctuation(["。", "？", "！", "?", "!", "，", ","])

    def region_at(value: float) -> tuple[int, int, int]:
        sample = value * sampling_rate
        for index, region in enumerate(mapping):
            if sample < region[1] or index == len(mapping) - 1:
                return region
        return mapping[-1]

    def source_time(value: float, region: tuple[int, int, int]) -> float:
        compact_start, compact_end, source_start = region
        sample = min(max(value * sampling_rate, compact_start), compact_end)
        return (source_start + sample - compact_start) / sampling_rate

    segments: list[dict[str, object]] = []
    for segment in result.segments:
        if not str(segment.text).strip():
            continue
        words = segment.words or []
        if not words:
            region = region_at((float(segment.start) + float(segment.end)) / 2)
            start, end = source_time(float(segment.start), region), source_time(float(segment.end), region)
            if end > start:
                segments.append({"start": round(start, 3), "end": round(end, 3),
                                 "text": str(segment.text).strip(), "words": []})
            continue
        groups: list[tuple[tuple[int, int, int], list[Any]]] = []
        for word in words:
            region = region_at((float(word.start) + float(word.end)) / 2)
            if not groups or groups[-1][0] != region:
                groups.append((region, []))
            groups[-1][1].append(word)
        for region, group in groups:
            start = source_time(float(group[0].start), region)
            end = source_time(float(group[-1].end), region)
            text = "".join(str(word.word) for word in group).strip()
            if not text or end <= start:
                continue
            mapped_words = [
                {"start": round(source_time(float(word.start), region), 3),
                 "end": round(source_time(float(word.end), region), 3),
                 "word": str(word.word),
                 "probability": float(getattr(word, "probability", None) or 0.0)}
                for word in group
            ]
            segments.append({"start": round(start, 3), "end": round(end, 3),
                             "text": text, "words": mapped_words})
    return segments, len(mapping), len(compact) / sampling_rate


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
        colab_vad = bool(request.get("colab_vad", False))
        backend = "faster" if colab_vad else "pytorch"
        compute_type = _faster_compute_type(device) if colab_vad else "pytorch"
        if colab_vad:
            reason = f"{reason} CTranslate2 dùng {compute_type}."
        model_key = (model_name, device, str(request["model_cache_dir"]), backend, compute_type)
        model = _BATCH_MODELS.get(model_key)
        if model is None:
            _progress(8, f"Đang nạp Stable Whisper {model_name} trên {actual_device}…")
            if colab_vad:
                model = stable_whisper.load_faster_whisper(
                    model_name, device=device,
                    compute_type=compute_type,
                    download_root=str(request["model_cache_dir"]),
                )
            else:
                model = stable_whisper.load_model(
                    model_name, device=device,
                    download_root=str(request["model_cache_dir"]),
                )
            if request.get("batch_session"):
                _BATCH_MODELS[model_key] = model
        if request.get("type") == "preload":
            _emit({"type": "result", "actual_device": actual_device,
                   "compute_type": compute_type})
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
        use_recovery = (not colab_vad and bool(request.get("segment_recovery"))
                        and bool(request.get("vad_filter", True)))
        regions = _vad_regions(audio, sampling_rate) if mode in {"VAD", "Hybrid"} or use_recovery else []
        _progress(15, f"Đang nhận dạng {language or 'ngôn ngữ tự động'} bằng Stable Whisper…")

        if colab_vad and mode == "Whisper":
            segments, region_count, compact_duration = _colab_vad_segments(model, audio, language)
            recovered = []
            metadata = {
                "enabled": True, "mode": "colab_vad_compact",
                "vad_region_count": region_count,
                "compact_duration_seconds": round(compact_duration, 3),
            }
        elif mode == "VAD":
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
            "compute_type": compute_type,
            "device_selection_reason": reason,
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
