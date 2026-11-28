"""Read video frames with RapidOCR CUDA and align the text to an existing SRT."""

from __future__ import annotations

import json
import math
import sys
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ...common.models import StepId
from ...config.step_defaults import with_defaults
from .ocr_engine import MODEL_SIZES, crop_region, extract_text, initialize_cuda_ocr, run_ocr_image, validate_ocr_options
from .ocr_region import resolve_region
from .ocr_segment_matcher import ScanResult, read_srt, scan_segment, validate_extension, validate_interval

_ENGINES: dict[tuple[bool, str, str, str], Any] = {}
_DEVICE = "NVIDIA GPU (CUDA)"


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _engine(settings: dict[str, Any]) -> Any:
    use_default = bool(settings["ocr_default_model"])
    det_model = str(settings["ocr_det_model"])
    rec_model = str(settings["ocr_rec_model"])
    method = str(settings["ocr_method"])
    if use_default:
        det_model = rec_model = "small"
    if det_model not in MODEL_SIZES or rec_model not in MODEL_SIZES:
        raise ValueError("Kích thước model OCR không hợp lệ.")
    key = (use_default, det_model, rec_model, method)
    if key not in _ENGINES:
        _ENGINES[key] = initialize_cuda_ocr(
            lambda message: _emit({"type": "progress", "value": 2, "message": message}),
            det_model, rec_model, use_default, method,
        )
    return _ENGINES[key]


def _scan(settings: dict[str, Any], engine: Any) -> tuple[list[str], int]:
    import cv2

    interval = validate_interval(float(settings["ocr_interval_seconds"]))
    short_interval = validate_interval(float(settings["ocr_short_interval_seconds"]))
    extension = validate_extension(float(settings["ocr_extension_seconds"]))
    image_mode = settings["ocr_image_mode"]
    method = settings["ocr_method"]
    by_frame = settings["ocr_seek_mode"] == "Frame index"
    text_score = float(settings["ocr_text_score"])
    region = resolve_region(settings)
    segments = read_srt(Path(settings["input_srt"]))
    # Keep empty point cues without treating them as scan boundaries.
    timed = [(position, segment) for position, segment in enumerate(segments)
             if segment.text or segment.end > segment.start]
    lines = [""] * len(segments)
    scan_count = 0
    capture = cv2.VideoCapture(str(settings["input_video"]))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not capture.isOpened() or not math.isfinite(fps) or not math.isfinite(frame_count) or fps <= 0 or frame_count < 1:
            raise ValueError("Không mở được video hoặc không xác định được thời lượng để quét OCR.")
        frames = int(frame_count)
        duration = frames / fps
        previous = ScanResult()
        attempted_frames: OrderedDict[int | float, tuple[str, float]] = OrderedDict()
        for index, (position, segment) in enumerate(timed):

            def recognize(target: float) -> tuple[str, float]:
                nonlocal scan_count
                key = max(0, min(frames - 1, int(target * fps + 1e-9))) if by_frame else round(target, 9)
                if key in attempted_frames:
                    attempted_frames.move_to_end(key)
                    return attempted_frames[key]
                if by_frame:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, key)
                else:
                    capture.set(cv2.CAP_PROP_POS_MSEC, target * 1000)
                valid, frame = capture.read()
                if not valid:
                    return "", 0.0
                crop = crop_region(frame, region)
                if crop.size == 0:
                    return "", 0.0
                result = run_ocr_image(engine, crop, cv2, image_mode, method, text_score)
                reading = extract_text(result, text_score)
                scan_count += 1
                attempted_frames[key] = reading
                if len(attempted_frames) > 512:
                    attempted_frames.popitem(last=False)
                return reading

            preceding = timed[index - 1][1] if index else None
            following = timed[index + 1][1] if index + 1 < len(timed) else None
            previous = scan_segment(
                segment, following, previous, duration, interval, recognize, preceding,
                extension=extension, short_interval=short_interval,
                short_fallback=bool(settings["ocr_short_fallback"]),
            )
            lines[position] = previous.text
            _emit({"type": "progress", "value": 5 + round(93 * (index + 1) / len(timed)),
                   "message": f"Đang quét OCR: mục {index + 1}/{len(timed)}"})
        if timed and not scan_count:
            raise ValueError("Không giải mã được khung hình nào tại các mốc SRT để quét OCR.")
    finally:
        capture.release()
    return lines, scan_count


def run(request: dict[str, Any]) -> None:
    settings = with_defaults(StepId.STT, request)
    validate_ocr_options(
        settings["ocr_image_mode"], settings["ocr_method"], settings["ocr_seek_mode"],
        float(settings["ocr_text_score"]),
    )
    engine = _engine(settings)
    lines, scan_count = _scan(settings, engine)
    _emit({"type": "result", "lines": lines, "scan_count": scan_count, "actual_device": _DEVICE})


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
