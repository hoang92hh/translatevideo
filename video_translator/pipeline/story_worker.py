from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..config.batch_prompts import PROMPT_BATCH_ROOT
from ..config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ..config.prompt_templates import PROMPT_TEMPLATE_ROOT
from ..config.web_translation import CHATGPT_WEB_PROVIDER_NAME, project_url_error
from ..errors import error_payload
from ..steps.step3_translation.providers import create_translation_provider


STORY_PROMPT_FOLDERS = {
    GEMINI_PROVIDER_NAME: "google_gemini",
    CHATGPT_WEB_PROVIDER_NAME: "chatgpt_web",
}


def validate_story_settings(settings: dict[str, Any]) -> Path:
    provider = str(settings.get("provider", GEMINI_PROVIDER_NAME))
    folder = STORY_PROMPT_FOLDERS.get(provider)
    if folder is None:
        raise ValueError("Tạo story hiện hỗ trợ Google Gemini và ChatGPT Web. Chọn provider trong Thiết lập project → Step 3.")
    if provider == CHATGPT_WEB_PROVIDER_NAME:
        error = project_url_error(provider, str(settings.get("project_url", "")))
        if error:
            raise ValueError(error)
    path = PROMPT_BATCH_ROOT / folder / "story.md"
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy prompt tạo story: {path}")
    return path


def validate_video_analysis_settings(settings: dict[str, Any]) -> Path:
    provider = str(settings.get("provider", GEMINI_PROVIDER_NAME))
    folder = STORY_PROMPT_FOLDERS.get(provider)
    if folder is None:
        raise ValueError("Phân tích video hiện chỉ hỗ trợ Google Gemini và ChatGPT Web.")
    if provider == CHATGPT_WEB_PROVIDER_NAME:
        error = project_url_error(provider, str(settings.get("project_url", "")))
        if error:
            raise ValueError(error)
    path = PROMPT_TEMPLATE_ROOT / folder / "story" / "video_analysis.md"
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy prompt phân tích video: {path}")
    return path


class StoryWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(
        self, project_root: Path, source_language: str,
        settings: dict[str, Any], videos: list[dict[str, Any]],
        selected_positions: list[int], missing_positions: list[int],
        timeline_warning_positions: list[int], group_number: int,
        output_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.source_language = source_language
        self.settings = dict(settings)
        self.videos = videos
        self.selected_positions = list(selected_positions)
        self.missing_positions = list(missing_positions)
        self.timeline_warning_positions = list(timeline_warning_positions)
        self.group_number = group_number
        self.output_path = output_path

    def run(self) -> None:
        try:
            if not self.videos:
                raise ValueError("Không có lời thoại đã lưu trong Group để tạo story.")
            prompt_path = validate_story_settings(self.settings)
            prompt = prompt_path.read_text(encoding="utf-8")
            stem = f"story_group_{self.group_number}"
            provider_name = str(self.settings.get("provider", GEMINI_PROVIDER_NAME))
            provider = create_translation_provider(
                provider_name, str(self.settings.get("model", GEMINI_DEFAULT_MODEL)),
                self.source_language, "Vietnamese", self.settings,
            )
            payload = {"source_language": self.source_language, "videos": self.videos}
            self.progress_changed.emit(0, "Đang gửi lời thoại cho AI viết hồ sơ câu chuyện bằng tiếng Việt…")
            markdown = provider.generate_story(
                payload, prompt, f"{stem}-input.json", f"{stem}-response.json",
            ).strip()
            if not markdown:
                raise ValueError("AI trả về nội dung story trống.")
            coverage = [
                f"> Group: {self.group_number}. Kích thước và thành viên Group đi theo STT đã lưu của project.",
                f"> Phạm vi: các video có STT {', '.join(map(str, self.selected_positions))} tại lúc bắt đầu batch.",
            ]
            for video in self.videos:
                coverage.append(
                    f"> Video {video['video_order']}: {video['video_filename']} (mã video: {video['video_id']})."
                )
            if self.missing_positions:
                coverage.append(
                    f"> Chưa có lời thoại của video STT {', '.join(map(str, self.missing_positions))}; hồ sơ chưa bao phủ các video này."
                )
            if self.timeline_warning_positions:
                coverage.append(
                    f"> Đã dùng lời thoại của video STT {', '.join(map(str, self.timeline_warning_positions))} còn lỗi timeline; cần kiểm tra transcript trước khi dịch."
                )
            content = "\n".join(coverage) + "\n\n" + markdown + "\n"
            folder = self.project_root / "stories"
            folder.mkdir(parents=True, exist_ok=True)
            path = self.output_path or folder / f"{stem}.md"
            with path.open("x", encoding="utf-8") as stream:
                try:
                    stream.write(content)
                except Exception:
                    stream.close()
                    path.unlink(missing_ok=True)
                    raise
            self.progress_changed.emit(100, f"Đã tạo story cho Group {self.group_number}")
            self.succeeded.emit(str(path))
        except Exception as exc:
            self.failed.emit(error_payload(exc))


class VideoAnalysisWorker(QThread):
    """Create a temporary context file for one video's legacy batch run."""

    succeeded = Signal(str)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(
        self, output_path: Path, source_language: str, settings: dict[str, Any],
        video: dict[str, Any],
    ) -> None:
        super().__init__()
        self.output_path = output_path
        self.source_language = source_language
        self.settings = dict(settings)
        self.video = dict(video)

    def run(self) -> None:
        try:
            prompt_path = validate_video_analysis_settings(self.settings)
            prompt = prompt_path.read_text(encoding="utf-8")
            provider_name = str(self.settings.get("provider", GEMINI_PROVIDER_NAME))
            provider = create_translation_provider(
                provider_name, str(self.settings.get("model", GEMINI_DEFAULT_MODEL)),
                self.source_language, "Vietnamese", self.settings,
            )
            self.progress_changed.emit(0, "Đang phân tích toàn bộ nội dung video…")
            markdown = provider.generate_story(
                {"source_language": self.source_language, "videos": [self.video]},
                prompt, "video-analysis-input.json", "video-analysis-response.json",
            ).strip()
            if not markdown:
                raise ValueError("AI trả về nội dung phân tích video trống.")
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(
                f"# Hồ sơ phân tích video\n\n{markdown}\n", encoding="utf-8",
            )
            self.progress_changed.emit(100, f"Đã lưu {self.output_path.name}")
            self.succeeded.emit(str(self.output_path))
        except Exception as exc:
            self.failed.emit(error_payload(exc))
