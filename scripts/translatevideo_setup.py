"""Online installer for a clean, machine-specific application environment."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


CUDA_INDEX = "https://download.pytorch.org/whl/cu126"


def run(*command: str, cwd: Path | None = None) -> None:
    print(">", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def cuda_candidate() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        print("GPU:", result.stdout.strip(), flush=True)
        capabilities = [
            float(line.split(",")[1].strip())
            for line in result.stdout.splitlines() if len(line.split(",")) >= 3
        ]
        return any(capability >= 6.0 for capability in capabilities)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False


def install(root: Path, staging: Path, profile: str, manifest: dict) -> None:
    run("py", "-3.13", "-m", "venv", str(staging / "app" / ".venv"))
    python = staging / "app" / ".venv" / "Scripts" / "python.exe"
    run(str(python), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools<82")
    requirements = manifest["project"]["dependencies"] + manifest["project"]["optional-dependencies"][profile]
    run(str(python), "-m", "pip", "install", *requirements)
    if profile == "cuda":
        run(str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps",
            "torch==2.11.0+cu126", "torchvision==0.26.0+cu126", "--index-url", CUDA_INDEX)
        run(str(python), "-c", "import torch, onnxruntime as ort, ctranslate2; "
            "assert torch.cuda.is_available(), 'PyTorch CUDA unavailable'; "
            "assert 'CUDAExecutionProvider' in ort.get_available_providers(), 'ONNX CUDA unavailable'; "
            "assert ctranslate2.get_cuda_device_count() > 0, 'CTranslate2 CUDA unavailable'")
    run(str(python), "-m", "pip", "check")
    (staging / "providers").mkdir()
    (staging / "models").mkdir()
    with (staging / "requirements-installed.txt").open("w", encoding="utf-8") as output:
        subprocess.run([str(python), "-m", "pip", "freeze"], stdout=output, check=True)
    (staging / "profile.txt").write_text(profile + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Cài môi trường TransLanguage")
    parser.add_argument("--cpu", action="store_true", help="Chỉ cài CPU")
    args = parser.parse_args()
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    bundled = Path(getattr(sys, "_MEIPASS", root)) / "pyproject.toml"
    if not bundled.is_file():
        print("setup.exe không chứa cấu hình dependency.")
        return 1
    with bundled.open("rb") as stream:
        manifest = tomllib.load(stream)
    if (root / "environment").exists():
        print("environment đã tồn tại. Hãy giữ/đổi tên bản cũ trước khi cài môi trường mới.")
        return 1
    if not shutil.which("py"):
        print("Thiếu Python Launcher. Cài Python 3.13 x64 trước khi chạy setup.exe.")
        return 1
    try:
        run("py", "-3.13", "--version")
    except subprocess.CalledProcessError:
        print("Không tìm thấy Python 3.13 x64.")
        return 1
    ffmpeg = root / "tool" / "ffmpeg" / "bin"
    if not all((ffmpeg / name).is_file() for name in ("ffmpeg.exe", "ffprobe.exe")):
        print("Thiếu tool\\ffmpeg\\bin\\ffmpeg.exe hoặc ffprobe.exe.")
        return 1
    os.environ["PATH"] = str(ffmpeg) + os.pathsep + os.environ.get("PATH", "")
    target = root / "environment"
    profile = "cuda" if not args.cpu and cuda_candidate() else "cpu"
    try:
        install(root, target, profile, manifest)
    except (OSError, subprocess.CalledProcessError) as exc:
        if profile != "cuda":
            print(f"Cài đặt thất bại: {exc}. environment chưa hoàn tất; không dùng launcher.")
            return 1
        print(f"CUDA không đạt kiểm tra ({exc}); tạo lại môi trường sạch theo CPU.")
        shutil.rmtree(target)
        try:
            install(root, target, "cpu", manifest)
        except (OSError, subprocess.CalledProcessError) as cpu_exc:
            print(f"Cài đặt CPU thất bại: {cpu_exc}. environment chưa hoàn tất.")
            return 1
        profile = "cpu"
    print(f"Hoàn tất: environment ({profile}). Mở translatevideo.exe để chạy ứng dụng.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
