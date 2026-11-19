"""Step 1.5 Whisper worker with mandatory VAD and standalone SRT timing."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from video_translator.common.models import StepId
from video_translator.config.step_defaults import default_value

_BATCH_MODELS: dict[tuple[str, str, str, str], Any] = {}


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _progress(value: int, message: str) -> None:
    _emit({"type": "progress", "value": value, "message": message})


def _device(requested: str) -> tuple[str, str, str]:
    import torch

    if requested not in {"Auto", "CPU", "GPU"}:
        raise ValueError(f"Thiết bị không hợp lệ: {requested}")
    if requested == "CPU":
        return "cpu", "CPU", "Người dùng chọn CPU."
    if requested == "GPU" and not torch.cuda.is_available():
        raise RuntimeError("PyTorch không phát hiện GPU CUDA khả dụng.")
    if requested == "GPU" or torch.cuda.is_available():
        return "cuda", "NVIDIA GPU (CUDA)", "PyTorch sử dụng GPU CUDA."
    return "cpu", "CPU", "Auto chọn CPU vì PyTorch không phát hiện GPU CUDA."


def _prepare_cuda() -> None:
    import torch

    if sys.platform == "win32":
        torch_lib = Path(torch.__file__).resolve().parent / "lib"
        os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
        for name in ("cublasLt64_12.dll", "cublas64_12.dll", "cudnn64_9.dll"):
            ctypes.WinDLL(str(torch_lib / name))
    torch.ones(1, device="cuda").item()


def run(request: dict[str, Any]) -> None:
    requested = str(request.get("original_request", request.get("requested_device", "Auto")))
    attempted_device = ""
    try:
        _progress(2, "Đang kiểm tra Whisper và thiết bị…")
        device, actual_device, reason = _device(str(request.get("requested_device", "Auto")))
        attempted_device = device
        if device == "cuda":
            _prepare_cuda()
        import ctranslate2
        import stable_whisper
        from .srt_pipeline import read_audio, transcribe_audio

        model_name = str(request.get("model_name", default_value(StepId.STT, "model")))
        compute_type = str(request.get("compute_type", default_value(StepId.STT, "compute_type")))
        if model_name not in {"medium", "large-v2", "large-v3"}:
            raise ValueError(f"Model Whisper không hợp lệ: {model_name}")
        if compute_type not in {"int8_float32", "float16", "float32"}:
            raise ValueError(f"Kiểu tính toán không hợp lệ: {compute_type}")
        supported = ctranslate2.get_supported_compute_types(device)
        if compute_type not in supported:
            raise ValueError(f"{actual_device} không hỗ trợ {compute_type}. Các kiểu hỗ trợ: {sorted(supported)}")
        if requested == "Auto" and device == "cpu" and request.get("fallback_reason"):
            reason = f"Auto chuyển sang CPU: {request['fallback_reason']}"
        reason = f"{reason} CTranslate2 dùng {compute_type}."
        model_key = (model_name, device, str(request["model_cache_dir"]), compute_type)
        model = _BATCH_MODELS.get(model_key)
        if model is None:
            _progress(8, f"Đang nạp Whisper {model_name} trên {actual_device}…")
            model = stable_whisper.load_faster_whisper(
                model_name, device=device, compute_type=compute_type,
                download_root=str(request["model_cache_dir"]),
            )
            if request.get("batch_session"):
                _BATCH_MODELS[model_key] = model
        actual_compute_type = str(model.model.compute_type)
        if request.get("type") == "preload":
            _emit({"type": "result", "actual_device": actual_device, "compute_type": actual_compute_type})
            return

        source = Path(str(request["input_audio"]))
        language = str(request.get("language") or "")
        if language not in {"zh", "en", "vi", "ja", "ko"}:
            raise ValueError(f"Ngôn ngữ project không được hỗ trợ: {language}")
        audio = read_audio(source)
        output_root = str(request.get("diagnostics_dir") or source.parent / "srt_diagnostics" / source.stem)
        cues, report, report_path = transcribe_audio(
            audio, output_root, model=model, model_name=model_name, language=language,
            compute_type=actual_compute_type, progress=_progress,
        )
        _progress(98, "Đang hoàn thiện segment và SRT…")
        _emit({
            "type": "result", "segments": cues,
            "asr_recovery": {
                "enabled": True, "mode": "whisper_vad", "report_path": report_path,
                "status": report["status"], "human_verified": report["human_verified"],
                "vad_region_count": len(report["vad_regions"]),
                "uncovered_speech": report["uncovered_speech"],
                "review_cue_ids": report["review_cue_ids"],
                "failed_windows": report["failed_windows"],
                "start_refinement": report["start_refinement"],
                "warnings": report["warnings"],
            },
            "language": language, "language_probability": None,
            "duration_seconds": len(audio) / 16000,
            "requested_device": requested, "actual_device": actual_device,
            "compute_type": actual_compute_type, "device_selection_reason": reason,
        })
    except Exception as exc:
        _emit({
            "type": "error", "title": "Whisper không thể nhận dạng", "message": str(exc),
            "suggestion": "Kiểm tra model, kiểu tính toán và cấu hình CPU/GPU rồi chạy lại.",
            "technical_detail": traceback.format_exc(),
            "fallback_to_cpu": requested == "Auto" and attempted_device == "cuda",
        })


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
            _emit({"type": "error", "title": "Yêu cầu Step 1.5 không hợp lệ",
                   "message": str(exc), "technical_detail": traceback.format_exc()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
