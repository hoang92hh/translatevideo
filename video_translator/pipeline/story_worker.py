from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

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
    path = PROMPT_TEMPLATE_ROOT / folder / "story" / "story.md"
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy prompt tạo story: {path}")
    return path


class StoryWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(object)
    progress_changed = Signal(int, str)

    def __init__(
        self, project_root: Path, source_language: str,
        settings: dict[str, Any], videos: list[dict[str, Any]],
        selected_positions: list[int], missing_positions: list[int],
        timeline_warning_positions: list[int],
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.source_language = source_language
        self.settings = dict(settings)
        self.videos = videos
        self.selected_positions = list(selected_positions)
        self.missing_positions = list(missing_positions)
        self.timeline_warning_positions = list(timeline_warning_positions)

    def run(self) -> None:
        try:
            if not self.videos:
                raise ValueError("Không có lời thoại từ lần chạy Step 2 này để tạo story.")
            prompt_path = validate_story_settings(self.settings)
            prompt = prompt_path.read_text(encoding="utf-8")
            first, last = min(self.selected_positions), max(self.selected_positions)
            stem = f"story_{first}_{last}"
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
                f"> Phạm vi: các video có STT {', '.join(map(str, self.selected_positions))} tại lúc bắt đầu batch.",
            ]
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
            # Exclusive creation preserves existing files, including user-edited stories.
            version = 1
            while True:
                suffix = "" if version == 1 else f"_v{version}"
                path = folder / f"{stem}{suffix}.md"
                try:
                    with path.open("x", encoding="utf-8") as stream:
                        try:
                            stream.write(content)
                        except Exception:
                            stream.close()
                            path.unlink(missing_ok=True)
                            raise
                    break
                except FileExistsError:
                    version += 1
            self.progress_changed.emit(100, f"Đã lưu {path.name}")
            self.succeeded.emit(str(path))
        except Exception as exc:
            self.failed.emit(error_payload(exc))
