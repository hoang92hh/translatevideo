from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui import MainWindow, apply_application_style


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TransLanguage")
    app.setOrganizationName("TransLanguage")
    apply_application_style(app)
    window = MainWindow()
    window.show()
    return app.exec()

