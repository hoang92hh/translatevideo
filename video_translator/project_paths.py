"""Portable paths in project manifests.

Only paths inside the directory shared by input and the project are converted.
Other absolute paths keep their original meaning.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


PREFIX = "project-relative:"


def srt_path_for_video(source: str | Path, *, ocr: bool = False) -> Path:
    video = Path(source).resolve()
    suffix = "_ocr.srt" if ocr else ".srt"
    return video.parent.parent / "srt" / f"{video.stem}{suffix}"


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def encode_paths(value: Any, project_root: Path, *, video: bool = False) -> Any:
    base = project_root.resolve().parent.parent if video else project_root.resolve().parent
    if isinstance(value, dict):
        return {key: encode_paths(item, project_root, video=video) for key, item in value.items()}
    if isinstance(value, list):
        return [encode_paths(item, project_root, video=video) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        path = Path(value)
        if _within(path, base):
            return PREFIX + os.path.relpath(path, project_root).replace("\\", "/")
    return value


def decode_paths(value: Any, project_root: Path, old_root: str = "", *, video: bool = False) -> Any:
    if isinstance(value, dict):
        return {key: decode_paths(item, project_root, old_root, video=video) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_paths(item, project_root, old_root, video=video) for item in value]
    if isinstance(value, str):
        if value.startswith(PREFIX):
            return str((project_root / value[len(PREFIX):]).resolve())
        if old_root and Path(value).is_absolute():
            old_base = Path(old_root).parent.parent if video else Path(old_root).parent
            new_base = project_root.parent.parent if video else project_root.parent
            path = Path(value)
            if _within(path, old_base):
                return str((new_base / path.relative_to(old_base)).resolve())
    return value
