"""Read the source video and return OCR text aligned to existing SRT cues."""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from threading import Event
from pathlib import Path
from typing import Any

from video_translator.common.models import StepId
from video_translator.config.step_defaults import default_value

from .ocr_region import resolve_region
from .ocr_segment_matcher import (
    NOT_FOUND,
    ScanResult,
    join_ocr_fragments,
    norm,
    read_srt,
    scan_segment,
    srt_reference,
    validate_interval,
    validate_scan_limit,
)

LANGUAGE_CODES = {"zh": "ch_sim", "en": "en", "vi": "vi", "ja": "ja", "ko": "ko"}
_READERS: dict[tuple[str, bool], Any] = {}


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _reader(language: str, requested_device: str) -> tuple[Any, str]:
    import easyocr
    import torch

    if requested_device not in {"Auto", "CPU", "GPU"}:
        raise ValueError(f"Thiết bị OCR không hợp lệ: {requested_device}")
    if requested_device == "GPU" and not torch.cuda.is_available():
        raise RuntimeError("PyTorch không phát hiện GPU CUDA để chạy OCR.")
    use_gpu = requested_device != "CPU" and torch.cuda.is_available()
    key = (language, use_gpu)
    reader = _READERS.get(key)
    if reader is None:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
        cache = base / "TransLanguage" / "easyocr-models"
        cache.mkdir(parents=True, exist_ok=True)
        reader = easyocr.Reader(
            [LANGUAGE_CODES[language]], gpu=use_gpu,
            model_storage_directory=str(cache), verbose=False,
        )
        _READERS[key] = reader
    return reader, "NVIDIA GPU (CUDA)" if use_gpu else "CPU"


def _scan(request: dict[str, Any], reader: Any) -> tuple[list[str], int, float]:
    import cv2

    interval = validate_interval(float(request.get(
        "ocr_interval_seconds", default_value(StepId.STT, "ocr_interval_seconds"),
    )))
    limit = validate_scan_limit(request.get(
        "ocr_max_scans_per_side", default_value(StepId.STT, "ocr_max_scans_per_side"),
    ))
    x, y, width, height = resolve_region(request)
    path = Path(request["input_srt"])
    segments = read_srt(path)
    # Blank point cues are intentional markers in Step 1.5. Keep their text empty
    # and leave them out of the neighbor matching and scan boundaries.
    timed = [segment for segment in segments if segment.text or segment.end > segment.start]
    capture = cv2.VideoCapture(str(request["input_video"]))
    lines: dict[str, str] = {}
    scan_count = 0
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not capture.isOpened() or not math.isfinite(fps) or not math.isfinite(frame_count) or min(fps, frame_count) <= 0:
            raise ValueError("Không mở được video hoặc không xác định được thời lượng để quét OCR.")
        duration = frame_count / fps
        previous = ScanResult()
        cancelled = Event()
        for index, segment in enumerate(timed):
            attempted_frames: set[int] = set()

            def recognize(target: float) -> str:
                nonlocal scan_count
                if not capture.set(cv2.CAP_PROP_POS_MSEC, target * 1000):
                    raise ValueError("Không thể tua video đến khung hình cần quét OCR.")
                valid, frame = capture.read()
                if not valid:
                    return ""
                position = capture.get(cv2.CAP_PROP_POS_FRAMES)
                if math.isfinite(position) and position > 0:
                    frame_id = round(position) - 1
                    if frame_id in attempted_frames:
                        return ""
                    attempted_frames.add(frame_id)
                frame_height, frame_width = frame.shape[:2]
                left = min(frame_width - 1, round(x * frame_width))
                top = min(frame_height - 1, round(y * frame_height))
                right = max(left + 1, min(frame_width, round((x + width) * frame_width)))
                bottom = max(top + 1, min(frame_height, round((y + height) * frame_height)))
                crop = frame[top:bottom, left:right]
                blocks = reader.readtext(crop, detail=0, paragraph=False)
                scan_count += 1
                return join_ocr_fragments([" ".join(str(block).split()) for block in blocks])

            preceding = timed[index - 1] if index else None
            following = timed[index + 1] if index + 1 < len(timed) else None
            result = scan_segment(
                segment, following, previous, duration, interval, recognize, cancelled,
                preceding, all_segments=timed, current_index=index, max_scans_per_side=limit,
            )
            if result.previous_update and preceding:
                lines[preceding.index] = result.previous_update
            if result.parts:
                lines[segment.index] = result.text
            elif len(norm(srt_reference(segment.text))) <= 2:
                lines[segment.index] = "@" + segment.text
            else:
                lines[segment.index] = NOT_FOUND
            previous = result
            _emit({"type": "progress", "value": 5 + round(93 * (index + 1) / len(timed)),
                   "message": f"Đang quét OCR: mục {index + 1}/{len(timed)}"})
        if timed and not scan_count:
            raise ValueError("Không giải mã được khung hình nào tại các mốc SRT để quét OCR.")
    finally:
        capture.release()
    return [lines.get(segment.index, "") for segment in segments], scan_count, duration


def run(request: dict[str, Any]) -> None:
    requested = str(request.get("requested_device", default_value(StepId.STT, "device")))
    attempted_gpu = False
    try:
        import torch

        attempted_gpu = requested != "CPU" and torch.cuda.is_available()
        language = str(request["language"])
        if language not in LANGUAGE_CODES:
            raise ValueError(f"Ngôn ngữ OCR không được hỗ trợ: {language}")
        _emit({"type": "progress", "value": 2, "message": "Đang nạp model OCR…"})
        reader, actual_device = _reader(language, requested)
        if request.get("type") == "preload":
            _emit({"type": "result", "actual_device": actual_device})
            return
        lines, scan_count, duration = _scan(request, reader)
        _emit({"type": "result", "lines": lines, "scan_count": scan_count,
               "duration_seconds": duration, "actual_device": actual_device})
    except Exception as exc:
        _emit({"type": "error", "message": str(exc),
               "technical_detail": traceback.format_exc(),
               "fallback_to_cpu": requested == "Auto" and attempted_gpu})


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if request.get("type") == "shutdown":
                break
            run(request)
        except Exception as exc:
            _emit({"type": "error", "message": str(exc), "technical_detail": traceback.format_exc()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
