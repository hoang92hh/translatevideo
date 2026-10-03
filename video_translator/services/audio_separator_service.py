from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..errors import UserFacingError

ProgressCallback = Callable[[int, str], None]


class AudioSeparationError(UserFacingError):
    pass


@dataclass(frozen=True, slots=True)
class SeparatedAudio:
    voice_path: str
    background_path: str
    model_name: str


class AudioSeparatorService:
    def __init__(self, model_name: str, output_dir: str | Path, ffmpeg_path: str = "Auto") -> None:
        self.model_name = model_name
        self.output_dir = Path(output_dir)
        self.ffmpeg_path = ffmpeg_path

    @staticmethod
    def model_cache_dir() -> Path:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".cache"
        path = root / "TransLanguage" / "audio-separator-models"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def separate(
        self,
        input_audio: str | Path,
        progress: ProgressCallback | None = None,
    ) -> SeparatedAudio:
        ffmpeg, ffprobe = self._configure_media_tools()
        try:
            from audio_separator.separator import Separator
        except ModuleNotFoundError as exc:
            raise AudioSeparationError(
                "Thiếu thư viện cho MDX",
                f"Không tìm thấy module Python “{exc.name or 'không xác định'}”.",
                "Chạy: python -m pip install -e .",
                repr(exc),
            ) from exc
        except (ImportError, OSError) as exc:
            detail = str(exc)
            if "Application Control" in detail or "DLL load failed" in detail:
                raise AudioSeparationError(
                    "Windows đã chặn thư viện MDX",
                    "Một DLL/PYD cần cho audio-separator không được Windows Application Control cho phép.",
                    "Kiểm tra Code Integrity log và sử dụng Python environment đã được phê duyệt.",
                    repr(exc),
                ) from exc
            raise AudioSeparationError(
                "Không thể nạp audio-separator",
                "audio-separator đã được cài nhưng không thể khởi tạo dependency.",
                "Mở chi tiết kỹ thuật để xác định module hoặc DLL gây lỗi.",
                repr(exc),
            ) from exc

        source = Path(input_audio)
        if not source.is_file():
            raise AudioSeparationError(
                "Không tìm thấy audio đầu vào",
                f"File không tồn tại: {source}",
                "Chạy lại Original Mix hoặc chọn một candidate còn tồn tại.",
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(15, "Đang tải engine và model MDX…")
        try:
            separator = Separator(
                log_level=logging.WARNING,
                model_file_dir=str(self.model_cache_dir()),
                output_dir=str(self.output_dir),
                output_format="WAV",
            )
            separator.load_model(model_filename=self.model_name)
            if progress:
                progress(30, "Đang tách Voice và Background bằng MDX…")
            generated = separator.separate(str(source))
        except Exception as exc:
            raise self._classify_runtime_error(exc, ffmpeg, ffprobe) from exc

        resolved = [self._resolve_output(path) for path in generated]
        voice = self._find_stem(resolved, ("vocals", "vocal", "voice"))
        background = self._find_stem(resolved, ("instrumental", "no_vocals", "karaoke", "background"))
        if not voice or not background:
            names = ", ".join(path.name for path in resolved)
            raise AudioSeparationError(
                "Kết quả MDX không hợp lệ",
                "Provider không trả về đủ hai stem Voice và Background.",
                "Thử model MDX khác hoặc kiểm tra file audio đầu vào.",
                f"Generated files: {names}",
            )
        voice_target = self._normalize_name(voice, "voice.wav")
        background_target = self._normalize_name(background, "background.wav")
        if progress:
            progress(98, "Đang hoàn thiện các stem…")
        return SeparatedAudio(str(voice_target), str(background_target), self.model_name)

    def _resolve_output(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.output_dir / path
        return path.resolve()

    @staticmethod
    def _find_stem(paths: list[Path], tokens: tuple[str, ...]) -> Path | None:
        return next((path for path in paths if any(token in path.name.lower() for token in tokens)), None)

    def _normalize_name(self, source: Path, filename: str) -> Path:
        if not source.is_file():
            raise AudioSeparationError(
                "Không tìm thấy stem đầu ra",
                f"Provider báo hoàn thành nhưng file không tồn tại: {source}",
                "Kiểm tra dung lượng ổ đĩa và quyền ghi của thư mục project.",
            )
        target = self.output_dir / filename
        if source != target:
            target.unlink(missing_ok=True)
            shutil.move(str(source), str(target))
        return target

    def _configure_media_tools(self) -> tuple[str, str]:
        configured = self.ffmpeg_path.strip()
        if not configured or configured.lower() == "auto":
            resolved = shutil.which("ffmpeg")
            if not resolved:
                raise AudioSeparationError(
                    "Không tìm thấy FFmpeg",
                    "MDX cần FFmpeg nhưng executable không có trong PATH.",
                    "Chọn trực tiếp ffmpeg.exe trong phần cấu hình Step 1.",
                )
            ffmpeg = Path(resolved)
        else:
            ffmpeg = Path(configured).expanduser()
            if ffmpeg.is_dir():
                ffmpeg = ffmpeg / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not ffmpeg.is_file():
            raise AudioSeparationError(
                "Đường dẫn FFmpeg không hợp lệ",
                f"Không tìm thấy executable: {ffmpeg}",
                "Nhấn “Chọn file…” và chọn ffmpeg.exe trong thư mục bin của FFmpeg.",
            )

        # WinGet thường cung cấp ffmpeg.exe qua một symbolic link trong thư mục
        # Links. Resolve link trước để có thể tìm ffprobe.exe cạnh binary thật.
        ffmpeg = ffmpeg.resolve()
        probe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        ffprobe = ffmpeg.with_name(probe_name)
        if not ffprobe.is_file():
            discovered_probe = shutil.which("ffprobe")
            if discovered_probe:
                ffprobe = Path(discovered_probe)
            else:
                raise AudioSeparationError(
                    "Không tìm thấy FFprobe",
                    f"Đã tìm thấy FFmpeg nhưng không có {probe_name} trong thư mục: {ffmpeg.parent}",
                    "Cài bản FFmpeg đầy đủ hoặc chọn ffmpeg.exe trong thư mục bin có cả ffprobe.exe.",
                )

        tool_folder = str(ffmpeg.parent.resolve())
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if tool_folder.lower() not in {part.lower() for part in path_parts}:
            os.environ["PATH"] = tool_folder + os.pathsep + os.environ.get("PATH", "")
        os.environ["FFMPEG_BINARY"] = str(ffmpeg.resolve())
        os.environ["FFPROBE_BINARY"] = str(ffprobe.resolve())
        return str(ffmpeg.resolve()), str(ffprobe.resolve())

    def _classify_runtime_error(self, exc: Exception, ffmpeg: str, ffprobe: str) -> AudioSeparationError:
        detail = str(exc)
        lowered = detail.lower()
        if isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) == 2:
            missing = getattr(exc, "filename", None) or "executable/file không được thư viện cung cấp"
            return AudioSeparationError(
                "MDX không tìm thấy công cụ hoặc file",
                f"Không tìm thấy: {missing}",
                "FFmpeg đã được cấu hình tự động. Mở chi tiết kỹ thuật nếu lỗi vẫn lặp lại.",
                f"FFmpeg: {ffmpeg}\nFFprobe: {ffprobe}\nOriginal error: {repr(exc)}",
            )
        if isinstance(exc, PermissionError):
            return AudioSeparationError(
                "MDX không thể truy cập file",
                "File audio, model hoặc thư mục output đang bị khóa hoặc không có quyền ghi.",
                "Dừng trình phát, đóng ứng dụng đang mở file và thử lại.",
                repr(exc),
            )
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
            return AudioSeparationError(
                "Không đủ dung lượng lưu trữ",
                "Không còn đủ dung lượng để tải model hoặc ghi các stem WAV.",
                f"Giải phóng dung lượng tại cache {self.model_cache_dir()} và thư mục project.",
                repr(exc),
            )
        if "out of memory" in lowered or "allocate memory" in lowered:
            return AudioSeparationError(
                "Không đủ bộ nhớ cho MDX",
                "MDX không đủ RAM hoặc VRAM để xử lý audio.",
                "Đóng bớt ứng dụng hoặc sử dụng model nhẹ hơn.",
                repr(exc),
            )
        if any(token in lowered for token in ("connection", "timeout", "ssl", "download", "http")):
            return AudioSeparationError(
                "Không thể tải model MDX",
                "Không thể kết nối hoặc tải đầy đủ model cần thiết.",
                "Kiểm tra Internet, proxy/firewall rồi chạy lại. Model đã tải một phần sẽ nằm trong cache.",
                repr(exc),
            )
        if "model" in lowered and any(token in lowered for token in ("not found", "unsupported", "invalid")):
            return AudioSeparationError(
                "Model MDX không hợp lệ",
                f"Không thể sử dụng model: {self.model_name}",
                "Chọn model khác trong Step 1 hoặc xóa bản model lỗi khỏi cache để tải lại.",
                repr(exc),
            )
        return AudioSeparationError(
            "MDX không thể tách audio",
            "Provider gặp lỗi trong quá trình tải model hoặc tách Voice/Background.",
            "Mở phần chi tiết kỹ thuật để xác định nguyên nhân cụ thể.",
            repr(exc),
        )
