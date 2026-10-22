from __future__ import annotations

import ctypes
from difflib import SequenceMatcher
import json
import math
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any


_DLL_DIRECTORY_HANDLES: list[object] = []

RECOVERY_MAX_WINDOW_SECONDS = 30.0
RECOVERY_OVERLAP_SECONDS = 2.0
RECOVERY_CONTEXT_SECONDS = 0.75
RECOVERY_MIN_GAP_SECONDS = 1.0
RECOVERY_ACTIVITY_DB = -52.0
RECOVERY_SILENCE_DB = -65.0
RECOVERY_DUPLICATE_BOUNDARY_SECONDS = 0.15


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def progress(value: int, message: str) -> None:
    emit({"type": "progress", "value": value, "message": message})


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_intervals(
    start: float,
    end: float,
    covered: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    cursor = start
    for covered_start, covered_end in covered:
        if covered_end <= cursor or covered_start >= end:
            continue
        clipped_start = max(start, covered_start)
        clipped_end = min(end, covered_end)
        if clipped_start > cursor:
            gaps.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def _rms_db(audio: Any, sampling_rate: int, start: float, end: float) -> float:
    first = max(0, round(start * sampling_rate))
    last = min(len(audio), round(end * sampling_rate))
    if last <= first:
        return -120.0
    samples = audio[first:last]
    power = float((samples * samples).mean()) if len(samples) else 0.0
    return 10.0 * math.log10(max(power, 1e-12))


def _has_audio_activity(audio: Any, sampling_rate: int, start: float, end: float) -> bool:
    frame_seconds = 0.1
    hop_seconds = 0.05
    cursor = start
    active_frames = 0
    while cursor < end:
        frame_end = min(end, cursor + frame_seconds)
        if _rms_db(audio, sampling_rate, cursor, frame_end) >= RECOVERY_ACTIVITY_DB:
            active_frames += 1
            if active_frames >= 2:
                return True
        cursor += hop_seconds
    return False


def _normalize_text(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())


def _interval_overlap(first: tuple[float, float], second: tuple[float, float]) -> float:
    return max(0.0, min(first[1], second[1]) - max(first[0], second[0]))


def _is_duplicate(candidate: dict[str, object], existing: list[dict[str, object]]) -> bool:
    candidate_text = _normalize_text(str(candidate.get("text", "")))
    candidate_range = (float(candidate["start"]), float(candidate["end"]))
    candidate_duration = max(0.001, candidate_range[1] - candidate_range[0])
    for item in existing:
        item_range = (float(item["start"]), float(item["end"]))
        item_duration = max(0.001, item_range[1] - item_range[0])
        overlap = _interval_overlap(candidate_range, item_range)
        item_text = _normalize_text(str(item.get("text", "")))
        if not candidate_text or not item_text:
            continue
        similarity = SequenceMatcher(None, candidate_text, item_text).ratio()
        same_text = candidate_text == item_text or similarity >= 0.82
        if not same_text:
            continue

        overlap_ratio = overlap / min(candidate_duration, item_duration)
        boundary_distance = min(
            abs(candidate_range[0] - item_range[1]),
            abs(item_range[0] - candidate_range[1]),
        )
        # Recovery windows may return a short suffix/prefix that touches the
        # original ASR segment. Treat identical text at that boundary as the
        # same recognition, even when the raw overlap ratio is small. A real
        # repeated word remains valid when it has a meaningful pause between
        # the two intervals.
        if overlap_ratio >= 0.45 or (
            boundary_distance <= RECOVERY_DUPLICATE_BOUNDARY_SECONDS + 0.001
        ):
            return True
    return False


def _segment_word_coverage(segments: list[dict[str, object]]) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for segment in segments:
        words = segment.get("words")
        valid_words = words if isinstance(words, list) else []
        word_intervals = [
            (
                max(0.0, float(word["start"]) - 0.15),
                float(word["end"]) + 0.15,
            )
            for word in valid_words
            if isinstance(word, dict)
            and word.get("start") is not None
            and word.get("end") is not None
        ]
        if word_intervals:
            intervals.extend(word_intervals)
        else:
            intervals.append((float(segment["start"]), float(segment["end"])))
    return _merge_intervals(intervals)


def _recovery_windows(
    candidates: list[dict[str, object]],
    duration: float,
) -> list[tuple[float, float]]:
    expanded = _merge_intervals(
        [
            (
                max(0.0, float(candidate["start"]) - RECOVERY_CONTEXT_SECONDS),
                min(duration, float(candidate["end"]) + RECOVERY_CONTEXT_SECONDS),
            )
            for candidate in candidates
        ]
    )
    windows: list[tuple[float, float]] = []
    for start, end in expanded:
        cursor = start
        while cursor < end:
            window_end = min(end, cursor + RECOVERY_MAX_WINDOW_SECONDS)
            windows.append((cursor, window_end))
            if window_end >= end:
                break
            cursor = max(cursor + 0.001, window_end - RECOVERY_OVERLAP_SECONDS)
    return windows


def _candidate_for_segment(
    start: float,
    end: float,
    candidates: list[dict[str, object]],
) -> dict[str, object] | None:
    return max(
        candidates,
        key=lambda item: _interval_overlap(
            (start, end),
            (float(item["start"]), float(item["end"])),
        ),
        default=None,
    )


def _recover_missing_segments(
    model: Any,
    audio: Any,
    sampling_rate: int,
    language: str | None,
    duration: float,
    main_segments: list[dict[str, object]],
    speech_chunks: list[dict[str, int]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    kept = [
        (float(chunk["start"]) / sampling_rate, float(chunk["end"]) / sampling_rate)
        for chunk in speech_chunks
    ]
    coverage = _segment_word_coverage(main_segments)
    raw_candidates: list[dict[str, object]] = []

    cursor = 0.0
    for start, end in kept:
        if start - cursor >= RECOVERY_MIN_GAP_SECONDS:
            raw_candidates.append({"start": cursor, "end": start, "reasons": {"vad_rejected"}})
        cursor = max(cursor, end)
    if duration - cursor >= RECOVERY_MIN_GAP_SECONDS:
        raw_candidates.append({"start": cursor, "end": duration, "reasons": {"vad_rejected"}})

    for start, end in kept:
        for gap_start, gap_end in _subtract_intervals(start, end, coverage):
            if gap_end - gap_start >= RECOVERY_MIN_GAP_SECONDS:
                raw_candidates.append(
                    {"start": gap_start, "end": gap_end, "reasons": {"recognized_coverage_gap"}}
                )

    active_candidates: list[dict[str, object]] = []
    rejected_silence_count = 0
    for candidate in sorted(raw_candidates, key=lambda item: float(item["start"])):
        start = float(candidate["start"])
        end = float(candidate["end"])
        if not _has_audio_activity(audio, sampling_rate, start, end):
            rejected_silence_count += 1
            continue
        if active_candidates and start <= float(active_candidates[-1]["end"]):
            active_candidates[-1]["end"] = max(float(active_candidates[-1]["end"]), end)
            reasons = active_candidates[-1]["reasons"]
            assert isinstance(reasons, set)
            reasons.update(candidate["reasons"])
        else:
            active_candidates.append(dict(candidate))

    windows = _recovery_windows(active_candidates, duration)
    metadata: dict[str, object] = {
        "enabled": True,
        "max_window_seconds": RECOVERY_MAX_WINDOW_SECONDS,
        "overlap_seconds": RECOVERY_OVERLAP_SECONDS,
        "candidate_interval_count": len(active_candidates),
        "vad_rejected_candidate_count": sum(
            1 for item in active_candidates if "vad_rejected" in item["reasons"]
        ),
        "recognized_coverage_gap_count": sum(
            1 for item in active_candidates if "recognized_coverage_gap" in item["reasons"]
        ),
        "windows_processed": len(windows),
        "accepted_segment_count": 0,
        "uncertain_segment_count": 0,
        "rejected_silence_count": rejected_silence_count,
        "rejected_duplicate_count": 0,
        "missing_word_alignment_count": 0,
        "recovered_duration_seconds": 0.0,
        "accepted_segments": [],
        "uncertain_segments": [],
    }
    if not windows:
        return [], metadata

    clip_timestamps = ",".join(f"{start:.3f},{end:.3f}" for start, end in windows)
    recovered_stream, _ = model.transcribe(
        audio,
        language=language,
        task="transcribe",
        beam_size=5,
        vad_filter=False,
        word_timestamps=True,
        condition_on_previous_text=False,
        clip_timestamps=clip_timestamps,
    )

    accepted: list[dict[str, object]] = []
    accepted_details = metadata["accepted_segments"]
    uncertain_details = metadata["uncertain_segments"]
    assert isinstance(accepted_details, list)
    assert isinstance(uncertain_details, list)
    for segment in recovered_stream:
        text = segment.text.strip()
        if not text:
            continue
        start = round(float(segment.start), 3)
        end = round(float(segment.end), 3)
        candidate = _candidate_for_segment(start, end, active_candidates)
        candidate_overlap = 0.0 if candidate is None else _interval_overlap(
            (start, end),
            (float(candidate["start"]), float(candidate["end"])),
        )
        if candidate is None or candidate_overlap / max(0.001, end - start) < 0.25:
            continue
        # Whisper may return a segment that extends beyond the suspicious
        # interval. Keep only the portion that recovery was asked to inspect;
        # otherwise context audio can be reintroduced as a duplicate.
        candidate_start = float(candidate["start"])
        candidate_end = float(candidate["end"])
        clipped_start = max(start, candidate_start)
        clipped_end = min(end, candidate_end)
        if clipped_end <= clipped_start:
            continue
        start = round(clipped_start, 3)
        end = round(clipped_end, 3)
        words = [
            {
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                "word": word.word,
                "probability": round(float(word.probability or 0.0), 6),
            }
            for word in (segment.words or [])
            if word.start is not None
            and word.end is not None
            and float(word.end) > clipped_start
            and float(word.start) < clipped_end
        ]
        if not words:
            metadata["missing_word_alignment_count"] = int(
                metadata["missing_word_alignment_count"]
            ) + 1
        probabilities = [float(word["probability"]) for word in words]
        mean_word_probability = (
            sum(probabilities) / len(probabilities) if probabilities else None
        )
        candidate_db = _rms_db(
            audio,
            sampling_rate,
            float(candidate["start"]),
            float(candidate["end"]),
        )
        detail = {
            "start": start,
            "end": end,
            "text": text,
            "avg_logprob": round(float(segment.avg_logprob), 6),
            "no_speech_prob": round(float(segment.no_speech_prob), 6),
            "compression_ratio": round(float(segment.compression_ratio), 6),
            "mean_word_probability": (
                round(mean_word_probability, 6)
                if mean_word_probability is not None
                else None
            ),
            "candidate_rms_db": round(candidate_db, 3),
            "reasons": sorted(str(reason) for reason in candidate["reasons"]),
        }
        item: dict[str, object] = {
            "start": start,
            "end": end,
            "text": text,
            "words": words,
        }

        if candidate_db < RECOVERY_SILENCE_DB:
            detail["reason"] = "silent_region"
            metadata["rejected_silence_count"] = int(metadata["rejected_silence_count"]) + 1
            uncertain_details.append(detail)
            continue
        normalized = _normalize_text(text)
        low_evidence = (
            len(normalized) <= 1
            and candidate_db < -38.0
            and (
                float(segment.avg_logprob) < -0.75
                or float(segment.no_speech_prob) > 0.6
            )
        )
        alignment_uncertain = (
            not words
            and (
                float(segment.avg_logprob) < -0.8
                or candidate_db < -45.0
            )
        )
        if low_evidence or alignment_uncertain:
            detail["reason"] = (
                "short_text_low_energy" if low_evidence else "missing_word_alignment"
            )
            uncertain_details.append(detail)
            continue
        if _is_duplicate(item, [*main_segments, *accepted]):
            metadata["rejected_duplicate_count"] = int(
                metadata["rejected_duplicate_count"]
            ) + 1
            continue

        accepted.append(item)
        accepted_details.append(detail)

    metadata["accepted_segment_count"] = len(accepted)
    metadata["uncertain_segment_count"] = len(uncertain_details)
    metadata["recovered_duration_seconds"] = round(
        sum(max(0.0, float(item["end"]) - float(item["start"])) for item in accepted),
        3,
    )
    return accepted, metadata


class CudaUnavailableError(RuntimeError):
    pass


def configure_bundled_cuda() -> list[str]:
    if os.name == "nt":
        root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
        candidates = (root / "cublas" / "bin", root / "cudnn" / "bin")
    else:
        root = Path(sys.prefix) / "lib" / "python"
        candidates = ()

    folders = [str(path.resolve()) for path in candidates if path.is_dir()]
    if not folders:
        return []
    current_parts = os.environ.get("PATH", "").split(os.pathsep)
    current_lower = {part.lower() for part in current_parts}
    additions = [folder for folder in folders if folder.lower() not in current_lower]
    if additions:
        os.environ["PATH"] = os.pathsep.join([*additions, os.environ.get("PATH", "")])
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        registered = {str(getattr(handle, "path", "")).lower() for handle in _DLL_DIRECTORY_HANDLES}
        for folder in folders:
            if folder.lower() not in registered:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(folder))
    return folders


def cuda_runtime_issue() -> str:
    configure_bundled_cuda()
    import ctranslate2

    try:
        device_count = ctranslate2.get_cuda_device_count()
    except Exception as exc:
        return f"CTranslate2 không thể kiểm tra CUDA: {exc}"
    if device_count < 1:
        return "CTranslate2 không phát hiện NVIDIA GPU hỗ trợ CUDA."

    library_names = (
        ("cublas64_12.dll", "cudnn64_9.dll")
        if os.name == "nt"
        else ("libcublas.so.12", "libcudnn.so.9")
    )
    missing: list[str] = []
    for name in library_names:
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    if missing:
        return f"Không thể tải thư viện CUDA: {', '.join(missing)}."
    return ""


def select_device(requested: str) -> tuple[str, str, str]:
    if requested == "CPU":
        return "cpu", "CPU", "Người dùng chọn bắt buộc CPU."
    if requested == "GPU":
        issue = cuda_runtime_issue()
        if issue:
            raise CudaUnavailableError(issue)
        return "cuda", "NVIDIA GPU (CUDA)", "Người dùng chọn bắt buộc GPU."

    issue = cuda_runtime_issue()
    if issue:
        return "cpu", "CPU", f"Auto chọn CPU: {issue}"
    return "cuda", "NVIDIA GPU (CUDA)", "Auto chọn GPU vì CUDA, cuBLAS và cuDNN khả dụng."


def classify_error(exc: Exception, requested: str, attempted_device: str) -> dict[str, Any]:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    lowered = detail.lower()
    fallback = requested == "Auto" and attempted_device == "cuda"
    if isinstance(exc, ModuleNotFoundError):
        module = exc.name or "faster_whisper"
        return {
            "title": "Thiếu thư viện Faster Whisper",
            "message": f"Không tìm thấy module “{module}”.",
            "suggestion": "Cài dependency bằng đúng Python chạy ứng dụng: python -m pip install -e .",
            "technical_detail": detail,
            "fallback_to_cpu": False,
        }
    if isinstance(exc, CudaUnavailableError):
        return {
            "title": "GPU chưa sẵn sàng cho Faster Whisper",
            "message": str(exc),
            "suggestion": "Cài CUDA 12, cuBLAS và cuDNN 9; hoặc chọn Auto/CPU trong Step 2.",
            "technical_detail": detail,
            "fallback_to_cpu": False,
        }
    if "out of memory" in lowered or "failed to allocate" in lowered:
        return {
            "title": "Không đủ bộ nhớ cho Faster Whisper",
            "message": "Model không đủ RAM hoặc VRAM để nhận dạng audio.",
            "suggestion": "Chọn model nhỏ hơn, đóng bớt ứng dụng hoặc chuyển sang CPU.",
            "technical_detail": detail,
            "fallback_to_cpu": fallback,
        }
    if any(token in lowered for token in ("cublas", "cudnn", "cuda", "nvcuda", "cudart")):
        return {
            "title": "Không thể khởi tạo Faster Whisper trên GPU",
            "message": "CUDA, cuBLAS hoặc cuDNN không khả dụng hay không tương thích.",
            "suggestion": "Cài CUDA 12, cuBLAS và cuDNN 9; hoặc chọn CPU trong Step 2.",
            "technical_detail": detail,
            "fallback_to_cpu": fallback,
        }
    if any(token in lowered for token in ("connection", "timeout", "ssl", "download", "http")):
        return {
            "title": "Không thể tải model Faster Whisper",
            "message": "Không thể kết nối hoặc tải đầy đủ model nhận dạng.",
            "suggestion": "Kiểm tra Internet, proxy/firewall rồi chạy lại.",
            "technical_detail": detail,
            "fallback_to_cpu": False,
        }
    if any(token in lowered for token in ("averror", "invalid data", "could not decode", "failed to decode")):
        return {
            "title": "Không thể đọc audio đầu vào",
            "message": "Faster Whisper không giải mã được file audio đã chọn.",
            "suggestion": "Chạy lại Step 1 hoặc chọn một candidate audio khác.",
            "technical_detail": detail,
            "fallback_to_cpu": False,
        }
    return {
        "title": "Faster Whisper không thể nhận dạng",
        "message": str(exc) or "Đã xảy ra lỗi chưa xác định trong worker nhận dạng.",
        "suggestion": "Mở chi tiết kỹ thuật để xác định nguyên nhân cụ thể.",
        "technical_detail": detail,
        "fallback_to_cpu": False,
    }


def run(request: dict[str, Any]) -> None:
    requested = str(request.get("original_request", request.get("requested_device", "Auto")))
    worker_request = str(request.get("requested_device", "Auto"))
    attempted_device = ""
    try:
        progress(2, "Đang kiểm tra Faster Whisper và thiết bị…")
        configure_bundled_cuda()
        from faster_whisper import WhisperModel
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        device, actual_device, device_selection_reason = select_device(worker_request)
        attempted_device = device
        fallback_reason = str(request.get("fallback_reason", ""))
        if requested == "Auto" and worker_request == "CPU" and fallback_reason:
            device_selection_reason = f"Auto chuyển sang CPU: {fallback_reason}"
        progress(5, device_selection_reason)
        model_name = str(request.get("model_name", "medium"))
        progress(8, f"Đang tải model {model_name} trên {actual_device}…")
        model = WhisperModel(
            model_name,
            device=device,
            compute_type="default",
            download_root=str(request["model_cache_dir"]),
        )
        compute_type = str(model.model.compute_type)
        progress(15, "Đang phân tích audio và nhận dạng lời nói…")
        segment_stream, info = model.transcribe(
            str(request["input_audio"]),
            language=request.get("language") or None,
            task="transcribe",
            beam_size=5,
            vad_filter=bool(request.get("vad_filter", True)),
            word_timestamps=True,
        )
        duration = float(info.duration) if info.duration is not None else None
        segments: list[dict[str, object]] = []
        progress_span = 70 if bool(request.get("vad_filter", True)) else 82
        for segment in segment_stream:
            text = segment.text.strip()
            if text:
                segments.append(
                    {
                        "start": round(float(segment.start), 3),
                        "end": round(float(segment.end), 3),
                        "text": text,
                        "words": [
                            {
                                "start": round(float(word.start), 3),
                                "end": round(float(word.end), 3),
                                "word": word.word,
                                "probability": round(float(word.probability or 0.0), 6),
                            }
                            for word in (segment.words or [])
                            if word.start is not None and word.end is not None
                        ],
                    }
                )
            if duration and duration > 0:
                value = 15 + round(min(float(segment.end) / duration, 1.0) * progress_span)
                progress(value, f"Đang nhận dạng đến {float(segment.end):.1f}s / {duration:.1f}s…")
        recovered_segments: list[dict[str, object]] = []
        recovery_metadata: dict[str, object] = {"enabled": False}
        if bool(request.get("vad_filter", True)) and duration:
            progress(90, "Đang kiểm tra các vùng có khả năng thiếu lời thoại…")
            sampling_rate = int(model.feature_extractor.sampling_rate)
            audio = decode_audio(str(request["input_audio"]), sampling_rate=sampling_rate)
            speech_chunks = get_speech_timestamps(
                audio,
                VadOptions(),
                sampling_rate=sampling_rate,
            )
            recovered_segments, recovery_metadata = _recover_missing_segments(
                model=model,
                audio=audio,
                sampling_rate=sampling_rate,
                language=request.get("language") or None,
                duration=duration,
                main_segments=segments,
                speech_chunks=speech_chunks,
            )
        progress(98, "Đang hoàn thiện transcript…")
        emit(
            {
                "type": "result",
                "segments": segments,
                "recovered_segments": recovered_segments,
                "asr_recovery": recovery_metadata,
                "language": str(info.language or ""),
                "language_probability": (
                    float(info.language_probability)
                    if info.language_probability is not None
                    else None
                ),
                "duration_seconds": duration,
                "requested_device": requested,
                "actual_device": actual_device,
                "compute_type": compute_type,
                "device_selection_reason": device_selection_reason,
            }
        )
    except Exception as exc:
        emit({"type": "error", **classify_error(exc, requested, attempted_device)})


def main() -> int:
    line = sys.stdin.readline()
    if not line:
        emit(
            {
                "type": "error",
                "title": "Worker Faster Whisper thiếu dữ liệu đầu vào",
                "message": "Không nhận được yêu cầu nhận dạng từ ứng dụng.",
                "suggestion": "Đóng và mở lại ứng dụng rồi thử lại.",
                "technical_detail": "stdin was empty",
                "fallback_to_cpu": False,
            }
        )
        return 1
    run(json.loads(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
