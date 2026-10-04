from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from contextlib import contextmanager
from importlib import metadata
from typing import Any, Iterator


AUTO_DEVICE = "Auto"
CPU_DEVICE = "CPU"
CUDA_DEVICE = "NVIDIA GPU (CUDA)"


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _error(
    title: str,
    message: str,
    suggestion: str = "",
    fallback_to_cpu: bool = False,
    diagnostics: list[dict[str, Any]] | None = None,
) -> None:
    technical_detail = traceback.format_exc()
    if diagnostics:
        diagnostic_text = json.dumps(diagnostics, ensure_ascii=False, indent=2)
        technical_detail = f"{technical_detail}\n\nDevice diagnostics:\n{diagnostic_text}"
    _emit(
        {
            "type": "error",
            "title": title,
            "message": message,
            "suggestion": suggestion,
            "technical_detail": technical_detail,
            "fallback_to_cpu": fallback_to_cpu,
        }
    )


def _package_installed(name: str) -> bool:
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return False
    return True


@contextmanager
def _capture_onnx_sessions(ort: Any) -> Iterator[list[Any]]:
    """Bắt chính session được tạo khi load model mà không tải model lần hai."""
    original_factory = ort.InferenceSession
    sessions: list[Any] = []

    def tracking_factory(*args: Any, **kwargs: Any) -> Any:
        session = original_factory(*args, **kwargs)
        sessions.append(session)
        return session

    ort.InferenceSession = tracking_factory
    try:
        yield sessions
    finally:
        ort.InferenceSession = original_factory


def _emit_diagnostic(
    diagnostics: list[dict[str, Any]],
    stage: str,
    **values: Any,
) -> None:
    entry = {"stage": stage, **values}
    diagnostics.append(entry)
    _emit({"type": "diagnostic", **entry})


def main() -> int:
    diagnostics: list[dict[str, Any]] = []
    try:
        request = json.loads(sys.stdin.readline())
    except Exception:
        _error("Yêu cầu worker MDX không hợp lệ", "Không thể đọc cấu hình do ứng dụng chính gửi tới.")
        return 2

    requested = str(request.get("requested_device", AUTO_DEVICE))
    if requested not in {AUTO_DEVICE, CPU_DEVICE, CUDA_DEVICE}:
        _emit(
            {
                "type": "error",
                "title": "Thiết bị MDX không hợp lệ",
                "message": f"Không hỗ trợ lựa chọn thiết bị: {requested}",
                "suggestion": "Chọn Auto, CPU hoặc NVIDIA GPU (CUDA).",
                "technical_detail": requested,
            }
        )
        return 2

    # Phải ẩn CUDA trước khi import PyTorch/ONNX để chế độ CPU không dùng lại
    # runtime GPU đã được nạp ở một lần chạy trước.
    if requested == CPU_DEVICE:
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    try:
        import onnxruntime as ort
        import torch
    except Exception:
        profile = "cuda" if requested == CUDA_DEVICE else "cpu"
        _error(
            "Không thể nạp runtime MDX",
            "Thiếu hoặc lỗi PyTorch/ONNX Runtime cho thiết bị đã chọn.",
            f'Cài lại môi trường bằng: python -m pip install -e ".[{profile}]"',
        )
        return 3

    providers = ort.get_available_providers()
    torch_cuda = bool(torch.cuda.is_available())
    onnx_cuda = "CUDAExecutionProvider" in providers
    cuda_ready = torch_cuda and onnx_cuda

    if requested == CUDA_DEVICE and not cuda_ready:
        missing = []
        if not torch_cuda:
            missing.append("PyTorch CUDA")
        if not onnx_cuda:
            missing.append("ONNX CUDAExecutionProvider")
        _emit(
            {
                "type": "error",
                "title": "NVIDIA GPU chưa sẵn sàng",
                "message": "Không thể chạy MDX bằng CUDA vì thiếu " + " và ".join(missing) + ".",
                "suggestion": (
                    'Cài profile CUDA bằng python -m pip install -e ".[cuda]", '
                    "cài PyTorch CUDA phù hợp, hoặc chọn Auto/CPU."
                ),
                "technical_detail": (
                    f"torch={torch.__version__}; torch.cuda.is_available={torch_cuda}; "
                    f"onnxruntime={ort.__version__}; providers={providers}"
                ),
            }
        )
        return 4

    actual_device = CUDA_DEVICE if requested != CPU_DEVICE and cuda_ready else CPU_DEVICE
    execution_provider = "CUDAExecutionProvider" if actual_device == CUDA_DEVICE else "CPUExecutionProvider"
    device_name = torch.cuda.get_device_name(0) if actual_device == CUDA_DEVICE else "CPU"
    if requested == AUTO_DEVICE and actual_device == CPU_DEVICE:
        _emit(
            {
                "type": "progress",
                "value": 18,
                "message": "Auto không tìm thấy CUDA đầy đủ; đang dùng CPU…",
            }
        )
    else:
        _emit(
            {
                "type": "progress",
                "value": 18,
                "message": f"Đã chọn {actual_device}: {device_name}",
            }
        )

    try:
        from audio_separator.separator import Separator

        separator = Separator(
            log_level=logging.WARNING,
            model_file_dir=str(request["model_cache_dir"]),
            output_dir=str(request["output_dir"]),
            output_format="WAV",
        )

        # Gán rõ thiết bị trước load_model(), vì audio-separator sao chép hai
        # thuộc tính này vào model instance khi khởi tạo kiến trúc MDX.
        if actual_device == CUDA_DEVICE:
            separator.torch_device = torch.device("cuda")
            separator.onnx_execution_provider = ["CUDAExecutionProvider"]
        else:
            separator.torch_device = torch.device("cpu")
            separator.onnx_execution_provider = ["CPUExecutionProvider"]

        _emit_diagnostic(
            diagnostics,
            "before_load_model",
            requested_device=requested,
            resolved_device=actual_device,
            torch_version=torch.__version__,
            torch_cuda_version=torch.version.cuda,
            torch_cuda_available=torch_cuda,
            separator_torch_device=str(separator.torch_device),
            requested_onnx_providers=list(separator.onnx_execution_provider),
            available_onnx_providers=providers,
            device_name=device_name,
        )
        _emit(
            {
                "type": "progress",
                "value": 22,
                "message": (
                    f"Đang load model trên {separator.torch_device} · "
                    f"{separator.onnx_execution_provider[0]}…"
                ),
            }
        )

        with _capture_onnx_sessions(ort) as captured_sessions:
            separator.load_model(model_filename=str(request["model_name"]))

        model_instance = separator.model_instance
        model_torch_device = getattr(model_instance, "torch_device", None)
        uses_pytorch_inference = bool(getattr(model_instance, "uses_pytorch_inference", False))
        session_providers: list[str] | None = None

        if uses_pytorch_inference:
            model_device_type = getattr(model_torch_device, "type", str(model_torch_device))
            expected_type = "cuda" if actual_device == CUDA_DEVICE else "cpu"
            if model_device_type != expected_type:
                raise RuntimeError(
                    f"Model PyTorch yêu cầu {expected_type} nhưng thực tế dùng {model_device_type}."
                )
            execution_provider = "PyTorch CUDA" if expected_type == "cuda" else "PyTorch CPU"
        else:
            if not captured_sessions:
                raise RuntimeError(
                    "Không bắt được ONNX InferenceSession do model tạo ra; "
                    "không thể xác minh thiết bị inference thực tế."
                )
            session_providers = list(captured_sessions[-1].get_providers())
            expected_provider = (
                "CUDAExecutionProvider" if actual_device == CUDA_DEVICE else "CPUExecutionProvider"
            )
            if expected_provider not in session_providers:
                raise RuntimeError(
                    f"ONNX Runtime yêu cầu {expected_provider} nhưng session thực tế dùng "
                    f"{session_providers or 'provider không xác định'}."
                )
            execution_provider = expected_provider

        _emit_diagnostic(
            diagnostics,
            "after_load_model",
            model_type=type(model_instance).__name__,
            uses_pytorch_inference=uses_pytorch_inference,
            model_torch_device=str(model_torch_device),
            captured_session_count=len(captured_sessions),
            active_model_providers=session_providers,
            actual_device=actual_device,
            execution_provider=execution_provider,
        )
        _emit(
            {
                "type": "progress",
                "value": 28,
                "message": f"Model đã xác nhận thiết bị: {execution_provider}",
            }
        )
        _emit({"type": "progress", "value": 30, "message": "Đang tách Voice và Background bằng MDX…"})
        generated = separator.separate(str(request["input_audio"]))
    except ModuleNotFoundError as exc:
        _emit(
            {
                "type": "error",
                "title": "Thiếu thư viện cho MDX",
                "message": f"Không tìm thấy module Python “{exc.name or 'không xác định'}”.",
                "suggestion": 'Cài profile CPU hoặc CUDA bằng python -m pip install -e ".[cpu]" / ".[cuda]".',
                "technical_detail": traceback.format_exc(),
            }
        )
        return 5
    except Exception:
        _error(
            "MDX không thể tách audio",
            "Worker gặp lỗi trong lúc tải model hoặc tách Voice/Background.",
            "Thử chọn CPU nếu GPU thiếu VRAM, hoặc mở chi tiết kỹ thuật để xác định nguyên nhân.",
            fallback_to_cpu=requested == AUTO_DEVICE and actual_device == CUDA_DEVICE,
            diagnostics=diagnostics,
        )
        return 6

    _emit(
        {
            "type": "result",
            "generated": generated,
            "requested_device": requested,
            "actual_device": actual_device,
            "execution_provider": execution_provider,
            "device_name": device_name,
            "torch_version": torch.__version__,
            "onnxruntime_version": ort.__version__,
            "onnx_providers": providers,
            "active_model_providers": session_providers,
            "diagnostics": diagnostics,
            "gpu_runtime_installed": _package_installed("onnxruntime-gpu"),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
