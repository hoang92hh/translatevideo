from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings


DIARIZATION_MODEL_NAME = "pyannote-speaker-diarization-community-1"
DIARIZATION_MODEL_ENV = "TRANSLANGUAGE_DIARIZATION_MODEL"
DIARIZATION_MODEL_SETTING = "diarization/modelPath"
REQUIRED_MODEL_FILES = (
    "config.yaml",
    "embedding/pytorch_model.bin",
    "segmentation/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
)


def bundled_model_path() -> Path:
    return Path(__file__).resolve().parents[2] / "models" / DIARIZATION_MODEL_NAME


def configured_model_path() -> Path:
    selected = str(QSettings("TransLanguage", "TransLanguage").value(DIARIZATION_MODEL_SETTING, "")).strip()
    if selected:
        return Path(selected).expanduser()
    environment = os.environ.get(DIARIZATION_MODEL_ENV, "").strip()
    if environment:
        return Path(environment).expanduser()
    return bundled_model_path()


def set_configured_model_path(path: str | Path | None) -> None:
    settings = QSettings("TransLanguage", "TransLanguage")
    if path:
        settings.setValue(DIARIZATION_MODEL_SETTING, str(Path(path).expanduser().resolve()))
    else:
        settings.remove(DIARIZATION_MODEL_SETTING)
    settings.sync()


def missing_model_files(path: str | Path) -> list[str]:
    root = Path(path)
    missing: list[str] = []
    for relative in REQUIRED_MODEL_FILES:
        file_path = root / relative
        if not file_path.is_file() or file_path.stat().st_size <= 128:
            missing.append(relative)
            continue
        try:
            prefix = file_path.read_bytes()[:80]
        except OSError:
            missing.append(relative)
            continue
        if prefix.startswith(b"version https://git-lfs.github.com/spec/v1"):
            missing.append(f"{relative} (chỉ là Git LFS pointer)")
    return missing
