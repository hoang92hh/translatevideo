from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config.diarization import configured_model_path, missing_model_files
from ..errors import UserFacingError

ProgressCallback = Callable[[int, str], None]

SPEAKER_CHANGE_MIN_DURATION_SECONDS = 0.8
SPEAKER_CHANGE_MIN_WORDS = 2


class SpeechToTextError(UserFacingError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker_id: str = ""
    merge_parts: tuple[dict[str, object], ...] = ()


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
    diarization_model: str = ""
    diarization_device: str = ""
    diarization_turns: tuple[dict[str, object], ...] = ()


class SpeechToTextService:
    def __init__(
        self,
        model_name: str,
        device: str,
        language: str | None,
        vad_filter: bool,
        diarization: bool = True,
        merge_adjacent_segments: bool = True,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.language = language
        self.vad_filter = vad_filter
        self.diarization = diarization
        self.merge_adjacent_segments = merge_adjacent_segments

    @staticmethod
    def model_cache_dir() -> Path:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".cache"
        path = root / "TransLanguage" / "faster-whisper-models"
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
            "vad_filter": self.vad_filter,
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
                "Kết quả Faster Whisper không hợp lệ",
                "Worker không trả về danh sách segment hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            )
        try:
            normalized = [
                {
                    "start": float(item["start"]),
                    "end": float(item["end"]),
                    "text": str(item["text"]).strip(),
                    "words": list(item.get("words", [])),
                }
                for item in raw_segments
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechToTextError(
                "Kết quả Faster Whisper không hợp lệ",
                "Một hoặc nhiều segment thiếu timestamp hoặc nội dung hợp lệ.",
                "Mở chi tiết kỹ thuật để kiểm tra dữ liệu worker.",
                repr(raw_segments),
            ) from exc
        if not normalized:
            raise SpeechToTextError(
                "Không phát hiện lời nói",
                "Faster Whisper đã xử lý xong nhưng không tạo được segment nào.",
                "Thử chọn Original Mix, tắt Voice activity detection hoặc kiểm tra lại audio từ Step 1.",
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
                    "Kiểm tra file Voice và thư mục model local rồi chạy lại Step 2.",
                )
            segments = self._segments_with_speakers(
                normalized,
                turns,
                merge_adjacent_segments=self.merge_adjacent_segments,
            )
            diarization_model = str(diarization_result.get("model", ""))
            diarization_device = str(diarization_result.get("actual_device", ""))
            diarization_turns = tuple(dict(turn) for turn in turns)
        else:
            segments = [
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item["text"]),
                )
                for item in normalized
            ]

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
            diarization_model=diarization_model,
            diarization_device=diarization_device,
            diarization_turns=diarization_turns,
        )

    @staticmethod
    def _speaker_for_interval(start: float, end: float, turns: list[dict[str, object]]) -> str:
        best_speaker = ""
        best_overlap = 0.0
        midpoint = (start + end) / 2.0
        for turn in turns:
            turn_start = float(turn.get("start", 0.0))
            turn_end = float(turn.get("end", turn_start))
            overlap = max(0.0, min(end, turn_end) - max(start, turn_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = str(turn.get("speaker_id", ""))
            elif not best_speaker and turn_start <= midpoint <= turn_end:
                best_speaker = str(turn.get("speaker_id", ""))
        return best_speaker or "SPEAKER_UNKNOWN"

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
        merge_adjacent_segments: bool = True,
    ) -> list[TranscriptSegment]:
        word_units: list[dict[str, object]] = []
        for raw_index, raw in enumerate(raw_segments):
            words = raw.get("words")
            if not isinstance(words, list) or not words:
                word_units.append(
                    {
                        "raw_index": raw_index,
                        "start": float(raw["start"]),
                        "end": float(raw["end"]),
                        "text": str(raw["text"]),
                        "speaker_id": cls._speaker_for_interval(
                            float(raw["start"]), float(raw["end"]), turns
                        ),
                    }
                )
                continue

            valid_word_found = False
            for word in words:
                if not isinstance(word, dict):
                    continue
                valid_word_found = True
                start = float(word.get("start", raw["start"]))
                end = float(word.get("end", start))
                text = str(word.get("word", ""))
                speaker_id = cls._speaker_for_interval(start, end, turns)
                word_units.append(
                    {
                        "raw_index": raw_index,
                        "start": start,
                        "end": end,
                        "text": text.strip(),
                        "speaker_id": speaker_id,
                    }
                )
            if not valid_word_found:
                word_units.append(
                    {
                        "raw_index": raw_index,
                        "start": float(raw["start"]),
                        "end": float(raw["end"]),
                        "text": str(raw["text"]),
                        "speaker_id": cls._speaker_for_interval(
                            float(raw["start"]), float(raw["end"]), turns
                        ),
                    }
                )

        cls._smooth_word_speakers(word_units)

        atomic: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        for unit in word_units:
            if (
                current is None
                or current["raw_index"] != unit["raw_index"]
                or current["speaker_id"] != unit["speaker_id"]
            ):
                if current is not None:
                    atomic.append(current)
                current = dict(unit)
            else:
                current["end"] = unit["end"]
                current["text"] = cls._join_text(str(current["text"]), str(unit["text"]))
        if current is not None:
            atomic.append(current)

        merged: list[dict[str, object]] = []
        for source_id, item in enumerate(atomic, start=1):
            start = round(float(item["start"]), 3)
            end = round(float(item["end"]), 3)
            part = {
                "source_id": source_id,
                "start": start,
                "end": end,
                "duration": round(max(0.0, end - start), 3),
                "text": str(item["text"]).strip(),
            }
            if (
                merge_adjacent_segments
                and merged
                and merged[-1]["speaker_id"] == item["speaker_id"]
                and abs(start - float(merged[-1]["end"])) < 0.01
            ):
                merged[-1]["end"] = end
                merged[-1]["text"] = cls._join_text(
                    str(merged[-1]["text"]), str(item["text"])
                )
                parts = merged[-1]["merge_parts"]
                assert isinstance(parts, list)
                parts.append(part)
            else:
                merged.append(
                    {
                        "start": start,
                        "end": end,
                        "text": str(item["text"]).strip(),
                        "speaker_id": str(item["speaker_id"]),
                        "merge_parts": [part],
                    }
                )

        result: list[TranscriptSegment] = []
        for item in merged:
            parts = item["merge_parts"]
            assert isinstance(parts, list)
            total = sum(float(part["duration"]) for part in parts)
            ratios: list[float]
            if total > 0:
                ratios = [float(part["duration"]) / total for part in parts]
            else:
                ratios = [1.0 / len(parts)] * len(parts)
            rounded = [round(value, 8) for value in ratios]
            rounded[-1] = round(1.0 - sum(rounded[:-1]), 8)
            for part, ratio in zip(parts, rounded, strict=True):
                part["ratio"] = ratio
            result.append(
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item["text"]),
                    speaker_id=str(item["speaker_id"]),
                    merge_parts=tuple(parts),
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
        command = [sys.executable, "-m", "video_translator.services.speaker_diarization_worker"]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None
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
        command = [sys.executable, "-m", "video_translator.services.faster_whisper_worker"]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result: dict[str, object] | None = None
        worker_error: dict[str, object] | None = None

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
                if progress:
                    progress(5, "CUDA không thể khởi tạo; Auto đang thử lại bằng CPU…")
                fallback_reason = str(worker_error.get("message", "CUDA không thể khởi tạo."))
                fallback_request = {
                    **request,
                    "requested_device": "CPU",
                    "original_request": "Auto",
                    "fallback_reason": fallback_reason,
                }
                fallback_result = self._run_worker(fallback_request, progress)
                fallback_result["requested_device"] = "Auto"
                return fallback_result
            detail = str(worker_error.get("technical_detail", ""))
            if stderr:
                detail = f"{detail}\n\nWorker stderr:\n{stderr}".strip()
            raise SpeechToTextError(
                str(worker_error.get("title", "Faster Whisper không thể nhận dạng")),
                str(worker_error.get("message", "Worker Faster Whisper gặp lỗi.")),
                str(worker_error.get("suggestion", "")),
                detail,
            )
        if return_code != 0 or result is None:
            raise SpeechToTextError(
                "Worker Faster Whisper kết thúc bất thường",
                "Tiến trình nhận dạng không trả về kết quả hợp lệ.",
                "Kiểm tra dependency, model và cấu hình CPU/GPU rồi thử lại.",
                stderr or f"Worker exit code: {return_code}",
            )
        return result
