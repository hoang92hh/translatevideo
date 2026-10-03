from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication


APP_STYLE = """
QWidget { background: #0c111b; color: #e8edf6; font-family: "Segoe UI"; font-size: 13px; }
QFrame#topBar { background: #111827; border-bottom: 1px solid #253047; }
QLabel#brand, QLabel#brandLarge { color: #7dd3fc; font-weight: 800; letter-spacing: 2px; }
QLabel#brand { font-size: 18px; }
QLabel#brandLarge { font-size: 22px; }
QLabel#heroTitle { font-size: 34px; font-weight: 750; }
QLabel#dialogTitle, QLabel#pageTitle { font-size: 25px; font-weight: 750; }
QLabel#stepNumber {
    min-width: 48px; max-width: 48px; min-height: 48px; max-height: 48px;
    border-radius: 12px; background: #172554; color: #7dd3fc;
    font-size: 17px; font-weight: 800; qproperty-alignment: AlignCenter;
}
QLabel#muted { color: #8f9bb0; }
QLabel#inputValue {
    color: #c4e8ff; background: #0a1322; border: 1px solid #22304a;
    border-radius: 8px; padding: 12px;
}
QLabel#resultSummary { color: #b9f6d2; padding: 4px 0; }
QLabel#progressLabel { background: #172033; border: 1px solid #2b3a55; border-radius: 9px; padding: 9px 13px; }
QLabel#statusBadge { border-radius: 9px; padding: 7px 11px; font-size: 11px; font-weight: 800; background: #202a3d; color: #9aa7bb; }
QLabel#statusBadge[state="done"] { background: #123527; color: #86efac; }
QLabel#statusBadge[state="running"] { background: #173357; color: #7dd3fc; }
QLabel#statusBadge[state="ready"] { background: #302852; color: #c4b5fd; }
QLabel#statusBadge[state="stale"] { background: #463516; color: #fcd34d; }
QLabel#statusBadge[state="error"] { background: #4b1e28; color: #fda4af; }
QFrame#card { background: #121a29; border: 1px solid #253047; border-radius: 12px; }
QLabel#cardTitle { font-size: 15px; font-weight: 700; }
QPushButton { background: #1d293d; border: 1px solid #34445f; border-radius: 8px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #263650; border-color: #4a6389; }
QPushButton:disabled { color: #5f6b7d; background: #151c29; border-color: #252d3a; }
QPushButton#primaryButton { background: #0284c7; color: white; border: 1px solid #38bdf8; padding: 10px 18px; }
QPushButton#primaryButton:hover { background: #0ea5e9; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget {
    background: #0b1220; border: 1px solid #2a3850; border-radius: 7px; padding: 8px;
    selection-background-color: #0369a1;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #38bdf8; }
QComboBox::drop-down { border: none; width: 24px; }
QCheckBox { spacing: 8px; }
QProgressBar {
    background: #0b1220; border: 1px solid #2a3850; border-radius: 6px;
    min-height: 14px; text-align: center; color: #e8edf6;
}
QProgressBar::chunk { background: #0284c7; border-radius: 5px; }
QListWidget#recentProjects::item { padding: 12px; border-bottom: 1px solid #202c41; }
QListWidget#recentProjects::item:hover { background: #162237; }
QTableWidget { background: #0b1220; alternate-background-color: #101827; border: 1px solid #29364d; border-radius: 8px; gridline-color: #253047; selection-background-color: #164e63; }
QHeaderView::section { background: #182235; color: #aab6c9; border: none; border-bottom: 1px solid #33415a; padding: 9px; font-weight: 700; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #101725; color: #8996aa; padding: 13px 18px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #7dd3fc; background: #131d2d; border-bottom-color: #38bdf8; }
QTabBar::tab:hover { color: #d8e4f5; background: #151f30; }
QScrollArea { background: transparent; }
QScrollBar:vertical { background: #0c111b; width: 10px; }
QScrollBar::handle:vertical { background: #334155; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def apply_application_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    palette = app.palette()
    palette.setColor(palette.ColorRole.Highlight, QColor("#0284c7"))
    palette.setColor(palette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setFont(QFont("Segoe UI", 10))
