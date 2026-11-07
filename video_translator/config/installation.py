"""Configuration scoped to one installed copy of TransLanguage."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QSettings


API_KEY_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "OPENCODE_API_KEY")


def installation_root() -> Path:
    configured = os.environ.get("TRANSLANGUAGE_DISTRIBUTION_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[2]


def installation_settings() -> QSettings:
    normalized = os.path.normcase(str(installation_root())).encode("utf-8")
    identifier = hashlib.sha256(normalized).hexdigest()[:16]
    return QSettings("TransLanguage", f"TransLanguage-{identifier}")


def load_api_key_environment() -> Path:
    """Load only keys from this installation's file into the current process."""
    for name in API_KEY_VARIABLES:
        os.environ.pop(name, None)
    path = installation_root() / "api_keys.env"
    if not path.is_file():
        return path
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in API_KEY_VARIABLES:
            raise ValueError(f"api_keys.env dòng {line_number}: tên biến không hợp lệ.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if value:
            os.environ[name] = value
    return path
