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

from video_translator.config.tts import (
    EDGE_TTS_PROVIDER,
    MELO_LANGUAGE_CODES,
    MELO_OPENVOICE_PROVIDER,
    PIPER_PROVIDER,
    VIENEU_PROVIDER,
)


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


def speaker_profile(settings: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    speaker_id = str(item.get("speaker_id", "")).strip() or "SPEAKER_UNKNOWN"
    raw_profiles = settings.get("speaker_profiles", {})
    profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
    raw_profile = profiles.get(speaker_id, {})
    if not isinstance(raw_profile, dict):
        raw_profile = {}
    if speaker_id not in {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"}:
        shared = settings.get("shared_speaker_profile", {})
        if isinstance(shared, dict):
            raw_profile = {**shared, **raw_profile}
    return {
        "speaker_id": speaker_id,
        "voice": str(
            raw_profile.get("model_path")
            or raw_profile.get("voice")
            or settings.get("model_path")
            or settings.get("voice", "Default")
        ).strip(),
        "reference_voice": str(
            raw_profile.get("reference_voice") or settings.get("reference_voice", "")
        ).strip(),
        "piper_speaker_id": raw_profile.get("piper_speaker_id"),
    }


def segment_result(
    item: dict[str, Any],
    provider: str,
    model: str,
    voice: str,
    reference_voice: str,
    actual_device: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": int(item["id"]),
        "speaker_id": str(item.get("speaker_id", "")),
        "provider": provider,
        "model": model,
        "voice": voice,
        "reference_voice": reference_voice,
        "actual_device": actual_device,
        "settings": settings or {},
    }


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
    files: list[str] = []
    segment_results: list[dict[str, Any]] = []
    speed = float(settings.get("speed", 1.0))
    for index, item in enumerate(segments, start=1):
        profile = speaker_profile(settings, item)
        reference = profile["reference_voice"] or None
        voice_name = profile["voice"] or "Default"
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
        segment_results.append(
            segment_result(item, VIENEU_PROVIDER, "VieNeu-TTS v3 Turbo", voice_name, reference or "", actual)
        )
        progress(index, len(segments), f"VieNeu-TTS: đã tạo {index}/{len(segments)} segment…")
    voices = {item["voice"] for item in segment_results}
    return {
        "files": files,
        "actual_device": actual,
        "model": "VieNeu-TTS v3 Turbo",
        "voice": next(iter(voices)) if len(voices) == 1 else "Nhiều giọng theo speaker",
        "segment_results": segment_results,
    }


async def run_edge_async(settings: dict[str, Any], segments: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    import edge_tts

    speed = float(settings.get("speed", 1.0))
    rate = f"{round((speed - 1.0) * 100):+d}%"
    files: list[str] = []
    segment_results: list[dict[str, Any]] = []
    for index, item in enumerate(segments, start=1):
        profile = speaker_profile(settings, item)
        voice = profile["voice"] or "vi-VN-HoaiMyNeural"
        target = output / f"segment_{int(item['id']):04d}.wav"
        temporary = output / f"segment_{int(item['id']):04d}.source.mp3"
        await edge_tts.Communicate(str(item["text"]), voice, rate=rate).save(str(temporary))
        normalize_audio(temporary, target)
        files.append(str(target.resolve()))
        segment_results.append(
            segment_result(item, EDGE_TTS_PROVIDER, "Microsoft Edge TTS", voice, "", "Online")
        )
        progress(index, len(segments), f"Edge TTS: đã tạo {index}/{len(segments)} segment…")
    voices = {item["voice"] for item in segment_results}
    return {
        "files": files,
        "actual_device": "Online",
        "model": "Microsoft Edge TTS",
        "voice": next(iter(voices)) if len(voices) == 1 else "Nhiều giọng theo speaker",
        "segment_results": segment_results,
    }


def run_piper(settings: dict[str, Any], segments: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    import wave

    from piper import PiperVoice, SynthesisConfig

    native_settings = {
        "length_scale": float(settings.get("length_scale", 1.0)),
        "noise_scale": float(settings.get("noise_scale", 0.667)),
        "noise_w_scale": float(settings.get("noise_w_scale", 0.8)),
        "volume": float(settings.get("volume", 1.0)),
        "normalize_audio": bool(settings.get("normalize_audio", True)),
    }
    voice_cache: dict[str, Any] = {}
    files: list[str] = []
    segment_results: list[dict[str, Any]] = []
    for index, item in enumerate(segments, start=1):
        profile = speaker_profile(settings, item)
        model_path = Path(profile["voice"]).expanduser().resolve()
        config_path = Path(f"{model_path}.json")
        cache_key = str(model_path)
        if cache_key not in voice_cache:
            voice_cache[cache_key] = PiperVoice.load(
                str(model_path),
                config_path=str(config_path),
            )
        voice = voice_cache[cache_key]
        requested_speaker_id = profile.get("piper_speaker_id")
        speaker_id = int(requested_speaker_id) if requested_speaker_id is not None else None
        actual_speaker_id = speaker_id
        if actual_speaker_id is None and int(voice.config.num_speakers) > 1:
            actual_speaker_id = int(voice.config.default_speaker_id)
        synthesis_config = SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=native_settings["length_scale"],
            noise_scale=native_settings["noise_scale"],
            noise_w_scale=native_settings["noise_w_scale"],
            volume=native_settings["volume"],
            normalize_audio=native_settings["normalize_audio"],
        )
        target = output / f"segment_{int(item['id']):04d}.wav"
        temporary = output / f"segment_{int(item['id']):04d}.source.wav"
        with wave.open(str(temporary), "wb") as wav_file:
            voice.synthesize_wav(str(item["text"]), wav_file, syn_config=synthesis_config)
        normalize_audio(temporary, target)
        files.append(str(target.resolve()))
        segment_results.append(
            segment_result(
                item,
                PIPER_PROVIDER,
                "Piper TTS",
                str(model_path),
                "",
                "CPU (ONNX)",
                {**native_settings, "piper_speaker_id": actual_speaker_id},
            )
        )
        progress(index, len(segments), f"Piper TTS: đã tạo {index}/{len(segments)} segment…")
    voices = {item["voice"] for item in segment_results}
    return {
        "files": files,
        "actual_device": "CPU (ONNX)",
        "model": "Piper TTS",
        "voice": next(iter(voices)) if len(voices) == 1 else "Nhiều model theo speaker",
        "segment_results": segment_results,
    }


def run_melo(settings: dict[str, Any], segments: list[dict[str, Any]], output: Path, target_language: str, repo_root: Path) -> dict[str, Any]:
    language = MELO_LANGUAGE_CODES.get(target_language)
    if not language:
        raise RuntimeError(f"MeloTTS không hỗ trợ ngôn ngữ đích {target_language}.")
    requested = str(settings.get("device", "Auto"))
    device, actual = select_torch_device(requested)
    from melo.api import TTS

    model = TTS(language=language, device=device)
    speakers = model.hps.data.spk2id
    speed = float(settings.get("speed", 1.0))
    profiles = [speaker_profile(settings, item) for item in segments]
    converter = None
    if any(profile["reference_voice"] for profile in profiles):
        import torch
        from openvoice import se_extractor
        from openvoice.api import ToneColorConverter

        checkpoint_root = Path(os.environ.get("TRANSLANGUAGE_OPENVOICE_CHECKPOINTS", repo_root / ".runtimes" / "melo" / "checkpoints_v2"))
        converter_dir = checkpoint_root / "converter"
        converter = ToneColorConverter(str(converter_dir / "config.json"), device=device)
        converter.load_ckpt(str(converter_dir / "checkpoint.pth"))
    target_cache: dict[str, Any] = {}
    source_cache: dict[str, Any] = {}
    files: list[str] = []
    segment_results: list[dict[str, Any]] = []
    for index, (item, profile) in enumerate(zip(segments, profiles, strict=True), start=1):
        selected = profile["voice"]
        speaker_name = selected if selected in speakers else next(iter(speakers))
        speaker_id = speakers[speaker_name]
        reference = profile["reference_voice"]
        target = output / f"segment_{int(item['id']):04d}.wav"
        normalized = output / f"segment_{int(item['id']):04d}.normalized.wav"
        if not reference or converter is None:
            model.tts_to_file(str(item["text"]), speaker_id, str(target), speed=speed, quiet=True)
        else:
            import torch
            from openvoice import se_extractor

            if reference not in target_cache:
                target_cache[reference], _ = se_extractor.get_se(
                    reference,
                    converter,
                    target_dir=str(output / "reference" / f"profile_{len(target_cache) + 1}"),
                    vad=True,
                )
            if speaker_name not in source_cache:
                source_key = speaker_name.lower().replace("_", "-")
                source_path = checkpoint_root / "base_speakers" / "ses" / f"{source_key}.pth"
                if not source_path.is_file():
                    matches = list((checkpoint_root / "base_speakers" / "ses").glob(f"{language.lower()}*.pth"))
                    if not matches:
                        raise FileNotFoundError(
                            f"Không tìm thấy source speaker embedding trong {source_path.parent}"
                        )
                    source_path = matches[0]
                source_cache[speaker_name] = torch.load(source_path, map_location=device).to(device)
            temporary = output / f"segment_{int(item['id']):04d}.base.wav"
            model.tts_to_file(str(item["text"]), speaker_id, str(temporary), speed=speed, quiet=True)
            converter.convert(
                audio_src_path=str(temporary),
                src_se=source_cache[speaker_name],
                tgt_se=target_cache[reference],
                output_path=str(target),
                message="@TransLanguage",
            )
            temporary.unlink(missing_ok=True)
        normalize_audio(target, normalized)
        normalized.replace(target)
        files.append(str(target.resolve()))
        segment_results.append(
            segment_result(
                item,
                MELO_OPENVOICE_PROVIDER,
                "MeloTTS + OpenVoice V2",
                speaker_name,
                reference,
                actual,
            )
        )
        progress(index, len(segments), f"MeloTTS/OpenVoice: đã tạo {index}/{len(segments)} segment…")
    voices = {item["voice"] for item in segment_results}
    return {
        "files": files,
        "actual_device": actual,
        "model": "MeloTTS + OpenVoice V2",
        "voice": next(iter(voices)) if len(voices) == 1 else "Nhiều giọng theo speaker",
        "segment_results": segment_results,
    }


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
        elif provider == PIPER_PROVIDER:
            if str(settings.get("device", "CPU")) not in {"CPU", "Auto", ""}:
                raise RuntimeError("Piper TTS hiện chỉ hỗ trợ CPU (ONNX).")
            result = run_piper(settings, segments, output)
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
