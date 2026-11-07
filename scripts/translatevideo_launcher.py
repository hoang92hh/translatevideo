"""Run the bundled application source with the machine-specific Python environment."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tkinter import Tk, messagebox


def fail(message: str) -> int:
    root = Tk()
    root.withdraw()
    messagebox.showerror("TransLanguage", message)
    root.destroy()
    return 1


def main() -> int:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    python = root / "environment" / "app" / ".venv" / "Scripts" / "python.exe"
    if not python.is_file() or not (root / "environment" / "profile.txt").is_file():
        return fail("Chưa có môi trường ứng dụng. Hãy chạy setup.exe trước.")
    source = Path(getattr(sys, "_MEIPASS", root))
    if not (source / "video_translator" / "app" / "main.py").is_file():
        return fail("translatevideo.exe không chứa mã ứng dụng.")
    env = os.environ.copy()
    env["PATH"] = str(root / "tool" / "ffmpeg" / "bin") + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(source) + os.pathsep + env.get("PYTHONPATH", "")
    env["TRANSLANGUAGE_DISTRIBUTION_ROOT"] = str(root)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen([str(python), "-m", "video_translator.app"], cwd=root, env=env, creationflags=flags)
        return process.wait()
    except OSError as exc:
        return fail(f"Không mở được ứng dụng: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
