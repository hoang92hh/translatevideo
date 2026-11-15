"""Dán toàn bộ file vào MỘT ô Colab rồi Run; chọn GPU trước khi chạy.

Chọn thư mục WAV trong /content, tạo SRT và tải ZIP kèm báo cáo cần kiểm tra.
"""
from __future__ import annotations

# COLAB BOOTSTRAP BEGIN
if __name__ == "__main__":
    import subprocess
    import sys

    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q",
        "stable-ts[fw]==2.19.1", "faster-whisper==1.2.1", "silero-vad==6.2.3",
        "av<19", "soundfile", "scipy",
    ])
# COLAB BOOTSTRAP END

import importlib.metadata
import json
from dataclasses import asdict
from datetime import datetime
from math import gcd
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import soundfile as sf
import stable_whisper
import torch
from faster_whisper.feature_extractor import FeatureExtractor
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


SAMPLE_RATE = 16000


def text_key(text):
    return "".join(text.split())


def audio_windows(start, end, window_seconds, overlap_seconds):
    window_samples = round(window_seconds * SAMPLE_RATE)
    step = window_samples - round(overlap_seconds * SAMPLE_RATE)
    while start < end:
        window_end = min(start + window_samples, end)
        yield start, window_end
        if window_end == end:
            break
        start += step


def timing_issues(words, duration):
    issues = []
    previous_end = 0.0
    for index, word in enumerate(words):
        start, end = word["start"], word["end"]
        reasons = []
        if not np.isfinite([start, end]).all():
            reasons.append("nonfinite_timestamp")
        elif start < -0.04 or end > duration + 0.04 or end <= start:
            reasons.append("invalid_timestamp")
        if end - start > 2.0:
            reasons.append("long_word")
        if start < previous_end - 0.08:
            reasons.append("overlapping_words")
        probability = word.get("probability")
        if probability is None or not np.isfinite(probability) or probability < 0.15:
            reasons.append("low_probability")
        if reasons:
            issues.append({"word_index": index, "reasons": reasons})
        previous_end = end
    return issues


def align_segment(model, clip, raw, record):
    """Re-align only a problematic native segment within its source context."""
    duration = len(clip) / SAMPLE_RATE
    words = raw.get("words") or []
    original_issues = timing_issues(words, duration)
    text_matches = text_key("".join(word["word"] for word in words)) == text_key(raw["text"])
    invalid = any(set(issue["reasons"]) - {"low_probability"}
                  for issue in original_issues)
    if words and text_matches and not invalid:
        return words, original_issues, "asr_words"

    context_start = max(0, round((raw["start"] - 0.5) * SAMPLE_RATE))
    context_end = min(len(clip), round((raw["end"] + 0.5) * SAMPLE_RATE))
    best_words = words if text_matches else []
    best_issues = original_issues if best_words else []
    best_score = None
    best_source = "asr_words_needs_review"
    if best_words:
        invalid_count = sum("invalid_timestamp" in issue["reasons"]
                            or "nonfinite_timestamp" in issue["reasons"] for issue in best_issues)
        best_score = (invalid_count, len(best_issues))
    for token_step in (100, 200):
        attempt = {"segment_id": raw["id"], "token_step": token_step}
        try:
            aligned = model.align(
                np.ascontiguousarray(clip[context_start:context_end]), raw["text"],
                language="zh", token_step=token_step, remove_instant_words=False,
                nonspeech_skip=None, regroup=False, suppress_silence=False,
                suppress_word_ts=False, vad=False, verbose=None)
            aligned_words = [word.to_dict() for segment in aligned.segments
                             for word in (segment.words or [])] if aligned is not None else []
            if text_key("".join(word["word"] for word in aligned_words)) != text_key(raw["text"]):
                raise ValueError("Căn thời gian chưa giữ đủ text của segment gốc.")
            for word in aligned_words:
                word["start"] += context_start / SAMPLE_RATE
                word["end"] += context_start / SAMPLE_RATE
            issues = timing_issues(aligned_words, duration)
            invalid_count = sum("invalid_timestamp" in issue["reasons"]
                                or "nonfinite_timestamp" in issue["reasons"] for issue in issues)
            score = (invalid_count, len(issues))
            attempt.update(words=aligned_words, issues=issues)
            if best_score is None or score < best_score:
                best_words, best_issues, best_score = aligned_words, issues, score
                best_source = "realigned_segment"
        except Exception as error:
            attempt["error"] = f"{type(error).__name__}: {error}"
        record["alignment_attempts"].append(attempt)
        if best_words and not best_issues:
            break
    if best_words:
        return best_words, best_issues, best_source
    return [], [{"word_index": None, "reasons": ["missing_word_timing"]}], "asr_segment_needs_review"


def recognize_window(model, audio, start, end, out, window_id, *, recovery=False,
                     chunk_seconds=25.0):
    """Preserve native ASR utterances and their boundaries on the original audio."""
    clip = np.ascontiguousarray(audio[start:end])
    offset = start / SAMPLE_RATE
    duration = len(clip) / SAMPLE_RATE
    chunk_length = max(1, round(chunk_seconds))
    record = {"id": window_id, "start": offset, "end": end / SAMPLE_RATE,
              "recovery": recovery, "chunk_seconds": chunk_length,
              "decode_attempts": [], "alignment_attempts": []}
    path = out / f"{window_id}.json"
    raw_segments, complete = [], False
    for temperature in (0.0, 0.2):
        attempt = {"temperature": temperature, "segments": []}
        try:
            segments, _ = model.transcribe_original(
                clip, language="zh", task="transcribe", initial_prompt=None,
                without_timestamps=False, word_timestamps=True, vad_filter=False,
                no_speech_threshold=None, hallucination_silence_threshold=None,
                condition_on_previous_text=False, temperature=temperature,
                beam_size=5, repetition_penalty=1.0, chunk_length=chunk_length)
            for segment in segments:
                attempt["segments"].append(asdict(segment))
        except Exception as error:
            attempt["error"] = f"{type(error).__name__}: {error}"
        record["decode_attempts"].append(attempt)
        if attempt["segments"]:
            raw_segments = attempt["segments"]
            complete = "error" not in attempt
        dump_json(path, record)
        if complete and any(segment["text"].strip() for segment in raw_segments):
            break

    recognized = []
    for index, raw in enumerate(raw_segments):
        if not raw["text"].strip():
            continue
        words, issues, timing_source = align_segment(model, clip, raw, record)
        reasons = {reason for issue in issues for reason in issue["reasons"]}
        if not complete:
            reasons.add("incomplete_recognition")
        segment_id = f"{window_id}:{index}"
        global_words = []
        for word_index, word in enumerate(words):
            word = dict(word)
            word.update(id=f"{segment_id}:{word_index}",
                        start=word["start"] + offset, end=word["end"] + offset)
            global_words.append(word)
        segment = {"id": segment_id, "source": window_id, "text": raw["text"].strip(),
                   "start": raw["start"] + offset, "end": raw["end"] + offset,
                   "words": global_words, "issues": sorted(reasons),
                   "timing_source": timing_source,
                   "avg_logprob": raw.get("avg_logprob"),
                   "no_speech_prob": raw.get("no_speech_prob")}
        recognized.append(segment)
        record["segments"] = recognized
        dump_json(path, record)
    if recognized:
        status = "recognized" if complete else "needs_review"
    elif all("error" in attempt for attempt in record["decode_attempts"]):
        status = "recognition_failed"
    else:
        status = "empty_recognition"
    record.update(segments=recognized, status=status, duration_seconds=duration)
    dump_json(path, record)
    return recognized, record


def uncovered_speech(regions, cues, *, min_gap=0.4):
    coverage = sorted((cue["start"] - 0.08, cue["end"] + 0.08)
                      for cue in cues if np.isfinite([cue["start"], cue["end"]]).all()
                      and cue["end"] > cue["start"])
    gaps = []
    for region in regions:
        cursor = region["start"]
        for start, end in coverage:
            if end <= cursor or start >= region["end"]:
                continue
            if start - cursor >= min_gap:
                gaps.append({"start": cursor, "end": min(start, region["end"])})
            cursor = max(cursor, end)
        if region["end"] - cursor >= min_gap:
            gaps.append({"start": cursor, "end": region["end"]})
    return gaps


def sentence_cues(segments, split_gap, max_segment_seconds=6.0, max_chars=40):
    """Split each native utterance independently at word timestamps."""
    cues = []
    for segment in sorted(segments, key=lambda item: (item["start"], item["end"])):
        # Keep zero-duration text with a timed neighbour in the same ASR utterance.
        words = []
        pending_text = ""
        attached_text = False
        for word in segment["words"]:
            if not np.isfinite([word["start"], word["end"]]).all() or word["end"] < word["start"]:
                words = []
                break
            if word["end"] == word["start"]:
                attached_text = True
                if words:
                    words[-1]["word"] += word["word"]
                else:
                    pending_text += word["word"]
                continue
            word = dict(word)
            word["word"] = pending_text + word["word"]
            pending_text = ""
            words.append(word)
        cue_issues = list(segment["issues"])
        if attached_text:
            cue_issues.append("untimed_text_attached_to_neighbor")
        valid_times = words and all(np.isfinite([word["start"], word["end"]]).all()
                                   and word["end"] > word["start"] for word in words)
        valid_times = (valid_times and text_key("".join(word["word"] for word in words))
                       == text_key(segment["text"]))
        if not valid_times:
            cues.append({"start": segment["start"], "end": segment["end"],
                         "text": segment["text"], "source_segment_id": segment["id"],
                         "words": [],
                         "issues": sorted(set(cue_issues) | {"missing_word_timing"}),
                         "timing_source": "asr_segment_needs_review"})
            continue
        groups, group = [], []
        for word in words:
            last_text = group[-1]["word"].rstrip().rstrip('"\'”’」』') if group else ""
            gap = word["start"] - group[-1]["end"] if group else 0.0
            duration = word["end"] - group[0]["start"] if group else 0.0
            chars = sum(len(item["word"].strip()) for item in group) + len(word["word"].strip())
            spoken = any(char.isalnum() for char in word["word"])
            split = (gap >= split_gap or last_text.endswith(tuple("。？！?!"))
                     or duration > max_segment_seconds or chars > max_chars)
            if group and spoken and split:
                groups.append(group)
                group = []
            group.append(word)
        if group:
            groups.append(group)
        for group in groups:
            spoken_words = [word for word in group if any(char.isalnum() for char in word["word"])] or group
            cues.append({"start": min(word["start"] for word in spoken_words),
                         "end": max(word["end"] for word in spoken_words),
                         "text": "".join(word["word"] for word in group).strip(),
                         "words": [dict(word) for word in group],
                         "source_segment_id": segment["id"], "issues": list(cue_issues),
                         "timing_source": segment["timing_source"]})
    cues.sort(key=lambda cue: (cue["start"], cue["end"]))
    for index, cue in enumerate(cues, 1):
        cue["id"] = index
        if cue["end"] - cue["start"] > max_segment_seconds + 0.04:
            cue["issues"].append("long_segment_needs_review")
        if len(text_key(cue["text"])) > max_chars:
            cue["issues"].append("long_text_needs_review")
    return cues


def refine_cue_starts(model, audio, cues, regions, *, precision=0.02, max_shift=0.5):
    """Refine first spoken-word starts while retaining each cue's text and end."""
    duration = len(audio) / SAMPLE_RATE
    probability_threshold = 0.5
    summary = {"steps": "s", "word_level": False, "precision": precision,
               "max_shift_seconds": max_shift, "rel_dur_change": None,
               "prob_threshold": probability_threshold, "status": "completed"}
    candidates, prepared = [], []
    previous_word_end = 0.0
    for cue_index, cue in enumerate(cues):
        details = {"original_start": cue["start"], "proposed_start": None,
                   "status": "pending", "reasons": []}
        cue["start_refinement"] = details
        # Punctuation-only entries have no spoken onset to refine.
        word_indices = [index for index, word in enumerate(cue.get("words", []))
                        if any(char.isalnum() for char in word["word"])]
        words = [cue["words"][index] for index in word_indices]
        reason = None
        if not words:
            reason = "start_refine_missing_words"
        elif not all(np.isfinite([word["start"], word["end"]]).all()
                     and 0 <= word["start"] < min(word["end"], duration)
                     and word["end"] <= duration + precision for word in words):
            reason = "start_refine_invalid_word_timing"
        elif (not np.isfinite([cue["start"], cue["end"]]).all()
              or not 0 <= cue["start"] < min(cue["end"], duration)
              or cue["end"] > duration + precision
              or abs(words[0]["start"] - cue["start"]) > 0.001):
            reason = "start_refine_invalid_cue_timing"
        elif (words[0]["start"] < previous_word_end
              or any(second["start"] < first["end"]
                     for first, second in zip(words, words[1:]))):
            reason = "start_refine_overlapping_words"
        elif not all(word.get("probability") is not None
                     and np.isfinite(word["probability"]) for word in words):
            reason = "start_refine_missing_probability"
        elif words[0]["probability"] < probability_threshold:
            reason = "start_refine_low_probability"
        if reason:
            details.update(status="skipped", reasons=[reason])
            cue["issues"].append(reason)
            continue
        # Tokenize the current text afresh, including text attached from zero-duration words.
        clean_words = [{"word": word["word"], "start": word["start"],
                        "end": word["end"], "probability": word["probability"],
                        "tokens": None} for word in words]
        prepared.append({"words": clean_words, "round_ts": False})
        candidates.append((cue_index, word_indices[0], clean_words))
        previous_word_end = words[-1]["end"]

    refined = None
    if prepared:
        print(f"Tinh chỉnh start {len(prepared)}/{len(cues)} đoạn; bước dò {precision:.2f}s.")
        original_extractor = model.feature_extractor
        try:
            # Stable-ts packs refinement batches up to 30s; ASR previously set this to 25s.
            model.feature_extractor = FeatureExtractor(
                feature_size=original_extractor.mel_filters.shape[0],
                sampling_rate=original_extractor.sampling_rate,
                hop_length=original_extractor.hop_length, n_fft=original_extractor.n_fft,
                chunk_length=30)
            result = stable_whisper.WhisperResult(
                {"language": "zh", "segments": prepared}, check_sorted=False)
            refined = model.refine(
                audio, result, steps="s", word_level=False, precision=precision,
                abs_dur_change=max_shift, rel_dur_change=None,
                prob_threshold=probability_threshold, inplace=False, verbose=False)
            if refined is None or len(refined.segments) != len(candidates):
                raise ValueError("Refine trả về số segment khác dữ liệu đầu vào.")
        except Exception as error:
            refined = None
            summary.update(status="failed", error=f"{type(error).__name__}: {error}")
            for cue_index, _, _ in candidates:
                cue = cues[cue_index]
                cue["start_refinement"].update(status="failed", reasons=["start_refine_failed"])
                cue["issues"].append("start_refine_failed")
        finally:
            model.feature_extractor = original_extractor

    if refined is not None:
        for (cue_index, word_index, expected), segment in zip(candidates, refined.segments):
            cue = cues[cue_index]
            details = cue["start_refinement"]
            words = segment.words or []
            stable_content = len(words) == len(expected) and all(
                word.word == original["word"] and abs(word.end - original["end"]) <= 1e-6
                and (index == 0 or abs(word.start - original["start"]) <= 1e-6)
                for index, (word, original) in enumerate(zip(words, expected)))
            if not stable_content:
                details.update(status="rejected", reasons=["start_refine_changed_content_or_end"])
                cue["issues"].append("start_refine_changed_content_or_end")
                continue
            proposed = words[0].start
            finite = bool(np.isfinite(proposed))
            details["proposed_start"] = proposed if finite else None
            reasons = []
            if (not finite or not 0 <= proposed < duration
                    or round(proposed * 1000) >= round(min(expected[0]["end"], cue["end"]) * 1000)):
                reasons.append("start_refine_invalid_start")
            if finite and abs(proposed - cue["start"]) > max_shift + 1e-6:
                reasons.append("start_refine_excessive_shift")
            if finite and cue_index and proposed < cues[cue_index - 1]["end"] - 1e-6:
                reasons.append("start_refine_overlaps_previous")
            if reasons:
                details.update(status="rejected", reasons=reasons)
                cue["issues"].extend(reasons)
                continue
            changed = abs(proposed - cue["start"]) > 1e-6
            cue["start"] = proposed
            cue["words"][word_index]["start"] = proposed
            details["status"] = "refined" if changed else "unchanged"
            if not any(region["start"] - precision <= proposed <= region["end"] + precision
                       for region in regions):
                details["reasons"].append("start_refine_outside_vad")
                cue["issues"].append("start_refine_outside_vad")
    summary["counts"] = {status: sum(cue["start_refinement"]["status"] == status for cue in cues)
                         for status in ("refined", "unchanged", "skipped", "rejected", "failed")}
    return summary


def add_recovery_segments(existing, incoming, report):
    """Use supplementary recognition only in uncovered time ranges."""
    added = []
    protected = list(existing)
    for segment in incoming:
        words = segment["words"]
        if not words:
            report["alternatives"].append({"segment_id": segment["id"],
                                           "reason": "missing_word_timing"})
            continue
        groups, group = [], []
        for word in words:
            valid = (np.isfinite([word["start"], word["end"]]).all()
                     and word["end"] > word["start"])
            overlap = any(min(word["end"], cue["end"]) > max(word["start"], cue["start"]) - 0.04
                          for cue in protected)
            if valid and not overlap:
                group.append(word)
            else:
                if group:
                    groups.append(group)
                    group = []
        if group:
            groups.append(group)
        kept_text = "".join(word["word"] for group in groups for word in group)
        if text_key(kept_text) != text_key(segment["text"]):
            report["alternatives"].append({"segment_id": segment["id"],
                                           "reason": "overlaps_existing_utterance",
                                           "text": segment["text"]})
        for index, group in enumerate(groups):
            recovered = dict(segment)
            recovered.update(id=f"{segment['id']}:gap-{index}", words=group,
                             text="".join(word["word"] for word in group).strip(),
                             start=group[0]["start"], end=group[-1]["end"])
            added.append(recovered)
            protected.append(recovered)
    return added


def run_vad_stable_srt(audio_path, output_root="/content/srt_results", *,
                       model_name="large-v2", vad_threshold=0.35, split_gap=0.3,
                       window_seconds=25.0, overlap_seconds=3.0,
                       max_segment_seconds=6.0, max_chars=40):
    """Preserve ASR utterances, split short cues, and recover uncovered speech."""
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy {audio_path}.")
    if not torch.cuda.is_available():
        raise RuntimeError("Chọn Runtime > Change runtime type > GPU trên Colab.")
    if (not np.isfinite([vad_threshold, split_gap, window_seconds, overlap_seconds,
                         max_segment_seconds, max_chars]).all()
            or not 0 < vad_threshold < 1 or split_gap <= 0 or max_segment_seconds <= 0
            or max_chars < 1 or not 0 < overlap_seconds < window_seconds <= 30
            or round(overlap_seconds * SAMPLE_RATE) < 1
            or round(overlap_seconds * SAMPLE_RATE) >= round(window_seconds * SAMPLE_RATE)):
        raise ValueError("Thông số VAD, cửa sổ hoặc độ dài segment không hợp lệ.")
    audio = read_audio(audio_path)
    duration = len(audio) / SAMPLE_RATE
    out = Path(output_root) / (audio_path.stem + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "audio": str(audio_path), "source_duration_seconds": duration,
        "model": model_name, "language": "zh", "vad_threshold": vad_threshold,
        "split_gap": split_gap, "chunk_seconds": max(1, round(window_seconds)),
        "max_segment_seconds": max_segment_seconds, "max_chars": max_chars,
        "vad_regions": [], "windows": [], "alternatives": [], "warnings": [],
        "versions": {name: importlib.metadata.version(name)
                     for name in ("stable-ts", "faster-whisper", "silero-vad")},
    }
    try:
        vad_model = load_silero_vad()
        regions = get_speech_timestamps(
            torch.from_numpy(audio), vad_model, sampling_rate=SAMPLE_RATE,
            threshold=vad_threshold, min_speech_duration_ms=100,
            min_silence_duration_ms=200, speech_pad_ms=50, return_seconds=False)
        report["vad_regions"] = [{"start": region["start"] / SAMPLE_RATE,
                                  "end": region["end"] / SAMPLE_RATE} for region in regions]
        del vad_model
    except Exception as error:
        report["warnings"].append(f"VAD thất bại: {type(error).__name__}: {error}")
    if not report["vad_regions"]:
        report["warnings"].append("VAD chưa xác định được tiếng nói; cần kiểm tra audio.")

    model = stable_whisper.load_faster_whisper(model_name, device="cuda", compute_type="float16")
    print(f"Nhận dạng toàn bộ {duration:.2f}s và giữ ranh giới từng segment ASR.")
    segments, record = recognize_window(model, audio, 0, len(audio), out, "main-0001",
                                        chunk_seconds=window_seconds)
    report["windows"].append(record)
    cues = sentence_cues(segments, split_gap, max_segment_seconds, max_chars)
    gaps = uncovered_speech(report["vad_regions"], cues)
    report["uncovered_speech_before_recovery"] = gaps
    retry_ranges = [(0, min(len(audio), 15 * SAMPLE_RATE)),
                    (max(0, len(audio) - 15 * SAMPLE_RATE), len(audio))]
    for gap in gaps:
        retry_ranges.append((max(0, round((gap["start"] - 1.0) * SAMPLE_RATE)),
                             min(len(audio), round((gap["end"] + 1.0) * SAMPLE_RATE))))
    recovery_windows = sorted({span for start, end in retry_ranges
                               for span in audio_windows(start, end, min(15.0, window_seconds),
                                                         min(3.0, overlap_seconds))
                               if span != (0, len(audio))})
    for index, (start, end) in enumerate(recovery_windows, 1):
        print(f"Rà vùng thiếu [{index}/{len(recovery_windows)}] "
              f"{start / SAMPLE_RATE:.2f}–{end / SAMPLE_RATE:.2f}s")
        incoming, record = recognize_window(model, audio, start, end, out,
                                            f"recovery-{index:04}", recovery=True,
                                            chunk_seconds=window_seconds)
        report["windows"].append(record)
        additions = add_recovery_segments(cues, incoming, report)
        segments.extend(additions)
        cues = sentence_cues(segments, split_gap, max_segment_seconds, max_chars)
        dump_json(out / "report.json", report)

    report["start_refinement"] = refine_cue_starts(model, audio, cues, report["vad_regions"])
    if report["start_refinement"]["status"] == "failed":
        report["warnings"].append("Tinh chỉnh start thất bại; xem start_refinement.")
    for cue in cues:
        if cue["end"] - cue["start"] > max_segment_seconds + 0.04:
            cue["issues"].append("long_segment_needs_review")
        if not any(min(cue["end"], region["end"]) > max(cue["start"], region["start"])
                   for region in report["vad_regions"]):
            cue["issues"].append("outside_vad_speech")
    for index, cue in enumerate(cues):
        for following in cues[index + 1:]:
            if following["start"] >= cue["end"] - 0.02:
                break
            cue["issues"].append("overlapping_cues")
            following["issues"].append("overlapping_cues")
    for cue in cues:
        cue["issues"] = sorted(set(cue["issues"]))
    expected = {segment["id"]: text_key(segment["text"]) for segment in segments}
    exported = {segment_id: text_key("".join(cue["text"] for cue in cues
                                            if cue["source_segment_id"] == segment_id))
                for segment_id in expected}
    text_mismatches = [segment_id for segment_id in expected if expected[segment_id] != exported[segment_id]]
    if text_mismatches:
        report["warnings"].append("Có segment chưa giữ đủ text; xem text_mismatch_segment_ids.")
    failed_windows = [window["id"] for window in report["windows"]
                      if window["status"] == "recognition_failed"]
    if failed_windows:
        report["warnings"].append("Có lượt nhận dạng bị lỗi; xem failed_windows.")
    gaps = uncovered_speech(report["vad_regions"], cues)
    uncertain_cues = [cue["id"] for cue in cues if cue["issues"]]
    review_required = bool(gaps or uncertain_cues or report["warnings"])
    srt = out / f"{audio_path.stem}.srt"
    report.update(srt=str(srt), status="needs_review" if review_required else "automatic_checks_passed",
                  human_verified=False, segments=segments, cues=cues, uncovered_speech=gaps,
                  failed_windows=failed_windows, review_cue_ids=uncertain_cues,
                  text_mismatch_segment_ids=text_mismatches)
    dump_json(out / "report.json", report)
    if not cues:
        raise RuntimeError(f"Chưa nhận được text. Dữ liệu chẩn đoán đã lưu tại {out}.")
    write_srt(srt, cues)
    print(f"SRT: {srt} | {len(cues)} đoạn")
    if review_required:
        print(f"CẦN KIỂM TRA: {len(uncertain_cues)} đoạn, {len(gaps)} vùng tiếng nghi thiếu. "
              "Xem report.json.")
    return {"srt": str(srt), "folder": str(out), "cues": cues,
            "report": report, "review_required": review_required}


# COLAB RUNNER
if __name__ == "__main__":
    OUTPUT_ROOT = "/content/srt_results"
    MODEL = "large-v2"
    SPLIT_GAP = 0.3
    MAX_SEGMENT_SECONDS = 6.0
    MAX_CHARS = 40

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
                diagnostic_folders = []
                review_count = 0
                for index, wav in enumerate(wavs, 1):
                    print(f"[{index}/{len(wavs)}] {wav.name}")
                    result = run_vad_stable_srt(
                        wav, OUTPUT_ROOT, model_name=MODEL, split_gap=SPLIT_GAP,
                        max_segment_seconds=MAX_SEGMENT_SECONDS, max_chars=MAX_CHARS)
                    srt_paths.append(Path(result["srt"]))
                    diagnostic_folders.append(Path(result["folder"]))
                    review_count += int(result["review_required"])
                archive = (Path(OUTPUT_ROOT) /
                           f"srt-{folder.name}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.zip")
                with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
                    for srt_path in srt_paths:
                        zip_file.write(srt_path, arcname=srt_path.name)
                    for diagnostic_folder in diagnostic_folders:
                        for json_path in sorted(diagnostic_folder.glob("*.json")):
                            zip_file.write(json_path, arcname=(
                                f"diagnostics/{diagnostic_folder.name}/{json_path.name}"))
                print(f"Đã tạo {len(srt_paths)} SRT: {archive}")
                if review_count:
                    print(f"{review_count} file cần nghe kiểm tra các vùng ghi trong report.json.")
                files.download(str(archive))
            finally:
                run_button.disabled = False

    refresh_button.on_click(refresh_folders)
    choose_button.on_click(choose_folder)
    run_button.on_click(run_selected_folder)
    refresh_folders()
    display(folder_picker, widgets.HBox([refresh_button, choose_button, run_button]), status)
