from __future__ import annotations

import re
from urllib.parse import urlparse

from PySide6.QtCore import QSettings


CHATGPT_WEB_PROVIDER_NAME = "ChatGPT Web"
CLAUDE_WEB_PROVIDER_NAME = "Claude Web"

CHATGPT_EXTENSION_ID = "chatgpt"
CLAUDE_EXTENSION_ID = "claude"

WEB_CURRENT_MODEL = "Theo model đang chọn trên web"
WEB_MODEL_CHOICES = (WEB_CURRENT_MODEL,)

DEFAULT_TRANSLATION_PROVIDER_SETTING = "translation/defaultProvider"


def project_url_error(provider: str, value: str) -> str:
    """Return a user-facing validation error, or an empty string when valid."""
    url = value.strip()
    if not url:
        return "Nhập URL Project trước khi chạy."
    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL Project không hợp lệ."
    if parsed.scheme != "https":
        return "URL Project phải bắt đầu bằng https://."
    path = parsed.path.rstrip("/")
    if provider == CHATGPT_WEB_PROVIDER_NAME:
        if parsed.netloc.lower() != "chatgpt.com" or not re.fullmatch(
            r"/g/g-p-[^/]+/project", path
        ):
            return "Dán URL Project ChatGPT có dạng https://chatgpt.com/g/g-p-.../project."
    elif provider == CLAUDE_WEB_PROVIDER_NAME:
        if parsed.netloc.lower() != "claude.ai" or not re.match(
            r"^/(?:project|projects)/[^/]+", path
        ):
            return "Dán URL Project Claude trên claude.ai (đường dẫn /project/... hoặc /projects/...)."
    return ""


def default_translation_provider() -> str:
    return str(
        QSettings("TransLanguage", "TransLanguage").value(
            DEFAULT_TRANSLATION_PROVIDER_SETTING,
            "Google Gemini",
        )
    ).strip() or "Google Gemini"


def set_default_translation_provider(provider: str) -> None:
    settings = QSettings("TransLanguage", "TransLanguage")
    settings.setValue(DEFAULT_TRANSLATION_PROVIDER_SETTING, provider)
    settings.sync()
