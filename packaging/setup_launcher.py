from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    setup = root / "scripts" / "setup_application.ps1"
    if not setup.is_file():
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Không tìm thấy script cài đặt:\n{setup}",
            "TransLanguage",
            0x10,
        )
        return 1
    return subprocess.call(
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
