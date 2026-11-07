from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ..config.installation import load_api_key_environment
from ..infrastructure.web_bridge_service import web_bridge_service
from ..ui import MainWindow, apply_application_style


def main() -> int:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"MCMStudio.TransLanguage.{uuid4().hex}"
        )
    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parents[1] / "assets" / "translanguage_icon.png"
    app.setWindowIcon(QIcon(str(icon_path)))
    try:
        load_api_key_environment()
    except (OSError, ValueError) as exc:
        QMessageBox.critical(None, "Không đọc được api_keys.env", str(exc))
        return 1
    app.setApplicationName("TransLanguage")
    app.setOrganizationName("TransLanguage")
    apply_application_style(app)
    web_bridge_service.start()
    app.aboutToQuit.connect(web_bridge_service.stop)
    window = MainWindow()
    window.show()
    return app.exec()


