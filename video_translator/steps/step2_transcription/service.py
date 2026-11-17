from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from ...config.diarization import configured_model_path, missing_model_files
from ...config.step_defaults import default_value, with_defaults
from ...common.models import StepId
from ...errors import UserFacingError
from .batch_session import run_batch_worker

ProgressCallback = Callable[[int, str], None]

SPEAKER_CHANGE_MIN_DURATION_SECONDS = 0.8
SPEAKER_CHANGE_MIN_WORDS = 2


class SpeechToTextError(UserFacingError):
    pass


def preload_batch_models(session_key: str, settings: dict[str, object]) -> None:
    if not settings:
        return
    settings = with_defaults(StepId.STT, settings)
    request: dict[str, object] = {
        "type": "preload",
        "batch_session": True,
        "model_name": str(settings["model"]),
        "model_cache_dir": str(SpeechToTextService.model_cache_dir()),
        "requested_device": str(settings["device"]),
        "compute_type": str(settings["compute_type"]),
    }
    result, error, stderr, _ = run_batch_worker(session_key, "stt", request)
    if error and request["requested_device"] == "Auto" and error.get("fallback_to_cpu"):
        request["requested_device"] = "CPU"
        request["original_request"] = "Auto"
        result, error, stderr, _ = run_batch_worker(session_key, "stt", request)
    if error or result is None:
        raise SpeechToTextError(
            "Không thể nạp model Step 1.5", str((error or {}).get("message", "Worker không trả về kết quả.")),
            "Kiểm tra model và thiết bị đã cấu hình.", stderr,
        )
    if bool(settings["diarization"]):
        model_path = configured_model_path().resolve()
        device = "GPU" if "GPU" in str(result.get("actual_device", "")) else "CPU"
        diar_request: dict[str, object] = {
            "type": "preload", "batch_session": True,
            "model_path": str(model_path), "device": device,
        }
        diar_result, diar_error, stderr, _ = run_batch_worker(session_key, "diarization", diar_request)
        if diar_error and device == "GPU" and settings.get("device") == "Auto":
            diar_request["device"] = "CPU"
            diar_result, diar_error, stderr, _ = run_batch_worker(session_key, "diarization", diar_request)
        if diar_error or diar_result is None:
            raise SpeechToTextError(
                "Không thể nạp model diarization", str((diar_error or {}).get("message", "Worker không trả về kết quả.")),
                "Kiểm tra model local và thiết bị đã cấu hình.", stderr,
            )

    if settings["ocr_enabled"]:
        from .ocr_text import OcrTextService

        source_language = str(settings.get("_source_language", ""))
        language = {"Chinese": "zh", "English": "en", "Vietnamese": "vi",
                    "Japanese": "ja", "Korean": "ko"}.get(source_language, source_language)
        OcrTextService(language, settings, session_key).preload()


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker_id: str = ""
    merge_parts: tuple[dict[str, object], ...] = ()
    confidence: float | None = None


TIMELINE_ISSUE_LABELS = {
    "missing_timeline": "Chưa có Start/End",
    "invalid_interval": "Start/End không hợp lệ",
    "non_increasing_start": "Start trùng hoặc sai thứ tự",
    "overlap": "Chồng thời gian",
}


def assess_transcript_timeline(segments) -> list[dict[str, object]]:
    """Collect editable timing errors without changing any transcript content."""
    flags: dict[int, set[str]] = {}
    valid = []
    previous_valid = None
    for index, item in enumerate(segments, start=1):
        segment_id = getattr(item, "id", index)
        if (not getattr(item, "source_text", "").strip()
                and math.isfinite(item.start) and item.start >= 0
                and item.end == item.start):
            continue
        if item.start == 0.0 and item.end == 0.0:
            flags.setdefault(segment_id, set()).add("missing_timeline")
        elif (not math.isfinite(item.start) or not math.isfinite(item.end)
                or item.start < 0 or item.end <= item.start):
            flags.setdefault(segment_id, set()).add("invalid_interval")
        else:
            valid.append((segment_id, item))
            if previous_valid and item.start <= previous_valid[1].start:
                for affected in (segment_id, previous_valid[0]):
                    flags.setdefault(affected, set()).add("non_increasing_start")
            previous_valid = (segment_id, item)
    active = []
    for segment_id, item in sorted(valid, key=lambda pair: pair[1].start):
        active = [(previous_id, previous) for previous_id, previous in active if previous.end > item.start]
        for previous_id, _ in active:
            flags.setdefault(previous_id, set()).add("overlap")
            flags.setdefault(segment_id, set()).add("overlap")
        active.append((segment_id, item))
    return [{"id": getattr(item, "id", index), "issues": sorted(flags[getattr(item, "id", index)])}
            for index, item in enumerate(segments, start=1) if getattr(item, "id", index) in flags]


@dataclass(frozen=True, slots=True)
class Transcription:
    segments: list[TranscriptSegment]
    language: str
    language_probability: float | None
    duration_seconds: float | None
    requested_device: str
    actual_device: str
    compute_type: str
    device_selection_reason: str
    device_fallback: dict[str, object] | None = None
    diarization_model: str = ""
    diarization_device: str = ""
    diarization_turns: tuple[dict[str, object], ...] = ()
    asr_recovery: dict[str, object] | None = None


class SpeechToTextService:
    def __init__(
        self,
        model_name: str,
        device: str,
        language: str | None,
        diarization: bool = False,
        compute_type: str = default_value(StepId.STT, "compute_type"),
        batch_session: str = "",
        diagnostics_dir: str = "",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.language = language
        self.compute_type = compute_type
        self.diagnostics_dir = diagnostics_dir
        self.diarization = diarization
        self.batch_session = batch_session

    @staticmethod
    def model_cache_dir() -> Path:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".cache"
        path = root / "TransLanguage" / "stable-whisper-models"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def transcribe(
        self,
        input_audio: str | Path,
        progress: ProgressCallback | None = None,
    ) -> Transcription:
        source = Path(input_audio)
        if not source.is_file():
            raise SpeechToTextError(
                "Không tìm thấy audio đầu vào",
                f"File không tồn tại: {source}",
                "Chọn lại Voice hoặc Original Mix còn tồn tại từ Step 1.",
            )
        request = {
            "input_audio": str(source.resolve()),
            "model_name": self.model_name,
            "model_cache_dir": str(self.model_cache_dir()),
            "requested_device": self.device,
            "language": self.language,
            "compute_type": self.compute_type,
            "diagnostics_dir": self.diagnostics_dir,
        }
        stt_progress = (
            (lambda value, message: progress(round(value * 0.58), message))
            if progress and self.diarization
            else progress
        )
        result = self._run_worker(request, stt_progress)
        raw_segments = result.get("segments")
        if not isinstance(raw_segments, list):
            raise SpeechToTextError(
                "Kết quả Whisper không hợp lệ",
                "Worker không trả về danh sách segment hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            )
        try:
            normalized = [
                {
                    "start": float(item["start"]),
                    "end": float(item["end"]),
                    "text": str(item["text"]),
                    "words": list(item.get("words", [])),
                }
                for item in raw_segments
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechToTextError(
                "Kết quả Whisper không hợp lệ",
                "Một hoặc nhiều segment thiếu timestamp hoặc nội dung hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            ) from exc
        if not normalized:
            raise SpeechToTextError(
                "Không phát hiện lời nói",
                "Whisper đã xử lý xong nhưng không tạo được segment nào.",
                "Thử chọn Original Mix hoặc kiểm tra lại audio từ Step 1.",
            )
        diarization_model = ""
        diarization_device = ""
        diarization_turns: tuple[dict[str, object], ...] = ()
        if self.diarization:
            model_path = configured_model_path().resolve()
            missing = missing_model_files(model_path)
            if missing:
                raise SpeechToTextError(
                    "Model speaker diarization chưa sẵn sàng",
                    f"Không thể dùng model local tại: {model_path}",
                    "Mở Cài đặt → API & Providers → Speaker diarization — Local để chọn đúng thư mục model.",
                    "Thiếu hoặc không hợp lệ:\n" + "\n".join(missing),
                )
            if progress:
                progress(60, "Đang chuẩn bị speaker diarization…")
            diarization_device = "GPU" if "GPU" in str(result.get("actual_device", "")) else "CPU"
            diarization_progress = (
                (lambda value, message: progress(60 + round(value * 0.38), message))
                if progress
                else None
            )
            try:
                diarization_result = self._run_diarization_worker(
                    source,
                    model_path,
                    diarization_device,
                    diarization_progress,
                )
            except SpeechToTextError:
                if self.device != "Auto" or diarization_device != "GPU":
                    raise
                if progress:
                    progress(60, "Diarization GPU lỗi; Auto đang thử lại bằng CPU…")
                diarization_result = self._run_diarization_worker(
                    source,
                    model_path,
                    "CPU",
                    diarization_progress,
                )
            turns = diarization_result.get("turns")
            if not isinstance(turns, list) or not turns:
                raise SpeechToTextError(
                    "Không xác định được người nói",
                    "Speaker diarization không trả về khoảng lời nói hợp lệ.",
                    "Kiểm tra file Voice và thư mục model local rồi chạy lại Step 1.5.",
                )
            segments = self._segments_with_speakers(
                normalized,
                turns,
            )
            confident_segments = []
            for segment in segments:
                probabilities = [
                    float(word.get("probability") or 0.0)
                    for source in normalized
                    for word in source["words"]
                    if float(word.get("start", 0.0)) < segment.end
                    and float(word.get("end", 0.0)) > segment.start
                ]
                confident_segments.append(replace(
                    segment,
                    confidence=sum(probabilities) / len(probabilities) if probabilities else None,
                ))
            segments = confident_segments
            diarization_model = str(diarization_result.get("model", ""))
            diarization_device = str(diarization_result.get("actual_device", ""))
            diarization_turns = tuple(dict(turn) for turn in turns)
        else:
            segments = [
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item["text"]),
                    confidence=(
                        sum(float(word.get("probability") or 0.0) for word in item["words"])
                        / len(item["words"])
                        if item["words"] else None
                    ),
                )
                for item in normalized
            ]

        recovery_metadata = (
            dict(result.get("asr_recovery", {}))
            if isinstance(result.get("asr_recovery"), dict)
            else {"enabled": False}
        )
        probability = result.get("language_probability")
        duration = result.get("duration_seconds")
        return Transcription(
            segments=segments,
            language=str(result.get("language", "")),
            language_probability=float(probability) if probability is not None else None,
            duration_seconds=float(duration) if duration is not None else None,
            requested_device=str(result.get("requested_device", self.device)),
            actual_device=str(result.get("actual_device", "")),
            compute_type=str(result.get("compute_type", "default")),
            device_selection_reason=str(result.get("device_selection_reason", "")),
            device_fallback=dict(result.get("device_fallback") or {}),
            diarization_model=diarization_model,
            diarization_device=diarization_device,
            diarization_turns=diarization_turns,
            asr_recovery=recovery_metadata,
        )

    @staticmethod
    def _speaker_for_interval(start: float, end: float, turns: list[dict[str, object]]) -> str:
        overlaps: dict[str, float] = {}
        for turn in turns:
            turn_start = float(turn.get("start", 0.0))
            turn_end = float(turn.get("end", turn_start))
            overlap = max(0.0, min(end, turn_end) - max(start, turn_start))
            speaker = str(turn.get("speaker_id", ""))
            if overlap > 0 and speaker:
                overlaps[speaker] = overlaps.get(speaker, 0.0) + overlap
        return sorted(overlaps, key=lambda speaker: (-overlaps[speaker], speaker))[0] if overlaps else "SPEAKER_UNKNOWN"

    @staticmethod
    def _join_text(left: str, right: str) -> str:
        if not left:
            return right.strip()
        if not right:
            return left.strip()
        left_char = left.rstrip()[-1]
        right_char = right.lstrip()[0]
        cjk = lambda char: "\u3400" <= char <= "\u9fff"
        separator = "" if cjk(left_char) and cjk(right_char) else " "
        return f"{left.rstrip()}{separator}{right.lstrip()}"

    @staticmethod
    def _speaker_runs(units: list[dict[str, object]]) -> list[tuple[int, int]]:
        if not units:
            return []
        runs: list[tuple[int, int]] = []
        start = 0
        for index in range(1, len(units)):
            if units[index]["speaker_id"] != units[start]["speaker_id"]:
                runs.append((start, index))
                start = index
        runs.append((start, len(units)))
        return runs

    @staticmethod
    def _run_is_stable(
        units: list[dict[str, object]],
        start: int,
        end: int,
    ) -> bool:
        spoken_duration = sum(
            max(0.0, float(unit["end"]) - float(unit["start"]))
            for unit in units[start:end]
        )
        word_count = sum(1 for unit in units[start:end] if str(unit["text"]).strip())
        return (
            spoken_duration >= SPEAKER_CHANGE_MIN_DURATION_SECONDS
            or word_count >= SPEAKER_CHANGE_MIN_WORDS
        )

    @classmethod
    def _smooth_word_speakers(cls, units: list[dict[str, object]]) -> None:
        """Remove isolated speaker changes before they can create tiny segments."""
        if not units:
            return

        # Diarization can leave very small uncovered gaps. Attach those words to
        # the nearest known speaker instead of creating SPEAKER_UNKNOWN islands.
        for index, unit in enumerate(units):
            if unit["speaker_id"] != "SPEAKER_UNKNOWN":
                continue
            previous = next(
                (
                    candidate
                    for candidate in reversed(units[:index])
                    if candidate["speaker_id"] != "SPEAKER_UNKNOWN"
                ),
                None,
            )
            following = next(
                (
                    candidate
                    for candidate in units[index + 1 :]
                    if candidate["speaker_id"] != "SPEAKER_UNKNOWN"
                ),
                None,
            )
            if previous is None and following is None:
                continue
            if previous is None:
                unit["speaker_id"] = following["speaker_id"]
            elif following is None:
                unit["speaker_id"] = previous["speaker_id"]
            elif previous["speaker_id"] == following["speaker_id"]:
                unit["speaker_id"] = previous["speaker_id"]
            else:
                previous_gap = max(0.0, float(unit["start"]) - float(previous["end"]))
                following_gap = max(0.0, float(following["start"]) - float(unit["end"]))
                unit["speaker_id"] = (
                    previous["speaker_id"]
                    if previous_gap <= following_gap
                    else following["speaker_id"]
                )

        # A speaker change is accepted when it covers at least 0.8 seconds of
        # recognized speech OR at least two words. Short islands are attached
        # only to a stable neighboring run, preventing unstable runs from
        # repeatedly exchanging labels.
        for _ in range(len(units)):
            runs = cls._speaker_runs(units)
            stable = [cls._run_is_stable(units, start, end) for start, end in runs]
            changes: list[tuple[int, int, str]] = []
            for run_index, (start, end) in enumerate(runs):
                if stable[run_index]:
                    continue
                previous_index = run_index - 1 if run_index > 0 and stable[run_index - 1] else None
                following_index = (
                    run_index + 1
                    if run_index + 1 < len(runs) and stable[run_index + 1]
                    else None
                )
                if previous_index is None and following_index is None:
                    continue
                if previous_index is None:
                    target = str(units[runs[following_index][0]]["speaker_id"])
                elif following_index is None:
                    target = str(units[runs[previous_index][0]]["speaker_id"])
                else:
                    previous_run = runs[previous_index]
                    following_run = runs[following_index]
                    previous_speaker = str(units[previous_run[0]]["speaker_id"])
                    following_speaker = str(units[following_run[0]]["speaker_id"])
                    if previous_speaker == following_speaker:
                        target = previous_speaker
                    else:
                        previous_gap = max(
                            0.0,
                            float(units[start]["start"])
                            - float(units[previous_run[1] - 1]["end"]),
                        )
                        following_gap = max(
                            0.0,
                            float(units[following_run[0]]["start"])
                            - float(units[end - 1]["end"]),
                        )
                        target = previous_speaker if previous_gap <= following_gap else following_speaker
                changes.append((start, end, target))
            if not changes:
                break
            for start, end, target in changes:
                for unit in units[start:end]:
                    unit["speaker_id"] = target

    @classmethod
    def _segments_with_speakers(
        cls,
        raw_segments: list[dict[str, object]],
        turns: list[dict[str, object]],
        merge_adjacent_segments: bool = False,
        source_id_offset: int = 0,
    ) -> list[TranscriptSegment]:
        """Keep each ASR utterance intact and assign an acoustic speaker only.

        Word alignment must not replace, split, or duplicate the original text.
        The legacy merge argument is accepted without changing the result.
        """
        result: list[TranscriptSegment] = []
        for raw_index, raw in enumerate(raw_segments):
            start = float(raw["start"])
            end = float(raw["end"])
            text = str(raw["text"])
            result.append(
                TranscriptSegment(
                    start=start,
                    end=end,
                    text=text,
                    speaker_id=cls._speaker_for_interval(start, end, turns),
                    merge_parts=({
                        "source_id": source_id_offset + raw_index + 1,
                        "raw_index": raw_index,
                        "start": start,
                        "end": end,
                        "duration": round(max(0.0, end - start), 3),
                        "text": text,
                        "ratio": 1.0,
                    },),
                )
            )
        return result

    def _run_diarization_worker(
        self,
        source: Path,
        model_path: Path,
        device: str,
        progress: ProgressCallback | None,
    ) -> dict[str, object]:
        request = {
            "input_audio": str(source.resolve()),
            "model_path": str(model_path),
            "device": device,
        }
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        command = [sys.executable, "-m", "video_translator.steps.step2_transcription.providers.faster_whisper.diarization_worker"]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None
        if self.batch_session:
            completed, worker_error, stderr, return_code = run_batch_worker(
                self.batch_session, "diarization", {**request, "batch_session": True}, progress,
            )
        else:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr_file,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=environment,
                    creationflags=creation_flags,
                )
                assert process.stdin is not None
                assert process.stdout is not None
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.close()
                for raw_line in process.stdout:
                    try:
                        event = json.loads(raw_line.strip())
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "progress" and progress:
                        progress(int(event.get("value", 0)), str(event.get("message", "")))
                    elif event.get("type") == "result":
                        completed = event
                    elif event.get("type") == "error":
                        worker_error = event
                return_code = process.wait()
                stderr_file.seek(0)
                stderr = stderr_file.read().strip()
        if worker_error or return_code != 0 or completed is None:
            detail = str((worker_error or {}).get("technical_detail", ""))
            if stderr:
                detail = f"{detail}\n\nWorker stderr:\n{stderr}".strip()
            message = str((worker_error or {}).get("message", "Worker kết thúc bất thường."))
            raise SpeechToTextError(
                "Speaker diarization thất bại",
                message,
                "Kiểm tra thư mục model local, FFmpeg và cấu hình GPU rồi thử lại.",
                detail,
            )
        return completed

    def _run_worker(
        self,
        request: dict[str, object],
        progress: ProgressCallback | None,
    ) -> dict[str, object]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        command = [sys.executable, "-m", "video_translator.steps.step2_transcription.providers.faster_whisper.stable_worker"]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None

        if self.batch_session:
            result, worker_error, stderr, return_code = run_batch_worker(
                self.batch_session, "stt", {**request, "batch_session": True}, progress,
            )
        else:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr_file,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=environment,
                    creationflags=creation_flags,
                )
                assert process.stdin is not None
                assert process.stdout is not None
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.close()

                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("type")
                    if event_type == "progress" and progress:
                        progress(int(event.get("value", 0)), str(event.get("message", "")))
                    elif event_type == "result":
                        result = event
                    elif event_type == "error":
                        worker_error = event

                return_code = process.wait()
                stderr_file.seek(0)
                stderr = stderr_file.read().strip()

        if worker_error:
            if self.device == "Auto" and bool(worker_error.get("fallback_to_cpu", False)):
                fallback_reason = str(worker_error.get("message", "CUDA không thể khởi tạo."))
                if progress:
                    progress(5, f"GPU lỗi: {fallback_reason}; Auto đang thử lại bằng CPU…")
                fallback_request = {
                    **request,
                    "requested_device": "CPU",
                    "original_request": "Auto",
                    "fallback_reason": fallback_reason,
                }
                fallback_result = self._run_worker(fallback_request, progress)
                fallback_result["requested_device"] = "Auto"
                fallback_result["device_fallback"] = {
                    "attempted_device": "cuda",
                    "title": str(worker_error.get("title", "")),
                    "message": fallback_reason,
                    "technical_detail": str(worker_error.get("technical_detail", "")),
                    "worker_stderr": stderr,
                    "worker_exit_code": return_code,
                }
                return fallback_result
            detail = str(worker_error.get("technical_detail", ""))
            if stderr:
                detail = f"{detail}\n\nWorker stderr:\n{stderr}".strip()
            raise SpeechToTextError(
                str(worker_error.get("title", "Whisper không thể nhận dạng")),
                str(worker_error.get("message", "Worker Whisper gặp lỗi.")),
                str(worker_error.get("suggestion", "")),
                detail,
            )
        if return_code != 0 or result is None:
            raise SpeechToTextError(
                "Worker Whisper kết thúc bất thường",
                "Tiến trình nhận dạng không trả về kết quả hợp lệ.",
                "Kiểm tra dependency, model và cấu hình CPU/GPU rồi thử lại.",
                stderr or f"Worker exit code: {return_code}",
            )
        return result



