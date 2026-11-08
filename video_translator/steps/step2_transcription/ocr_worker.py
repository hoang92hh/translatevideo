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


def _read_text(reader: Any, image: Any, centered: bool = False) -> tuple[str, float]:
    lines = [
        (box, str(value).strip(), float(confidence))
        for box, value, confidence in reader.readtext(image, detail=1, paragraph=False)
        if str(value).strip() and float(confidence) >= MIN_CONFIDENCE
        and (not centered or 0.22 <= sum(point[0] for point in box) / (4 * image.shape[1]) <= 0.78)
    ]
    lines.sort(key=lambda row: (sum(point[1] for point in row[0]) / 4,
                                min(point[0] for point in row[0])))
    score = sum(row[2] for row in lines)
    return " ".join(row[1] for row in lines), (
        score / len(lines) if centered and lines else score
    )


def _subtitle_rows(reader: Any, image: Any) -> list[tuple[int, str, float]]:
    bands: dict[int, list[tuple[float, str, float]]] = {}
    for box, value, confidence in reader.readtext(image, detail=1, paragraph=False):
        center_x = sum(point[0] for point in box) / (4 * image.shape[1])
        center_y = sum(point[1] for point in box) / (4 * image.shape[0])
        if not (0.22 <= center_x <= 0.78) or float(confidence) < MIN_CONFIDENCE:
            continue
        text = str(value).strip()
        if not text or not any(char.isalpha() for char in text):
            continue
        band = round(center_y * 8)
        bands.setdefault(band, []).append((center_x, text, float(confidence)))
    return [
        (band, " ".join(text for _, text, _ in sorted(rows)),
         sum(confidence for _, _, confidence in rows) / len(rows))
        for band, rows in bands.items()
    ]


def _overlaps(start: float, end: float, intervals: list[tuple[float, float]]) -> bool:
    return any(start < right and end > left for left, right in intervals)


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _scan(
    video: str, reader: Any, sample_times: list[float] | None,
    intervals: list[tuple[float, float]] | None,
    sample_seconds: float,
    require_variation: bool = True,
) -> tuple[list[dict[str, object]], float]:
    import av

    observations: list[dict[str, object]] = []
    duration = 0.0
    target = 0
    next_sample = 0.0
    with av.open(video) as container:
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else (
            container.duration / av.time_base if container.duration is not None else 0.0
        )
        scan_ranges = _merge_intervals(
            [(max(0.0, instant - 0.03), instant + 0.7) for instant in sample_times]
            if sample_times is not None else (intervals or [])
        )
        for range_start, range_end in scan_ranges:
            # Seek to the preceding keyframe, then use decoded frame timestamps
            # for the same sampling decisions as the sequential scanner.
            container.seek(
                max(0, int(range_start / float(stream.time_base))),
                stream=stream, backward=True, any_frame=False,
            )
            for frame in container.decode(stream):
                instant = float(frame.time) if frame.time is not None else (
                    float(frame.pts * stream.time_base) if frame.pts is not None else next_sample
                )
                if not math.isfinite(instant) or instant < range_start - 0.001:
                    continue
                if instant > range_end + 0.001:
                    break
                duration = max(duration, instant)
                if sample_times is not None:
                    while target < len(sample_times) and instant >= sample_times[target] - 0.03:
                        target += 1
                        if instant > sample_times[target - 1] + 0.7:
                            continue
                        break
                    else:
                        continue
                else:
                    if instant + 0.001 < next_sample or not _overlaps(
                        instant, instant + 0.001, intervals or [],
                    ):
                        continue
                    next_sample = instant + sample_seconds
                image = frame.to_ndarray(format="rgb24")
                height = image.shape[0]
                for band, text, confidence in _subtitle_rows(reader, image[int(height * 0.65):]):
                    observations.append({"start": instant, "end": instant + sample_seconds,
                                         "text": text, "confidence": confidence, "band": band})
    by_band: dict[int, list[dict[str, object]]] = {}
    for row in observations:
        by_band.setdefault(int(row["band"]), []).append(row)
    candidates = [
        rows for rows in by_band.values()
        if len({_comparison(str(row["text"])) for row in rows}) >= 2
    ]
    observations = max(candidates, key=len) if candidates else (
        max(by_band.values(), key=len) if by_band and not require_variation else []
    )
    return observations, duration


def _cues(observations: list[dict[str, object]], gap: float) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in sorted(observations, key=lambda item: float(item["start"])):
        if result and float(row["start"]) - float(result[-1]["end"]) <= gap and _same_subtitle(
            str(result[-1]["text"]), str(row["text"])
        ):
            result[-1]["end"] = max(float(result[-1]["end"]), float(row["end"]))
            if float(row["confidence"]) > float(result[-1]["confidence"]):
                result[-1]["text"] = row["text"]
                result[-1]["confidence"] = row["confidence"]
        else:
            result.append(dict(row))
    return result


def _stable_subtitle(
    observations: list[dict[str, object]], speech: list[tuple[float, float]],
) -> bool:
    letters = [row for row in observations if any(char.isalpha() for char in str(row["text"]))]
    if not letters:
        return False
    # A permanently repeated overlay is a logo, not a changing subtitle row.
    unique = {_comparison(str(row["text"])) for row in letters}
    if len(unique) < 2:
        return False
    aligned = [row for row in letters if _overlaps(float(row["start"]), float(row["end"]), speech)]
    return len(aligned) >= 2 and len(aligned) / len(letters) >= 0.35


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
        if request.get("type") == "preload":
            _emit({"type": "result", "actual_device": actual_device})
            return
        if request.get("selective"):
            intervals = [
                (max(0.0, float(row["start"]) - 0.2), float(row["end"]) + 0.2)
                for row in request.get("whisper_intervals", [])
                if float(row["end"]) > float(row["start"])
            ]
            dense, duration = _scan(
                str(request["input_video"]), reader, None,
                _merge_intervals(intervals), 0.5, False,
            )
            _emit({"type": "result", "segments": _cues(dense, 0.6),
                   "duration_seconds": duration, "actual_device": actual_device,
                   "stable_subtitle": bool(dense),
                   "reason": "không đọc được phụ đề trong khoảng cần cứu" if not dense else ""})
            return
        if request.get("hybrid"):
            with av.open(str(request["input_video"])) as container:
                stream = container.streams.video[0]
                duration_hint = float(stream.duration * stream.time_base) if stream.duration is not None else (
                    container.duration / av.time_base if container.duration is not None else 0.0
                )
            speech = [
                (float(row["start"]), float(row["end"]))
                for row in request.get("whisper_intervals", [])
                if float(row["end"]) > float(row["start"])
            ]
            sample_times = [duration_hint * index / 48 for index in range(48)] if duration_hint > 0 else [float(index * 5) for index in range(48)]
            _emit({"type": "progress", "value": 8, "message": "Đang quét thưa tối đa 48 khung hình…"})
            sparse, duration = _scan(str(request["input_video"]), reader, sample_times, None, 0.5)
            if not _stable_subtitle(sparse, speech):
                _emit({"type": "result", "segments": [], "duration_seconds": duration,
                       "actual_device": actual_device, "stable_subtitle": False,
                       "reason": "không thấy hàng phụ đề cháy ổn định"})
                return
            intervals = [(max(0.0, start - 0.5), end) for start, end in speech]
            _emit({"type": "progress", "value": 35, "message": "Đang quét dày trong khoảng lời Whisper…"})
            dense, duration = _scan(str(request["input_video"]), reader, None,
                                    _merge_intervals(intervals), 0.5)
            if not _stable_subtitle(dense, speech):
                _emit({"type": "result", "segments": [], "duration_seconds": duration,
                       "actual_device": actual_device, "stable_subtitle": False,
                       "reason": "không thấy hàng phụ đề cháy ổn định"})
                return
            _emit({"type": "result", "segments": _cues(dense, 0.6),
                   "duration_seconds": duration, "actual_device": actual_device,
                   "stable_subtitle": True})
            return
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
