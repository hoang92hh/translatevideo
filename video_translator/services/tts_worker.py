from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from ..config.tts import EDGE_TTS_PROVIDER, MELO_LANGUAGE_CODES, MELO_OPENVOICE_PROVIDER, VIENEU_PROVIDER


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(index: int, total: int, message: str) -> None:
    emit({"type": "progress", "value": max(1, min(99, round(index / max(1, total) * 100))), "message": message})


def select_torch_device(requested: str) -> tuple[str, str]:
    import torch

    cuda_available = bool(torch.cuda.is_available())
    if requested == "GPU":
        if not cuda_available:
            raise RuntimeError("Đã chọn GPU nhưng CUDA không khả dụng trong runtime của provider.")
        return "cuda", "GPU (CUDA)"
    if requested == "CPU":
        return "cpu", "CPU"
    return ("cuda", "GPU (CUDA)") if cuda_available else ("cpu", "CPU")


def normalize_audio(source: Path, target: Path, speed: float = 1.0) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH để chuẩn hóa output TTS.")
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if abs(speed - 1.0) > 0.001:
        command.extend(["-filter:a", f"atempo={speed:.4f}"])
    command.extend(["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(target)])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "FFmpeg không thể chuẩn hóa output TTS.")
    source.unlink(missing_ok=True)


def run_vieneu(settings: dict[str, Any], segments: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    requested = str(settings.get("device", "Auto"))
    if requested == "CPU":
        backend, actual = "onnx", "CPU (ONNX)"
    else:
        try:
            import torch
            has_cuda = bool(torch.cuda.is_available())
        except ImportError:
            has_cuda = False
        if requested == "GPU" and not has_cuda:
            raise RuntimeError("Đã chọn GPU nhưng PyTorch CUDA không khả dụng cho VieNeu-TTS.")
        backend, actual = ("pytorch", "GPU (CUDA)") if has_cuda else ("onnx", "CPU (ONNX)")
    from vieneu import Vieneu
    import soundfile as sf

    model = Vieneu(backend=backend)
    reference = str(settings.get("reference_voice", "")).strip() or None
    voice_name = str(settings.get("voice", "Default"))
    files: list[str] = []
    speed = float(settings.get("speed", 1.0))
    for index, item in enumerate(segments, start=1):
        target = output / f"segment_{int(item['id']):04d}.wav"
        temporary = output / f"segment_{int(item['id']):04d}.source.wav"
        kwargs: dict[str, Any] = {"text": str(item["text"])}
        if reference:
            kwargs["ref_audio"] = reference
        elif voice_name != "Default":
            kwargs["voice"] = voice_name
        audio = model.infer(**kwargs)
        sf.write(temporary, audio, int(getattr(model, "sample_rate", 48000)), subtype="PCM_16")
        normalize_audio(temporary, target, speed)
        files.append(str(target.resolve()))
        progress(index, len(segments), f"VieNeu-TTS: đã tạo {index}/{len(segments)} segment…")
    return {"files": files, "actual_device": actual, "model": "VieNeu-TTS v3 Turbo", "voice": voice_name}


async def run_edge_async(settings: dict[str, Any], segments: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    import edge_tts

    voice = str(settings.get("voice", "vi-VN-HoaiMyNeural"))
    speed = float(settings.get("speed", 1.0))
    rate = f"{round((speed - 1.0) * 100):+d}%"
    files: list[str] = []
    for index, item in enumerate(segments, start=1):
        target = output / f"segment_{int(item['id']):04d}.wav"
        temporary = output / f"segment_{int(item['id']):04d}.source.mp3"
        await edge_tts.Communicate(str(item["text"]), voice, rate=rate).save(str(temporary))
        normalize_audio(temporary, target)
        files.append(str(target.resolve()))
        progress(index, len(segments), f"Edge TTS: đã tạo {index}/{len(segments)} segment…")
    return {"files": files, "actual_device": "Online", "model": "Microsoft Edge TTS", "voice": voice}


def run_melo(settings: dict[str, Any], segments: list[dict[str, Any]], output: Path, target_language: str, repo_root: Path) -> dict[str, Any]:
    language = MELO_LANGUAGE_CODES.get(target_language)
    if not language:
        raise RuntimeError(f"MeloTTS không hỗ trợ ngôn ngữ đích {target_language}.")
    requested = str(settings.get("device", "Auto"))
    device, actual = select_torch_device(requested)
    from melo.api import TTS

    model = TTS(language=language, device=device)
    speakers = model.hps.data.spk2id
    selected = str(settings.get("voice", "")).strip()
    speaker_name = selected if selected in speakers else next(iter(speakers))
    speaker_id = speakers[speaker_name]
    speed = float(settings.get("speed", 1.0))
    reference = str(settings.get("reference_voice", "")).strip()
    converter = target_se = source_se = None
    if reference:
        import torch
        from openvoice import se_extractor
        from openvoice.api import ToneColorConverter

        checkpoint_root = Path(os.environ.get("TRANSLANGUAGE_OPENVOICE_CHECKPOINTS", repo_root / ".runtimes" / "melo" / "checkpoints_v2"))
        converter_dir = checkpoint_root / "converter"
        converter = ToneColorConverter(str(converter_dir / "config.json"), device=device)
        converter.load_ckpt(str(converter_dir / "checkpoint.pth"))
        target_se, _ = se_extractor.get_se(
            reference,
            converter,
            target_dir=str(output / "reference"),
            vad=True,
        )
        source_key = speaker_name.lower().replace("_", "-")
        source_path = checkpoint_root / "base_speakers" / "ses" / f"{source_key}.pth"
        if not source_path.is_file():
            matches = list((checkpoint_root / "base_speakers" / "ses").glob(f"{language.lower()}*.pth"))
            if not matches:
                raise FileNotFoundError(f"Không tìm thấy source speaker embedding trong {source_path.parent}")
            source_path = matches[0]
        source_se = torch.load(source_path, map_location=device).to(device)
    files: list[str] = []
    for index, item in enumerate(segments, start=1):
        target = output / f"segment_{int(item['id']):04d}.wav"
        normalized = output / f"segment_{int(item['id']):04d}.normalized.wav"
        if converter is None:
            model.tts_to_file(str(item["text"]), speaker_id, str(target), speed=speed, quiet=True)
        else:
            temporary = output / f"segment_{int(item['id']):04d}.base.wav"
            model.tts_to_file(str(item["text"]), speaker_id, str(temporary), speed=speed, quiet=True)
            converter.convert(audio_src_path=str(temporary), src_se=source_se, tgt_se=target_se, output_path=str(target), message="@TransLanguage")
            temporary.unlink(missing_ok=True)
        normalize_audio(target, normalized)
        normalized.replace(target)
        files.append(str(target.resolve()))
        progress(index, len(segments), f"MeloTTS/OpenVoice: đã tạo {index}/{len(segments)} segment…")
    return {"files": files, "actual_device": actual, "model": "MeloTTS + OpenVoice V2", "voice": speaker_name}


def main() -> None:
    try:
        request = json.loads(sys.stdin.readline())
        provider = str(request["provider"])
        settings = dict(request.get("settings", {}))
        segments = list(request.get("segments", []))
        output = Path(request["output_folder"])
        output.mkdir(parents=True, exist_ok=True)
        if provider == VIENEU_PROVIDER:
            result = run_vieneu(settings, segments, output)
        elif provider == EDGE_TTS_PROVIDER:
            if str(settings.get("device", "Auto")) not in {"Auto", ""}:
                raise RuntimeError("Edge TTS xử lý online nên không hỗ trợ lựa chọn CPU/GPU.")
            result = asyncio.run(run_edge_async(settings, segments, output))
        elif provider == MELO_OPENVOICE_PROVIDER:
            result = run_melo(settings, segments, output, str(request.get("target_language", "")), Path(request["repo_root"]))
        else:
            raise RuntimeError(f"Provider chưa được triển khai: {provider}")
        emit({"type": "result", "requested_device": str(settings.get("device", "Auto")), **result})
    except Exception as exc:
        message = str(exc)
        suggestion = "Kiểm tra cài đặt provider, model, thiết bị và file giọng tham chiếu rồi thử lại."
        if isinstance(exc, ModuleNotFoundError):
            message = f"Thiếu module Python: {exc.name}."
            suggestion = "Cài dependency Step 4 theo tài liệu cài đặt rồi khởi động lại ứng dụng."
        emit({"type": "error", "title": "Không thể tạo giọng nói", "message": message, "suggestion": suggestion, "technical_detail": traceback.format_exc()})


if __name__ == "__main__":
    main()
