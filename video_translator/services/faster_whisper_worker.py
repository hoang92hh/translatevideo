from __future__ import annotations

import ctypes
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


_DLL_DIRECTORY_HANDLES: list[object] = []


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def progress(value: int, message: str) -> None:
    emit({"type": "progress", "value": value, "message": message})


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
                value = 15 + round(min(float(segment.end) / duration, 1.0) * 82)
                progress(value, f"Đang nhận dạng đến {float(segment.end):.1f}s / {duration:.1f}s…")
        progress(98, "Đang hoàn thiện transcript…")
        emit(
            {
                "type": "result",
                "segments": segments,
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
