"""Colab: tạo hai SRT cho mỗi WAV trong thư mục, có và không có nhãn speaker.

Chạy toàn bộ file trong một ô Colab có GPU. Giải nén model
pyannote/speaker-diarization-community-1 vào /content/pyannote_model trước.
"""
from __future__ import annotations

# COLAB BOOTSTRAP BEGIN
if __name__ == "__main__":
    import importlib.metadata
    import subprocess
    import sys
    from datetime import datetime

    requirements = {
        "stable-ts": "stable-ts[fw]",
        "faster-whisper": "stable-ts[fw]",
        "silero-vad": "silero-vad",
        "soundfile": "soundfile",
        "scipy": "scipy",
        "pyannote.audio": "pyannote.audio",
        "nvidia-cublas-cu12": "nvidia-cublas-cu12",
        "nvidia-cudnn-cu12": "nvidia-cudnn-cu12==9.*",
    }
    missing = []
    for distribution, package in requirements.items():
        try:
            importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            if package not in missing:
                missing.append(package)
    if missing:
        print(f"[{datetime.now():%H:%M:%S}] Đang cài: {', '.join(missing)}", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    else:
        print(f"[{datetime.now():%H:%M:%S}] Các thư viện đã có; bỏ qua pip.", flush=True)
# COLAB BOOTSTRAP END

import ctypes
import json
from collections import defaultdict
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
print(f"[{datetime.now():%H:%M:%S}] Đang import pyannote.audio...", flush=True)
from pyannote.audio import Pipeline
from pyannote.audio.pipelines.utils.hook import ProgressHook
print(f"[{datetime.now():%H:%M:%S}] Import pyannote.audio hoàn tất.", flush=True)


def log(message):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def prepare_cuda_libraries():
    """Make CUDA 12 libraries visible to CTranslate2 in the Colab kernel."""
    import nvidia.cublas
    import nvidia.cudnn

    libraries = (
        Path(nvidia.cublas.__path__[0]) / "lib/libcublasLt.so.12",
        Path(nvidia.cublas.__path__[0]) / "lib/libcublas.so.12",
        Path(nvidia.cudnn.__path__[0]) / "lib/libcudnn.so.9",
    )
    for library in libraries:
        if not library.is_file():
            raise FileNotFoundError(f"Thiếu thư viện GPU: {library}")
        ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
    log("Đã nạp thư viện CUDA 12 cho faster-whisper.")


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


def stamp(value):
    milliseconds = max(0, round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def compact_speech(audio, regions):
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


def region_index_at(value, mapping):
    sample = value * 16000
    for index, region in enumerate(mapping):
        if sample < region["compact_end"] or index == len(mapping) - 1:
            return index
    return len(mapping) - 1


def original_time(value, region):
    sample = min(max(value * 16000, region["compact_start"]), region["compact_end"])
    return (region["source_start"] + sample - region["compact_start"]) / 16000


def original_cues(result, mapping):
    """Preserve stable-ts splits and split additionally at every VAD join."""
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
        cue["index"] = str(index)
        cue["timeline"] = f"{stamp(cue['start'])} --> {stamp(cue['end'])}"
        cue["text_lines"] = [cue["text"]]
    return cues


def write_plain_srt(path, cues):
    blocks = [
        "\n".join([cue["index"], cue["timeline"], *cue["text_lines"]])
        for cue in cues
    ]
    Path(path).write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def run_asr(audio_path, output, *, model_name="large-v2",
            vad_threshold=0.35, split_gap=0.7):
    """Return original-timeline cues and keep the ASR model loaded."""
    if not 0 < vad_threshold < 1 or split_gap <= 0:
        raise ValueError("vad_threshold hoặc split_gap không hợp lệ.")
    log("Đang đọc WAV và chạy VAD...")
    audio = read_audio(audio_path)
    vad_model = load_silero_vad()
    regions = get_speech_timestamps(
        torch.from_numpy(audio), vad_model, sampling_rate=16000,
        threshold=vad_threshold, min_speech_duration_ms=100,
        min_silence_duration_ms=1000, speech_pad_ms=300,
        return_seconds=False,
    )
    compact, mapping = compact_speech(audio, regions)
    log(f"VAD: {len(mapping)} vùng tiếng; nhận dạng {len(compact) / 16000:.2f}s "
        f"trên {len(audio) / 16000:.2f}s WAV gốc.")
    log(f"Đang nạp stable-ts/faster-whisper {model_name} lên GPU...")
    model = stable_whisper.load_faster_whisper(
        model_name, device="cuda", compute_type="float16",
    )
    log("Đang nhận dạng text và timestamp...")
    result = model.transcribe(
        compact, language="zh", task="transcribe",
        initial_prompt=None,
        word_timestamps=True, regroup=False, vad=False,
        demucs=False, beam_size=5,
        condition_on_previous_text=False,
        temperature=(0.0, 0.2, 0.4, 0.6),
        compression_ratio_threshold=2.0,
        repetition_penalty=1.1,
        hallucination_silence_threshold=2.0,
        verbose=False,
    )
    result.save_as_json(str(output / "stable_compact.json"))
    result.split_by_gap(split_gap)
    result.split_by_punctuation(["。", "？", "！", "?", "!"])
    result.split_by_punctuation(["，", ","], min_chars=40)
    cues = original_cues(result, mapping)
    if not cues:
        raise RuntimeError("stable-ts không nhận được câu thoại nào từ các vùng VAD.")
    plain_srt = output / f"{audio_path.stem}.srt"
    write_plain_srt(plain_srt, cues)
    report = {
        "audio": str(audio_path), "srt": str(plain_srt),
        "source_duration_seconds": len(audio) / 16000,
        "compact_duration_seconds": len(compact) / 16000,
        "model": model_name, "language": "zh", "vad_threshold": vad_threshold,
        "split_gap": split_gap, "initial_prompt": None,
        "vad_regions": [
            {"start": r["source_start"] / 16000, "end": r["source_end"] / 16000}
            for r in mapping
        ],
        "cues": [{"id": int(c["index"]), "start": c["start"],
                  "end": c["end"], "text": c["text"]} for c in cues],
    }
    (output / "asr_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    log(f"Đã tạo SRT không speaker: {plain_srt}")
    return cues, plain_srt, model


def find_model_dir(root):
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"Chưa thấy thư mục model: {root}. Hãy giải nén file RAR trước.")
    if (root / "config.yaml").is_file():
        return root
    matches = sorted(
        (p.parent for p in root.rglob("config.yaml")
         if "speaker-diarization-community-1" in str(p.parent).lower()),
        key=lambda path: (len(path.parts), str(path)),
    )
    if not matches:
        raise FileNotFoundError(
            f"Không tìm thấy config.yaml của community-1 trong {root}. "
            "Kiểm tra thư mục sau giải nén và biến MODEL_ROOT."
        )
    return matches[0]


def speaker_turns(annotation):
    turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append({
            "start": float(turn.start), "end": float(turn.end),
            "speaker": str(speaker),
        })
    return sorted(turns, key=lambda item: (item["start"], item["end"], item["speaker"]))


def choose_speaker(cue, turns):
    """Choose the label with the largest total overlap on the original timeline."""
    overlap = defaultdict(float)
    for turn in turns:
        if turn["start"] >= cue["end"]:
            break
        length = min(cue["end"], turn["end"]) - max(cue["start"], turn["start"])
        if length > 0:
            overlap[turn["speaker"]] += length
    if not overlap:
        return "SPEAKER_UNKNOWN", 0.0
    speaker = sorted(overlap, key=lambda label: (-overlap[label], label))[0]
    return speaker, overlap[speaker]


def write_tagged_srt(path, cues):
    blocks = []
    for cue in cues:
        lines = cue["text_lines"].copy()
        lines[0] = f"[{cue['speaker']}] {lines[0]}"
        blocks.append("\n".join([cue["index"], cue["timeline"], *lines]))
    Path(path).write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def run_combined(audio_path, model_root="/content/pyannote_model",
                 output_root="/content/srt_results", *,
                 model_name="large-v2", vad_threshold=0.35, split_gap=0.7,
                 min_speakers=2, max_speakers=10):
    """Create plain and speaker-tagged SRTs from one original-timeline WAV."""
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy WAV: {audio_path}")
    if not torch.cuda.is_available():
        raise RuntimeError("Bật GPU trong Colab trước khi chạy.")
    if min_speakers < 1 or max_speakers < min_speakers:
        raise ValueError("Giới hạn số speaker không hợp lệ.")
    model_dir = find_model_dir(model_root)
    output = Path(output_root) / (
        audio_path.stem + "-speaker-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    output.mkdir(parents=True, exist_ok=False)
    prepare_cuda_libraries()

    cues, plain_srt, asr_model = run_asr(
        audio_path, output, model_name=model_name,
        vad_threshold=vad_threshold, split_gap=split_gap,
    )
    # Keep the ASR model loaded as requested. This also makes it available
    # for subsequent WAVs if a batch runner is added later.
    log(f"Đang nạp speaker model: {model_dir}")
    pipeline = Pipeline.from_pretrained(str(model_dir))
    log("Đã nạp speaker model. Đang chuyển lên GPU...")
    pipeline.to(torch.device("cuda"))
    log(f"Đang phân tách speaker trên WAV gốc: {audio_path}")
    with ProgressHook() as hook:
        diarization = pipeline(
            str(audio_path), min_speakers=min_speakers, max_speakers=max_speakers,
            hook=hook,
        )
    log("Pyannote đã phân tích xong WAV. Đang gắn nhãn SRT...")
    regular = getattr(diarization, "speaker_diarization", diarization)
    exclusive = getattr(diarization, "exclusive_speaker_diarization", None)
    label_source = exclusive if exclusive is not None else regular
    turns = speaker_turns(label_source)
    if not turns:
        raise RuntimeError("Pyannote không tìm thấy lượt nói nào trong WAV.")

    with (output / "speaker_turns.rttm").open("w", encoding="utf-8") as file:
        regular.write_rttm(file)
    labeled = []
    for cue in cues:
        speaker, overlap = choose_speaker(cue, turns)
        labeled.append({**cue, "speaker": speaker, "overlap_seconds": overlap})

    tagged_srt = output / f"{audio_path.stem}_speaker.srt"
    write_tagged_srt(tagged_srt, labeled)
    report = {
        "audio": str(audio_path), "plain_srt": str(plain_srt),
        "speaker_srt": str(tagged_srt), "speaker_model": str(model_dir),
        "min_speakers": min_speakers, "max_speakers": max_speakers,
        "label_source": "exclusive" if exclusive is not None else "regular",
        "speaker_turns": turns,
        "cue_labels": [
            {"index": cue["index"], "start": cue["start"], "end": cue["end"],
             "speaker": cue["speaker"], "overlap_seconds": cue["overlap_seconds"]}
            for cue in labeled
        ],
    }
    (output / "speaker_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"Đã gắn speaker: {len(labeled)} mục; "
        f"SPEAKER_UNKNOWN: {sum(c['speaker'] == 'SPEAKER_UNKNOWN' for c in labeled)}")
    log(f"SRT có speaker: {tagged_srt}")
    return {"plain_srt": str(plain_srt), "speaker_srt": str(tagged_srt),
            "folder": str(output), "asr_model": asr_model, "speaker_model": pipeline}


# COLAB RUNNER
if __name__ == "__main__":
    MODEL_ROOT = "/content/pyannote_model"
    OUTPUT_ROOT = "/content/srt_results"

    if not torch.cuda.is_available():
        raise RuntimeError("Bật GPU: Runtime > Change runtime type > T4 GPU.")
    from google.colab import files
    from IPython.display import display
    import gc
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
                    log(f"[{index}/{len(wavs)}] {wav.name}")
                    result = run_combined(wav, MODEL_ROOT, OUTPUT_ROOT)
                    srt_paths.extend([Path(result["plain_srt"]),
                                      Path(result["speaker_srt"])])
                    del result
                    gc.collect()
                    torch.cuda.empty_cache()
                archive = (Path(OUTPUT_ROOT) /
                           f"srt-speaker-{folder.name}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip")
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
