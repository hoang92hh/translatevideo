from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ..config.deepseek import DEEPSEEK_PROVIDER_NAME
from ..config.opencode_go import OPENCODE_GO_PROVIDER_NAME
from ..config.prompt_templates import DEFAULT_PROMPT_TEMPLATE_ID, load_prompt_template, render_prompt_template
from ..config.web_translation import CHATGPT_WEB_PROVIDER_NAME, project_url_error
from ..errors import error_payload
from ..steps.step3_translation.providers import create_translation_provider


STORY_PROVIDERS = {
    GEMINI_PROVIDER_NAME, DEEPSEEK_PROVIDER_NAME,
    OPENCODE_GO_PROVIDER_NAME, CHATGPT_WEB_PROVIDER_NAME,
}


def validate_story_settings(settings: dict[str, Any]) -> Path:
    provider = str(settings.get("provider", GEMINI_PROVIDER_NAME))
    if provider not in STORY_PROVIDERS:
        raise ValueError("Tạo story hiện hỗ trợ Google Gemini, DeepSeek, OpenCode Go và ChatGPT Web. Chọn provider trong Thiết lập project → Step 3.")
    if provider == CHATGPT_WEB_PROVIDER_NAME:
        error = project_url_error(provider, str(settings.get("project_url", "")))
        if error:
            raise ValueError(error)
    return load_prompt_template(
        provider, str(settings.get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID)),
    ).path


def validate_video_analysis_settings(settings: dict[str, Any]) -> Path:
    return validate_story_settings(settings)


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
            validate_story_settings(self.settings)
            stem = f"story_group_{self.group_number}"
            provider_name = str(self.settings.get("provider", GEMINI_PROVIDER_NAME))
            speaker_context = bool(self.settings.get("speaker_context_translation")) and provider_name in {GEMINI_PROVIDER_NAME, DEEPSEEK_PROVIDER_NAME}
            prompt = render_prompt_template(
                provider_name,
                str(self.settings.get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID)),
                source_language=self.source_language,
                target_language="Vietnamese",
                input_filename=f"{stem}-input.json",
                output_filename=f"{stem}-response.json",
            )
            provider = create_translation_provider(
                provider_name, str(self.settings.get("model", GEMINI_DEFAULT_MODEL)),
                self.source_language, "Vietnamese", self.settings,
            )
            videos = [
                {**{key: value for key, value in video.items() if key != "speaker_segments"},
                 "source_text": video.get("speaker_segments", video.get("source_text", [])) if speaker_context else video.get("source_text", [])}
                for video in self.videos
            ]
            payload = {"source_language": self.source_language, "videos": videos}
            self.progress_changed.emit(0, "Đang gửi lời thoại cho AI viết hồ sơ câu chuyện bằng tiếng Việt…")
            markdown = provider.generate_story(
                payload, prompt, f"{stem}-input.json", f"{stem}-response.json",
            ).strip()
            if not markdown:
                raise ValueError("AI trả về nội dung story trống.")
            content = markdown + "\n"
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
        self, output_path: Path | None, source_language: str, settings: dict[str, Any],
        video: dict[str, Any], *, target_language: str = "Vietnamese",
        in_memory_story: bool = False,
    ) -> None:
        super().__init__()
        self.output_path = output_path
        self.source_language = source_language
        self.settings = dict(settings)
        self.video = dict(video)
        self.target_language = target_language
        self.in_memory_story = in_memory_story

    def run(self) -> None:
        try:
            validate_video_analysis_settings(self.settings)
            provider_name = str(self.settings.get("provider", GEMINI_PROVIDER_NAME))
            speaker_context = bool(self.settings.get("speaker_context_translation")) and provider_name in {GEMINI_PROVIDER_NAME, DEEPSEEK_PROVIDER_NAME}
            if self.in_memory_story and provider_name not in {GEMINI_PROVIDER_NAME, DEEPSEEK_PROVIDER_NAME, OPENCODE_GO_PROVIDER_NAME}:
                raise ValueError("Prompt story trong bộ nhớ chỉ dành cho Google Gemini, DeepSeek và OpenCode Go.")
            prompt = render_prompt_template(
                provider_name,
                str(self.settings.get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID)),
                source_language=self.source_language,
                target_language=self.target_language,
                input_filename="video-analysis-input.json",
                output_filename="video-analysis-response.json",
            )
            provider = create_translation_provider(
                provider_name, str(self.settings.get("model", GEMINI_DEFAULT_MODEL)),
                self.source_language, self.target_language, self.settings,
            )
            self.progress_changed.emit(0, "Đang phân tích toàn bộ nội dung video…")
            video = {
                **{key: value for key, value in self.video.items() if key != "speaker_segments"},
                "source_text": self.video.get("speaker_segments", self.video.get("source_text", [])) if speaker_context else self.video.get("source_text", []),
            }
            markdown = provider.generate_story(
                {"source_language": self.source_language, "videos": [video]},
                prompt, "video-analysis-input.json", "video-analysis-response.json",
            ).strip()
            if not markdown:
                raise ValueError("AI trả về nội dung phân tích video trống.")
            if self.in_memory_story:
                self.progress_changed.emit(100, "Đã nhận story cho video hiện tại")
                self.succeeded.emit(markdown)
                return
            if self.output_path is None:
                raise ValueError("Thiếu đường dẫn lưu hồ sơ phân tích video.")
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(
                f"# Hồ sơ phân tích video\n\n{markdown}\n", encoding="utf-8",
            )
            self.progress_changed.emit(100, f"Đã lưu {self.output_path.name}")
            self.succeeded.emit(str(self.output_path))
        except Exception as exc:
            self.failed.emit(error_payload(exc))
