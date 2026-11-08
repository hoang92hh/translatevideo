"""Read subtitles in the lower part of video frames with a local OCR model."""

from __future__ import annotations

import difflib
import json
import math
import os
import sys
import traceback
import unicodedata
from typing import Any


SAMPLE_SECONDS = 0.25
MIN_CONFIDENCE = 0.35
_READERS: dict[tuple[str, bool], Any] = {}


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _comparison(value: str) -> str:
    return "".join(
        char for char in value.casefold()
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def _same_subtitle(left: str, right: str) -> bool:
    a, b = _comparison(left), _comparison(right)
    return bool(a and b) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


def _read_text(reader: Any, image: Any) -> tuple[str, float]:
    lines = [
        (box, str(value).strip(), float(confidence))
        for box, value, confidence in reader.readtext(image, detail=1, paragraph=False)
        if str(value).strip() and float(confidence) >= MIN_CONFIDENCE
    ]
    lines.sort(key=lambda row: (sum(point[1] for point in row[0]) / 4,
                                min(point[0] for point in row[0])))
    return " ".join(row[1] for row in lines), sum(row[2] for row in lines)


def _reader(language: str, device: str) -> tuple[Any, str]:
    import torch
    import easyocr

    if device == "GPU" and not torch.cuda.is_available():
        raise RuntimeError("Không có GPU CUDA cho OCR theo lựa chọn GPU.")
    gpu = device == "GPU" or (device == "Auto" and torch.cuda.is_available())
    cache = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                         "TransLanguage", "easyocr-models")
    for use_gpu in ([True, False] if gpu and device == "Auto" else [gpu]):
        key = (language, use_gpu)
        reader = _READERS.get(key)
        if reader is None:
            try:
                reader = easyocr.Reader(
                    [language], gpu=use_gpu, model_storage_directory=cache, verbose=False,
                )
            except RuntimeError:
                if use_gpu and device == "Auto":
                    continue
                raise
            _READERS[key] = reader
        return reader, "NVIDIA GPU (CUDA)" if use_gpu else "CPU"
    raise RuntimeError("Không thể nạp model OCR trên GPU hoặc CPU.")


def run(request: dict[str, Any]) -> None:
    try:
        import av

        language = str(request["language"])
        if language not in {"ch_sim", "en", "vi", "ja", "ko"}:
            raise ValueError(f"Ngôn ngữ OCR không được hỗ trợ: {language}")
        _emit({"type": "progress", "value": 2, "message": "Đang nạp model OCR…"})
        reader, actual_device = _reader(language, str(request["requested_device"]))
        cues: list[dict[str, object]] = []
        active: dict[str, object] | None = None
        next_sample = 0.0
        duration = 0.0
        last_progress = -1
        with av.open(str(request["input_video"])) as container:
            stream = container.streams.video[0]
            if stream.duration is not None:
                duration = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                duration = container.duration / av.time_base
            for frame in container.decode(stream):
                instant = float(frame.time) if frame.time is not None else (
                    float(frame.pts * stream.time_base) if frame.pts is not None else next_sample
                )
                if not math.isfinite(instant) or instant + 0.001 < next_sample:
                    continue
                next_sample = instant + SAMPLE_SECONDS
                image = frame.to_ndarray(format="rgb24")
                height = image.shape[0]
                subtitle_area = image[int(height * 0.65):]
                text, score = _read_text(reader, subtitle_area)
                if text:
                    if active is None or not _same_subtitle(str(active["text"]), text):
                        if active is not None:
                            active["end"] = max(float(active["start"]) + 0.001, instant)
                            cues.append({"start": active["start"], "end": active["end"],
                                         "text": active["text"]})
                        active = {"start": instant, "last_seen": instant,
                                  "text": text, "score": score}
                    else:
                        active["last_seen"] = instant
                        if score > float(active["score"]):
                            active["text"] = text
                            active["score"] = score
                elif active is not None and instant - float(active["last_seen"]) >= SAMPLE_SECONDS * 2:
                    active["end"] = min(instant, float(active["last_seen"]) + SAMPLE_SECONDS)
                    cues.append({"start": active["start"], "end": active["end"],
                                 "text": active["text"]})
                    active = None
                if duration > 0:
                    value = min(95, 5 + int(instant / duration * 90))
                    if value >= last_progress + 5:
                        _emit({"type": "progress", "value": value,
                               "message": f"Đang nhận diện phụ đề: {instant:.1f}/{duration:.1f}s"})
                        last_progress = value
                duration = max(duration, instant + SAMPLE_SECONDS)
        if active is not None:
            cues.append({"start": active["start"],
                         "end": max(float(active["start"]) + 0.001,
                                    min(duration, float(active["last_seen"]) + SAMPLE_SECONDS)),
                         "text": active["text"]})
        _emit({"type": "result", "segments": cues,
               "duration_seconds": duration, "actual_device": actual_device})
    except Exception as exc:
        _emit({"type": "error", "message": str(exc),
               "technical_detail": traceback.format_exc()})


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if request.get("type") == "shutdown":
            break
        run(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
