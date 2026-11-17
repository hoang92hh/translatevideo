"""Scan original video frames and return one line for each changed OCR text."""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from video_translator.common.models import StepId
from video_translator.config.step_defaults import default_value

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


def _crop(image: Any, bottom_margin_percent: float, width_percent: float, height_percent: float) -> Any:
    height, width = image.shape[:2]
    crop_width = max(1, round(width * width_percent / 100))
    crop_height = max(1, round(height * height_percent / 100))
    left = (width - crop_width) // 2
    bottom_margin = round(height * bottom_margin_percent / 100)
    top = max(0, height - crop_height - bottom_margin)
    return image[top:top + crop_height, left:left + crop_width]


def _scan(request: dict[str, Any], reader: Any) -> tuple[list[str], int, float]:
    import av

    interval = float(request.get("ocr_interval_seconds", default_value(StepId.STT, "ocr_interval_seconds")))
    bottom_margin_percent = float(request.get(
        "ocr_bottom_margin_percent", default_value(StepId.STT, "ocr_bottom_margin_percent"),
    ))
    width_percent = float(request.get("ocr_width_percent", default_value(StepId.STT, "ocr_width_percent")))
    height_percent = float(request.get("ocr_height_percent", default_value(StepId.STT, "ocr_height_percent")))
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Chu kỳ quét OCR phải là số giây lớn hơn 0.")
    if not all(math.isfinite(value) and 0 < value <= 100 for value in (width_percent, height_percent)):
        raise ValueError("Chiều rộng và chiều cao vùng OCR phải lớn hơn 0 và tối đa 100%.")
    if (not math.isfinite(bottom_margin_percent)
            or not 0 <= bottom_margin_percent <= 100 - height_percent + 1e-9):
        raise ValueError(f"Khoảng cách mép dưới OCR phải từ 0% đến {100 - height_percent:g}%.")

    lines: list[str] = []
    scan_count = 0
    next_sample = 0.0
    last_progress = -1
    elapsed = 0.0
    with av.open(str(request["input_video"])) as container:
        if not container.streams.video:
            raise ValueError("File đầu vào không có luồng video để quét OCR.")
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else (
            float(container.duration / av.time_base) if container.duration is not None else 0.0
        )
        origin = float(stream.start_time * stream.time_base) if stream.start_time is not None else None
        frame_rate = float(stream.average_rate) if stream.average_rate else 0.0
        for frame_index, frame in enumerate(container.decode(stream)):
            if frame.time is not None:
                if origin is None:
                    origin = float(frame.time)
                instant = max(0.0, float(frame.time) - origin)
            elif frame_rate > 0:
                instant = frame_index / frame_rate
            else:
                raise ValueError("Video thiếu timestamp và frame rate để đặt lịch quét OCR.")
            if not math.isfinite(instant):
                raise ValueError("Timestamp của khung hình không hợp lệ.")
            elapsed = max(elapsed, instant)
            if instant + 1e-6 < next_sample:
                continue
            next_sample = (math.floor((instant + 1e-6) / interval) + 1) * interval
            image = _crop(
                frame.to_ndarray(format="rgb24"), bottom_margin_percent, width_percent, height_percent,
            )
            blocks = reader.readtext(image, detail=0, paragraph=True)
            text = " ".join(" ".join(str(block) for block in blocks).split())
            if text and (not lines or text != lines[-1]):
                lines.append(text)
            scan_count += 1
            value = min(98, 5 + round(instant / duration * 90)) if duration > 0 else 5
            if value >= last_progress + 5:
                _emit({"type": "progress", "value": value,
                       "message": f"Đang quét OCR video gốc: {instant:.1f}s · {len(lines)} dòng"})
                last_progress = value
    if scan_count == 0:
        raise ValueError("Không giải mã được khung hình nào để quét OCR.")
    return lines, scan_count, max(duration, elapsed)


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
