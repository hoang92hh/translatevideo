"""Dán toàn bộ file vào MỘT ô Colab rồi Run; chọn GPU trước khi chạy.

Chọn thư mục WAV trong /content bằng giao diện, tạo SRT và tải ZIP chứa các SRT.
"""
from __future__ import annotations

# COLAB BOOTSTRAP BEGIN
if __name__ == "__main__":
    import subprocess
    import sys

    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q",
        "stable-ts[fw]", "silero-vad", "soundfile", "scipy",
    ])
# COLAB BOOTSTRAP END

import importlib.metadata
import json
from datetime import datetime
from math import gcd
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import soundfile as sf
import stable_whisper
import torch
from scipy.signal import resample_poly
from silero_vad import get_speech_timestamps, load_silero_vad


def dump_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_audio(path):
    audio, rate = sf.read(path, dtype="float32")
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if rate != 16000:
        divisor = gcd(rate, 16000)
        audio = resample_poly(audio, 16000 // divisor, rate // divisor)
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("WAV rỗng hoặc có mẫu âm thanh không hợp lệ.")
    return audio


def stamp(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def write_srt(path, cues):
    Path(path).write_text("\n\n".join(
        f"{cue['id']}\n{stamp(cue['start'])} --> {stamp(cue['end'])}\n{cue['text']}"
        for cue in cues
    ) + "\n", encoding="utf-8")


def compact_speech(audio, regions):
    """Remove VAD silence and retain an exact sample-based source map."""
    pieces, mapping = [], []
    cursor = 0
    for region in regions:
        start, end = int(region["start"]), int(region["end"])
        if end <= start:
            continue
        pieces.append(audio[start:end])
        mapping.append({"compact_start": cursor, "compact_end": cursor + end - start,
                        "source_start": start, "source_end": end})
        cursor += end - start
    if not pieces:
        raise RuntimeError("VAD không tìm thấy tiếng nói trong WAV.")
    return np.ascontiguousarray(np.concatenate(pieces)), mapping


def region_index_at(seconds, mapping):
    sample = seconds * 16000
    for index, region in enumerate(mapping):
        if sample < region["compact_end"] or index == len(mapping) - 1:
            return index
    return len(mapping) - 1


def original_time(seconds, region):
    sample = min(max(seconds * 16000, region["compact_start"]),
                 region["compact_end"])
    return (region["source_start"] + sample - region["compact_start"]) / 16000


def original_cues(result, mapping):
    """Keep stable-ts sentence boundaries and split at every VAD join."""
    cues = []
    for segment in result.segments:
        if not segment.text.strip():
            continue
        words = segment.words or []
        if not words:
            region = mapping[region_index_at((segment.start + segment.end) / 2, mapping)]
            start, end = original_time(segment.start, region), original_time(segment.end, region)
            if end > start:
                cues.append({"start": start, "end": end, "text": segment.text.strip()})
            continue
        groups = []
        for word in words:
            region_index = region_index_at((word.start + word.end) / 2, mapping)
            if not groups or groups[-1][0] != region_index:
                groups.append((region_index, []))
            groups[-1][1].append(word)
        for region_index, group in groups:
            region = mapping[region_index]
            start = original_time(group[0].start, region)
            end = original_time(group[-1].end, region)
            content = "".join(word.word for word in group).strip()
            if content and end > start:
                cues.append({"start": start, "end": end, "text": content})
    for index, cue in enumerate(cues, 1):
        cue["id"] = index
    return cues


def run_vad_stable_srt(audio_path, output_root="/content/srt_results", *,
                       model_name="large-v3", vad_threshold=0.25, split_gap=0.28):
    """Transcribe silence-removed audio and restore original WAV timestamps."""
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy {audio_path}; upload WAV hoặc sửa AUDIO.")
    if not torch.cuda.is_available():
        raise RuntimeError("Chọn Runtime > Change runtime type > GPU trên Colab.")
    if not 0 < vad_threshold < 1 or split_gap <= 0:
        raise ValueError("vad_threshold phải nằm trong (0, 1) và split_gap phải dương.")

    audio = read_audio(audio_path)
    out = Path(output_root) / (audio_path.stem + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True, exist_ok=False)
    vad_model = load_silero_vad()
    regions = get_speech_timestamps(
        torch.from_numpy(audio), vad_model, sampling_rate=16000,
        threshold=vad_threshold, min_speech_duration_ms=100,
        min_silence_duration_ms=1000, speech_pad_ms=150,
        return_seconds=False)
    compact, mapping = compact_speech(audio, regions)
    del vad_model

    print(f"VAD: {len(mapping)} vùng tiếng; nhận dạng {len(compact) / 16000:.2f}s "
          f"trên {len(audio) / 16000:.2f}s WAV gốc.")
    model = stable_whisper.load_faster_whisper(model_name, device="cuda", compute_type="float16")
    result = model.transcribe(
        compact, language="zh", task="transcribe",
        initial_prompt="以下是普通话的句子。",
        word_timestamps=True, regroup=False, vad=True,
        vad_threshold=vad_threshold, demucs=False, beam_size=5,
        condition_on_previous_text=True, verbose=False)
    result.save_as_json(str(out / "stable_compact.json"))
    # Split only: never merge adjacent stable-ts sentences.
    result.split_by_gap(split_gap)
    result.split_by_punctuation(["。", "？", "！", "?", "!", "，", ","])
    cues = original_cues(result, mapping)
    if not cues:
        raise RuntimeError("stable-ts không nhận được câu thoại nào từ các vùng VAD.")
    srt = out / f"{audio_path.stem}.srt"
    write_srt(srt, cues)
    report = {
        "audio": str(audio_path), "srt": str(srt),
        "source_duration_seconds": len(audio) / 16000,
        "compact_duration_seconds": len(compact) / 16000,
        "model": model_name, "language": "zh", "vad_threshold": vad_threshold,
        "split_gap": split_gap, "initial_prompt": "以下是普通话的句子。",
        "vad_regions": [{"start": r["source_start"] / 16000,
                         "end": r["source_end"] / 16000} for r in mapping],
        "cues": cues,
        "versions": {name: importlib.metadata.version(name)
                     for name in ("stable-ts", "faster-whisper", "silero-vad")},
    }
    dump_json(out / "report.json", report)
    print(f"SRT: {srt} | {len(cues)} câu")
    return {"srt": str(srt), "folder": str(out), "cues": cues, "report": report}


# COLAB RUNNER
if __name__ == "__main__":
    OUTPUT_ROOT = "/content/srt_results"
    MODEL = "large-v3"
    SPLIT_GAP = 0.28

    if not torch.cuda.is_available():
        raise RuntimeError("Bật GPU: Runtime > Change runtime type > T4 GPU, rồi chạy lại ô.")
    from google.colab import files
    from IPython.display import display
    import ipywidgets as widgets

    content_root = Path("/content")
    folder_picker = widgets.Dropdown(
        description="Thư mục:", layout=widgets.Layout(width="600px"))
    refresh_button = widgets.Button(description="Làm mới danh sách")
    choose_button = widgets.Button(description="Chọn thư mục")
    run_button = widgets.Button(description="Tạo SRT", button_style="primary")
    status = widgets.Output()
    selected_folder = {"path": None}

    def refresh_folders(_=None):
        folders = [content_root, *(path for path in content_root.rglob("*")
                                   if path.is_dir())]
        current = folder_picker.value
        folder_picker.options = [(str(folder), str(folder)) for folder in folders]
        if current in [str(folder) for folder in folders]:
            folder_picker.value = current

    def choose_folder(_):
        selected_folder["path"] = folder_picker.value
        with status:
            status.clear_output()
            print(f"Đã chọn: {selected_folder['path']}")

    def run_selected_folder(_):
        with status:
            status.clear_output()
            if selected_folder["path"] is None:
                print("Hãy bấm 'Chọn thư mục' trước khi tạo SRT.")
                return
            folder = Path(selected_folder["path"])
            if not folder.is_dir():
                print(f"Thư mục không còn tồn tại: {folder}. Hãy làm mới và chọn lại.")
                return
            print(f"Đường dẫn thư mục: {folder}")
            wavs = sorted((item for item in folder.iterdir()
                           if item.is_file() and item.suffix.lower() == ".wav"),
                          key=lambda item: item.name.lower())
            if not wavs:
                print(f"Không có WAV trực tiếp trong {folder}.")
                return
            run_button.disabled = True
            try:
                srt_paths = []
                for index, wav in enumerate(wavs, 1):
                    print(f"[{index}/{len(wavs)}] {wav.name}")
                    result = run_vad_stable_srt(
                        wav, OUTPUT_ROOT, model_name=MODEL, split_gap=SPLIT_GAP)
                    srt_paths.append(Path(result["srt"]))
                archive = (Path(OUTPUT_ROOT) /
                           f"srt-{folder.name}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip")
                with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
                    for srt_path in srt_paths:
                        zip_file.write(srt_path, arcname=srt_path.name)
                print(f"Đã tạo {len(srt_paths)} SRT: {archive}")
                files.download(str(archive))
            finally:
                run_button.disabled = False

    refresh_button.on_click(refresh_folders)
    choose_button.on_click(choose_folder)
    run_button.on_click(run_selected_folder)
    refresh_folders()
    display(folder_picker, widgets.HBox([refresh_button, choose_button, run_button]), status)
