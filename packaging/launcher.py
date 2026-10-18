from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def message(title: str, body: str, error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, body, title, flags)


def main() -> int:
    root = application_root()
    pythonw = root / "environment" / "main" / "Scripts" / "pythonw.exe"
    python = root / "environment" / "main" / "Scripts" / "python.exe"
    entrypoint = root / "app" / "main.py"
    if not entrypoint.is_file():
        entrypoint = root / "main.py"

    if not pythonw.is_file() and not python.is_file():
        setup = root / "scripts" / "setup_application.ps1"
        if not setup.is_file():
            message("TransLanguage", f"Không tìm thấy bộ cài runtime:\n{setup}", True)
            return 1
        answer = ctypes.windll.user32.MessageBoxW(
            None,
            "TransLanguage chưa có môi trường chạy trên máy này. Cài đặt ngay?",
            "Thiết lập TransLanguage",
            0x21,
        )
        if answer != 1:
            return 1
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(setup),
                "-ApplicationRoot",
                str(root),
            ],
            cwd=root,
            check=False,
        )
        if completed.returncode != 0:
            message("TransLanguage", "Cài đặt runtime chưa hoàn tất. Hãy mở Setup-TransLanguage.exe để thử lại.", True)
            return completed.returncode or 1

    executable = pythonw if pythonw.is_file() else python
    if not executable.is_file() or not entrypoint.is_file():
        message("TransLanguage", "Thiếu runtime hoặc mã ứng dụng. Hãy chạy lại bộ cài.", True)
        return 1

    environment = os.environ.copy()
    environment["TRANSLANGUAGE_HOME"] = str(root)
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(entrypoint.parent), environment.get("PYTHONPATH", "")) if value
    )
    subprocess.Popen(
        [str(executable), str(entrypoint)],
        cwd=root,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
