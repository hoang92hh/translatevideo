from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(value: int, message: str) -> None:
    emit({"type": "progress", "value": value, "message": message})


def _turns(annotation: object) -> list[dict[str, object]]:
    turns: list[dict[str, object]] = []
    try:
        iterator = iter(annotation)  # pyannote.audio 4.x
        for turn, speaker in iterator:
            turns.append(
                {
                    "start": round(float(turn.start), 3),
                    "end": round(float(turn.end), 3),
                    "speaker_id": str(speaker),
                }
            )
    except (AttributeError, TypeError, ValueError):
        turns.clear()
        itertracks = getattr(annotation, "itertracks", None)
        if not callable(itertracks):
            raise
        for turn, _, speaker in itertracks(yield_label=True):
            turns.append(
                {
                    "start": round(float(turn.start), 3),
                    "end": round(float(turn.end), 3),
                    "speaker_id": str(speaker),
                }
            )
    ordered = sorted(turns, key=lambda item: (float(item["start"]), float(item["end"])))
    labels: dict[str, str] = {}
    for turn in ordered:
        raw = str(turn["speaker_id"])
        if raw not in labels:
            labels[raw] = f"SPEAKER_{len(labels):02d}"
        turn["speaker_id"] = labels[raw]
    return ordered


def run(request: dict[str, Any]) -> None:
    model_path = Path(str(request["model_path"])).resolve()
    if not (model_path / "config.yaml").is_file():
        raise RuntimeError(f"Thư mục model local không hợp lệ: {model_path}")
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    progress(5, "Đang nạp speaker diarization model local…")
    import soundfile as sf
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(str(model_path))
    requested_device = str(request.get("device", "CPU"))
    actual_device = "CPU"
    if requested_device == "GPU" and torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
        actual_device = "NVIDIA GPU (CUDA)"

    input_audio = Path(str(request["input_audio"]))
    progress(15, "Đang đọc audio vào bộ nhớ…")
    samples, sample_rate = sf.read(input_audio, dtype="float32", always_2d=True)
    waveform = torch.from_numpy(samples.T.copy())

    progress(20, f"Đang phân biệt người nói trên {actual_device}…")
    output = pipeline(
        {
            "waveform": waveform,
            "sample_rate": int(sample_rate),
            "uri": input_audio.stem,
        }
    )
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization")
    turns = _turns(annotation)
    progress(98, "Đã hoàn thành speaker diarization.")
    emit(
        {
            "type": "result",
            "model": str(model_path),
            "actual_device": actual_device,
            "turns": turns,
        }
    )


def main() -> int:
    line = sys.stdin.readline()
    if not line:
        emit({"type": "error", "message": "Worker không nhận được dữ liệu đầu vào."})
        return 1
    try:
        run(json.loads(line))
    except Exception as exc:
        emit(
            {
                "type": "error",
                "message": str(exc) or exc.__class__.__name__,
                "technical_detail": repr(exc),
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
