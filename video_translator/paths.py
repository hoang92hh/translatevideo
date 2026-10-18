from __future__ import annotations

import os
import sys
from pathlib import Path


APPLICATION_HOME_ENV = "TRANSLANGUAGE_HOME"


def application_home() -> Path:
    """Return the portable application root or the repository root."""
    configured = os.environ.get(APPLICATION_HOME_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def code_root() -> Path:
    home = application_home()
    packaged = home / "app"
    if (packaged / "video_translator").is_dir():
        return packaged
    return Path(__file__).resolve().parents[1]


def environment_root() -> Path:
    return application_home() / "environment"


def downloads_root() -> Path:
    return application_home() / "downloads"


def models_root() -> Path:
    return application_home() / "models"


def reference_root() -> Path:
    return application_home() / "reference"


def extensions_root() -> Path:
    home = application_home()
    preferred = home / "extensions"
    legacy = home / "extension"
    return preferred if preferred.exists() or not legacy.exists() else legacy


def projects_root() -> Path:
    return application_home() / "projects"


def tools_root() -> Path:
    return application_home() / "tools"


def runtime_python(runtime_name: str) -> Path:
    runtime = environment_root() / runtime_name
    candidates = (
        runtime / "Scripts" / "python.exe",
        runtime / "bin" / "python",
    )
    return next((path.resolve() for path in candidates if path.is_file()), candidates[0])


def bundled_tool(program: str) -> Path | None:
    executable = f"{program}.exe" if os.name == "nt" else program
    candidates = (
        tools_root() / "ffmpeg" / "bin" / executable,
        tools_root() / "ffmpeg" / executable,
        tools_root() / executable,
    )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def ensure_application_directories() -> None:
    for path in (
        environment_root(),
        downloads_root(),
        models_root(),
        reference_root(),
        extensions_root(),
        projects_root(),
        tools_root(),
    ):
        path.mkdir(parents=True, exist_ok=True)
    ffmpeg = bundled_tool("ffmpeg")
    if ffmpeg:
        folder = str(ffmpeg.parent)
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if folder.casefold() not in {item.casefold() for item in path_parts}:
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


def running_from_portable_layout() -> bool:
    return bool(os.environ.get(APPLICATION_HOME_ENV, "").strip()) or getattr(sys, "frozen", False)
