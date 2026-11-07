from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import traceback
import time
import wave
from array import array
from pathlib import Path
from typing import Any

from video_translator.config.speaker_profiles import resolve_speaker_profile
from video_translator.steps.step4_tts.providers import provider_id, provider_value

EDGE_TTS_PROVIDER = provider_id("edge")
MELO_OPENVOICE_PROVIDER = provider_id("melo")
PIPER_PROVIDER = provider_id("piper")
VIENEU_PROVIDER = provider_id("vieneu")
MELO_LANGUAGE_CODES = provider_value("melo", "LANGUAGE_CODES")


_CURRENT_REQUEST_ID = ""
_VIENEU_MODELS: dict[str, Any] = {}
_PIPER_VOICES: dict[str, Any] = {}
_MELO_MODELS: dict[tuple[str, str], Any] = {}
_MELO_CONVERTERS: dict[str, Any] = {}


def emit(payload: dict[str, Any]) -> None:
    if _CURRENT_REQUEST_ID and "request_id" not in payload:
        payload = {**payload, "request_id": _CURRENT_REQUEST_ID}
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


def validate_waveform(audio: Any, sample_rate: int) -> tuple[str, list[str], float]:
    import numpy as np
    original_type = type(audio).__name__
    try:
        # VieNeu may return a CPU/GPU torch tensor or an array with leading
        # singleton dimensions depending on the backend/runtime version.
        if hasattr(audio, "detach"):
            audio = audio.detach().to("cpu").numpy()
        elif isinstance(audio, dict):
            audio = audio.get("audio", audio.get("waveform", audio.get("samples")))
        elif isinstance(audio, (tuple, list)) and audio:
            # Some runtimes return (waveform, sample_rate).
            if len(audio) >= 2 and isinstance(audio[1], (int, float)):
                sample_rate = int(audio[1])
            audio = audio[0]
        values = np.asarray(audio, dtype=np.float32)
        values = np.squeeze(values)
    except (TypeError, ValueError, RuntimeError):
        return "invalid_audio", [f"waveform_conversion_failed:{original_type}"], 0.0
    if values.size == 0 or not np.isfinite(values).all() or sample_rate <= 0:
        return "invalid_audio", [f"empty_or_nonfinite:{original_type}:{getattr(values, 'shape', None)}"], 0.0
    if values.ndim == 1:
        mono = values
    elif values.ndim == 2:
        # Accept either [channels, frames] or [frames, channels].
        channel_axis = 0 if values.shape[0] <= 8 and values.shape[0] < values.shape[1] else 1
        mono = values.mean(axis=channel_axis)
    else:
        # Remaining dimensions are runtime wrappers, not separate audio
        # streams; flatten them instead of rejecting a valid waveform.
        mono = values.reshape(-1)
    duration = float(mono.size) / sample_rate
    if float(np.max(np.abs(mono))) < 1e-5:
        return "silent_audio", [], duration
    return "valid", [], duration


def validate_wav(path: Path) -> tuple[str, list[str], float]:
    try:
        import soundfile as sf
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
        return validate_waveform(audio, int(sample_rate))
    except Exception as exc:
        return "invalid_audio", [repr(exc)], 0.0


def normalize_audio(source: Path, target: Path, speed: float = 1.0) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH để chuẩn hóa output TTS.")
    if abs(speed - 1.0) <= 0.001:
        try:
            import soundfile as sf
            info = sf.info(source)
            if info.samplerate == 48000 and info.channels == 1 and info.subtype == "PCM_16":
                source.replace(target)
                return
        except Exception:
            pass
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if abs(speed - 1.0) > 0.001:
        command.extend(["-filter:a", f"atempo={speed:.4f}"])
    command.extend(["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(target)])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "FFmpeg không thể chuẩn hóa output TTS.")
    source.unlink(missing_ok=True)


def auto_normalize_voice(path: Path, gain_db: float) -> None:
    """Apply the requested gain, capped by the WAV's peak headroom."""
    if not math.isfinite(gain_db) or not -10.0 <= gain_db <= 10.0:
        raise ValueError("Mức điều chỉnh voice phải nằm trong khoảng -10 đến +10 dB.")
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise RuntimeError(f"WAV TTS không phải PCM 16-bit mono: {path}")
        peak = 0
        while frames := source.readframes(65536):
            samples = array("h")
            samples.frombytes(frames)
            if sys.byteorder != "little":
                samples.byteswap()
            peak = max(peak, max((abs(sample) for sample in samples), default=0))
    requested_scale = 10 ** (gain_db / 20)
    ceiling = 10 ** (-1.0 / 20)
    safe_scale = min(requested_scale, ceiling * 32767 / peak) if peak else requested_scale
    if abs(safe_scale - 1.0) < 1e-9:
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH để điều chỉnh âm lượng TTS.")
    temporary = path.with_name(f"{path.stem}.gain.wav")
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-filter:a", f"volume={safe_scale:.12f}",
        "-c:a", "pcm_s16le", str(temporary),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "FFmpeg không thể điều chỉnh âm lượng TTS.")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def speaker_profile(settings: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    speaker_id = str(item.get("speaker_id", "")).strip() or "SPEAKER_UNKNOWN"
    raw_profile = resolve_speaker_profile(settings, speaker_id)
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

    model = _VIENEU_MODELS.get(backend)
    if model is None:
        emit({"type": "progress", "value": 1, "message": "Đang nạp model VieNeu-TTS…"})
        model = Vieneu(backend=backend)
        _VIENEU_MODELS[backend] = model
    files: list[str] = []
    segment_results: list[dict[str, Any]] = []
    speed = float(settings.get("speed", 1.0))
    deep_validation = bool(settings.get("deep_validation", False))
    cache_root = output.parent.parent / "_cache" / "vieneu"
    cache_root.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(segments, start=1):
        started = time.perf_counter()
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
        cache_key = hashlib.sha256(json.dumps({
            "text": str(item["text"]), "model": "VieNeu-TTS v3 Turbo",
            "backend": backend, "voice": voice_name, "reference": reference or "",
            "speed": speed,
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        cached = cache_root / f"{cache_key}.wav"
        cache_hit = cached.is_file() and validate_wav(cached)[0] == "valid"
        if cache_hit:
            shutil.copy2(cached, target)
        else:
            audio = model.infer(**kwargs)
            raw_status, raw_warnings, _ = validate_waveform(
                audio, int(getattr(model, "sample_rate", 48000))
            )
            if raw_status in {"invalid_audio", "silent_audio"}:
                raise RuntimeError(
                    f"VieNeu waveform không hợp lệ: {raw_status} tại segment {item['id']} "
                    f"({'; '.join(raw_warnings) or 'không có chi tiết'})"
                )
            sf.write(temporary, audio, int(getattr(model, "sample_rate", 48000)), subtype="PCM_16")
            normalize_audio(temporary, target, speed)
            final_status, final_warnings, _ = validate_wav(target)
            if final_status in {"invalid_audio", "silent_audio"}:
                target.unlink(missing_ok=True)
                raise RuntimeError(f"WAV chuẩn hóa không hợp lệ: {final_status} tại segment {item['id']}")
            shutil.copy2(target, cached)
        files.append(str(target.resolve()))
        segment_results.append(
            segment_result(
                item, VIENEU_PROVIDER, "VieNeu-TTS v3 Turbo", voice_name, reference or "", actual,
                {"validation_mode": "deep" if deep_validation else "fast",
                 "validation_status": "valid", "cache_hit": cache_hit,
                 "processing_time": round(time.perf_counter() - started, 4)},
            )
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
    files: list[str] = []
    segment_results: list[dict[str, Any]] = []
    for index, item in enumerate(segments, start=1):
        profile = speaker_profile(settings, item)
        model_path = Path(profile["voice"]).expanduser().resolve()
        config_path = Path(f"{model_path}.json")
        cache_key = str(model_path)
        if cache_key not in _PIPER_VOICES:
            _PIPER_VOICES[cache_key] = PiperVoice.load(
                str(model_path),
                config_path=str(config_path),
            )
        voice = _PIPER_VOICES[cache_key]
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

    model_key = (language, device)
    model = _MELO_MODELS.get(model_key)
    if model is None:
        model = TTS(language=language, device=device)
        _MELO_MODELS[model_key] = model
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
        converter = _MELO_CONVERTERS.get(device)
        if converter is None:
            converter = ToneColorConverter(str(converter_dir / "config.json"), device=device)
            converter.load_ckpt(str(converter_dir / "checkpoint.pth"))
            _MELO_CONVERTERS[device] = converter
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


def preload_models(request: dict[str, Any]) -> None:
    provider = str(request["provider"])
    settings = dict(request.get("settings", {}))
    if provider == VIENEU_PROVIDER:
        requested = str(settings.get("device", "Auto"))
        if requested == "CPU":
            backend = "onnx"
        else:
            try:
                import torch
                has_cuda = bool(torch.cuda.is_available())
            except ImportError:
                has_cuda = False
            if requested == "GPU" and not has_cuda:
                raise RuntimeError("Đã chọn GPU nhưng CUDA không khả dụng cho VieNeu-TTS.")
            backend = "pytorch" if has_cuda else "onnx"
        if backend not in _VIENEU_MODELS:
            from vieneu import Vieneu
            _VIENEU_MODELS[backend] = Vieneu(backend=backend)
    elif provider == PIPER_PROVIDER:
        from piper import PiperVoice
        profiles = [settings.get("shared_speaker_profile", {})]
        profiles.extend((settings.get("speaker_profiles") or {}).values())
        profiles.extend((settings.get("speaker_group_profiles") or {}).values())
        profiles.append(settings)
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            raw = str(profile.get("model_path") or profile.get("voice") or "").strip()
            if not raw or not raw.lower().endswith(".onnx"):
                continue
            path = str(Path(raw).expanduser().resolve())
            if path not in _PIPER_VOICES:
                _PIPER_VOICES[path] = PiperVoice.load(path, config_path=f"{path}.json")
    elif provider == MELO_OPENVOICE_PROVIDER:
        language = MELO_LANGUAGE_CODES.get(str(request.get("target_language", "")))
        if not language:
            raise RuntimeError("Ngôn ngữ đích không được MeloTTS hỗ trợ.")
        device, _ = select_torch_device(str(settings.get("device", "Auto")))
        from melo.api import TTS
        key = (language, device)
        if key not in _MELO_MODELS:
            _MELO_MODELS[key] = TTS(language=language, device=device)
        profiles = [settings.get("shared_speaker_profile", {})]
        profiles.extend((settings.get("speaker_profiles") or {}).values())
        profiles.extend((settings.get("speaker_group_profiles") or {}).values())
        if settings.get("reference_voice") or any(
            isinstance(profile, dict) and profile.get("reference_voice") for profile in profiles
        ):
            from openvoice.api import ToneColorConverter
            root = Path(str(request["repo_root"]))
            checkpoint_root = Path(os.environ.get(
                "TRANSLANGUAGE_OPENVOICE_CHECKPOINTS", root / ".runtimes" / "melo" / "checkpoints_v2",
            ))
            converter_dir = checkpoint_root / "converter"
            if device not in _MELO_CONVERTERS:
                converter = ToneColorConverter(str(converter_dir / "config.json"), device=device)
                converter.load_ckpt(str(converter_dir / "checkpoint.pth"))
                _MELO_CONVERTERS[device] = converter
    emit({"type": "result", "model": provider})


def handle_request(request: dict[str, Any]) -> None:
    try:
        if request.get("type") == "preload":
            preload_models(request)
            return
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
        if bool(settings.get("tts_auto_normalize", True)):
            gain_db = float(settings.get("tts_gain_db", 2.0))
            for audio_file in result["files"]:
                auto_normalize_voice(Path(audio_file), gain_db)
        emit({"type": "result", "requested_device": str(settings.get("device", "Auto")), **result})
    except Exception as exc:
        message = str(exc)
        suggestion = "Kiểm tra cài đặt provider, model, thiết bị và file giọng tham chiếu rồi thử lại."
        if isinstance(exc, ModuleNotFoundError):
            message = f"Thiếu module Python: {exc.name}."
            suggestion = "Cài dependency Step 4 theo tài liệu cài đặt rồi khởi động lại ứng dụng."
        emit({"type": "error", "title": "Không thể tạo giọng nói", "message": message, "suggestion": suggestion, "technical_detail": traceback.format_exc()})


def main() -> None:
    global _CURRENT_REQUEST_ID
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        if request.get("type") == "shutdown":
            break
        _CURRENT_REQUEST_ID = str(request.get("request_id", ""))
        try:
            handle_request(request)
        finally:
            _CURRENT_REQUEST_ID = ""


if __name__ == "__main__":
    main()


