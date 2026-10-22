from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..infrastructure.web_bridge_service import web_bridge_service
from ..ui import MainWindow, apply_application_style


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TransLanguage")
    app.setOrganizationName("TransLanguage")
    apply_application_style(app)
    web_bridge_service.start()
    app.aboutToQuit.connect(web_bridge_service.stop)
    window = MainWindow()
    window.show()
    return app.exec()


